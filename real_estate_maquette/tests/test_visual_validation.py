# -*- coding: utf-8 -*-
"""M2 / M3 — uploading a file is not a deployment.

Every GLB here is built by `build_glb()` rather than committed as a binary, so
a test can state exactly which meshes exist and the validator is exercised
against the real container format instead of a mock.
"""

import base64

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import VisualCommon, build_glb


@tagged('post_install', '-at_install')
class TestGlbParsing(VisualCommon):
    """The container, read server-side."""

    def test_a_real_glb_is_parsed(self):
        project = self._project()
        self._attach_glb(project, ['U1', 'U2', 'U3'], materials=4, textures=2)

        report = self.Validation._validate_project(project)

        self.assertEqual(report.mesh_count, 3)
        self.assertEqual(report.node_count, 3)
        self.assertEqual(report.material_count, 4)
        self.assertEqual(report.texture_count, 2)
        self.assertEqual(report.gltf_version, '2.0')
        self.assertTrue(report.file_bytes)

    def test_a_file_that_is_not_a_glb_is_critical(self):
        """A .gltf JSON file or a .zip renamed to .glb fails here."""
        project = self._project()
        project.maquette_glb = base64.b64encode(b'{"asset": {"version": "2.0"}}')

        report = self.Validation._validate_project(project)

        self.assertTrue(report.has_critical)
        self.assertIn('not_glb', report.issue_ids.mapped('code'))

    def test_a_truncated_file_is_caught(self):
        project = self._project()
        project.maquette_glb = base64.b64encode(b'glTF\x02\x00')

        report = self.Validation._validate_project(project)

        self.assertTrue(report.has_critical)

    def test_a_declared_length_mismatch_is_flagged(self):
        project = self._project()
        raw = bytearray(build_glb(['U1']))
        raw[8:12] = (len(raw) + 500).to_bytes(4, 'little')
        project.maquette_glb = base64.b64encode(bytes(raw))

        report = self.Validation._validate_project(project)

        self.assertIn('length_mismatch', report.issue_ids.mapped('code'))

    def test_a_glb_with_no_json_chunk_is_critical(self):
        import struct
        project = self._project()
        body = struct.pack('<II', 4, 0x004E4942) + b'\x00\x00\x00\x00'
        raw = struct.pack('<III', 0x46546C67, 2, 12 + len(body)) + body
        project.maquette_glb = base64.b64encode(raw)

        report = self.Validation._validate_project(project)

        self.assertIn('no_json_chunk', report.issue_ids.mapped('code'))

    def test_a_binary_chunk_is_measured_but_not_decoded(self):
        project = self._project()
        self._attach_glb(project, ['U1'], padding_bytes=2048)

        report = self.Validation._validate_project(project)

        self.assertFalse(report.has_critical)
        self.assertGreater(report.file_bytes, 2048)


@tagged('post_install', '-at_install')
class TestExtensionSupport(VisualCommon):
    """What the bundled r160 loader can and cannot decode."""

    def test_a_required_extension_with_no_decoder_is_critical(self):
        """KTX2 and Meshopt are not bundled — the model would fail in the
        browser, and finding that out from a customer is too late."""
        for ext in ('KHR_texture_basisu', 'EXT_meshopt_compression'):
            with self.subTest(ext=ext):
                project = self._project()
                self._attach_glb(project, ['U1'], extensions_required=[ext])

                report = self.Validation._validate_project(project)

                self.assertTrue(report.has_critical)
                self.assertIn('undecodable_extension',
                              report.issue_ids.mapped('code'))

    def test_an_unknown_required_extension_warns(self):
        project = self._project()
        self._attach_glb(project, ['U1'],
                         extensions_required=['VENDOR_something_new'])

        report = self.Validation._validate_project(project)

        self.assertFalse(report.has_critical)
        self.assertIn('unknown_extension', report.issue_ids.mapped('code'))

    def test_draco_without_a_local_decoder_is_critical(self):
        """The decoder is not shipped with this module.

        0.4 pointed DRACOLoader at unpkg.com, so a Draco model needed public
        internet from the customer's browser. Pointing it at a local path that
        does not exist would just swap a CDN dependency for a 404, so the
        location is configurable and its absence is reported — a model that
        cannot render must not reach a customer.
        """
        project = self._project()
        self._attach_glb(project, ['U1'],
                         extensions_used=['KHR_draco_mesh_compression'])
        self.assertFalse(
            self.env['realestate.visual.assets'].draco_decoder_installed())

        report = self.Validation._validate_project(project)

        self.assertIn('draco_decoder_missing', report.issue_ids.mapped('code'))
        self.assertTrue(report.has_critical)

    def test_pointing_the_decoder_elsewhere_clears_it(self):
        """A deployment serving the decoder itself needs no code change."""
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_maquette.draco_decoder_path',
            'https://assets.example.internal/draco/')
        project = self._project()
        self._attach_glb(project, ['U1'],
                         extensions_used=['KHR_draco_mesh_compression'])

        report = self.Validation._validate_project(project)

        self.assertNotIn('draco_decoder_missing',
                         report.issue_ids.mapped('code'))

    def test_an_ordinary_model_raises_no_extension_issues(self):
        project = self._project()
        self._attach_glb(project, ['U1'])

        report = self.Validation._validate_project(project)

        codes = report.issue_ids.mapped('code')
        self.assertNotIn('undecodable_extension', codes)
        self.assertNotIn('draco_decoder_missing', codes)


