"""The provider counts what the tile opens, and hides what a role cannot read."""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.atmta_dashboard.models.dashboard_provider import AtmtaDashboardProvider


def _sections(self, scope):
    return [
        {'id': 'contacts', 'title': 'Contacts', 'icon': 'fa-users', 'tiles': [
            {'key': 'companies', 'label': 'Companies', 'model': 'res.partner',
             'domain': [('is_company', '=', True)], 'warning_above': 0},
            {'key': 'admins_only', 'label': 'Admins only', 'model': 'res.partner',
             'domain': [], 'groups': 'base.group_system'},
            {'key': 'mine', 'label': 'My Contacts', 'model': 'res.partner',
             'domain': [], 'user_field': 'user_id'},
            {'key': 'params', 'label': 'System Parameters', 'model': 'ir.config_parameter',
             'domain': []},
        ]},
        {'id': 'hidden', 'title': 'Hidden', 'tiles': [
            {'key': 'only_admin', 'label': 'Only admin', 'model': 'ir.config_parameter', 'domain': []},
        ]},
    ]


def _charts(self, scope):
    return [{
        'key': 'by_type', 'title': 'By type', 'type': 'bar', 'labels': ['Companies', 'People'],
        'series': [{'name': 'count', 'label': 'Count', 'data': [1, 2]}],
        'drill': [
            {'key': 'c', 'label': 'Companies', 'model': 'res.partner', 'domain': [('is_company', '=', True)]},
            None,
        ],
    }]


def _quick(self):
    return [{'key': 'contacts', 'label': 'Contacts', 'action': 'base.action_partner_form'},
            {'key': 'new_contact', 'label': 'New Contact', 'model': 'res.partner', 'context': {'default_is_company': True}},
            {'key': 'gone', 'label': 'Gone', 'action': 'base.no_such_action'}]


@tagged('post_install', '-at_install')
class TestDashboardProvider(TransactionCase):

    def setUp(self):
        super().setUp()
        for name, func in (('_dashboard_sections', _sections), ('_dashboard_charts', _charts),
                           ('_dashboard_quick_actions', _quick)):
            patcher = patch.object(AtmtaDashboardProvider, name, func, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.Provider = self.env['atmta.dashboard.provider']
        self.user = new_test_user(self.env, login='dash_user', groups='base.group_user')

    def _tile(self, payload, key):
        for section in payload['sections']:
            for tile in section['tiles']:
                if tile['key'] == key:
                    return tile
        return None

    def test_a_tile_counts_what_its_list_shows(self):
        payload = self.Provider.get_dashboard()
        expected = self.env['res.partner'].search_count(
            [('is_company', '=', True), ('company_id', 'in', self.env.companies.ids + [False])])
        self.assertEqual(self._tile(payload, 'companies')['value'], expected)
        action = self.Provider.action_drill('companies')
        self.assertEqual(action['res_model'], 'res.partner')
        self.assertEqual(self.env['res.partner'].search_count(action['domain']), expected)

    def test_a_tile_the_role_cannot_read_is_left_out(self):
        payload = self.Provider.with_user(self.user).get_dashboard()
        self.assertIsNone(self._tile(payload, 'params'))
        self.assertNotIn('hidden', [s['id'] for s in payload['sections']])
        with self.assertRaises(UserError):
            self.Provider.with_user(self.user).action_drill('params')

    def test_mine_narrows_to_the_current_user(self):
        mine = self.env['res.partner'].create({'name': 'Owned', 'user_id': self.user.id})
        self.env['res.partner'].create({'name': 'Not owned'})
        Provider = self.Provider.with_user(self.user)
        team = self._tile(Provider.get_dashboard('team'), 'mine')['value']
        own = self._tile(Provider.get_dashboard('mine'), 'mine')['value']
        self.assertLess(own, team)
        action = Provider.action_drill('mine', 'mine')
        self.assertIn(mine, self.env['res.partner'].search(action['domain']))

    def test_warning_flag(self):
        tile = self._tile(self.Provider.get_dashboard(), 'companies')
        self.assertEqual(tile['warning'], tile['value'] > 0)

    def test_chart_segments_open_their_records(self):
        payload = self.Provider.get_dashboard()
        self.assertEqual(payload['charts'][0]['drillable'], [True, False])
        self.assertEqual(self.Provider.action_drill_chart('by_type', 0)['res_model'], 'res.partner')
        with self.assertRaises(UserError):
            self.Provider.action_drill_chart('by_type', 1)

    def test_quick_actions_only_list_existing_actions(self):
        payload = self.Provider.get_dashboard()
        self.assertEqual([q['key'] for q in payload['quick_actions']], ['contacts', 'new_contact'])
        self.assertEqual(self.Provider.action_quick('contacts')['res_model'], 'res.partner')
        new = self.Provider.action_quick('new_contact')
        self.assertEqual(new['views'], [[False, 'form']])
        self.assertTrue(new['context']['default_is_company'])

    def test_role_gated_tiles(self):
        admin = self.env.ref('base.user_admin')
        self.assertIsNotNone(self._tile(self.Provider.with_user(admin).get_dashboard(), 'admins_only'))
        self.assertIsNone(self._tile(self.Provider.with_user(self.user).get_dashboard(), 'admins_only'))

    def test_unknown_keys_are_refused(self):
        with self.assertRaises(UserError):
            self.Provider.action_drill('nope')
        with self.assertRaises(UserError):
            self.Provider.action_quick('nope')
