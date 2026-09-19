# -*- coding: utf-8 -*-
"""The legacy Construction app is shown to Construction users only.

Its root menu had no group, so every internal user saw the app, and its Control
Tower read development projects on opening: for anyone without Developer or
Brokerage rights it crashed with "You are not allowed to access 'Real Estate
Development Project' records". Found by a browser crawl of every app.
"""

from odoo.tests.common import TransactionCase, new_test_user, tagged

from .common import shown_menu_ids


@tagged('post_install', '-at_install')
class TestConstructionMenuAccess(TransactionCase):

    def test_only_construction_users_see_the_app(self):
        root = self.env.ref('real_estate_construction.menu_construction_root')
        employee = new_test_user(self.env, login='cons_menu_employee',
                                 groups='base.group_user')
        builder = new_test_user(self.env, login='cons_menu_user',
                                groups='base.group_user,real_estate_construction.group_construction_user')
        self.assertNotIn(root.id, shown_menu_ids(self.env, employee))
        self.assertIn(root.id, shown_menu_ids(self.env, builder))

    def test_construction_users_can_read_projects(self):
        """The Control Tower lists development projects to pick from.

        Construction User had no access to them, so the Control Tower crashed
        for the users it is meant for (decision 15 Sep 2026: read access, as
        Developer and Brokerage read-only have). Read only.
        """
        builder = new_test_user(self.env, login='cons_project_reader',
                                groups='base.group_user,real_estate_construction.group_construction_user')
        Project = self.env['realestate.project'].with_user(builder)
        self.assertTrue(Project.has_access('read'))
        for operation in ('write', 'create', 'unlink'):
            self.assertFalse(Project.has_access(operation), operation)
        Project.search_read([], ['display_name'], limit=5)
