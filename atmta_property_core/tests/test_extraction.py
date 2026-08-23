# -*- coding: utf-8 -*-
"""What Wave 3 claims about Property Core, asserted rather than promised.

Property is a harder model to move than Project was. It delegates to
``product.template`` through ``_inherits``, so every property row owns a
product row; it keeps a materialised hierarchy through ``_parent_store``; and
its code comes from a sequence with a live counter. Each of those is something
an extraction can quietly break while every ordinary test still passes, so each
gets a test of its own.
"""

from odoo.tests import TransactionCase, tagged

CORE_MODELS = ('realestate.property', 'property.type', 'property.usage', 'property.image')


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave3')
class TestPropertyCoreExtraction(TransactionCase):

    def test_every_owned_model_is_reachable(self):
        for model in CORE_MODELS:
            self.assertIn(model, self.env, "%s is not in the registry." % model)
            self.env[model].search([], limit=1)

    def test_the_model_identifiers_belong_to_this_module(self):
        """Sixteen ACL rows across two modules resolve through these. If
        ownership did not move, those references are dangling."""
        for model in CORE_MODELS:
            xmlid = 'atmta_property_core.model_' + model.replace('.', '_')
            record = self.env.ref(xmlid, raise_if_not_found=False)
            self.assertTrue(record, "%s does not exist." % xmlid)
            self.assertEqual(record.model, model)

    def test_the_tables_were_not_recreated(self):
        for table in ('realestate_property', 'property_type',
                      'property_usage', 'property_image'):
            self.env.cr.execute("SELECT to_regclass(%s)", (table,))
            self.assertIsNotNone(self.env.cr.fetchone()[0], "Table %s is gone." % table)

    # -- the boundary this module exists to draw --------------------------

    def test_core_declares_nothing_that_reads_a_lease_or_a_sale(self):
        """Property Core sits under Leasing, Brokerage, Handover, Visual and
        Operations. If a core field named one of their models, the dependency
        would invert and this module could not be installed on its own."""
        forbidden_prefixes = ('realestate.contract', 'realestate.sale',
                              'realestate.unit.turn', 'realestate.lease',
                              'realestate.handover', 'realestate.project')
        offenders = []
        for model in CORE_MODELS:
            for name, field in self.env[model]._fields.items():
                if 'atmta_property_core' not in (field._modules or ()):
                    continue
                comodel = getattr(field, 'comodel_name', None) or ''
                if comodel.startswith(forbidden_prefixes):
                    offenders.append('%s.%s -> %s' % (model, name, comodel))
        self.assertFalse(
            offenders,
            "Property Core declares fields pointing at modules above it: %s" % offenders)

    def test_this_module_depends_only_on_odoo(self):
        module = self.env['ir.module.module'].search(
            [('name', '=', 'atmta_property_core')], limit=1)
        self.assertTrue(module, "atmta_property_core is not installed.")
        deps = set(module.dependencies_id.mapped('name'))
        self.assertEqual(
            deps, {'base', 'mail', 'product', 'web_editor'},
            "Property Core may depend on Odoo alone. Found: %s" % sorted(deps))

    # -- the three things unique to this model ----------------------------

    def test_the_delegated_product_survives(self):
        """``_inherits = {'product.template': 'product_tmpl_id'}`` means every
        property owns a product. A property without one cannot be read at all."""
        self.assertEqual(
            self.env['realestate.property']._inherits,
            {'product.template': 'product_tmpl_id'})
        self.env.cr.execute("""
            SELECT count(*) FROM realestate_property p
            LEFT JOIN product_template t ON t.id = p.product_tmpl_id
            WHERE p.product_tmpl_id IS NULL OR t.id IS NULL""")
        self.assertEqual(self.env.cr.fetchone()[0], 0,
                         "Properties exist with no delegated product.template.")

    def test_creating_a_property_still_creates_its_product(self):
        before = self.env['product.template'].search_count([])
        prop = self.env['realestate.property'].create({'name': 'W3 Delegation Test'})
        self.assertTrue(prop.product_tmpl_id,
                        "The delegated product was not created.")
        self.assertEqual(self.env['product.template'].search_count([]), before + 1)
        self.assertEqual(prop.name, 'W3 Delegation Test',
                         "`name` is delegated to the product template.")

    def test_the_hierarchy_is_still_materialised(self):
        """``_parent_store`` keeps `parent_path`. A child written after the move
        must still get one, and the level constraint must still refuse a child
        that is not deeper than its parent."""
        from odoo.exceptions import ValidationError
        Property = self.env['realestate.property']
        parent = Property.create({'name': 'W3 Tree Parent', 'hierarchy_level': 'building'})
        child = Property.create({'name': 'W3 Tree Child', 'hierarchy_level': 'unit',
                                 'parent_id': parent.id})
        child.flush_recordset()
        self.assertTrue(child.parent_path, "parent_path was not materialised.")
        self.assertTrue(child.parent_path.startswith(parent.parent_path))
        self.assertEqual(parent.child_count, 1)

        with self.assertRaises(ValidationError):
            Property.create({'name': 'W3 Tree Bad', 'hierarchy_level': 'compound',
                             'parent_id': parent.id})

    def test_the_property_code_sequence_still_fires(self):
        """`create` pulls the code from a sequence that moved with the model."""
        prop = self.env['realestate.property'].create({'name': 'W3 Sequence Test'})
        self.assertTrue(prop.property_code)
        self.assertTrue(prop.property_code.startswith('PROP-'),
                        "Got %r; the sequence did not fire." % prop.property_code)

    def test_the_sequence_reference_points_at_this_module(self):
        """``_sync_property_code_sequence`` resolves the sequence by XML ID, so
        the reference had to move with it or the self-heal silently does
        nothing."""
        self.assertTrue(
            self.env.ref('atmta_property_core.seq_realestate_property_code',
                         raise_if_not_found=False),
            "The property code sequence is not owned by atmta_property_core.")
        self.env['realestate.property']._sync_property_code_sequence()
