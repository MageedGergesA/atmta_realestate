"""The Overview's map card (decision 15 Sep 2026: add the map to the dashboard).

The map existed only as its own screen (Units → Map); the first-generation
dashboard had a map card that was hidden and never loaded. The card shows the
leasable units that have coordinates, coloured by status, and opens a unit or
the full map through the backend.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestDashboardMap(LeaseCase):

    def setUp(self):
        super().setUp()
        self.Dashboard = self.env['realestate.rental.dashboard'].with_context(
            allowed_company_ids=[self.company.id])

    def _units(self, payload):
        return {unit['id']: unit for unit in payload['units']}

    def test_located_leasable_units_are_on_the_map(self):
        self.unit_a.write({'latitude': 24.7136, 'longitude': 46.6753})
        self.building.write({'latitude': 24.71, 'longitude': 46.67})
        payload = self.Dashboard.get_map()
        units = self._units(payload)

        self.assertIn(self.unit_a.id, units)
        self.assertNotIn(self.building.id, units, "a building is a container, not a unit")
        self.assertNotIn(self.unit_b.id, units, "a unit without coordinates cannot be placed")
        unit = units[self.unit_a.id]
        self.assertEqual((unit['lat'], unit['lng'], unit['code'], unit['name']),
                         (24.7136, 46.6753, 'TST-A-101', 'Unit A-101'))
        self.assertEqual(unit['status'], self.unit_a.state)
        self.assertEqual(unit['status_label'], dict(
            self.unit_a._fields['state']._description_selection(self.env))[self.unit_a.state])
        self.assertGreaterEqual(payload['without_coordinates'], 1)
        self.assertFalse(payload['truncated'])

    def test_another_companys_units_are_not_sent(self):
        other_company = self.env['res.company'].create({'name': 'Map Other Company'})
        stranger = self.env['realestate.property'].create({
            'name': 'Elsewhere 1', 'property_code': 'MAP-OTHER-1', 'hierarchy_level': 'unit',
            'usage_category': 'apartment', 'company_id': other_company.id,
            'latitude': 30.0444, 'longitude': 31.2357,
        })
        self.unit_a.write({'latitude': 24.7136, 'longitude': 46.6753})
        units = self._units(self.Dashboard.get_map())
        self.assertIn(self.unit_a.id, units)
        self.assertNotIn(stranger.id, units)

    def test_a_leasing_agent_gets_the_map_and_opens_a_unit(self):
        agent = new_test_user(self.env, login='re_map_agent', company_id=self.company.id,
                              groups='base.group_user,atmta_real_estate.group_rental_agent')
        self.unit_a.write({'latitude': 24.7136, 'longitude': 46.6753})
        dashboard = self.Dashboard.with_user(agent)
        self.assertIn(self.unit_a.id, self._units(dashboard.get_map()))

        action = dashboard.action_open_unit(self.unit_a.id)
        self.assertEqual((action['type'], action['res_model'], action['res_id'], action['views']),
                         ('ir.actions.act_window', 'realestate.property', self.unit_a.id,
                          [(False, 'form')]))

    def test_the_full_map_opens_from_the_card(self):
        action = self.Dashboard.action_open_map()
        self.assertEqual((action['type'], action['tag']),
                         ('ir.actions.client', 'realestate.properties_map'))

    def test_opening_a_missing_or_forbidden_unit_is_refused(self):
        with self.assertRaises(UserError):
            self.Dashboard.action_open_unit(0)
        employee = new_test_user(self.env, login='re_map_employee', company_id=self.company.id,
                                 groups='base.group_user')
        with self.assertRaises(AccessError):
            self.Dashboard.with_user(employee).action_open_unit(self.unit_a.id)
