"""Available Units: what can be let now (Phase 4; RENTAL_UX_SPEC.md §8)."""

from datetime import timedelta

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo.tests.common import new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestAvailableUnits(LeaseCase):

    def _search_filter_domain(self, name):
        Property = self.env['realestate.property']
        view = self.env.ref('atmta_property_core.view_property_search')
        arch = etree.fromstring(Property.get_view(view.id, 'search')['arch'])
        node = arch.xpath("//filter[@name='%s']" % name)
        self.assertTrue(node, name)
        return safe_eval(node[0].get('domain'), {
            'context_today': lambda: self.today,
            'relativedelta': relativedelta,
        })

    def _found(self, name, unit):
        return unit in self.env['realestate.property'].search(
            self._search_filter_domain(name) + [('id', '=', unit.id)])

    def test_the_action_lists_leasable_units_available_now_first(self):
        action = self.env.ref('atmta_real_estate.action_available_units')
        self.assertEqual(safe_eval(action.domain), [('is_leasable', '=', True)])
        self.assertIn('search_default_available_to_lease', action.context)
        first = action.view_ids.sorted('sequence')[:1]
        self.assertEqual(first.view_id, self.env.ref('atmta_real_estate.view_available_units_list'))

    def test_the_list_shows_what_a_leasing_agent_compares(self):
        view = self.env.ref('atmta_real_estate.view_available_units_list')
        arch = etree.fromstring(
            self.env['realestate.property'].get_view(view.id, 'list')['arch'])
        columns = arch.xpath('//list/field[not(@column_invisible)]/@name')
        for name in ('property_code', 'parent_id', 'property_type_id', 'usage_category',
                     'bedroom_count', 'area_sqm', 'furnished_status', 'available_from',
                     'occupancy_status'):
            self.assertIn(name, columns)
        self.assertEqual(arch.xpath('//list/@default_order'), ['available_from, property_code'])

    def test_new_lease_opens_a_draft_lease_for_the_unit(self):
        action = self.unit_b.action_new_lease()
        self.assertEqual(action['res_model'], 'realestate.contract')
        self.assertEqual(action['context']['default_property_id'], self.unit_b.id)
        self.assertEqual(action['context']['default_company_id'], self.company.id)
        lease = self.env['realestate.contract'].with_context(**action['context']).create({
            'partner_id': self.tenant.id,
            'start_date': self.today,
            'end_date': self.today + relativedelta(years=1),
            'price': 1000.0,
            'currency_id': self.currency.id,
        })
        self.assertEqual(self.allocations_of(lease).property_id, self.unit_b)

    def test_new_lease_is_offered_on_available_units_to_leasing_agents(self):
        agent = new_test_user(
            self.env, login='re_avail_agent',
            groups='base.group_user,atmta_real_estate.group_rental_agent',
            company_id=self.company.id)
        for xmlid, view_type in (('atmta_real_estate.view_available_units_list', 'list'),
                                 ('atmta_property_core.view_property_form', 'form')):
            view = self.env.ref(xmlid)
            arch = etree.fromstring(self.env['realestate.property'].with_user(agent).get_view(
                view.id, view_type)['arch'])
            buttons = arch.xpath("//button[@name='action_new_lease']")
            self.assertTrue(buttons, xmlid)
            self.assertEqual(buttons[0].get('invisible'), 'not is_available_for_lease')

    def test_available_within_30_days_finds_units_freeing_up_soon(self):
        soon = self.unit_b
        soon.available_from = self.today + timedelta(days=10)
        later = self.parking
        later.available_from = self.today + timedelta(days=60)
        self.assertTrue(self._found('available_within_30', soon))
        self.assertFalse(self._found('available_within_30', later))
        self.assertFalse(self._found('available_within_30', self.unit_a), "Available now.")

    def test_awaiting_signature_finds_units_reserved_by_an_unsigned_lease(self):
        lease = self.make_lease(prop=self.unit_b)
        lease.action_to_proposal()
        lease.action_submit_for_approval()
        lease.action_approve_lease()
        self.assertEqual(lease.lifecycle_state, 'pending_signature')
        self.assertTrue(self._found('awaiting_signature', self.unit_b))
        self.assertFalse(self._found('awaiting_signature', self.unit_a))

    def test_furnished_finds_furnished_and_semi_furnished_units(self):
        self.unit_a.furnished_status = 'semi'
        self.unit_b.furnished_status = 'unfurnished'
        self.assertTrue(self._found('furnished', self.unit_a))
        self.assertFalse(self._found('furnished', self.unit_b))

    def test_the_unit_form_no_longer_shows_the_legacy_rental_history(self):
        view = self.env.ref('atmta_property_core.view_property_form')
        arch = etree.fromstring(
            self.env['realestate.property'].get_view(view.id, 'form')['arch'])
        self.assertFalse(arch.xpath("//field[@name='rental_history_ids']"))
        self.assertTrue(arch.xpath("//page[@name='lease_allocations']"))
