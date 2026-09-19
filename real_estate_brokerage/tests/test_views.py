# -*- coding: utf-8 -*-
"""M31 — the views actually render, for the users who will open them.

Module loading validates a view's *arch*. It does not render it, and it does not
render it **as an agent**, which is where the interesting failures are: a field
behind `groups=` that a manager can see and an agent cannot, referenced by an
`invisible=` expression, loads fine and then throws when the agent opens the
form. Module 3 hit exactly that class of bug in the browser and nowhere else.
"""

from odoo.tests import tagged

from .common import BrokerageCommon

#: Every model this module puts in front of a human, and the view types it
#: offers for it. `get_views` resolves inheritance, evaluates `groups=` and
#: builds the field dictionary — the work that actually fails.
RENDERED_VIEWS = [
    ('crm.lead', ['form', 'list', 'search']),
    ('realestate.listing', ['form', 'list', 'search']),
    ('realestate.listing.mandate', ['form', 'list']),
    ('realestate.property.match', ['form', 'list', 'search']),
    ('realestate.viewing', ['form', 'list']),
    ('realestate.offer', ['form', 'list']),
    ('realestate.transaction', ['form', 'list']),
    ('realestate.broker.agreement', ['form', 'list']),
    ('realestate.lead.registration', ['form', 'list', 'search']),
    ('res.partner', ['form']),
]


@tagged('post_install', '-at_install')
class TestViewsRender(BrokerageCommon):

    def test_every_view_renders_for_a_manager(self):
        for model, view_types in RENDERED_VIEWS:
            for view_type in view_types:
                with self.subTest(model=model, view=view_type):
                    self.env[model].get_views([(None, view_type)])

    def test_every_view_renders_for_an_agent(self):
        """The case that only shows up in a browser, made cheap.

        An agent cannot read `minimum_price`, `floor_price` or
        `colliding_registration_id`. If any view references one outside a
        `groups=` guard, this is where it surfaces.
        """
        for model, view_types in RENDERED_VIEWS:
            for view_type in view_types:
                with self.subTest(model=model, view=view_type):
                    self.env[model].with_user(self.agent).get_views(
                        [(None, view_type)])

    def test_the_menus_point_at_actions_that_exist(self):
        menus = self.env['ir.ui.menu'].search(
            [('id', 'child_of',
              self.env.ref('real_estate_brokerage.menu_brokerage_root').id)])

        self.assertTrue(menus)
        for menu in menus:
            if not menu.action:
                continue
            with self.subTest(menu=menu.name):
                self.assertTrue(menu.action.exists(),
                                "%s points at a missing action" % menu.name)

    def test_a_manager_sees_the_confidential_floor_and_an_agent_does_not(self):
        """M22, checked through the view layer rather than the ORM."""
        manager_fields = self.env['realestate.listing'].get_views(
            [(None, 'form')])['models']['realestate.listing']['fields']
        agent_fields = self.env['realestate.listing'].with_user(
            self.agent).get_views(
                [(None, 'form')])['models']['realestate.listing']['fields']

        self.assertIn('minimum_price', manager_fields)
        self.assertNotIn('minimum_price', agent_fields)
