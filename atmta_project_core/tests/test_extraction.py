# -*- coding: utf-8 -*-
"""What Wave 2 claims about Project Core, asserted rather than promised.

Two kinds of test live here. The first kind checks that the extraction did not
change behaviour: a project still counts its phases, a boundary still moves the
centroid, the records still answer to the same names. The second kind checks the
thing an extraction is actually *for* -- that the boundary drawn around this
module holds, and that a later change cannot quietly reach across it.

The second kind is the one worth having. A refactor that preserves behaviour but
leaves the dependency inverted has bought nothing.
"""

from odoo.tests import TransactionCase, tagged

CORE_MODELS = (
    'realestate.project',
    'realestate.phase',
    'realestate.project.boundary.point',
)


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave2')
class TestProjectCoreExtraction(TransactionCase):

    # -- the models arrived, and they arrived here ------------------------

    def test_every_owned_model_is_reachable(self):
        for model in CORE_MODELS:
            self.assertIn(model, self.env,
                          "%s is not in the registry." % model)
            self.env[model].search([], limit=1)

    def test_the_model_identifiers_belong_to_this_module(self):
        """The ACLs and record rules that gate these models resolve through
        these identifiers. If ownership did not move, the references re-pointed
        in Wave 2 are dangling."""
        for model in CORE_MODELS:
            xmlid = 'atmta_project_core.model_' + model.replace('.', '_')
            record = self.env.ref(xmlid, raise_if_not_found=False)
            self.assertTrue(record, "%s does not exist." % xmlid)
            self.assertEqual(record.model, model)

    def test_the_tables_were_not_recreated(self):
        """A rename or a recreate would have silently dropped every row."""
        for table in ('realestate_project', 'realestate_phase',
                      'realestate_project_boundary_point'):
            self.env.cr.execute("SELECT to_regclass(%s)", (table,))
            self.assertIsNotNone(self.env.cr.fetchone()[0],
                                 "Table %s is gone." % table)

    # -- the boundary this module exists to draw --------------------------

    def test_core_declares_no_field_reading_a_property(self):
        """The whole point of Wave 2.

        ``atmta_property_core`` depends on ``atmta_project_core``. If a core
        class named ``realestate.property``, that edge would invert and this
        module could never be installed without the module that owns Property.
        """
        for model in CORE_MODELS:
            for name, field in self.env[model]._fields.items():
                comodel = getattr(field, 'comodel_name', None)
                if comodel != 'realestate.property':
                    continue
                # `_modules` lists every module that declared this field, so
                # a core declaration cannot hide behind a later override.
                self.assertNotIn(
                    'atmta_project_core', field._modules or (),
                    "atmta_project_core declares %s.%s, which points at "
                    "realestate.property. That inverts the dependency the "
                    "architecture draws between Project Core and Property "
                    "Core." % (model, name))

    def test_this_module_depends_on_nothing_inside_atmta(self):
        module = self.env['ir.module.module'].search(
            [('name', '=', 'atmta_project_core')], limit=1)
        self.assertTrue(module, "atmta_project_core is not installed.")
        deps = set(module.dependencies_id.mapped('name'))
        self.assertEqual(
            deps, {'base', 'mail'},
            "Project Core is the floor of the architecture; it may depend on "
            "Odoo alone. Found: %s" % sorted(deps))

    # -- behaviour that had to survive being cut in half ------------------

    def test_phase_count_still_counts_phases(self):
        """``phase_count`` was computed alongside four property counters. The
        split had to keep it working without them."""
        project = self.env['realestate.project'].create({'name': 'W2 Counter Test'})
        self.assertEqual(project.phase_count, 0)
        self.env['realestate.phase'].create(
            [{'name': 'P1', 'project_id': project.id},
             {'name': 'P2', 'project_id': project.id}])
        project.invalidate_recordset()
        self.assertEqual(project.phase_count, 2)

    def test_a_boundary_still_moves_the_centroid(self):
        """``create``/``write``/``unlink`` on the boundary model all call back
        into the project. Both ends moved; the callback still has to fire."""
        project = self.env['realestate.project'].create({'name': 'W2 Plot Test'})
        Point = self.env['realestate.project.boundary.point']
        points = Point.create([
            {'project_id': project.id, 'latitude': 10.0, 'longitude': 20.0},
            {'project_id': project.id, 'latitude': 20.0, 'longitude': 20.0},
            {'project_id': project.id, 'latitude': 20.0, 'longitude': 30.0},
            {'project_id': project.id, 'latitude': 10.0, 'longitude': 30.0},
        ])
        project.invalidate_recordset()
        self.assertEqual(project.boundary_point_count, 4)
        self.assertAlmostEqual(project.latitude, 15.0, places=5)
        self.assertAlmostEqual(project.longitude, 25.0, places=5)

        points[0].write({'latitude': 0.0})
        project.invalidate_recordset()
        self.assertAlmostEqual(project.latitude, 12.5, places=5)

        points[0].unlink()
        project.invalidate_recordset()
        self.assertEqual(project.boundary_point_count, 3)

    def test_the_lifecycle_transitions_still_move_the_state(self):
        project = self.env['realestate.project'].create({'name': 'W2 State Test'})
        self.assertEqual(project.state, 'planning')
        project.action_set_construction()
        self.assertEqual(project.state, 'construction')
        project.action_set_marketing()
        self.assertEqual(project.state, 'marketing')
        project.action_complete()
        self.assertEqual(project.state, 'completed')
        project.action_reset_planning()
        self.assertEqual(project.state, 'planning')

    def test_the_project_code_sequence_still_fires(self):
        """``create`` pulls the code from an ``ir.sequence`` whose record stayed
        in ``real_estate_developer``. It is looked up by code, not by XML-ID, so
        it has to keep resolving across the module split."""
        project = self.env['realestate.project'].create({'name': 'W2 Sequence Test'})
        self.assertTrue(project.code)
        self.assertNotEqual(project.code, 'New')
