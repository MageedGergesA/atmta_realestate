"""Multi-party leases (Phase 7)."""

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLeaseParties(LeaseCase):

    def setUp(self):
        super().setUp()
        self.lease = self.make_lease()

    def test_primary_party_is_created_from_partner_id(self):
        """A lease created the old way still gets a correct party list."""
        primary = self.lease.party_ids.filtered('is_primary')
        self.assertEqual(len(primary), 1)
        self.assertEqual(primary.partner_id, self.tenant)
        self.assertEqual(primary.role, 'tenant')

    def test_corporate_tenant_gets_the_company_role(self):
        company_partner = self.env['res.partner'].create(
            {'name': 'Acme Ltd', 'is_company': True})
        lease = self.make_lease(prop=self.unit_b, partner=company_partner)
        primary = lease.party_ids.filtered('is_primary')
        self.assertEqual(primary.role, 'company')

    def test_only_one_primary_party(self):
        with self.assertRaises(ValidationError):
            self.env['realestate.contract.party'].create({
                'contract_id': self.lease.id,
                'partner_id': self.co_tenant.id,
                'role': 'co_tenant',
                'is_primary': True,
            })

    def test_making_a_party_primary_demotes_the_other(self):
        co = self.env['realestate.contract.party'].create({
            'contract_id': self.lease.id,
            'partner_id': self.co_tenant.id,
            'role': 'co_tenant',
        })
        co.action_make_primary()
        self.assertTrue(co.is_primary)
        self.assertEqual(
            len(self.lease.party_ids.filtered('is_primary')), 1)
        # And the canonical field followed.
        self.assertEqual(self.lease.partner_id, self.co_tenant)

    def test_non_billable_role_cannot_be_primary(self):
        guarantor = self.env['realestate.contract.party'].create({
            'contract_id': self.lease.id,
            'partner_id': self.guarantor.id,
            'role': 'guarantor',
        })
        with self.assertRaises(UserError):
            guarantor.action_make_primary()

    def test_changing_partner_id_updates_the_primary_party(self):
        self.lease.partner_id = self.co_tenant
        primary = self.lease.party_ids.filtered('is_primary')
        self.assertEqual(primary.partner_id, self.co_tenant)

    def test_responsibility_must_total_100_once_every_share_is_set(self):
        primary = self.lease.party_ids.filtered('is_primary')
        self.env['realestate.contract.party'].create({
            'contract_id': self.lease.id,
            'partner_id': self.co_tenant.id,
            'role': 'co_tenant',
            'responsibility_pct': 40.0,
        })
        # Still incomplete -- no error yet, so the grid can be filled in.
        self.assertTrue(self.lease.party_ids)
        with self.assertRaises(ValidationError):
            primary.responsibility_pct = 30.0

    def test_responsibility_totalling_100_is_accepted(self):
        primary = self.lease.party_ids.filtered('is_primary')
        self.env['realestate.contract.party'].create({
            'contract_id': self.lease.id,
            'partner_id': self.co_tenant.id,
            'role': 'co_tenant',
            'responsibility_pct': 40.0,
        })
        primary.responsibility_pct = 60.0
        self.assertEqual(primary.responsibility_pct, 60.0)

    def test_blank_responsibility_is_allowed(self):
        """Most leases never declare shares; that must stay valid."""
        self.env['realestate.contract.party'].create({
            'contract_id': self.lease.id,
            'partner_id': self.co_tenant.id,
            'role': 'co_tenant',
        })
        self.assertTrue(self.lease.party_ids)

    def test_role_collections_are_exposed(self):
        self.env['realestate.contract.party'].create({
            'contract_id': self.lease.id,
            'partner_id': self.guarantor.id,
            'role': 'guarantor',
        })
        self.assertIn(self.guarantor, self.lease.guarantor_ids)

    def test_duplicate_partner_role_is_rejected(self):
        from psycopg2 import IntegrityError

        from odoo.tools import mute_logger
        self.env['realestate.contract.party'].create({
            'contract_id': self.lease.id,
            'partner_id': self.co_tenant.id,
            'role': 'occupant',
        })
        # A savepoint keeps the failed INSERT from poisoning the cursor for the
        # rest of the test transaction.
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.cr.savepoint():
                self.env['realestate.contract.party'].create({
                    'contract_id': self.lease.id,
                    'partner_id': self.co_tenant.id,
                    'role': 'occupant',
                })
                self.env.flush_all()

    def test_party_dates_must_be_ordered(self):
        with self.assertRaises(ValidationError):
            self.env['realestate.contract.party'].create({
                'contract_id': self.lease.id,
                'partner_id': self.guarantor.id,
                'role': 'guarantor',
                'start_date': self.today,
                'end_date': self.today.replace(year=self.today.year - 1),
            })
