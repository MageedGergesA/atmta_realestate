"""Lease renewals (Phase 17)."""

from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLeaseRenewal(LeaseCase):

    def setUp(self):
        super().setUp()
        self.start = self.today.replace(day=1)
        self.end = self.start + relativedelta(years=1) - relativedelta(days=1)
        self.lease = self.make_lease(start=self.start, end=self.end, rent=1000.0)
        self.activate(self.lease)
        self.renewal = self.env['realestate.contract.renewal'].create({
            'contract_id': self.lease.id,
            'proposed_start_date': self.end + relativedelta(days=1),
            'proposed_end_date': self.end + relativedelta(years=1),
            'proposed_rent': 1100.0,
        })

    def test_current_terms_are_snapshotted(self):
        self.assertEqual(self.renewal.current_start_date, self.start)
        self.assertEqual(self.renewal.current_end_date, self.end)
        self.assertAlmostEqual(self.renewal.current_rent, 1000.0, places=2)

    def test_increase_is_computed(self):
        self.assertAlmostEqual(self.renewal.increase_amount, 100.0, places=2)
        self.assertAlmostEqual(self.renewal.increase_pct, 10.0, places=2)

    def test_full_workflow_creates_the_next_lease(self):
        self.renewal.action_propose()
        self.renewal.action_negotiate()
        self.renewal.action_approve()
        self.renewal.action_accept()
        self.renewal.action_create_renewal_lease()

        self.assertEqual(self.renewal.state, 'renewed')
        new_lease = self.renewal.new_contract_id
        self.assertTrue(new_lease)
        self.assertEqual(new_lease.start_date, self.end + relativedelta(days=1))
        self.assertAlmostEqual(new_lease.price, 1100.0, places=2)
        self.assertEqual(new_lease.old_contract_id, self.lease)
        self.assertEqual(new_lease.partner_id, self.tenant)

    def test_historical_lease_is_not_rewritten(self):
        """The whole reason renewals are a separate record."""
        original_end = self.lease.end_date
        original_rent = self.lease.price
        self.renewal.action_propose()
        self.renewal.action_approve()
        self.renewal.action_accept()
        self.renewal.action_create_renewal_lease()
        self.lease.invalidate_recordset()
        self.assertEqual(self.lease.end_date, original_end)
        self.assertAlmostEqual(self.lease.price, original_rent, places=2)
        self.assertTrue(self.lease.is_renewed)

    def test_renewal_lease_does_not_collide_with_its_predecessor(self):
        self.renewal.action_propose()
        self.renewal.action_approve()
        self.renewal.action_accept()
        self.renewal.action_create_renewal_lease()
        new_lease = self.renewal.new_contract_id
        # It must be activatable -- the predecessor ends the day before.
        self.activate(new_lease)
        self.assertEqual(new_lease.lifecycle_state, 'active')

    def test_renewal_must_start_after_the_current_term(self):
        self.renewal.proposed_start_date = self.end - relativedelta(months=1)
        with self.assertRaises(UserError):
            self.renewal.action_propose()

    def test_agreed_rent_overrides_the_proposal(self):
        self.renewal.action_propose()
        self.renewal.action_approve()
        self.renewal.agreed_rent = 1050.0
        self.renewal.action_accept()
        self.renewal.action_create_renewal_lease()
        self.assertAlmostEqual(
            self.renewal.new_contract_id.price, 1050.0, places=2)

    def test_cannot_create_the_lease_twice(self):
        self.renewal.action_propose()
        self.renewal.action_approve()
        self.renewal.action_accept()
        self.renewal.action_create_renewal_lease()
        with self.assertRaises(UserError):
            self.renewal.action_create_renewal_lease()

    def test_rejection_requires_a_reason(self):
        self.renewal.action_propose()
        with self.assertRaises(UserError):
            self.renewal.action_reject()

    def test_only_one_open_renewal_per_lease(self):
        from psycopg2 import IntegrityError

        from odoo.tools import mute_logger
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.cr.savepoint():
                self.env['realestate.contract.renewal'].create({
                    'contract_id': self.lease.id,
                    'proposed_start_date': self.end + relativedelta(days=1),
                    'proposed_end_date': self.end + relativedelta(years=2),
                    'proposed_rent': 1200.0,
                })
                self.env.flush_all()

    def test_charge_rules_carry_over(self):
        self.env['realestate.contract.charge.rule'].create({
            'contract_id': self.lease.id,
            'name': 'Service Charge',
            'calculation_type': 'fixed',
            'amount': 100.0,
            'frequency': 'monthly',
        })
        self.renewal.action_propose()
        self.renewal.action_approve()
        self.renewal.action_accept()
        self.renewal.action_create_renewal_lease()
        self.assertEqual(len(self.renewal.new_contract_id.charge_rule_ids), 1)

    def test_expiry_bucket_is_computed(self):
        soon = self.make_lease(
            prop=self.unit_b, start=self.today - relativedelta(months=11),
            end=self.today + relativedelta(days=20))
        self.activate(soon)
        soon.invalidate_recordset()
        self.assertEqual(soon.expiry_bucket, 'lte_30')

    def test_renewal_reminder_cron_raises_an_activity(self):
        soon = self.make_lease(
            prop=self.parking, start=self.today - relativedelta(months=11),
            end=self.today + relativedelta(days=25))
        self.activate(soon)
        created = self.env['realestate.contract']._cron_renewal_reminders()
        self.assertGreaterEqual(created, 1)
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'realestate.contract'),
            ('res_id', '=', soon.id),
        ])
        self.assertTrue(activities)

    def test_renewal_reminder_cron_is_idempotent(self):
        self.make_lease(
            prop=self.parking, start=self.today - relativedelta(months=11),
            end=self.today + relativedelta(days=25))
        lease = self.env['realestate.contract'].search(
            [('property_id', '=', self.parking.id)], limit=1)
        self.activate(lease)
        first = self.env['realestate.contract']._cron_renewal_reminders()
        second = self.env['realestate.contract']._cron_renewal_reminders()
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0)
