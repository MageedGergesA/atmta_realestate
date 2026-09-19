# -*- coding: utf-8 -*-
"""Findings of the visual lifecycle run on the 2D layer, reproduced first."""

from odoo.tests import tagged
from odoo.tests.common import HttpCase

from odoo.addons.real_estate_maquette.tests.common import VisualCommon

SQUARE = '[[10, 10], [40, 10], [40, 40], [10, 40]]'


@tagged('post_install', '-at_install')
class TestPlanShowsGalleryAvailability(VisualCommon, HttpCase):
    """The drill-down viewer coloured units from the legacy `property.state`.

    An unreleased unit reads `available` there, so it was painted green and
    offered, while the gallery (and Developer) say it is not on sale.
    """

    def setUp(self):
        super().setUp()
        # A Visual Manager who may also edit projects: the routes check the
        # project's own access rights before anything else.
        self.env.ref('base.user_admin').groups_id |= (
            self.env.ref('real_estate_maquette.group_visual_manager')
            | self.env.ref('real_estate_developer.group_dev_manager'))
        self.project = self._project()
        self.building = self._building(self.project)
        self.unit = self._unit(self.project, released=False,
                               parent_id=self.building.id)
        self.assertEqual(self.unit.visual_state, 'unreleased')
        self.env['realestate.plan.region'].create({
            'parent_property_id': self.building.id,
            'target_property_id': self.unit.id, 'polygon': SQUARE,
        })
        self.authenticate('admin', 'admin')

    def _rpc(self, route):
        return self.make_jsonrpc_request(route, {})

    def test_regions_carry_the_gallery_state(self):
        for route in ('/real_estate_plan/regions/%s' % self.building.id,
                      '/real_estate_plan/property_view/%s' % self.building.id):
            with self.subTest(route=route):
                data = self._rpc(route)
                region = next(r for r in data['regions']
                              if r['target_id'] == self.unit.id)
                self.assertEqual(region['target_state'], 'unreleased')
                child = next(c for c in data['children']
                             if c['id'] == self.unit.id)
                self.assertEqual(child['state'], 'unreleased')

    def test_master_plan_regions_carry_the_gallery_state(self):
        self._attach_master_plan(self.project)
        self.env['realestate.building.region'].create({
            'project_id': self.project.id, 'property_id': self.unit.id,
            'polygon': SQUARE,
        })

        data = self._rpc('/real_estate_plan/project_view/%s' % self.project.id)

        region = next(r for r in data['regions']
                      if r['target_id'] == self.unit.id)
        self.assertEqual(region['target_state'], 'unreleased')


@tagged('post_install', '-at_install')
class TestPlanFindings(VisualCommon):

    def test_a_plan_region_is_named_after_its_label_or_target(self):
        project = self._project()
        building = self._building(project)
        first = self._unit(project, released=False, parent_id=building.id)
        second = self._unit(project, released=False, parent_id=building.id)
        Region = self.env['realestate.plan.region']
        labelled = Region.create({
            'parent_property_id': building.id, 'target_property_id': first.id,
            'polygon': SQUARE, 'label': 'Corner Flat',
        })
        unlabelled = Region.create({
            'parent_property_id': building.id, 'target_property_id': second.id,
            'polygon': SQUARE,
        })

        self.assertEqual(labelled.display_name, 'Corner Flat')
        self.assertEqual(unlabelled.display_name, second.display_name)

    def test_lifted_slot_codes_are_the_position_on_the_floor(self):
        """A lifted template called its slots '-301', '-302'."""
        project = self._project()
        building = self._building(project)
        units = self.Property.browse()
        for index in range(2):
            units |= self._unit(project, parent_id=building.id, floor_number=3,
                                property_code='A-30%d' % (index + 1))
        for unit in units:
            self.env['realestate.plan.region'].create({
                'parent_property_id': building.id,
                'target_property_id': unit.id, 'polygon': SQUARE,
            })
        floor = self.env['realestate.building.floor'].create(
            {'building_id': building.id, 'unit_id': units[0].id})

        template = self.env[
            'realestate.visual.floor.template'].create_from_floor(floor)

        self.assertEqual(sorted(template.slot_ids.mapped('code')), ['01', '02'])

    def test_the_boundary_tab_warns_that_a_new_plan_unpublishes(self):
        """Uploading the plan there silently sent a live gallery to draft."""
        arch = self.env['realestate.project'].get_views(
            [(False, 'form')])['views']['form']['arch']

        self.assertIn('o_re_master_plan_unpublish_warning', arch)