@tagged('post_install', '-at_install')
class TestMappingQA(VisualCommon):
    """M3 — the dashboard the module had no data source for."""

    def test_a_clean_project_matches_everything(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._unit(project, mesh='U2')
        self._attach_glb(project, ['U1', 'U2'])

        report = self.Validation._validate_project(project)

        self.assertEqual(report.property_count, 2)
        self.assertEqual(report.matched_count, 2)
        self.assertEqual(report.unmatched_mesh_count, 0)
        self.assertEqual(report.property_without_mesh_count, 0)
        self.assertEqual(report.duplicate_mapping_count, 0)
        self.assertFalse(report.has_critical)

    def test_a_mesh_that_is_not_in_the_model_is_critical(self):
        """Clicking that unit does nothing, which is a broken gallery."""
        project = self._project()
        self._unit(project, mesh='GHOST')
        self._attach_glb(project, ['U1'])

        report = self.Validation._validate_project(project)

        self.assertTrue(report.has_critical)
        self.assertIn('mesh_not_in_model', report.issue_ids.mapped('code'))

    def test_the_orm_already_refuses_a_duplicate_mesh(self):
        """This turned out to be defended already, which is worth pinning.

        `property_maquette.py` carries a constraint rejecting a mesh name
        already used in the same project, so the ordinary authoring paths
        cannot create an ambiguous click at all.
        """
        from odoo.exceptions import ValidationError
        project = self._project()
        self._unit(project, mesh='SHARED')

        with self.assertRaises(ValidationError):
            self._unit(project, mesh='SHARED')

    def test_a_duplicate_that_arrived_another_way_is_still_critical(self):
        """Imports and migrations do not go through `@api.constrains`.

        The validator is the last line rather than the only one: written
        straight to the column, as a SQL import or a badly-written migration
        would, two units can still claim one mesh — and a click would then be
        ambiguous.
        """
        project = self._project()
        first = self._unit(project, mesh='SHARED')
        second = self._unit(project, mesh='OTHER')
        self._attach_glb(project, ['SHARED'])
        self.env.cr.execute(
            "UPDATE realestate_property SET maquette_mesh_name = %s "
            "WHERE id = %s", ('SHARED', second.id))
        self.env.invalidate_all()

        report = self.Validation._validate_project(project)

        self.assertEqual(report.duplicate_mapping_count, 1)
        self.assertTrue(report.has_critical)
        self.assertIn('duplicate_mapping', report.issue_ids.mapped('code'))
        self.assertTrue(first.exists())

    def test_unmatched_meshes_are_a_warning_not_an_error(self):
        """A model legitimately contains lift shafts and parked cars."""
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1', 'LIFT', 'TREE', 'CAR'])

        report = self.Validation._validate_project(project)

        self.assertEqual(report.unmatched_mesh_count, 3)
        self.assertFalse(report.has_critical)
        self.assertIn('unmatched_meshes', report.issue_ids.mapped('code'))

    def test_units_without_a_mesh_are_counted(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._unit(project)
        self._attach_glb(project, ['U1'])

        report = self.Validation._validate_project(project)

        self.assertEqual(report.property_without_mesh_count, 1)
        self.assertIn('units_without_mesh', report.issue_ids.mapped('code'))

    def test_unpriced_units_are_flagged(self):
        project = self._project()
        self._unit(project, mesh='U1', price=0.0)
        self._attach_glb(project, ['U1'])

        report = self.Validation._validate_project(project)

        self.assertIn('units_without_price', report.issue_ids.mapped('code'))

    def test_a_resolved_issue_is_closed_not_deleted(self):
        """"This was broken last Tuesday" stays answerable."""
        project = self._project()
        unit = self._unit(project, mesh='GHOST')
        self._attach_glb(project, ['U1'])
        first = self.Validation._validate_project(project)
        self.assertTrue(first.has_critical)

        unit.maquette_mesh_name = 'U1'
        second = self.Validation._validate_project(project)

        self.assertFalse(second.has_critical)
        self.assertTrue(first.issue_ids.filtered(
            lambda i: i.code == 'mesh_not_in_model').resolved)
        self.assertTrue(first.exists())

    def test_3d_enabled_with_no_model_is_critical(self):
        project = self._project()

        report = self.Validation._validate_project(project)

        self.assertTrue(report.has_critical)
        self.assertIn('no_glb', report.issue_ids.mapped('code'))

    def test_3d_disabled_with_no_model_is_not(self):
        project = self._project(visual_3d_enabled=False,
                                visual_2d_enabled=False)

        report = self.Validation._validate_project(project)

        self.assertFalse(report.has_critical)


@tagged('post_install', '-at_install')
class TestPublicationLifecycle(VisualCommon):
    """Uploading a file is not a deployment."""

    def test_a_new_project_is_draft(self):
        project = self._project()

        self.assertEqual(project.visual_publication_state, 'draft')
        self.assertFalse(project.visual_is_live)

    def test_validation_moves_a_clean_project_to_ready(self):
        project, _units = self._ready_project()

        self.assertEqual(project.visual_publication_state, 'ready')

    def test_validation_keeps_a_broken_project_in_draft(self):
        project = self._project()
        self._unit(project, mesh='GHOST')
        self._attach_glb(project, ['U1'])

        project.action_visual_validate()

        self.assertEqual(project.visual_publication_state, 'draft')

    def test_publishing_makes_it_live(self):
        project, _units = self._ready_project()

        project.action_visual_publish()

        self.assertEqual(project.visual_publication_state, 'published')
        self.assertTrue(project.visual_is_live)
        self.assertTrue(project.visual_published_on)
        self.assertEqual(project.visual_published_by_id, self.env.user)

    def test_an_unvalidated_project_cannot_be_published(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])

        with self.assertRaises(UserError):
            project.action_visual_publish()

    def test_critical_issues_block_publication(self):
        """M3: do not permit publication with critical mapping errors."""
        project = self._project()
        self._unit(project, mesh='GHOST')
        self._attach_glb(project, ['U1'])
        project.action_visual_validate()
        project.visual_publication_state = 'ready'   # forced, to isolate the gate

        with self.assertRaises(UserError) as caught:
            project.action_visual_publish()

        self.assertIn('critical', str(caught.exception).lower())

    def test_warnings_do_not_block_publication(self):
        """A validator that blocks on everything gets switched off."""
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1', 'LIFT', 'TREE'])
        self._attach_master_plan(project)
        project.action_visual_validate()

        project.action_visual_publish()

        self.assertEqual(project.visual_publication_state, 'published')

    def test_publishing_bumps_the_version(self):
        """Cache-busting on publish, not on every unrelated project edit."""
        project, _units = self._ready_project()
        before = project.visual_version

        project.action_visual_publish()

        self.assertEqual(project.visual_version, before + 1)

    def test_replacing_the_model_unpublishes_the_gallery(self):
        """The existing mapping was validated against a different file."""
        project, _units = self._ready_project()
        project.action_visual_publish()

        self._attach_glb(project, ['COMPLETELY', 'DIFFERENT'])

        self.assertEqual(project.visual_publication_state, 'draft')
        self.assertFalse(project.visual_is_live)

    def test_an_unrelated_edit_does_not_unpublish(self):
        project, _units = self._ready_project()
        project.action_visual_publish()

        project.name = 'Renamed Project'

        self.assertEqual(project.visual_publication_state, 'published')

    def test_unpublishing_returns_it_to_ready(self):
        project, _units = self._ready_project()
        project.action_visual_publish()

        project.action_visual_unpublish()

        self.assertEqual(project.visual_publication_state, 'ready')
        self.assertFalse(project.visual_is_live)


@tagged('post_install', '-at_install')
class TestReadiness(VisualCommon):
    """A number nobody has to interpret."""

    def test_a_complete_project_scores_a_hundred(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])
        self._attach_master_plan(project)
        project.property_ids.floor_plan_image = base64.b64encode(b'x' * 10)

        project.invalidate_recordset()

        self.assertEqual(project.visual_readiness_score, 100)

    def test_a_project_with_no_model_scores_low(self):
        project = self._project()
        self._unit(project)

        self.assertLess(project.visual_readiness_score, 50)

    def test_the_counts_are_reported_alongside_the_score(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._unit(project)
        self._attach_glb(project, ['U1'])

        project.invalidate_recordset()

        self.assertEqual(project.visual_unit_count, 2)
        self.assertEqual(project.visual_mapped_count, 1)
        self.assertEqual(project.visual_unmapped_count, 1)

    def test_the_detail_explains_the_score(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])

        project.invalidate_recordset()
        detail = project.visual_readiness_detail

        self.assertIn('Meshes mapped', detail)
        self.assertIn('Units priced', detail)

    def test_readiness_does_not_gate_publication(self):
        """A project can legitimately be at 80% because inventory is held back."""
        project = self._project()
        self._unit(project, mesh='U1')
        self._unit(project)              # no mesh: drags the score down
        self._attach_glb(project, ['U1'])
        self._attach_master_plan(project)
        project.action_visual_validate()

        project.action_visual_publish()

        self.assertLess(project.visual_readiness_score, 100)
        self.assertEqual(project.visual_publication_state, 'published')


