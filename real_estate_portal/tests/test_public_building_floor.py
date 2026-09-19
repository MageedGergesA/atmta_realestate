# -*- coding: utf-8 -*-
"""Anonymous building and floor JSON only for published projects, public fields only.

`/projects/portal/building/<id>.json` checked that the record was a building
and nothing else, and `/projects/portal/floor/<id>/units.json` checked only that
the floor existed. Both read under `sudo()`, so anybody could walk the ids and
read every building and floor of every company, published or not, and the
floor route returned each unit's internal base price and raw status: the leak
`units.json` had already been fixed for. Found in a review of the suite's public
routes.
"""

import base64
import json

from odoo.tests import tagged
from odoo.tests.common import HttpCase

from odoo.addons.real_estate_maquette.tests.common import VisualCommon

ONE_PIXEL_PNG = base64.b64decode(
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
    b'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')


@tagged('post_install', '-at_install')
class TestPublicBuildingAndFloor(VisualCommon, HttpCase):

    def setUp(self):
        super().setUp()
        self.env.user.groups_id |= self.env.ref('real_estate_maquette.group_visual_manager')
        self.project = self._project(visual_public_enabled=True)
        self.building = self._building(self.project)
        # Not released: an unlaunched unit has no public price (0.0), while its
        # internal base price is a real figure. The two must not be confused.
        self.unit = self._unit(self.project, released=False, price=1_000_000.0,
                               parent_id=self.building.id, floor_number=3)
        # A floor is identified by any unit on it; its number follows the unit.
        self.floor = self.env['realestate.building.floor'].create({
            'building_id': self.building.id, 'unit_id': self.unit.id,
        })
        self.assertIn(self.unit, self.floor.unit_ids)

    def _publish(self):
        self.project.master_plan_2d = base64.b64encode(ONE_PIXEL_PNG)
        self.project.visual_3d_enabled = False
        self.project.action_visual_validate()
        self.project.action_visual_publish()
        self.assertTrue(self.project.visual_is_live)

    def _get(self, url):
        self.authenticate(None, None)
        return self.url_open(url, allow_redirects=False)

    def test_an_unpublished_project_is_not_found(self):
        self.assertFalse(self.project.visual_is_live)
        for url in ('/projects/portal/building/%s.json' % self.building.id,
                    '/projects/portal/floor/%s/units.json' % self.floor.id):
            self.assertEqual(self._get(url).status_code, 404, url)

    def test_a_published_project_serves_public_fields_only(self):
        self._publish()
        building = self._get('/projects/portal/building/%s.json' % self.building.id)
        self.assertEqual(building.status_code, 200)
        self.assertEqual(json.loads(building.content)['building']['id'], self.building.id)

        floor = self._get('/projects/portal/floor/%s/units.json' % self.floor.id)
        self.assertEqual(floor.status_code, 200)
        units = {entry['id']: entry for entry in json.loads(floor.content)}
        entry = units[self.unit.id]
        self.assertEqual(entry['base_price'], self.unit.sudo()._public_price())
        self.assertEqual(entry['base_price'], 0.0,
                         "an unreleased unit's internal base price must not be served")
        self.assertNotIn('commercial_status', entry)
        self.assertNotIn('unavailable_reason', entry)
