# -*- coding: utf-8 -*-
"""What Procurement Core claims, asserted rather than described.

The module owns no models, so the usual "did the table survive" tests do not
apply. What matters here is narrower and easier to get wrong: that the floor
stays a floor, and that the policy every capability reads is still readable.
"""

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave5')
class TestProcurementCoreIsAFloor(TransactionCase):

    def _module(self):
        return self.env['ir.module.module'].search(
            [('name', '=', 'atmta_procurement_core')], limit=1)

    def test_it_is_installed_and_is_not_an_application(self):
        module = self._module()
        self.assertTrue(module, "atmta_procurement_core is not installed.")
        self.assertEqual(module.state, 'installed')
        self.assertFalse(module.application)

    def test_it_declares_no_model(self):
        """Odoo gives a `model_*` identifier to every module that *contributes*
        to a model, extensions included, so the presence of those identifiers
        proves nothing. What matters is whether this module is the one that
        declared the model with `_name` -- `_original_module` is that answer.
        """
        owned = self.env['ir.model.data'].search([
            ('module', '=', 'atmta_procurement_core'), ('model', '=', 'ir.model')])
        declared = []
        for record in self.env['ir.model'].browse(owned.mapped('res_id')):
            model = self.env.get(record.model)
            if model is not None and model._original_module == 'atmta_procurement_core':
                declared.append(record.model)
        self.assertFalse(
            declared,
            "Procurement Core declares model(s) %s. It carries extensions only."
            % declared)
        self.assertTrue(
            owned, "Expected extension identifiers on the shared models.")

    def test_it_does_not_depend_on_the_procurement_monolith(self):
        """The whole point. A floor that depends on the app above it is not a
        floor, and every later capability would inherit that inversion."""
        deps = set(self._module().dependencies_id.mapped('name'))
        self.assertNotIn('real_estate_procurement', deps)
        self.assertEqual(
            deps, {'atmta_project_core', 'product', 'stock'},
            "Procurement Core may depend on the project core and Odoo's product "
            "and stock modules -- `stock` because `_sync_re_storable` writes "
            "`is_storable`. Found: %s" % sorted(deps))

    def test_it_grants_nothing(self):
        for model, label in (('ir.model.access', 'ACL row'),
                             ('ir.rule', 'record rule'),
                             ('ir.ui.menu', 'menu'),
                             ('res.groups', 'group')):
            found = self.env['ir.model.data'].search([
                ('module', '=', 'atmta_procurement_core'), ('model', '=', model)])
            self.assertFalse(found, "Procurement Core ships %d %s(s)."
                             % (len(found), label))

    # -- the two things it does carry -------------------------------------

    def test_the_company_policy_is_declared_here(self):
        field = self.env['res.company']._fields.get('procurement_po_governance')
        self.assertIsNotNone(field, "Company procurement policy is missing.")
        self.assertIn('atmta_procurement_core', field._modules or (),
                      "Procurement Core must declare the company policy.")

    def test_the_project_policy_is_declared_here(self):
        field = self.env['realestate.project']._fields.get('procurement_budget_policy')
        self.assertIsNotNone(field, "Project procurement policy is missing.")
        self.assertIn('atmta_procurement_core', field._modules or ())

    def test_the_product_classification_is_declared_here(self):
        for name in ('is_construction_material', 'is_property_fitting',
                     'is_marketing_asset', 'is_maintenance_consumable',
                     'is_realestate_product'):
            field = self.env['product.template']._fields.get(name)
            self.assertIsNotNone(field, "product.template.%s is missing." % name)
            self.assertIn('atmta_procurement_core', field._modules or (),
                          "%s is not declared by Procurement Core." % name)

    def test_the_derived_classification_still_computes(self):
        product = self.env['product.template'].create({
            'name': 'W5 Core Classification Probe', 'type': 'consu'})
        # `_sync_re_storable` writes `is_storable`, a stock field; the module
        # declares `stock` precisely so this works.
        self.assertFalse(product.is_realestate_product)
        product.is_construction_material = True
        product.invalidate_recordset()
        self.assertTrue(
            product.is_realestate_product,
            "is_realestate_product no longer follows the classification "
            "booleans after the move.")
