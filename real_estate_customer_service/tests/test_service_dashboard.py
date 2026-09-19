"""Customer Service dashboard: each figure is the list it opens."""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestServiceDashboard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = new_test_user(cls.env, login='cs_agent',
                                  groups='base.group_user,real_estate_customer_service.group_service_agent')
        cls.other = new_test_user(cls.env, login='cs_other',
                                  groups='base.group_user,real_estate_customer_service.group_service_agent')
        cls.outsider = new_test_user(cls.env, login='cs_outsider', groups='base.group_user')
        reporter = cls.env['res.partner'].create({'name': 'Reporter'})
        Ticket = cls.env['realestate.customer.ticket']
        base = {'partner_id': reporter.id}
        cls.mine_urgent = Ticket.create(dict(base, title='Leak', priority='3', assignee_id=cls.agent.id, state='assigned'))
        cls.unassigned = Ticket.create(dict(base, title='Door'))
        cls.breached = Ticket.create(dict(base, title='AC', assignee_id=cls.other.id, state='in_progress'))
        cls.breached.write({'sla_deadline': fields.Datetime.now() - timedelta(hours=2), 'sla_breached': True})
        cls.closed = Ticket.create(dict(base, title='Done', state='closed'))
        cls.Dashboard = cls.env['realestate.service.dashboard']

    def _tiles(self, payload):
        return {t['key']: t for s in payload['sections'] for t in s['tiles']}

    def test_every_tile_matches_the_records_it_opens(self):
        Dashboard = self.Dashboard.with_user(self.agent)
        for scope in ('team', 'mine'):
            for key, tile in self._tiles(Dashboard.get_dashboard(scope)).items():
                action = Dashboard.action_drill(key, scope)
                with self.subTest(scope=scope, tile=key):
                    count = self.env['realestate.customer.ticket'].with_user(self.agent).search_count(action['domain'])
                    self.assertEqual(tile['value'], count)

    def test_queue_figures(self):
        tiles = self._tiles(self.Dashboard.with_user(self.agent).get_dashboard('team'))
        Ticket = self.env['realestate.customer.ticket']
        self.assertIn(self.unassigned, Ticket.search(self.Dashboard.action_drill('unassigned')['domain']))
        self.assertNotIn(self.closed, Ticket.search(self.Dashboard.action_drill('open')['domain']))
        self.assertTrue(tiles['breached']['warning'])
        self.assertIn(self.breached, Ticket.search(self.Dashboard.action_drill('breached')['domain']))

    def test_a_cancelled_ticket_is_not_counted_as_resolved(self):
        reporter = self.env['res.partner'].create({'name': 'Reporter 2'})
        ticket = self.env['realestate.customer.ticket'].create({
            'partner_id': reporter.id, 'title': 'Resolved then cancelled',
            'state': 'cancelled', 'resolved_on': fields.Datetime.now()})
        Ticket = self.env['realestate.customer.ticket']
        action = self.Dashboard.action_drill('resolved_month')
        self.assertNotIn(ticket, Ticket.search(action['domain']))

    def test_mine_is_my_queue(self):
        Dashboard = self.Dashboard.with_user(self.agent)
        Ticket = self.env['realestate.customer.ticket']
        mine = Ticket.search(Dashboard.action_drill('open', 'mine')['domain'])
        self.assertEqual(mine, self.mine_urgent)

    def test_charts_and_their_segments(self):
        payload = self.Dashboard.with_user(self.agent).get_dashboard('team')
        charts = {c['key']: c for c in payload['charts']}
        self.assertEqual(set(charts), {'open_by_category', 'opened_vs_resolved'})
        category = charts['open_by_category']
        self.assertEqual(sum(category['series'][0]['data']), self._tiles(payload)['open']['value'])
        trend = charts['opened_vs_resolved']
        self.assertEqual(len(trend['labels']), 6)
        action = self.Dashboard.with_user(self.agent).action_drill_chart('opened_vs_resolved', 5, 'team')
        self.assertEqual(self.env['realestate.customer.ticket'].search_count(action['domain']),
                         trend['series'][0]['data'][5])

    def test_a_user_without_service_access_sees_nothing(self):
        payload = self.Dashboard.with_user(self.outsider).get_dashboard()
        self.assertEqual(payload['sections'], [])
        self.assertEqual(payload['charts'], [])
        self.assertEqual(payload['quick_actions'], [])

    def test_the_dashboard_is_the_first_screen(self):
        root = self.env.ref('real_estate_customer_service.menu_customer_service_root')
        first = root.with_context(**{'ir.ui.menu.full_list': True}).child_id.sorted('sequence')[:1]
        self.assertEqual(first, self.env.ref('real_estate_customer_service.menu_customer_service_dashboard'))
        action = self.env.ref('real_estate_customer_service.action_service_dashboard')
        self.assertEqual(action.tag, 'atmta_dashboard')
        self.assertEqual(action.params['provider'], 'realestate.service.dashboard')


@tagged('post_install', '-at_install')
class TestDashboardScreen(HttpCase):
    """The screen renders in a real browser and a tile opens its list."""

    def test_the_dashboard_renders_and_drills(self):
        # The roles the dashboard's own menu is offered to.
        self.env.ref('base.user_admin').groups_id |= self.env.ref('real_estate_customer_service.menu_customer_service_root').groups_id
        self.browser_js(
            '/odoo/action-real_estate_customer_service.action_service_dashboard',
            '\nconst deadline = Date.now() + 20000;\nfunction wait(check, next, label) {\n    const found = check();\n    if (found) { return next(found); }\n    if (Date.now() > deadline) { console.error("timeout waiting for " + label + ": " + document.body.innerText.slice(0, 400)); return; }\n    setTimeout(() => wait(check, next, label), 200);\n}\nwait(() => document.querySelector(".o_ad_dashboard .o_ad_kpi_clickable, .o_ad_dashboard .o_ad_state"), (el) => {\n    if (el.classList.contains("o_ad_state")) { console.error("dashboard shows a state panel: " + el.innerText); return; }\n    const title = document.querySelector(".o_ad_title").innerText.trim();\n    if (!title) { console.error("no title"); return; }\n    const tiles = document.querySelectorAll(".o_ad_kpi").length;\n    el.click();\n    wait(() => document.querySelector(".o_list_view, .o_kanban_view"), () => {\n        console.log("dashboard ok: " + title + ", " + tiles + " tiles");\n        console.log("test successful");\n    }, "drill list");\n}, "dashboard tiles");\n',
            login='admin', timeout=90)
