"""Lease amendments (Phase 18)."""

from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLeaseAmendment(LeaseCase):

    def setUp(self):
        super().setUp()
        self.start = self.today.replace(day=1)
        self.end = self.start + relativedelta(years=1) - relativedelta(days=1)
        self.lease = self.make_lease(
            start=self.start, end=self.end, rent=1000.0,
            use_billing_engine=True)
        self.activate(self.lease)
        self.lease.action_generate_billing_schedule()

    def _amendment(self, **vals):
        base = {
            'contract_id': self.lease.id,
            'amendment_type': 'rent_change',
            'effective_date': self.start + relativedelta(months=6),
            'new_rent': 1200.0,
            'reason': 'Mid-term rent review.',
        }
        base.update(vals)
        return self.env['realestate.contract.amendment'].create(base)

    def _apply(self, amendment):
        amendment.action_propose()
        amendment.action_approve()
        amendment.action_sign()
        amendment.action_apply()
        return amendment

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def test_workflow_states(self):
        amendment = self._amendment()
        self.assertEqual(amendment.state, 'draft')
        amendment.action_propose()
        self.assertEqual(amendment.state, 'proposed')
        amendment.action_approve()
        self.assertEqual(amendment.state, 'approved')
        self.assertEqual(amendment.approver_id, self.env.user)
        amendment.action_sign()
        self.assertEqual(amendment.state, 'signed')
        amendment.action_apply()
        self.assertEqual(amendment.state, 'applied')
        self.assertTrue(amendment.applied_date)

    def test_cannot_apply_before_signing(self):
        amendment = self._amendment()
        amendment.action_propose()
        amendment.action_approve()
        with self.assertRaises(UserError):
            amendment.action_apply()

    # ------------------------------------------------------------------
    # Idempotency -- the headline requirement
    # ------------------------------------------------------------------
    def test_apply_is_idempotent(self):
        amendment = self._apply(self._amendment())
        applied_at = amendment.applied_date
        escalations = len(self.lease.escalation_rule_ids)
        amendment.action_apply()
        amendment.action_apply()
        self.assertEqual(amendment.applied_date, applied_at)
        self.assertEqual(len(self.lease.escalation_rule_ids), escalations)

    # ------------------------------------------------------------------
    # History preservation
    # ------------------------------------------------------------------
    def test_before_and_after_are_snapshotted(self):
        amendment = self._apply(self._amendment())
        self.assertIn('1000', amendment.previous_value)
        self.assertIn('1200', amendment.new_value)

    def test_rent_change_does_not_overwrite_the_contracted_rent(self):
        """The original commercial terms must remain readable."""
        self._apply(self._amendment())
        self.lease.invalidate_recordset()
        self.assertAlmostEqual(self.lease.price, 1000.0, places=2)
        # ...but the rent in force from the effective date is the new one.
        self.assertAlmostEqual(
            self.lease._rent_on(self.start + relativedelta(months=6)),
            1200.0, places=2)

    def test_applied_amendment_cannot_be_cancelled(self):
        amendment = self._apply(self._amendment())
        with self.assertRaises(UserError):
            amendment.action_cancel()

    # ------------------------------------------------------------------
    # Effective-date behaviour
    # ------------------------------------------------------------------
    def test_periods_before_the_effective_date_keep_the_old_rent(self):
        self._apply(self._amendment())
        early = self.lease.contract_payment_ids.filtered(
            lambda o: o.period_start < self.start + relativedelta(months=6))
        self.assertTrue(early)
        self.assertTrue(all(
            abs(o.gross_rent - 1000.0) < 0.01 for o in early))

    def test_periods_after_the_effective_date_use_the_new_rent(self):
        self._apply(self._amendment())
        later = self.lease.contract_payment_ids.filtered(
            lambda o: o.period_start >= self.start + relativedelta(months=6))
        self.assertTrue(later)
        self.assertTrue(all(
            abs(o.gross_rent - 1200.0) < 0.01 for o in later))

    def test_invoiced_periods_are_never_repriced(self):
        first = self.lease.contract_payment_ids.sorted('period_start')[0]
        first._create_invoices()
        original = first.amount
        # Effective inside the FIRST (already invoiced) period. An escalation
        # cannot take effect on the lease start date itself -- the starting
        # rent is the lease's base rent -- so day 2 is the earliest date that
        # still lands inside the billed period.
        self._apply(self._amendment(
            effective_date=self.start + relativedelta(days=1),
            new_rent=9999.0))
        first.invalidate_recordset()
        self.assertAlmostEqual(first.amount, original, places=2)

    # ------------------------------------------------------------------
    # Types
    # ------------------------------------------------------------------
    def test_term_extension(self):
        new_end = self.end + relativedelta(months=6)
        self._apply(self._amendment(
            amendment_type='term_extension', new_rent=0.0,
            new_end_date=new_end,
            effective_date=self.start + relativedelta(months=1)))
        self.lease.invalidate_recordset()
        self.assertEqual(self.lease.end_date, new_end)

    def test_term_extension_must_extend(self):
        with self.assertRaises(ValidationError):
            self._apply(self._amendment(
                amendment_type='term_extension', new_rent=0.0,
                new_end_date=self.end - relativedelta(months=1)))

    def test_term_reduction_cancels_future_obligations(self):
        new_end = self.start + relativedelta(months=6) - relativedelta(days=1)
        before = len(self.lease.contract_payment_ids)
        self._apply(self._amendment(
            amendment_type='term_reduction', new_rent=0.0,
            new_end_date=new_end,
            effective_date=self.start + relativedelta(months=1)))
        self.lease.invalidate_recordset()
        self.assertEqual(self.lease.end_date, new_end)
        self.assertLess(len(self.lease.contract_payment_ids), before)

    def test_property_addition(self):
        self._apply(self._amendment(
            amendment_type='property_addition', new_rent=0.0,
            property_id=self.parking.id, property_rent=150.0))
        allocations = self.allocations_of(self.lease)
        self.assertIn(self.parking, allocations.mapped('property_id'))

    def test_property_addition_respects_overlap(self):
        other = self.make_lease(
            prop=self.parking, start=self.start, end=self.end)
        self.activate(other)
        with self.assertRaises(ValidationError):
            self._apply(self._amendment(
                amendment_type='property_addition', new_rent=0.0,
                property_id=self.parking.id, property_rent=150.0))

    def test_property_removal_ends_rather_than_deletes(self):
        """The tenant DID occupy it -- the history must stand."""
        self._apply(self._amendment(
            amendment_type='property_removal', new_rent=0.0,
            property_id=self.unit_a.id))
        allocation = self.allocations_of(self.lease).filtered(
            lambda a: a.property_id == self.unit_a)
        self.assertTrue(allocation, "The allocation must not be deleted.")
        self.assertTrue(allocation.end_date < self.end)

    def test_party_change(self):
        self._apply(self._amendment(
            amendment_type='party_change', new_rent=0.0,
            new_partner_id=self.co_tenant.id))
        self.lease.invalidate_recordset()
        self.assertEqual(self.lease.partner_id, self.co_tenant)

    def test_deposit_change(self):
        self._apply(self._amendment(
            amendment_type='deposit_change', new_rent=0.0,
            new_deposit_amount=3000.0))
        self.lease.invalidate_recordset()
        self.assertAlmostEqual(self.lease.deposit_amount, 3000.0, places=2)

    def test_missing_payload_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._amendment(new_rent=0.0).action_propose()
