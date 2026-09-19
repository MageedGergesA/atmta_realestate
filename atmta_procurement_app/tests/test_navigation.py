"""V2 is the navigation: its root is open to every role."""

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestV2Navigation(TransactionCase):

    def test_the_root_is_not_restricted_to_a_pilot_group(self):
        root = self.env.ref('atmta_procurement_app.menu_root')
        pilot = self.env.ref('atmta_v2_pilot.group_atmta_v2_pilot', raise_if_not_found=False)
        self.assertTrue(root.active)
        if pilot:
            self.assertNotIn(pilot, root.groups_id)

    def test_every_entry_is_still_gated_by_a_role(self):
        root = self.env.ref('atmta_procurement_app.menu_root')
        for menu in self.env['ir.ui.menu'].with_context(**{'ir.ui.menu.full_list': True}).search([('id', 'child_of', root.id), ('action', '!=', False)]):
            gated, node = False, menu
            while node:
                if node.groups_id:
                    gated = True
                    break
                node = node.parent_id
            with self.subTest(menu=menu.complete_name):
                self.assertTrue(gated, "An entry reachable by every internal user.")
