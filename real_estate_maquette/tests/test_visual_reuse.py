# -*- coding: utf-8 -*-
"""M5 — typical floor templates and unit-type asset reuse.

Both exist to stop the same waste: drawing or uploading the same thing once per
unit when it is the same thing. Both are **presentation only** — neither owns a
price, an availability, a reservation, a buyer or a contract, and the tests
below check that as directly as they check the reuse itself.
"""

import base64

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import VisualCommon, build_glb


@tagged('post_install', '-at_install')
class TestUnitTypeAssetReuse(VisualCommon):
    """One asset package per layout, not one per unit."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.UnitType = cls.env['realestate.visual.unit.type']

    def _layout(self, project, **kwargs):
        vals = {
            'name': 'Layout B2',
            'code': 'B2',
            'project_id': project.id,
            'company_id': self.company.id,
            'floor_plan_image': base64.b64encode(b'shared-plan'),
            'interior_glb': base64.b64encode(build_glb(['ROOM'])),
        }
        vals.update(kwargs)
        return self.UnitType.create(vals)

    def test_the_existing_category_taxonomy_is_referenced_not_replaced(self):
        """`property.type` stays what the API and Brokerage mean by 'type'."""
        project = self._project()
        layout = self._layout(project,
                              property_type_id=self.property_type.id)

        self.assertEqual(layout.property_type_id, self.property_type)
        # And it is still just a category — two fields, no assets.
        self.assertFalse(hasattr(self.property_type, 'interior_glb'))

    def test_many_units_share_one_package(self):
        project = self._project()
        layout = self._layout(project)
        units = self.Property.browse()
        for _i in range(3):
            units |= self._unit(project, visual_unit_type_id=layout.id)

        self.assertEqual(layout.property_count, 3)
        for unit in units:
            with self.subTest(unit=unit.name):
                self.assertTrue(unit.interior_glb_effective)
                self.assertTrue(unit.visual_assets_from_type)

    def test_the_binary_is_stored_once_not_per_unit(self):
        """The whole point: 400 units, four interiors."""
        project = self._project()
        layout = self._layout(project)
        unit = self._unit(project, visual_unit_type_id=layout.id)

        self.assertFalse(unit.interior_glb)          # nothing copied
        self.assertEqual(unit.interior_glb_effective, layout.interior_glb)

    def test_a_unit_can_override_its_layout(self):
        project = self._project()
        layout = self._layout(project)
        unit = self._unit(project, visual_unit_type_id=layout.id)

        unit.floor_plan_image = base64.b64encode(b'this-unit-only')

        self.assertEqual(unit.floor_plan_image_effective,
                         base64.b64encode(b'this-unit-only'))
        self.assertFalse(unit.visual_assets_from_type)

    def test_an_override_can_be_cleared_back_to_the_layout(self):
        project = self._project()
        layout = self._layout(project)
        unit = self._unit(project, visual_unit_type_id=layout.id,
                          floor_plan_image=base64.b64encode(b'own'))

        unit.action_clear_visual_override()

        self.assertEqual(unit.floor_plan_image_effective,
                         layout.floor_plan_image)

    def test_clearing_without_a_layout_is_refused(self):
        project = self._project()
        unit = self._unit(project, floor_plan_image=base64.b64encode(b'own'))

        with self.assertRaises(ValidationError):
            unit.action_clear_visual_override()

    def test_a_unit_inheriting_a_plan_reports_that_it_has_one(self):
        """0.4 answered from `floor_plan_image` alone and hid the button."""
        project = self._project()
        layout = self._layout(project)
        unit = self._unit(project, visual_unit_type_id=layout.id)

        self.assertFalse(unit.floor_plan_image)
        self.assertTrue(unit.has_floor_plan_effective)

    def test_replacing_a_shared_asset_bumps_the_version(self):
        """"Which floor plan did the customer see in March?" stays answerable."""
        project = self._project()
        layout = self._layout(project)
        self._unit(project, visual_unit_type_id=layout.id)
        before = layout.version

        layout.floor_plan_image = base64.b64encode(b'revised-plan')

        self.assertEqual(layout.version, before + 1)
        self.assertTrue(layout.assets_updated_on)
        self.assertEqual(layout.assets_updated_by_id, self.env.user)

    def test_an_unrelated_edit_does_not_bump_the_version(self):
        project = self._project()
        layout = self._layout(project)
        before = layout.version

        layout.name = 'Layout B2 (renamed)'

        self.assertEqual(layout.version, before)

    def test_replacing_a_shared_asset_changes_no_commercial_data(self):
        """Visual assets are presentation content. Nothing else moves."""
        project = self._project()
        layout = self._layout(project)
        unit = self._unit(project, visual_unit_type_id=layout.id,
                          price=1000000.0)
        state_before = unit.visual_state
        price_before = unit.list_price_developer

        layout.interior_glb = base64.b64encode(build_glb(['NEW_ROOM']))

        self.assertEqual(unit.visual_state, state_before)
        self.assertEqual(unit.list_price_developer, price_before)

    def test_the_package_owns_nothing_commercial(self):
        """Asserted on the model, not on a promise in a docstring."""
        forbidden = ('base_price', 'list_price_developer', 'price',
                     'is_available_for_sale', 'commercial_status',
                     'visual_state', 'reservation_id', 'partner_id',
                     'contract_id', 'buyer_id')
        fields_present = set(self.UnitType._fields)

        for name in forbidden:
            with self.subTest(field=name):
                self.assertNotIn(name, fields_present)

    def test_overrides_are_counted_for_the_author(self):
        project = self._project()
        layout = self._layout(project)
        self._unit(project, visual_unit_type_id=layout.id)
        self._unit(project, visual_unit_type_id=layout.id,
                   floor_plan_image=base64.b64encode(b'own'))

        layout.invalidate_recordset()

        self.assertEqual(layout.property_count, 2)
        self.assertEqual(layout.override_count, 1)

    def test_a_layout_may_not_straddle_companies(self):
        other = self.env['res.company'].create({'name': 'Other Visual Co'})
        project = self._project()

        with self.assertRaises(ValidationError):
            self._layout(project, company_id=other.id)


@tagged('post_install', '-at_install')
class TestTypicalFloorTemplates(VisualCommon):
    """Draw a typical floor once."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['realestate.visual.floor.template']
        cls.Slot = cls.env['realestate.visual.floor.template.slot']
        cls.SlotMap = cls.env['realestate.visual.floor.slot.map']
        cls.Floor = cls.env['realestate.building.floor']

    SQUARE = '[[10, 10], [40, 10], [40, 40], [10, 40]]'

    def _template(self, project, slots=('A', 'B'), **kwargs):
        vals = {'name': 'Typical Floor T01', 'code': 'T01',
                'project_id': project.id, 'company_id': self.company.id}
        vals.update(kwargs)
        template = self.Template.create(vals)
        for index, code in enumerate(slots, start=1):
            self.Slot.create({
                'template_id': template.id, 'sequence': index * 10,
                'code': code, 'label': 'Unit %s' % code,
                'polygon': self.SQUARE,
            })
        return template

    def _floor(self, project, building, number, units=2):
        made = self.Property.browse()
        for index in range(units):
            made |= self._unit(project, parent_id=building.id,
                               floor_number=number,
                               property_code='U-%s%02d' % (number, index + 1))
        floor = self.Floor.create({
            'building_id': building.id, 'unit_id': made[0].id})
        return floor, made

    def test_one_template_serves_many_floors(self):
        project = self._project()
        building = self._building(project)
        template = self._template(project)
        floor3, _u3 = self._floor(project, building, 3)
        floor4, _u4 = self._floor(project, building, 4)

        (floor3 | floor4).write({'visual_template_id': template.id})

        self.assertEqual(template.floor_count, 2)
        self.assertEqual(template.slot_count, 2)

    def test_each_floor_maps_the_slots_to_its_own_units(self):
        """The physical units stay their own records — Rule 1."""
        project = self._project()
        building = self._building(project)
        template = self._template(project)
        floor3, units3 = self._floor(project, building, 3)
        floor4, units4 = self._floor(project, building, 4)
        (floor3 | floor4).write({'visual_template_id': template.id})
        slot_a = template.slot_ids[0]

        self.SlotMap.create({'floor_id': floor3.id, 'slot_id': slot_a.id,
                             'property_id': units3[0].id})
        self.SlotMap.create({'floor_id': floor4.id, 'slot_id': slot_a.id,
                             'property_id': units4[0].id})

        regions3 = {r['slot_code']: r for r in floor3.visual_regions()}
        regions4 = {r['slot_code']: r for r in floor4.visual_regions()}
        self.assertEqual(regions3['A']['property_id'], units3[0].id)
        self.assertEqual(regions4['A']['property_id'], units4[0].id)
        self.assertEqual(regions3['A']['polygon'], regions4['A']['polygon'])

    def test_geometry_is_not_duplicated(self):
        """Two floors, one set of polygons."""
        project = self._project()
        building = self._building(project)
        template = self._template(project)
        floor3, _u = self._floor(project, building, 3)
        floor4, _u = self._floor(project, building, 4)
        (floor3 | floor4).write({'visual_template_id': template.id})

        self.assertEqual(
            self.Slot.search_count([('template_id', '=', template.id)]), 2)

    def test_a_slot_cannot_point_at_a_unit_on_another_floor(self):
        """The most damaging mistake: somebody else's apartment behind a click."""
        project = self._project()
        building = self._building(project)
        template = self._template(project)
        floor3, _units3 = self._floor(project, building, 3)
        _floor4, units4 = self._floor(project, building, 4)
        floor3.visual_template_id = template.id

        with self.assertRaises(ValidationError):
            self.SlotMap.create({
                'floor_id': floor3.id, 'slot_id': template.slot_ids[0].id,
                'property_id': units4[0].id})

    def test_a_slot_from_another_template_is_refused(self):
        project = self._project()
        building = self._building(project)
        template = self._template(project)
        other = self._template(project, slots=('X',), code='T02',
                               name='Other Template')
        floor3, units3 = self._floor(project, building, 3)
        floor3.visual_template_id = template.id

        with self.assertRaises(ValidationError):
            self.SlotMap.create({
                'floor_id': floor3.id, 'slot_id': other.slot_ids[0].id,
                'property_id': units3[0].id})

    def test_a_unit_occupies_one_slot_per_floor(self):
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger
        project = self._project()
        building = self._building(project)
        template = self._template(project)
        floor3, units3 = self._floor(project, building, 3)
        floor3.visual_template_id = template.id
        self.SlotMap.create({
            'floor_id': floor3.id, 'slot_id': template.slot_ids[0].id,
            'property_id': units3[0].id})

        with self.assertRaises(IntegrityError), \
                mute_logger('odoo.sql_db'):
            self.SlotMap.create({
                'floor_id': floor3.id, 'slot_id': template.slot_ids[1].id,
                'property_id': units3[0].id})

    def test_validation_reports_missing_mappings_without_guessing(self):
        project = self._project()
        building = self._building(project)
        template = self._template(project)
        floor3, units3 = self._floor(project, building, 3)
        floor3.visual_template_id = template.id
        self.SlotMap.create({
            'floor_id': floor3.id, 'slot_id': template.slot_ids[0].id,
            'property_id': units3[0].id})

        report = template.validate_assignments()

        self.assertEqual(len(report), 1)
        self.assertFalse(report[0]['complete'])
        self.assertEqual(report[0]['missing'], ['B'])

    def test_a_fully_mapped_floor_validates_clean(self):
        project = self._project()
        building = self._building(project)
        template = self._template(project)
        floor3, units3 = self._floor(project, building, 3)
        floor3.visual_template_id = template.id
        for slot, unit in zip(template.slot_ids, units3):
            self.SlotMap.create({'floor_id': floor3.id, 'slot_id': slot.id,
                                 'property_id': unit.id})

        floor3.invalidate_recordset()
        report = template.validate_assignments()

        self.assertTrue(report[0]['complete'])
        self.assertTrue(floor3.visual_mapping_complete)
        self.assertEqual(floor3.visual_unmapped_slots, 0)

    def test_an_unmapped_slot_still_renders_as_unmapped_rather_than_vanishing(self):
        project = self._project()
        building = self._building(project)
        template = self._template(project)
        floor3, _units3 = self._floor(project, building, 3)
        floor3.visual_template_id = template.id

        regions = floor3.visual_regions()

        self.assertEqual(len(regions), 2)
        self.assertFalse(any(r['mapped'] for r in regions))

    def test_a_floor_with_no_template_is_unaffected(self):
        """Templates are additive: nothing that works today stops working."""
        project = self._project()
        building = self._building(project)
        floor3, _units = self._floor(project, building, 3)

        self.assertEqual(floor3.visual_regions(), [])
        self.assertFalse(floor3.visual_mapping_complete)

    def test_a_template_in_use_cannot_be_deleted(self):
        project = self._project()
        building = self._building(project)
        template = self._template(project)
        floor3, _units = self._floor(project, building, 3)
        floor3.visual_template_id = template.id

        with self.assertRaises(UserError):
            template.unlink()

    def test_detaching_leaves_the_floor_and_units_intact(self):
        project = self._project()
        building = self._building(project)
        template = self._template(project)
        floor3, units3 = self._floor(project, building, 3)
        floor3.visual_template_id = template.id
        self.SlotMap.create({
            'floor_id': floor3.id, 'slot_id': template.slot_ids[0].id,
            'property_id': units3[0].id})

        floor3.action_detach_template()

        self.assertFalse(floor3.visual_template_id)
        self.assertFalse(floor3.visual_slot_map_ids)
        self.assertTrue(floor3.exists())
        self.assertTrue(units3[0].exists())
        template.unlink()          # now removable

    def test_slot_geometry_obeys_the_same_rules_as_a_region(self):
        project = self._project()
        template = self._template(project, slots=())

        for bad in ('not json', '[[1,2]]', '[[1,2],[3,4],[200,5]]',
                    '[[1,2],[3,4],["x",5]]'):
            with self.subTest(polygon=bad):
                with self.assertRaises(ValidationError):
                    self.Slot.create({
                        'template_id': template.id, 'code': 'BAD',
                        'polygon': bad})

    def test_a_template_owns_nothing_commercial(self):
        forbidden = ('base_price', 'price', 'is_available_for_sale',
                     'commercial_status', 'visual_state', 'partner_id',
                     'reservation_id', 'contract_id')
        for model in (self.Template, self.Slot):
            for name in forbidden:
                with self.subTest(model=model._name, field=name):
                    self.assertNotIn(name, set(model._fields))


