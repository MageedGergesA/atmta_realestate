"""Lease incentives and rent-free periods (Phase 12)."""

from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestIncentives(LeaseCase):

    def setUp(self):
        super().setUp()
        self.start = self.today.replace(day=1)
        self.lease = self.make_lease(
            start=self.start,
            end=self.start + relativedelta(years=1) - relativedelta(days=1),
            rent=1000.0, use_billing_engine=True)
        self.activate(self.lease)

    def test_first_month_free_helper(self):
        incentive = self.lease.action_add_rent_free_months(months=1)
        self.assertEqual(incentive.start_date, self.start)
        self.assertEqual(
            incentive.end_date,
            self.start + relativedelta(months=1) - relativedelta(days=1))

    def test_rent_free_month_produces_a_zero_net_obligation(self):
        """The obligation still EXISTS -- that is the whole point."""
        self.lease.action_add_rent_free_months(months=1)
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('period_start')[0]
        self.assertEqual(len(self.lease.contract_payment_ids), 12)
        self.assertAlmostEqual(first.gross_rent, 1000.0, places=2)
        self.assertAlmostEqual(first.incentive_relief, 1000.0, places=2)
        self.assertAlmostEqual(first.amount, 0.0, places=2)

    def test_commercial_history_stays_visible(self):
        """A free month must not look like a missing month."""
        self.lease.action_add_rent_free_months(months=2)
        self.lease.action_generate_billing_schedule()
        free = self.lease.contract_payment_ids.filtered(
            lambda o: o.incentive_relief > 0)
        self.assertEqual(len(free), 2)
        self.assertTrue(all(o.gross_rent > 0 for o in free))

    def test_sixty_day_free_period_spans_two_months(self):
        self.env['realestate.contract.incentive'].create({
            'contract_id': self.lease.id,
            'name': 'First 60 days free',
            'incentive_type': 'rent_free',
            'start_date': self.start,
            'end_date': self.start + relativedelta(days=59),
        })
        self.lease.action_generate_billing_schedule()
        relieved = self.lease.contract_payment_ids.filtered(
            lambda o: o.incentive_relief > 0)
        self.assertGreaterEqual(len(relieved), 2)

    def test_percentage_discount(self):
        self.env['realestate.contract.incentive'].create({
            'contract_id': self.lease.id,
            'name': '10% promotional discount',
            'incentive_type': 'percent_discount',
            'percentage': 10.0,
            'start_date': self.start,
            'end_date': self.lease.end_date,
        })
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('period_start')[0]
        self.assertAlmostEqual(first.incentive_relief, 100.0, places=2)
        self.assertAlmostEqual(first.amount, 900.0, places=2)

    def test_fixed_discount(self):
        self.env['realestate.contract.incentive'].create({
            'contract_id': self.lease.id,
            'name': 'Fixed discount',
            'incentive_type': 'fixed_discount',
            'amount': 250.0,
            'start_date': self.start,
            'end_date': self.lease.end_date,
        })
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('period_start')[0]
        self.assertAlmostEqual(first.amount, 750.0, places=2)

    def test_fit_out_period(self):
        incentive = self.env['realestate.contract.incentive'].create({
            'contract_id': self.lease.id,
            'name': 'Fit-out period',
            'incentive_type': 'fit_out',
            'start_date': self.start,
            'end_date': self.start + relativedelta(months=1) - relativedelta(days=1),
        })
        self.assertAlmostEqual(incentive.total_value, 1000.0, places=2)

    def test_relief_never_exceeds_the_rent(self):
        """Two stacked discounts must not produce a negative invoice."""
        for pct in (60.0, 60.0):
            self.env['realestate.contract.incentive'].create({
                'contract_id': self.lease.id,
                'name': '%s%% discount' % pct,
                'incentive_type': 'percent_discount',
                'percentage': pct,
                'start_date': self.start,
                'end_date': self.lease.end_date,
            })
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('period_start')[0]
        self.assertGreaterEqual(first.amount, 0.0)
        self.assertLessEqual(first.incentive_relief, first.gross_rent)

    # ------------------------------------------------------------------
    # Constraints and governance
    # ------------------------------------------------------------------
    def test_overlapping_rent_free_periods_rejected(self):
        self.lease.action_add_rent_free_months(months=2)
        with self.assertRaises(ValidationError):
            self.env['realestate.contract.incentive'].create({
                'contract_id': self.lease.id,
                'name': 'Another free period',
                'incentive_type': 'rent_free',
                'start_date': self.start,
                'end_date': self.start + relativedelta(days=20),
            })

    def test_incentive_outside_the_lease_term_rejected(self):
        with self.assertRaises(ValidationError):
            self.env['realestate.contract.incentive'].create({
                'contract_id': self.lease.id,
                'name': 'Too early',
                'incentive_type': 'rent_free',
                'start_date': self.start - relativedelta(months=2),
                'end_date': self.start - relativedelta(months=1),
            })

    def test_percentage_must_be_sane(self):
        with self.assertRaises(ValidationError):
            self.env['realestate.contract.incentive'].create({
                'contract_id': self.lease.id,
                'name': 'Absurd',
                'incentive_type': 'percent_discount',
                'percentage': 150.0,
                'start_date': self.start,
                'end_date': self.lease.end_date,
            })

    def test_approval_requires_a_reason(self):
        incentive = self.lease.action_add_rent_free_months(months=1)
        with self.assertRaises(UserError):
            incentive.action_approve()

    def test_approval_is_recorded(self):
        incentive = self.lease.action_add_rent_free_months(months=1)
        incentive.reason = 'Agreed as part of a 3-year commitment.'
        incentive.action_approve()
        self.assertEqual(incentive.approved_by_id, self.env.user)

    def test_total_incentive_value_is_reported(self):
        self.lease.action_add_rent_free_months(months=2)
        self.lease.invalidate_recordset()
        self.assertAlmostEqual(self.lease.total_incentive_value, 2000.0, places=2)