@tagged('post_install', '-at_install')
class TestPublisherAuthority(VisualCommon):
    """M29 — authoring and publishing are deliberately different rights."""

    def test_an_author_cannot_publish(self):
        project, _units = self._ready_project()
        author = self._visual_user('real_estate_maquette.group_visual_author')

        with self.assertRaises(UserError):
            project.with_user(author).action_visual_publish()

    def test_an_author_cannot_validate_either(self):
        project = self._project()
        author = self._visual_user('real_estate_maquette.group_visual_author')

        with self.assertRaises(UserError):
            project.with_user(author).action_visual_validate()

    def test_a_publisher_can(self):
        project, _units = self._ready_project()
        publisher = self._visual_user(
            'real_estate_maquette.group_visual_publisher',
            'real_estate_developer.group_dev_manager')

        project.with_user(publisher).action_visual_publish()

        self.assertEqual(project.visual_publication_state, 'published')


@tagged('post_install', '-at_install')
class TestVisualMigration(VisualCommon):
    """M31 — a project that worked keeps working; a broken one stops.

    Introducing a publication lifecycle whose default is `draft` would switch
    off every existing customer's gallery on upgrade. That is safe and
    destructive at the same time, which is the combination that gets an upgrade
    rolled back on a Monday morning.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Migration = cls.env['realestate.visual.migration.runner']
        cls.MigrationLog = cls.env['realestate.visual.migration']

    def test_a_working_gallery_stays_live(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])
        self._attach_master_plan(project)

        self.Migration.run(project)

        self.assertEqual(project.visual_publication_state, 'published')
        log = self.MigrationLog.search([('project_id', '=', project.id)])
        self.assertEqual(log.outcome, 'legacy_valid')

    def test_warnings_do_not_take_a_gallery_offline(self):
        """Warnings never blocked anything before this release."""
        project = self._project()
        self._unit(project, mesh='U1')
        self._unit(project)                       # no mesh: a warning
        self._attach_glb(project, ['U1', 'LIFT'])

        self.Migration.run(project)

        self.assertEqual(project.visual_publication_state, 'published')
        log = self.MigrationLog.search([('project_id', '=', project.id)])
        self.assertEqual(log.outcome, 'legacy_valid')
        self.assertTrue(log.warning_count)

    def test_a_broken_gallery_is_taken_offline_and_reported(self):
        """It was already broken; now somebody knows."""
        project = self._project()
        self._unit(project, mesh='GHOST')
        self._attach_glb(project, ['U1'])

        self.Migration.run(project)

        self.assertEqual(project.visual_publication_state, 'draft')
        log = self.MigrationLog.search([('project_id', '=', project.id)])
        self.assertEqual(log.outcome, 'needs_review')
        self.assertTrue(log.critical_count)
        self.assertTrue(log.validation_id)

    def test_a_project_with_no_assets_is_left_in_draft(self):
        project = self._project()

        self.Migration.run(project)

        self.assertEqual(project.visual_publication_state, 'draft')
        log = self.MigrationLog.search([('project_id', '=', project.id)])
        self.assertEqual(log.outcome, 'no_assets')

    def test_nothing_is_remapped_renamed_or_deleted(self):
        """The migration reads. It does not tidy up."""
        project = self._project()
        unit = self._unit(project, mesh='GHOST')
        self._attach_glb(project, ['U1'])
        project.maquette_default_camera = '{"position":[1,2,3]}'

        self.Migration.run(project)

        self.assertEqual(unit.maquette_mesh_name, 'GHOST')
        self.assertTrue(project.maquette_glb)
        self.assertEqual(project.maquette_default_camera,
                         '{"position":[1,2,3]}')

    def test_the_inventory_it_preserved_is_recorded(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])
        project.maquette_default_camera = '{}'

        self.Migration.run(project)
        log = self.MigrationLog.search([('project_id', '=', project.id)])

        self.assertTrue(log.had_glb)
        self.assertTrue(log.had_camera)
        self.assertEqual(log.mesh_mappings, 1)

    def test_the_audit_changes_nothing(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])

        self.Migration.audit(project)

        self.assertEqual(project.visual_publication_state, 'draft')
        self.assertFalse(
            self.MigrationLog.search([('project_id', '=', project.id)]))

    def test_the_run_is_idempotent(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])

        self.Migration.run(project)
        first = project.visual_publication_state
        self.Migration.run(project)

        self.assertEqual(project.visual_publication_state, first)
        self.assertEqual(
            self.MigrationLog.search_count([('project_id', '=', project.id)]),
            1)

    def test_a_migrated_project_is_not_reprocessed_by_a_later_sweep(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])
        self.Migration.run(project)

        rows = self.Migration.audit(project)

        self.assertEqual(rows[0]['outcome'], 'already_migrated')


@tagged('post_install', '-at_install')
class TestMappingClassification(VisualCommon):
    """M7 — completing the migration, one mapping at a time.

    The project-level run answers "may this gallery be live". This answers
    "will *this* click work", which is a different question: a project can be
    perfectly publishable while a handful of its mappings point at meshes
    somebody deleted from the model three revisions ago.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Migration = cls.env['realestate.visual.migration.runner']

    def _rows_for(self, project, kind=None):
        rows = self.Migration.classify_mappings(project)
        return [r for r in rows if kind is None or r['kind'] == kind]

    def _classifications(self, project, kind=None):
        return {r['classification'] for r in self._rows_for(project, kind)}

    # -- mesh mappings -------------------------------------------------
    def test_a_mapping_with_no_model_at_all_is_a_missing_resource(self):
        project = self._project()
        self._unit(project, mesh='U1')

        rows = self._rows_for(project, 'mesh')

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['classification'], 'missing_resource')
        self.assertIn('no 3D model', rows[0]['reason'])

    def test_a_mapping_pointing_at_a_mesh_that_is_not_there_is_unmatched(self):
        project = self._project()
        self._unit(project, mesh='GHOST')
        self._attach_glb(project, ['U1'])

        rows = self._rows_for(project, 'mesh')

        self.assertEqual(rows[0]['classification'], 'unmatched')
        self.assertIn('GHOST', rows[0]['reason'])

    def test_two_units_claiming_one_mesh_are_both_duplicates(self):
        # `@api.constrains` blocks a second claimant, so this is written the
        # way the ones in production arrived: straight to the column, by a SQL
        # import that never saw the constraint.
        project = self._project()
        self._unit(project, mesh='U1')
        second = self._unit(project, mesh='OTHER')
        self._attach_glb(project, ['U1'])
        self.env.cr.execute(
            "UPDATE realestate_property SET maquette_mesh_name = %s "
            "WHERE id = %s", ('U1', second.id))
        self.env.invalidate_all()

        rows = self._rows_for(project, 'mesh')

        self.assertEqual(len(rows), 2)
        self.assertEqual({r['classification'] for r in rows}, {'duplicate'})

    def test_a_matched_mapping_nobody_has_validated_needs_validation(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])

        rows = self._rows_for(project, 'mesh')

        self.assertEqual(rows[0]['classification'], 'needs_validation')

    def test_a_matched_mapping_on_a_live_gallery_is_legacy_valid(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])
        self._attach_master_plan(project)
        self.Migration.run(project)

        rows = self._rows_for(project, 'mesh')

        self.assertEqual(rows[0]['classification'], 'legacy_valid')

    def test_a_matched_mapping_validated_but_not_live_is_valid(self):
        project = self._project()
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])
        self._attach_master_plan(project)
        project.action_visual_validate()

        rows = self._rows_for(project, 'mesh')

        self.assertEqual(rows[0]['classification'], 'valid')

    def test_an_unreadable_model_makes_every_mapping_a_missing_resource(self):
        project = self._project()
        self._unit(project, mesh='U1')
        project.maquette_glb = base64.b64encode(b'PK\x03\x04 not a glb')

        rows = self._rows_for(project, 'mesh')

        self.assertEqual(rows[0]['classification'], 'missing_resource')
        self.assertIn('could not be read', rows[0]['reason'])

    # -- plan regions --------------------------------------------------
    def _region(self, project, unit=None, polygon='[[0,0],[1,0],[1,1]]'):
        """A region, including the blank shape only an import produces.

        `polygon` is required at the ORM level and NOT NULL in the database, so
        the worst state this schema can hold is an empty string — which
        `create()` refuses and a SQL import does not. That is written directly,
        because a classifier that only sees rows the ORM would have allowed
        classifies nothing worth classifying.
        """
        region = self.env['realestate.building.region'].create({
            'project_id': project.id,
            'property_id': (unit or self._unit(project)).id,
            'polygon': polygon or '[[0,0],[1,0],[1,1]]',
        })
        if not polygon:
            self.env.cr.execute(
                "UPDATE realestate_building_region SET polygon = '' "
                "WHERE id = %s", (region.id,))
            self.env.invalidate_all()
        return region

    def test_a_region_with_no_plan_is_a_missing_resource(self):
        project = self._project()
        unit = self._unit(project)
        self._region(project, unit)

        rows = self._rows_for(project, 'region')

        self.assertEqual(rows[0]['classification'], 'missing_resource')

    def test_a_region_with_no_polygon_is_unmatched(self):
        project = self._project()
        self._attach_master_plan(project)
        unit = self._unit(project)
        self._region(project, unit, polygon=False)

        rows = self._rows_for(project, 'region')

        self.assertEqual(rows[0]['classification'], 'unmatched')

    def test_two_regions_on_one_unit_are_duplicates(self):
        project = self._project()
        self._attach_master_plan(project)
        unit = self._unit(project)
        self._region(project, unit)
        self._region(project, unit)

        rows = self._rows_for(project, 'region')

        self.assertEqual({r['classification'] for r in rows}, {'duplicate'})

    # -- the run as a whole --------------------------------------------
    def test_classifying_changes_nothing(self):
        """A report is a report. It does not repair anybody's model."""
        project = self._project()
        self._unit(project, mesh='GHOST')
        self._attach_glb(project, ['U1'])
        before = project.visual_publication_state

        self.Migration.classify_mappings(project)

        self.assertEqual(project.visual_publication_state, before)
        self.assertTrue(
            project.property_ids.filtered(
                lambda p: p.maquette_mesh_name == 'GHOST'),
            "The classifier cleared a mapping instead of reporting it.")

    def test_the_summary_counts_every_bucket(self):
        project = self._project()
        self._unit(project, mesh='GHOST')
        self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])

        summary = self.Migration.classification_summary(project)

        self.assertEqual(
            set(summary), set(self.Migration.MAPPING_CLASSIFICATION))
        self.assertEqual(summary['unmatched'], 1)
        self.assertEqual(summary['needs_validation'], 1)
        self.assertEqual(sum(summary.values()), 2)
