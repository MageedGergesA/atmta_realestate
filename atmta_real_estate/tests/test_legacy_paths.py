"""Legacy paths follow the enterprise rules.

Two things are pinned here.

**One billing path per lease.** A lease invoiced for its whole term the legacy
way (one invoice through the sales bridge, linked only by ``invoice_id``) keeps
that invoice until it ends. Its billing obligations stay uninvoiced by design,
so nothing may invoice them again per period, and the two schedule generators
may not run over each other's rows.

**Legacy lease buttons run the lifecycle checks.** Confirm, Activate, Terminate
and Reset used to move a lease directly, skipping approval, the self-approval
rule, the signature and allocation checks, and the termination settlement.
"""

from dateutil.relativedelta import relativedelta

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class LegacyPathCase(LeaseCase):

    def _to_pending_approval(self, lease):
        lease.action_to_proposal()
        lease.action_submit_for_approval()
        self.assertEqual(lease.lifecycle_state, 'pending_approval')
        return lease

    def _to_pending_signature(self, lease):
        self.company.re_allow_self_approval = True
        self._to_pending_approval(lease)
        lease.action_approve_lease()
        self.assertEqual(lease.lifecycle_state, 'pending_signature')
        return lease

    def _posted_invoice(self):
        """A posted customer invoice, taken from an engine lease on another unit.

        What these tests exercise is the billing authority, not the sales-order
        bridge that raises a whole-term invoice, so any posted invoice serves.
        """
        donor = self.make_lease(prop=self.unit_b, use_billing_engine=True)
        self.activate(donor)
        donor.action_generate_billing_schedule()
        obligation = donor.contract_payment_ids.sorted('date_due')[:1]
        obligation._create_invoices()
        self.assertEqual(obligation.move_id.state, 'posted')
        return obligation.move_id

    def _whole_term_lease(self):
        lease = self._to_pending_signature(self.make_lease())
        lease.invoice_id = self._posted_invoice()
        self.assertEqual(lease._billing_authority(), 'whole_lease')
        return lease

    def _legacy_row(self, lease):
        """An obligation shaped like the legacy generator's: no billing period."""
        return self.env['realestate.contract.payment'].create({
            'contract_id': lease.id,
            'date_due': self.today,
            'amount': 500.0,
        })


class TestOneBillingPathPerLease(LegacyPathCase):

    def test_a_whole_term_lease_cannot_be_invoiced_per_period(self):
        lease = self._whole_term_lease()
        row = self._legacy_row(lease)
        with self.assertRaises(UserError) as caught:
            row._create_invoices()
        self.assertIn('whole term', str(caught.exception))
        with self.assertRaises(UserError):
            lease.action_invoice_due_obligations()
        self.assertFalse(row.move_id, "nothing may be invoiced a second time")

    def test_a_whole_term_lease_cannot_get_an_engine_schedule(self):
        lease = self._whole_term_lease()
        lease.use_billing_engine = True
        with self.assertRaises(UserError) as caught:
            lease.action_generate_billing_schedule()
        self.assertIn('whole term', str(caught.exception))

    def test_a_whole_term_invoice_is_refused_on_an_engine_lease(self):
        lease = self._to_pending_signature(self.make_lease(use_billing_engine=True))
        self.assertEqual(lease.state, 'confirmed')
        with self.assertRaises(UserError) as caught:
            lease.action_generate_invoices()
        self.assertIn('billing engine', str(caught.exception))
        self.assertFalse(lease.invoice_id)

    def test_the_retired_generator_leaves_an_engine_schedule_alone(self):
        lease = self.make_lease(use_billing_engine=True)
        lease.action_generate_billing_schedule()
        rows = lease.contract_payment_ids
        self.assertTrue(rows)
        with self.assertRaises(UserError) as caught:
            lease.action_generate_payment_schedule()
        self.assertIn('retired', str(caught.exception))
        self.assertEqual(lease.contract_payment_ids, rows,
                         "the legacy generator must not delete engine rows")

    def test_the_legacy_generator_is_retired_for_every_lease(self):
        """Its plans now belong to Development & Sales, so nothing can feed it."""
        lease = self.make_lease(use_billing_engine=False)
        for method in ('action_generate_payment_schedule', 'action_generate_payment_lines'):
            with self.assertRaises(UserError, msg=method) as caught:
                getattr(lease, method)()
            self.assertIn('retired', str(caught.exception))
        self.assertFalse(lease.contract_payment_ids)

    def test_new_leases_use_the_billing_engine(self):
        self.assertTrue(self.make_lease().use_billing_engine)

    def test_the_engine_refuses_a_lease_that_still_has_legacy_rows(self):
        lease = self.make_lease(use_billing_engine=True)
        self._legacy_row(lease)
        with self.assertRaises(UserError) as caught:
            lease.action_generate_billing_schedule()
        self.assertIn('legacy schedule', str(caught.exception))
        self.assertEqual(len(lease.contract_payment_ids), 1,
                         "no parallel schedule may be created beside the legacy rows")

    def test_per_payment_invoicing_is_retired(self):
        lease = self.make_lease()
        self._legacy_row(lease)
        with self.assertRaises(UserError):
            lease.action_create_invoices()
        self.assertFalse(lease.contract_payment_ids.move_id)


