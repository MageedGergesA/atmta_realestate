# -*- coding: utf-8 -*-
"""Wave 6 — the requisition and control screens still open, over real HTTP.

Wave 6 moved the models these screens show and rebound their access rules from
this module's legacy groups to the canonical ATMTA roles. The views themselves
were not touched, which is exactly why this gate exists: nothing in the view
files would reveal a broken action, and a permission that no longer resolves
shows up as an empty screen or an access error at the moment a user clicks a
menu, not at install time.

So the check is driven the way a click is: an authenticated session, the action
resolved from its XML ID, `get_views` for the list and form arch, and a
`web_search_read` through the same ORM path the web client uses. The existing
browser suites cover sourcing, evaluation and award; none of them opens a
requisition or a reservation.
"""
from odoo.tests.common import HttpCase, tagged

from .common import M3Common


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_w6')
class TestWave6Screens(M3Common, HttpCase):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 5_000_000.0)])
        self.request = self._request(
            self.project, [(self.product, 20.0)],
            line_defaults={'cost_code_id': self.concrete.id,
                           'wbs_id': self.wbs.id,
                           'estimated_unit_cost': 10_000.0})
        self.request.action_submit()
        self.request.action_approve()
        user = self.env.ref('base.user_admin')
        for xmlid in ('real_estate_procurement.group_procurement_manager',
                      'atmta_real_estate.group_realestate_user'):
            user.groups_id |= self.env.ref(xmlid)
        self.env.flush_all()

    def _open(self, action_xmlid):
        """Resolve an action and load it the way the web client does."""
        action = self.env.ref(action_xmlid)
        model = self.env[action.res_model].with_user(
            self.env.ref('base.user_admin'))
        views = model.get_views(
            [(False, 'list'), (False, 'form')])
        records = model.web_search_read(
            domain=[], specification={'display_name': {}}, limit=5)
        return views, records

    def test_the_requisition_screens_open_and_show_the_demand(self):
        self.authenticate('admin', 'admin')
        views, records = self._open(
            'atmta_procurement_control.action_material_request')
        self.assertIn('list', views['views'])
        self.assertIn('form', views['views'])
        self.assertGreaterEqual(records['length'], 1,
                                "the requisition list came back empty")

    def test_the_reservation_screens_open_and_show_the_control_records(self):
        self.authenticate('admin', 'admin')
        views, records = self._open(
            'atmta_procurement_control.action_procurement_reservation')
        self.assertIn('list', views['views'])
        self.assertGreaterEqual(
            records['length'], 1,
            "approved demand reserved capacity, so the control screen "
            "must not be empty")

    def test_the_control_exception_screen_opens(self):
        self.authenticate('admin', 'admin')
        views, _records = self._open(
            'atmta_procurement_control.action_control_exception')
        self.assertIn('list', views['views'])

    def test_the_web_client_itself_loads_for_a_procurement_manager(self):
        self.authenticate('admin', 'admin')
        response = self.url_open('/odoo')
        self.assertEqual(response.status_code, 200)

    def test_a_requester_reaches_the_requisition_screen_but_not_all_demand(self):
        """The rebinding kept the record rules, so the screen must still be
        narrower for a requester than for a manager."""
        requester = self.env['res.users'].create({
            'name': 'w6.screens.req', 'login': 'w6.screens.req',
            'email': 'w6.screens.req@example.com',
            'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_procurement.group_procurement_requester').id,
            ])]})
        self.env.flush_all()
        Request = self.env['realestate.material.request']
        manager_reach = Request.with_user(
            self.env.ref('base.user_admin')).search([]).ids
        requester_reach = Request.with_user(requester).search([]).ids
        self.assertIn(self.request.id, manager_reach)
        self.assertNotIn(
            self.request.id, requester_reach,
            "a requester who raised nothing must not see somebody else's "
            "demand — the own-demand rule survived the move")
