"""Multi-company isolation and integrity (Phase 28)."""

from odoo.exceptions import ValidationError
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestMultiCompany(LeaseCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = cls.env['res.company'].create({'name': 'Second Estate Co'})
        cls.unit_b_co = cls.env['realestate.property'].create({
            'name': 'Other Co Unit',
            'property_code': 'OTH-U-001',
            'hierarchy_level': 'unit',
            'usage_category': 'apartment',
            'area_sqm': 90.0,
            'company_id': cls.company_b.id,
        })

    def test_lease_cannot_use_another_companys_property(self):
        with self.assertRaises(ValidationError):
            self.make_lease(prop=self.unit_b_co)

    def test_allocation_cannot_cross_companies(self):
        lease = self.make_lease(prop=self.unit_a)
        with self.assertRaises(ValidationError):
            self.env['realestate.contract.property.line'].create({
                'contract_id': lease.id,
                'property_id': self.unit_b_co.id,
                'start_date': lease.start_date,
                'end_date': lease.end_date,
            })

    def test_property_codes_may_repeat_across_companies(self):
        """The pre-upgrade global UNIQUE made this impossible."""
        twin = self.env['realestate.property'].create({
            'name': 'Same code, other company',
            'property_code': self.unit_a.property_code,
            'hierarchy_level': 'unit',
            'company_id': self.company_b.id,
        })
        self.assertEqual(twin.property_code, self.unit_a.property_code)
        self.assertNotEqual(twin.company_id, self.unit_a.company_id)

    def test_property_codes_still_unique_within_a_company(self):
        """Relaxing the constraint to per-company must not lose it entirely.

        The database constraint fires before the Python ``@api.constrains``,
        so an IntegrityError is the expected failure mode; the savepoint keeps
        it from poisoning the test transaction.
        """
        from psycopg2 import IntegrityError

        from odoo.tools import mute_logger
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.cr.savepoint():
                self.env['realestate.property'].create({
                    'name': 'Duplicate',
                    'property_code': self.unit_a.property_code,
                    'hierarchy_level': 'unit',
                    'company_id': self.company.id,
                })
                self.env.flush_all()

    def test_record_rule_hides_other_companies_leases(self):
        lease_a = self.make_lease(prop=self.unit_a)
        user_b = new_test_user(
            self.env, login='re_mc_user_b',
            groups='base.group_user,atmta_real_estate.group_rental_manager',
            company_id=self.company_b.id)
        user_b.company_ids = [(4, self.company_b.id)]
        visible = self.env['realestate.contract'].with_user(user_b).search([])
        self.assertNotIn(lease_a, visible)

    def test_record_rule_hides_other_companies_properties(self):
        user_b = new_test_user(
            self.env, login='re_mc_user_b2',
            groups='base.group_user,atmta_real_estate.group_rental_manager',
            company_id=self.company_b.id)
        user_b.company_ids = [(4, self.company_b.id)]
        visible = self.env['realestate.property'].with_user(user_b).search([])
        self.assertNotIn(self.unit_a, visible)
        self.assertIn(self.unit_b_co, visible)

    def test_obligations_inherit_the_lease_company(self):
        lease = self.make_lease(prop=self.unit_a, use_billing_engine=True)
        self.activate(lease)
        lease.action_generate_billing_schedule()
        self.assertTrue(lease.contract_payment_ids)
        self.assertTrue(all(
            o.company_id == self.company for o in lease.contract_payment_ids))

    def test_deposit_inherits_the_lease_company(self):
        lease = self.make_lease(prop=self.unit_a)
        deposit = self.env['realestate.contract.deposit'].create({
            'contract_id': lease.id,
            'partner_id': self.tenant.id,
            'requested_amount': 1000.0,
        })
        self.assertEqual(deposit.company_id, self.company)

    def test_each_company_has_its_own_proration_setting(self):
        self.company.re_proration_method = 'actual'
        self.company_b.re_proration_method = 'thirty_day'
        self.assertEqual(self.company.re_proration_method, 'actual')
        self.assertEqual(self.company_b.re_proration_method, 'thirty_day')