class TestLegacyLeaseButtonsFollowTheLifecycle(LegacyPathCase):

    def test_confirm_cannot_be_used_to_approve_your_own_lease(self):
        """Run as a real Rental Manager, not the test superuser.

        The self-approval rule deliberately exempts the superuser, and the test
        environment runs as one, so asserting it there would prove nothing.
        """
        self.company.re_allow_self_approval = False
        manager = new_test_user(
            self.env, login='w26_rental_manager',
            groups='base.group_user,atmta_real_estate.group_rental_manager',
            company_id=self.company.id)
        manager.company_ids = [(4, self.company.id)]
        lease = self.env['realestate.contract'].with_user(manager).create({
            'partner_id': self.tenant.id,
            'property_id': self.unit_a.id,
            'is_single_property': True,
            'is_multi_property': False,
            'start_date': self.today,
            'end_date': self.today + relativedelta(years=1, days=-1),
            'price': 1000.0,
            'company_id': self.company.id,
            'currency_id': self.currency.id,
        })
        lease.action_to_proposal()
        lease.action_submit_for_approval()
        self.assertEqual(lease.state, 'ready')
        with self.assertRaises(AccessError) as caught:
            lease.action_confirm()
        self.assertIn('cannot approve it yourself', str(caught.exception))
        self.assertEqual(lease.lifecycle_state, 'pending_approval')

    def test_confirm_sends_a_proposal_to_approval_when_the_company_requires_it(self):
        self.company.re_require_lease_approval = True
        lease = self.make_lease()
        lease.action_to_proposal()
        lease.action_confirm()
        self.assertEqual(lease.lifecycle_state, 'pending_approval')

    def test_confirm_sends_a_proposal_to_signature_when_approval_is_not_required(self):
        self.company.re_require_lease_approval = False
        lease = self.make_lease()
        lease.action_to_proposal()
        lease.action_confirm()
        self.assertEqual(lease.lifecycle_state, 'pending_signature')

    def test_the_legacy_activate_requires_a_signature(self):
        lease = self._whole_term_lease()
        self.assertEqual(lease.state, 'invoiced')
        self.assertEqual(lease.signature_status, 'pending')
        with self.assertRaises(UserError) as caught:
            lease.action_activate()
        self.assertIn('not been signed', str(caught.exception))
        self.assertEqual(lease.lifecycle_state, 'pending_signature')

    def test_the_legacy_terminate_opens_the_termination_process(self):
        lease = self.activate(self.make_lease())
        result = lease.action_terminate()
        self.assertEqual(result.get('res_model'), 'realestate.contract.termination')
        self.assertEqual(lease.lifecycle_state, 'active',
                         "the lease ends through the termination, not on the spot")

    def test_reset_to_draft_goes_through_the_managed_reopen(self):
        lease = self.make_lease()
        lease.action_to_proposal()
        lease.action_reset_to_draft()
        self.assertEqual(lease.lifecycle_state, 'draft')

    def test_the_legacy_deposit_buttons_are_refused(self):
        lease = self.make_lease()
        lease.deposit_amount = 1000.0
        for method in ('action_record_deposit', 'action_refund_deposit',
                       'action_partial_refund_deposit', 'action_forfeit_deposit',
                       'action_reset_deposit'):
            with self.assertRaises(UserError, msg=method):
                getattr(lease, method)()
        self.assertEqual(lease.deposit_state, 'none')
