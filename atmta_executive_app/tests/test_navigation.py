"""V2 is the navigation: its root is open to every role, and it replaces the
legacy roots it absorbs without losing a screen."""

from odoo.tests.common import TransactionCase, tagged

RETIRED_ROOTS = ('real_estate_investment.menu_investment_root',)
#: Legacy screens left out on purpose (see the manifest description).
EXCLUDED_ACTIONS = ()


def _actions_under(env, root):
    menus = env['ir.ui.menu'].with_context(active_test=False, **{'ir.ui.menu.full_list': True}).search(
        [('id', 'child_of', root.id), ('action', '!=', False)])
    return {f'{menu.action._name},{menu.action.id}' for menu in menus}


@tagged('post_install', '-at_install')
class TestV2Navigation(TransactionCase):

    def setUp(self):
        super().setUp()
        self.root = self.env.ref('atmta_executive_app.menu_root')

    def test_the_root_is_not_restricted_to_a_pilot_group(self):
        pilot = self.env.ref('atmta_v2_pilot.group_atmta_v2_pilot', raise_if_not_found=False)
        self.assertTrue(self.root.active)
        if pilot:
            self.assertNotIn(pilot, self.root.groups_id)

    def test_every_entry_is_still_gated_by_a_role(self):
        menus = self.env['ir.ui.menu'].with_context(**{'ir.ui.menu.full_list': True}).search([('id', 'child_of', self.root.id), ('action', '!=', False)])
        for menu in menus:
            gated, node = False, menu
            while node:
                if node.groups_id:
                    gated = True
                    break
                node = node.parent_id
            with self.subTest(menu=menu.complete_name):
                self.assertTrue(gated, "An entry reachable by every internal user.")

    def test_the_legacy_roots_it_replaces_are_retired(self):
        for xmlid in RETIRED_ROOTS:
            with self.subTest(root=xmlid):
                self.assertFalse(self.env.ref(xmlid).active)

    def test_no_legacy_screen_is_lost(self):
        reachable = _actions_under(self.env, self.root)
        excluded = {
            f'{action._name},{action.id}'
            for action in (self.env.ref(x, raise_if_not_found=False) for x in EXCLUDED_ACTIONS) if action
        }
        for xmlid in RETIRED_ROOTS:
            lost = _actions_under(self.env, self.env.ref(xmlid)) - reachable - excluded
            with self.subTest(root=xmlid):
                self.assertFalse(lost, sorted(lost))

    def test_uninstalling_gives_the_legacy_roots_back(self):
        from odoo.addons.atmta_executive_app import uninstall_hook
        uninstall_hook(self.env)
        for xmlid in RETIRED_ROOTS:
            with self.subTest(root=xmlid):
                self.assertTrue(self.env.ref(xmlid).active)
