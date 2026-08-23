# -*- coding: utf-8 -*-
"""atmta_base is a floor, and a floor is defined by what it does not do."""

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave4')
class TestAtmtaBaseIsAFloor(TransactionCase):

    def _module(self):
        return self.env['ir.module.module'].search([('name', '=', 'atmta_base')], limit=1)

    def test_it_is_installed_and_is_not_an_application(self):
        module = self._module()
        self.assertTrue(module, "atmta_base is not installed.")
        self.assertEqual(module.state, 'installed')
        self.assertFalse(module.application,
                         "atmta_base must not appear as an application.")

    def test_it_depends_on_odoo_alone(self):
        deps = set(self._module().dependencies_id.mapped('name'))
        self.assertEqual(
            deps, {'base'},
            "The foundation may depend on Odoo's base and nothing else. "
            "Found: %s" % sorted(deps))

    def test_it_owns_no_models(self):
        """The catalog assigns three; all three failed the audit. If a model
        ever lands here, that decision is being made silently."""
        owned = self.env['ir.model.data'].search([
            ('module', '=', 'atmta_base'), ('model', '=', 'ir.model')])
        self.assertFalse(
            owned.mapped('name'),
            "atmta_base owns model(s): %s. It is meant to own none."
            % owned.mapped('name'))

    def test_it_grants_nothing_and_shows_nothing(self):
        for model, label in (('ir.model.access', 'ACL row'),
                             ('ir.rule', 'record rule'),
                             ('ir.ui.menu', 'menu'),
                             ('ir.actions.act_window', 'action'),
                             ('res.groups', 'group')):
            found = self.env['ir.model.data'].search([
                ('module', '=', 'atmta_base'), ('model', '=', model)])
            self.assertFalse(
                found, "atmta_base ships %d %s(s); it must ship none."
                % (len(found), label))

    def test_it_owns_the_suite_category(self):
        category = self.env.ref('atmta_base.module_category_atmta',
                                raise_if_not_found=False)
        self.assertTrue(category, "The ATMTA suite category is missing.")
        self.assertEqual(category.name, 'ATMTA')
