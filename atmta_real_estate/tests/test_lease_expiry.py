"""One lease-expiry authority.

Two scheduled jobs used to end leases. The legacy "Check Contract Expiry" job
ended every lease whose legacy status read active once its term passed. It
ignored the company switch, leases on notice and arrears, and because it ran
daily beside ``_cron_expire_leases`` it overrode that job's safeguards.

These tests pin the single authority: whichever entry point runs, the
safeguards hold.
"""

from dateutil.relativedelta import relativedelta

from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLeaseExpiryAuthority(LeaseCase):

    def _expired_lease(self, **kwargs):
        """An active lease whose term ended a month ago."""
        start = self.today - relativedelta(months=13)
        end = self.today - relativedelta(months=1)
        lease = self.make_lease(start=start, end=end, rent=1000.0, **kwargs)
        self.activate(lease)
        self.assertEqual(lease.lifecycle_state, 'active')
        return lease

    def _run_legacy_entry_point(self):
        self.env['realestate.contract'].check_contract_expiry()

    def test_only_one_expiry_job_is_installed(self):
        self.assertFalse(
            self.env.ref('atmta_real_estate.ir_cron_check_contract_expiry',
                         raise_if_not_found=False),
            "the legacy expiry job must not be installed")
        Cron = self.env['ir.cron'].with_context(active_test=False)
        self.assertFalse(Cron.search([('code', 'ilike', 'check_contract_expiry')]),
                         "no scheduled job may call the legacy entry point")
        self.assertEqual(len(Cron.search([('code', 'ilike', '_cron_expire_leases')])), 1)

    def test_a_settled_expired_lease_is_ended_through_either_entry_point(self):
        lease = self._expired_lease()
        self._run_legacy_entry_point()
        self.assertEqual(lease.lifecycle_state, 'ended')

    def test_a_lease_with_arrears_stays_active(self):
        """The case the legacy job got wrong: it ended leases that still owe rent."""
        lease = self._expired_lease(use_billing_engine=True)
        lease.action_generate_billing_schedule()
        due = lease.contract_payment_ids.filtered(
            lambda o: o.date_due and o.date_due <= self.today
        ).sorted('date_due')[:1]
        self.assertTrue(due, "the expired lease must have a past-due obligation")
        due._create_invoices()
        lease.invalidate_recordset()
        self.assertIn(lease.payment_status, ('not_paid', 'partial', 'overdue'))

        self._run_legacy_entry_point()
        self.assertEqual(lease.lifecycle_state, 'active',
                         "a lease with arrears stays active so it remains on the arrears reports")

    def test_a_lease_on_notice_is_left_to_its_notice_process(self):
        lease = self._expired_lease()
        lease.action_give_notice()
        self.assertEqual(lease.lifecycle_state, 'notice')
        self._run_legacy_entry_point()
        self.assertEqual(lease.lifecycle_state, 'notice')

    def test_a_company_that_switched_expiry_off_keeps_its_leases(self):
        self.company.re_auto_expire_leases = False
        lease = self._expired_lease()
        self._run_legacy_entry_point()
        self.assertEqual(lease.lifecycle_state, 'active')
