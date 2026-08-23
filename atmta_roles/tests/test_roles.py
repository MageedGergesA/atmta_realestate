# -*- coding: utf-8 -*-
"""Canonical roles are identities, not privileges.

The whole value of this module rests on one claim: giving somebody a canonical
role changes nothing about what they can do. These tests are that claim, made
falsifiable. The dangerous failure is not a crash — it is a role that quietly
implies a legacy business group and hands over its ACLs, its record rules and
its menus to everyone who holds it.
"""

from odoo.tests import TransactionCase, tagged

CANONICAL_PREFIX = 'atmta_roles.'


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave4')
class RolesCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.role_data = cls.env['ir.model.data'].search([
            ('module', '=', 'atmta_roles'), ('model', '=', 'res.groups')])
        cls.roles = cls.env['res.groups'].browse(cls.role_data.mapped('res_id'))

    def _transitive(self, group):
        """Every group reachable through implied_ids, to any depth."""
        seen, stack = set(), list(group.ids)
        while stack:
            gid = stack.pop()
            if gid in seen:
                continue
            seen.add(gid)
            stack.extend(self.env['res.groups'].browse(gid).implied_ids.ids)
        return self.env['res.groups'].browse(sorted(seen - set(group.ids)))


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave4')
class TestCanonicalRolesExist(RolesCommon):

    def test_the_module_is_not_an_application(self):
        module = self.env['ir.module.module'].search(
            [('name', '=', 'atmta_roles')], limit=1)
        self.assertTrue(module)
        self.assertFalse(module.application)
        self.assertEqual(set(module.dependencies_id.mapped('name')), {'atmta_base'})

    def test_the_suite_root_role_exists_and_implies_employee(self):
        root = self.env.ref('atmta_roles.group_atmta_user')
        self.assertIn(self.env.ref('base.group_user'), root.implied_ids,
                      "ATMTA User must imply Odoo's employee group.")

    def test_every_domain_role_reaches_the_suite_root(self):
        root = self.env.ref('atmta_roles.group_atmta_user')
        for role in self.roles - root:
            self.assertIn(
                root, self._transitive(role),
                "%s does not reach ATMTA User." % role.name)


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave4')
class TestCanonicalRolesArePermissionNeutral(RolesCommon):

    def test_no_canonical_role_carries_an_acl(self):
        offenders = [(g.name, self.env['ir.model.access'].search_count([('group_id', '=', g.id)]))
                     for g in self.roles]
        offenders = [o for o in offenders if o[1]]
        self.assertFalse(offenders, "Canonical roles carry ACL rows: %s" % offenders)

    def test_no_canonical_role_carries_a_record_rule(self):
        offenders = [(g.name, self.env['ir.rule'].search_count([('groups', 'in', g.id)]))
                     for g in self.roles]
        offenders = [o for o in offenders if o[1]]
        self.assertFalse(offenders, "Canonical roles carry record rules: %s" % offenders)

    def test_no_canonical_role_reveals_a_menu(self):
        offenders = [(g.name, self.env['ir.ui.menu'].with_context(active_test=False)
                      .search_count([('groups_id', 'in', g.id)])) for g in self.roles]
        offenders = [o for o in offenders if o[1]]
        self.assertFalse(offenders, "Canonical roles gate menus: %s" % offenders)

    def test_no_canonical_role_implies_a_permission_bearing_group(self):
        """The release blocker.

        A canonical role implying a legacy business group would grant that
        group's every ACL, rule and menu to anyone holding the role. The only
        implication allowed out of this module is to `base.group_user`, and to
        other canonical roles.
        """
        employee = self.env.ref('base.group_user')
        # Odoo's own employee hierarchy is allowed: `base.group_user` natively
        # implies `base.group_no_one`, and ATMTA User implying the employee
        # group is the architecture-approved design. What must never appear is
        # an edge into a *business* group — those carry the ACLs and rules.
        native = set(employee.ids) | set(self._transitive(employee).ids)
        allowed = set(self.roles.ids) | native
        offenders = []
        for role in self.roles:
            for reached in self._transitive(role):
                if reached.id in allowed:
                    continue
                owner = self.env['ir.model.data'].search(
                    [('model', '=', 'res.groups'), ('res_id', '=', reached.id)], limit=1)
                acls = self.env['ir.model.access'].search_count([('group_id', '=', reached.id)])
                rules = self.env['ir.rule'].search_count([('groups', 'in', reached.id)])
                offenders.append('%s -> %s [%s] (acls=%s rules=%s)'
                                 % (role.name, reached.name,
                                    owner.module or '?', acls, rules))
        self.assertFalse(
            offenders,
            "Canonical roles imply groups outside the canonical set and "
            "outside Odoo's own employee hierarchy: %s" % offenders)

    def test_no_canonical_role_reaches_a_business_module_group(self):
        """Stated separately and by module ownership, because this is the edge
        that would silently hand a legacy capability's rights to everyone
        holding a marker role."""
        offenders = []
        for role in self.roles:
            for reached in self._transitive(role):
                owner = self.env['ir.model.data'].search(
                    [('model', '=', 'res.groups'), ('res_id', '=', reached.id)], limit=1)
                module = owner.module or ''
                if module.startswith('real_estate') or (
                        module.startswith('atmta') and module != 'atmta_roles'):
                    offenders.append('%s -> %s.%s' % (role.name, module, owner.name))
        self.assertFalse(
            offenders,
            "Canonical roles reach business-module groups: %s" % offenders)

    def test_the_implication_graph_has_no_cycle(self):
        for role in self.roles:
            self.assertNotIn(
                role, self._transitive(role),
                "%s reaches itself through implied_ids." % role.name)


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave4')
class TestARoleAloneGrantsNothing(RolesCommon):
    """The direct canonical-role user test the release gate asks for."""

    def test_a_user_holding_only_a_canonical_role_has_employee_rights_only(self):
        employee = self.env.ref('base.group_user')
        control = self.env['res.users'].create({
            'name': 'w4.employee.only', 'login': 'w4.employee.only',
            'groups_id': [(6, 0, [employee.id])]})
        subject = self.env['res.users'].create({
            'name': 'w4.role.only', 'login': 'w4.role.only',
            'groups_id': [(6, 0, [employee.id,
                                  self.env.ref('atmta_roles.group_procurement_manager').id])]})
        self.assertEqual(
            set(subject.groups_id.ids) - set(control.groups_id.ids),
            {self.env.ref('atmta_roles.group_procurement_manager').id,
             self.env.ref('atmta_roles.group_atmta_user').id},
            "Holding one canonical role brought more than the role and the "
            "suite root with it.")

    def test_roles_stay_combinable(self):
        """Measured on the live suite: real users hold up to ten Procurement
        roles at once. If the canonical roles ever became an exclusive
        selection, that would break silently — so it is asserted."""
        employee = self.env.ref('base.group_user')
        wanted = [self.env.ref('atmta_roles.group_procurement_manager'),
                  self.env.ref('atmta_roles.group_procurement_technical_evaluator'),
                  self.env.ref('atmta_roles.group_procurement_buyer'),
                  self.env.ref('atmta_roles.group_construction_manager')]
        user = self.env['res.users'].create({
            'name': 'w4.combined', 'login': 'w4.combined',
            'groups_id': [(6, 0, [employee.id] + [g.id for g in wanted])]})
        for g in wanted:
            self.assertIn(g, user.groups_id,
                          "%s did not stick; the roles are behaving as an "
                          "exclusive selection." % g.name)
