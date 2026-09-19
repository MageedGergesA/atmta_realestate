"""Property Operations dashboard: sections follow roles, figures follow records."""

from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestOperationsDashboard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dashboard = cls.env['realestate.operations.dashboard']
        cls.service_only = new_test_user(
            cls.env, login='ops_service', groups='base.group_user,real_estate_customer_service.group_service_agent')
        cls.nobody = new_test_user(cls.env, login='ops_nobody', groups='base.group_user')

    def _tiles(self, payload):
        return {t['key']: t for s in payload['sections'] for t in s['tiles']}

    def test_every_tile_matches_the_records_it_opens(self):
        payload = self.Dashboard.get_dashboard('team')
        self.assertTrue(payload['sections'])
        for key, tile in self._tiles(payload).items():
            action = self.Dashboard.action_drill(key, 'team')
            with self.subTest(tile=key):
                self.assertEqual(tile['value'], self.env[action['res_model']].search_count(action['domain']))

    def test_sections_follow_roles(self):
        sections = [s['id'] for s in self.Dashboard.with_user(self.service_only).get_dashboard()['sections']]
        self.assertEqual(sections, ['service'])
        self.assertEqual(self.Dashboard.with_user(self.nobody).get_dashboard()['sections'], [])

    def test_late_maintenance_is_flagged(self):
        prop = self.env['realestate.property'].create({'name': 'Ops Unit'})
        request = self.env['realestate.maintenance.request'].create({
            'property_id': prop.id, 'description': 'Late', 'state': 'scheduled',
            'scheduled_date': self.Dashboard._today().replace(year=self.Dashboard._today().year - 1)})
        tile = self._tiles(self.Dashboard.get_dashboard())['maintenance_late']
        self.assertTrue(tile['warning'])
        action = self.Dashboard.action_drill('maintenance_late')
        self.assertIn(request, self.env['realestate.maintenance.request'].search(action['domain']))

    def test_chart_segments_open_their_records(self):
        prop = self.env['realestate.property'].create({'name': 'Ops Unit 2'})
        self.env['realestate.maintenance.request'].create({'property_id': prop.id, 'description': 'x'})
        charts = {c['key']: c for c in self.Dashboard.get_dashboard()['charts']}
        chart = charts['maintenance_by_state']
        for index, count in enumerate(chart['series'][0]['data']):
            action = self.Dashboard.action_drill_chart('maintenance_by_state', index)
            with self.subTest(segment=chart['labels'][index]):
                self.assertEqual(count, self.env[action['res_model']].search_count(action['domain']))

    def test_overview_is_the_first_entry(self):
        root = self.env.ref('atmta_operations_app.menu_root')
        self.assertEqual(root.with_context(**{'ir.ui.menu.full_list': True}).child_id.sorted('sequence')[:1], self.env.ref('atmta_operations_app.menu_overview'))


@tagged('post_install', '-at_install')
class TestDashboardScreen(HttpCase):
    """The screen renders in a real browser and a tile opens its list."""

    def test_the_dashboard_renders_and_drills(self):
        # The roles the dashboard's own menu is offered to.
        self.env.ref('base.user_admin').groups_id |= self.env.ref('atmta_operations_app.menu_overview').groups_id
        self.browser_js(
            '/odoo/action-atmta_operations_app.action_operations_dashboard',
            '\nconst deadline = Date.now() + 20000;\nfunction wait(check, next, label) {\n    const found = check();\n    if (found) { return next(found); }\n    if (Date.now() > deadline) { console.error("timeout waiting for " + label + ": " + document.body.innerText.slice(0, 400)); return; }\n    setTimeout(() => wait(check, next, label), 200);\n}\nwait(() => document.querySelector(".o_ad_dashboard .o_ad_kpi_clickable, .o_ad_dashboard .o_ad_state"), (el) => {\n    if (el.classList.contains("o_ad_state")) { console.error("dashboard shows a state panel: " + el.innerText); return; }\n    const title = document.querySelector(".o_ad_title").innerText.trim();\n    if (!title) { console.error("no title"); return; }\n    const tiles = document.querySelectorAll(".o_ad_kpi").length;\n    el.click();\n    wait(() => document.querySelector(".o_list_view, .o_kanban_view"), () => {\n        console.log("dashboard ok: " + title + ", " + tiles + " tiles");\n        console.log("test successful");\n    }, "drill list");\n}, "dashboard tiles");\n',
            login='admin', timeout=90)