@tagged('post_install', '-at_install')
class TestTemplateFromExistingFloor(VisualCommon):
    """Lift a floor somebody already drew into a reusable template."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['realestate.visual.floor.template']
        cls.Floor = cls.env['realestate.building.floor']

    def setUp(self):
        super().setUp()
        if self.PlanRegion is None:
            self.skipTest("The drawn polygons belong to 2D Plan, which is not installed.")

    def _drawn_floor(self):
        project = self._project()
        building = self._building(project)
        units = self.Property.browse()
        for index in range(2):
            units |= self._unit(project, parent_id=building.id,
                                floor_number=3,
                                property_code='A-30%d' % (index + 1))
        for index, unit in enumerate(units):
            self.env['realestate.plan.region'].create({
                'parent_property_id': building.id,
                'target_property_id': unit.id,
                'label': 'Unit %d' % (index + 1),
                'polygon': '[[10, 10], [40, 10], [40, 40], [10, 40]]',
            })
        floor = self.Floor.create({
            'building_id': building.id, 'unit_id': units[0].id})
        return project, floor, units

    def test_a_drawn_floor_becomes_a_template(self):
        _project, floor, _units = self._drawn_floor()

        template = self.Template.create_from_floor(floor, name='Lifted T01')

        self.assertEqual(template.name, 'Lifted T01')
        self.assertEqual(template.slot_count, 2)

    def test_the_original_polygons_are_copied_not_moved(self):
        """Floor 3 keeps working exactly as it did."""
        _project, floor, units = self._drawn_floor()
        before = self.env['realestate.plan.region'].search_count(
            [('target_property_id', 'in', units.ids)])

        self.Template.create_from_floor(floor)

        self.assertEqual(
            self.env['realestate.plan.region'].search_count(
                [('target_property_id', 'in', units.ids)]), before)

    def test_slot_codes_come_from_the_source_units(self):
        _project, floor, _units = self._drawn_floor()

        template = self.Template.create_from_floor(floor)

        self.assertTrue(all(slot.code for slot in template.slot_ids))

    def test_a_floor_with_nothing_drawn_is_refused_with_a_reason(self):
        project = self._project()
        building = self._building(project)
        unit = self._unit(project, parent_id=building.id, floor_number=3)
        floor = self.Floor.create({
            'building_id': building.id, 'unit_id': unit.id})

        with self.assertRaises(UserError) as caught:
            self.Template.create_from_floor(floor)

        self.assertIn('no polygons', str(caught.exception).lower())
