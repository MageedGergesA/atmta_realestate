"""Procurement dashboard: figures follow the records and the roles."""

from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestProcurementDashboard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dashboard = cls.env['realestate.procurement.dashboard']
        cls.manager = new_test_user(cls.env, login='proc_dash_manager',
                                    groups='base.group_user,atmta_roles.group_procurement_manager')
        cls.nobody = new_test_user(cls.env, login='proc_dash_nobody', groups='base.group_user')

    def _tiles(self, payload):
        return {t['key']: t for s in payload['sections'] for t in s['tiles']}

    def test_every_tile_matches_the_records_it_opens(self):
        Dashboard = self.Dashboard.with_user(self.manager)
        tiles = self._tiles(Dashboard.get_dashboard())
        self.assertIn('requests_submitted', tiles)
        for key, tile in tiles.items():
            action = Dashboard.action_drill(key)
            with self.subTest(tile=key):
                count = self.env[action['res_model']].with_user(self.manager).search_count(action['domain'])
                self.assertEqual(tile['value'], count)

    def test_every_chart_segment_matches_its_records(self):
        Dashboard = self.Dashboard.with_user(self.manager)
        for chart in Dashboard.get_dashboard()['charts']:
            for index, count in enumerate(chart['series'][0]['data']):
                action = Dashboard.action_drill_chart(chart['key'], index)
                with self.subTest(chart=chart['key'], segment=chart['labels'][index]):
                    self.assertEqual(count, self.env[action['res_model']].with_user(self.manager).search_count(action['domain']))

    def test_a_user_without_a_procurement_role_sees_nothing(self):
        payload = self.Dashboard.with_user(self.nobody).get_dashboard()
        self.assertEqual(payload['sections'], [])
        self.assertEqual(payload['charts'], [])

    def test_overview_is_the_first_entry(self):
        root = self.env.ref('atmta_procurement_app.menu_root')
        self.assertEqual(root.with_context(**{'ir.ui.menu.full_list': True}).child_id.sorted('sequence')[:1], self.env.ref('atmta_procurement_app.menu_overview'))


@tagged('post_install', '-at_install')
class TestDashboardScreen(HttpCase):
    """The screen renders in a real browser and a tile opens its list."""

    def test_the_dashboard_renders_and_drills(self):
        # The roles the dashboard's own menu is offered to.
        self.env.ref('base.user_admin').groups_id |= self.env.ref('atmta_procurement_app.menu_overview').groups_id
        self.browser_js(
            '/odoo/action-atmta_procurement_app.action_procurement_dashboard',
            '\nconst deadline = Date.now() + 20000;\nfunction wait(check, next, label) {\n    const found = check();\n    if (found) { return next(found); }\n    if (Date.now() > deadline) { console.error("timeout waiting for " + label + ": " + document.body.innerText.slice(0, 400)); return; }\n    setTimeout(() => wait(check, next, label), 200);\n}\nwait(() => document.querySelector(".o_ad_dashboard .o_ad_kpi_clickable, .o_ad_dashboard .o_ad_state"), (el) => {\n    if (el.classList.contains("o_ad_state")) { console.error("dashboard shows a state panel: " + el.innerText); return; }\n    const title = document.querySelector(".o_ad_title").innerText.trim();\n    if (!title) { console.error("no title"); return; }\n    const tiles = document.querySelectorAll(".o_ad_kpi").length;\n    el.click();\n    wait(() => document.querySelector(".o_list_view, .o_kanban_view"), () => {\n        console.log("dashboard ok: " + title + ", " + tiles + " tiles");\n        console.log("test successful");\n    }, "drill list");\n}, "dashboard tiles");\n',
            login='admin', timeout=90)
