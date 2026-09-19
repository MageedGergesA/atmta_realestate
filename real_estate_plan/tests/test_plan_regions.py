# -*- coding: utf-8 -*-
"""The 2D layer's own tests. It shipped with none.

The Phase 0 audit found `realestate.plan.region` already well constrained —
JSON validity, at least three vertices, numeric coordinates, the 0–100 range,
target ≠ parent, a real parent/child relationship, SQL uniqueness. Every one of
those was a *claim*: nothing exercised them, and this module contributed zero
tests to the suite.

These make them guarantees, and cover the tenancy M4.5 added.
"""

import json

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class PlanRegionCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Property = cls.env['realestate.property']
        cls.Region = cls.env['realestate.plan.region']
        cls.env.user.groups_id |= cls.env.ref(
            'real_estate_maquette.group_visual_author')

    SQUARE = '[[10, 10], [40, 10], [40, 40], [10, 40]]'

    _seq = 0

    def _property(self, level='building', parent=None, **kwargs):
        type(self)._seq += 1
        vals = {
            'name': 'P-%03d' % type(self)._seq,
            'property_code': 'P-%03d' % type(self)._seq,
            'hierarchy_level': level,
            'company_id': self.company.id,
        }
        if parent is not None:
            vals['parent_id'] = parent.id
        vals.update(kwargs)
        return self.Property.create(vals)

    def _region(self, parent, target, **kwargs):
        vals = {
            'parent_property_id': parent.id,
            'target_property_id': target.id,
            'polygon': self.SQUARE,
        }
        vals.update(kwargs)
        return self.Region.create(vals)


@tagged('post_install', '-at_install')
class TestPolygonGeometry(PlanRegionCommon):
    """The constraints the audit found, now exercised."""

    def test_a_valid_region_is_accepted(self):
        building = self._property()
        floor = self._property('floor', parent=building)

        region = self._region(building, floor)

        self.assertEqual(json.loads(region.polygon), json.loads(self.SQUARE))

    def test_a_polygon_must_be_valid_json(self):
        building = self._property()
        floor = self._property('floor', parent=building)

        with self.assertRaises(ValidationError):
            self._region(building, floor, polygon='not json at all')

    def test_two_vertices_are_not_a_polygon(self):
        building = self._property()
        floor = self._property('floor', parent=building)

        with self.assertRaises(ValidationError):
            self._region(building, floor, polygon='[[10, 10], [40, 10]]')

    def test_a_vertex_must_be_a_numeric_pair(self):
        building = self._property()
        floor = self._property('floor', parent=building)

        for bad in ('[[10,10],[20,20],[30]]',
                    '[[10,10],[20,20],["x",30]]',
                    '[[10,10],[20,20],{"x":1,"y":2}]'):
            with self.subTest(polygon=bad):
                with self.assertRaises(ValidationError):
                    self._region(building, floor, polygon=bad)

    def test_coordinates_are_percentages(self):
        """Normalised, so regions survive any display size."""
        building = self._property()
        floor = self._property('floor', parent=building)

        for bad in ('[[10,10],[20,20],[101,30]]',
                    '[[10,10],[20,20],[30,-1]]'):
            with self.subTest(polygon=bad):
                with self.assertRaises(ValidationError):
                    self._region(building, floor, polygon=bad)

    def test_the_boundaries_themselves_are_allowed(self):
        building = self._property()
        floor = self._property('floor', parent=building)

        region = self._region(building, floor,
                              polygon='[[0,0],[100,0],[100,100],[0,100]]')

        self.assertTrue(region.exists())


@tagged('post_install', '-at_install')
class TestHierarchy(PlanRegionCommon):
    """A region points at a child, and only a child."""

    def test_a_region_cannot_point_at_its_own_parent(self):
        building = self._property()

        with self.assertRaises(ValidationError):
            self._region(building, building)

    def test_the_target_must_be_a_direct_child(self):
        building = self._property()
        other = self._property()

        with self.assertRaises(ValidationError):
            self._region(building, other)

    def test_a_grandchild_is_not_a_direct_child(self):
        building = self._property()
        floor = self._property('floor', parent=building)
        unit = self._property('unit', parent=floor)

        with self.assertRaises(ValidationError):
            self._region(building, unit)

    def test_one_region_per_target_on_a_plan(self):
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger
        building = self._property()
        floor = self._property('floor', parent=building)
        self._region(building, floor)

        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            self._region(building, floor)

    def test_the_same_target_may_appear_on_a_different_plan(self):
        """Uniqueness is per parent, not global."""
        first = self._property()
        second = self._property()
        floor_a = self._property('floor', parent=first)
        floor_b = self._property('floor', parent=second)

        self._region(first, floor_a)
        region = self._region(second, floor_b)

        self.assertTrue(region.exists())


@tagged('post_install', '-at_install')
class TestPlanRegionTenancy(PlanRegionCommon):
    """M4.5 added `company_id` and a record rule. This is that, checked.

    Before it, `base.group_user` held full CRUD on this model with no company
    rule at all — every internal user could edit any company's production
    polygon mappings.
    """

    def test_the_company_follows_the_property_it_is_drawn_on(self):
        building = self._property()
        floor = self._property('floor', parent=building)

        region = self._region(building, floor)

        self.assertEqual(region.company_id, building.company_id)

    def test_another_companys_regions_are_invisible(self):
        building = self._property()
        floor = self._property('floor', parent=building)
        region = self._region(building, floor)

        other_company = self.env['res.company'].create({'name': 'Rival Plan'})
        stranger = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Rival Planner',
                'login': 'rival.plan@test.example',
                'company_id': other_company.id,
                'company_ids': [(6, 0, [other_company.id])],
                'groups_id': [(6, 0, [
                    self.env.ref('base.group_user').id,
                    self.env.ref(
                        'real_estate_maquette.group_visual_author').id])]})

        visible = self.Region.with_user(stranger).search(
            [('id', '=', region.id)])

        self.assertFalse(visible)

    def test_an_ordinary_employee_can_read_but_not_edit(self):
        """The ACL that made everybody an author is gone."""
        from odoo.exceptions import AccessError
        building = self._property()
        floor = self._property('floor', parent=building)
        region = self._region(building, floor)
        employee = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Ordinary Employee',
                'login': 'ordinary.plan@test.example',
                'company_id': self.company.id,
                'company_ids': [(6, 0, [self.company.id])],
                'groups_id': [(6, 0, [self.env.ref('base.group_user').id])]})

        self.assertTrue(region.with_user(employee).read(['id']))

        with self.assertRaises(AccessError):
            region.with_user(employee).write({'label': 'edited'})

    def test_a_visual_author_can_edit(self):
        building = self._property()
        floor = self._property('floor', parent=building)
        region = self._region(building, floor)
        author = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Plan Author',
                'login': 'plan.author@test.example',
                'company_id': self.company.id,
                'company_ids': [(6, 0, [self.company.id])],
                'groups_id': [(6, 0, [
                    self.env.ref('base.group_user').id,
                    self.env.ref(
                        'real_estate_maquette.group_visual_author').id])]})

        region.with_user(author).write({'label': 'Edited by author'})

        self.assertEqual(region.label, 'Edited by author')
