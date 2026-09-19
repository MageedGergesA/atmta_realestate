"""Tenants are contacts that hold a lease (Phase 4; RENTAL_UX_SPEC.md §9)."""

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo.tests.common import new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestTenants(LeaseCase):

    def _partner_form(self, user):
        view = self.env.ref('base.view_partner_form')
        return etree.fromstring(
            self.env['res.partner'].with_user(user).get_view(view.id, 'form')['arch'])

    def test_a_contact_becomes_a_tenant_when_it_holds_a_lease(self):
        prospect = self.env['res.partner'].create({'name': 'Prospect Only'})
        self.assertFalse(prospect.is_rental_tenant)
        self.make_lease(partner=prospect)
        self.assertTrue(prospect.is_rental_tenant)
        Partner = self.env['res.partner']
        self.assertIn(prospect, Partner.search([('is_rental_tenant', '=', True)]))

    def test_the_tenants_screen_lists_tenants_only(self):
        action = self.env.ref('atmta_real_estate.action_rental_tenants')
        self.assertEqual(safe_eval(action.domain), [('is_rental_tenant', '=', True)])
        self.assertEqual(action.view_ids.sorted('sequence')[:1].view_id,
                         self.env.ref('atmta_real_estate.view_partner_tenant_list'))
        menu = self.env.ref('atmta_real_estate.menu_rental_tenants')
        self.assertEqual(menu.parent_id, self.env.ref('atmta_real_estate.real_estate_menu_root'))
        self.assertIn('No tenants yet', action.help)

    def test_current_lease_prefers_a_live_lease(self):
        pending = self.make_lease(prop=self.unit_b, start=self.today + relativedelta(months=1))
        pending.action_to_proposal()
        pending.action_submit_for_approval()
        pending.action_approve_lease()
        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.rental_current_lease_id, pending)
        live = self.activate(self.make_lease(prop=self.unit_a))
        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.rental_current_lease_id, live)

    def test_balance_due_sums_invoiced_outstanding_obligations(self):
        lease = self.activate(self.make_lease(
            prop=self.parking, start=self.today, end=self.today + relativedelta(years=1),
            rent=500.0, use_billing_engine=True))
        lease.action_generate_billing_schedule()
        first = lease.contract_payment_ids.sorted('date_due')[0]
        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.rental_balance_due, 0.0, "Nothing invoiced yet.")
        first._create_invoices()
        self.env.invalidate_all()
        self.assertAlmostEqual(self.tenant.rental_balance_due, first.amount_residual, places=2)
        self.assertEqual(self.tenant.rental_outstanding_obligation_ids, first)

    def test_rental_details_are_on_a_rental_tab_for_rental_roles_only(self):
        rental_user = new_test_user(
            self.env, login='re_tenant_rental',
            groups='base.group_user,atmta_real_estate.group_rental_user',
            company_id=self.company.id)
        outsider = new_test_user(
            self.env, login='re_tenant_outsider', groups='base.group_user',
            company_id=self.company.id)
        arch = self._partner_form(rental_user)
        page = arch.xpath("//page[@name='rental']")
        self.assertTrue(page)
        for name in ('id_number', 'cr_number', 'rental_lease_ids',
                     'rental_outstanding_obligation_ids', 'rental_deposit_ids'):
            self.assertTrue(page[0].xpath(".//field[@name='%s']" % name), name)
        outsider_arch = self._partner_form(outsider)
        self.assertFalse(outsider_arch.xpath("//page[@name='rental']"))
        self.assertFalse(outsider_arch.xpath("//field[@name='id_number']"))

    def test_the_tenant_list_shows_contact_lease_and_balance(self):
        view = self.env.ref('atmta_real_estate.view_partner_tenant_list')
        arch = etree.fromstring(self.env['res.partner'].get_view(view.id, 'list')['arch'])
        columns = arch.xpath('//list/field[not(@column_invisible)]/@name')
        self.assertEqual(columns, ['name', 'phone', 'email', 'rental_current_lease_id',
                                   'rental_balance_due'])
