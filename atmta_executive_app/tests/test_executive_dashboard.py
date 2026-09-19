"""Executive dashboard: every headline is the list it opens, in one currency."""

from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestExecutiveDashboard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dashboard = cls.env['realestate.executive.dashboard']
        cls.nobody = new_test_user(cls.env, login='exec_nobody', groups='base.group_user')

    def _tiles(self, payload):
        return {t['key']: t for s in payload['sections'] for t in s['tiles']}

    def test_every_counted_tile_matches_the_records_it_opens(self):
        tiles = self._tiles(self.Dashboard.get_dashboard())
        self.assertTrue({'occupancy', 'outstanding_rent', 'reservations', 'milestones_delayed',
                         'cheques_due_30', 'studies_approved'} <= set(tiles))
        for key, tile in tiles.items():
            action = self.Dashboard.action_drill(key)
            Model = self.env[action['res_model']]
            with self.subTest(tile=key):
                if key == 'occupancy':
                    continue
                if tile['format'] == 'monetary':
                    # `collected_month` counts payments, not instalments: it is
                    # money received, so it is measured where the date is.
                    measure = {'outstanding_rent': 'amount_residual', 'instalments_overdue': 'residual_amount',
                               'collected_month': 'amount', 'cheques_due_30': 'amount'}[key]
                    total = sum(Model.search(action['domain']).mapped(measure))
                    self.assertAlmostEqual(tile['value'], total, places=2)
                else:
                    self.assertEqual(tile['value'], Model.search_count(action['domain']))

    def test_occupancy_is_the_rental_rule(self):
        tile = self._tiles(self.Dashboard.get_dashboard())['occupancy']
        Property = self.env['realestate.property']
        company = [('company_id', 'in', self.env.companies.ids + [False])]
        leasable = Property.search_count([('is_leasable', '=', True)] + company)
        occupied = Property.search_count(self.Dashboard.action_drill('occupancy')['domain'])
        expected = round(occupied / leasable * 100.0, 1) if leasable else 0.0
        self.assertEqual(tile['value'], expected)

    def test_money_is_in_the_company_currency_only(self):
        currency = self.env.company.currency_id
        for key in ('outstanding_rent', 'instalments_overdue', 'cheques_due_30'):
            domain = self.Dashboard.action_drill(key)['domain']
            with self.subTest(tile=key):
                self.assertIn(('currency_id', '=', currency.id), domain)

    def test_receivables_chart_segments_open_their_records(self):
        chart = {c['key']: c for c in self.Dashboard.get_dashboard()['charts']}['receivables_at_risk']
        self.assertEqual(len(chart['labels']), 3)
        for index in range(3):
            action = self.Dashboard.action_drill_chart('receivables_at_risk', index)
            with self.subTest(segment=chart['labels'][index]):
                self.assertIn(action['res_model'], ('realestate.contract.payment', 'realestate.sale.installment',
                                                    'realestate.check'))

    def test_a_user_without_any_application_sees_nothing(self):
        payload = self.Dashboard.with_user(self.nobody).get_dashboard()
        self.assertEqual(payload['sections'], [])
        self.assertEqual(payload['charts'], [])

    def test_suite_overview_is_first_and_investment_is_still_reachable(self):
        root = self.env.ref('atmta_executive_app.menu_root')
        self.assertEqual(root.with_context(**{'ir.ui.menu.full_list': True}).child_id.sorted('sequence')[:1], self.env.ref('atmta_executive_app.menu_suite_overview'))
        investment = self.env.ref('atmta_executive_app.menu_overview')
        self.assertEqual(investment.action, self.env.ref('real_estate_investment.action_investment_dashboard'))
        self.assertEqual(investment.parent_id, self.env.ref('atmta_executive_app.menu_planning'))


@tagged('post_install', '-at_install')
class TestDashboardScreen(HttpCase):
    """The screen renders in a real browser and a tile opens its list."""

    def test_the_dashboard_renders_and_drills(self):
        # The roles the dashboard's own menu is offered to.
        self.env.ref('base.user_admin').groups_id |= self.env.ref('atmta_executive_app.menu_suite_overview').groups_id
        self.browser_js(
            '/odoo/action-atmta_executive_app.action_executive_dashboard',
            '\nconst deadline = Date.now() + 20000;\nfunction wait(check, next, label) {\n    const found = check();\n    if (found) { return next(found); }\n    if (Date.now() > deadline) { console.error("timeout waiting for " + label + ": " + document.body.innerText.slice(0, 400)); return; }\n    setTimeout(() => wait(check, next, label), 200);\n}\nwait(() => document.querySelector(".o_ad_dashboard .o_ad_kpi_clickable, .o_ad_dashboard .o_ad_state"), (el) => {\n    if (el.classList.contains("o_ad_state")) { console.error("dashboard shows a state panel: " + el.innerText); return; }\n    const title = document.querySelector(".o_ad_title").innerText.trim();\n    if (!title) { console.error("no title"); return; }\n    const tiles = document.querySelectorAll(".o_ad_kpi").length;\n    el.click();\n    wait(() => document.querySelector(".o_list_view, .o_kanban_view"), () => {\n        console.log("dashboard ok: " + title + ", " + tiles + " tiles");\n        console.log("test successful");\n    }, "drill list");\n}, "dashboard tiles");\n',
            login='admin', timeout=90)
