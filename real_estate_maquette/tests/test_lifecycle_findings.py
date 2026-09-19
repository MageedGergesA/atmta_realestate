# -*- coding: utf-8 -*-
"""Findings of the visual lifecycle run, each reproduced before it was fixed.

The run drove the gallery the way a team would: pin buildings on the master
plan, archive and republish, open the building preview, lift a drawn floor into
a template, and look for the buttons that publish a gallery.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import HttpCase

from .common import VisualCommon

SQUARE = '[[10, 10], [40, 10], [40, 40], [10, 40]]'


@tagged('post_install', '-at_install')
class TestRegionStaysInItsProject(VisualCommon):
    """Pinning a building of another project on a master plan moved it."""

    def test_a_building_of_another_project_is_refused(self):
        mine, other = self._project(), self._project()
        foreign = self._building(other)

        with self.assertRaises(ValidationError):
            self.BuildingRegion.create({
                'project_id': mine.id, 'property_id': foreign.id,
                'polygon': SQUARE,
            })

        self.assertEqual(foreign.project_id, other)

    def test_a_region_cannot_be_moved_onto_another_project(self):
        mine, other = self._project(), self._project()
        region = self.BuildingRegion.create({
            'project_id': mine.id, 'property_id': self._building(mine).id,
            'polygon': SQUARE,
        })

        with self.assertRaises(ValidationError):
            region.project_id = other

    def test_a_property_without_a_project_is_still_adopted(self):
        mine = self._project()
        stray = self.Property.create({
            'name': 'Stray Block', 'property_code': 'STRAY-B',
            'hierarchy_level': 'building', 'company_id': self.company.id,
        })
        loose_unit = self.Property.create({
            'name': 'Stray Unit', 'property_code': 'STRAY-U',
            'hierarchy_level': 'unit', 'company_id': self.company.id,
            'parent_id': stray.id,
        })

        self.BuildingRegion.create({
            'project_id': mine.id, 'property_id': stray.id, 'polygon': SQUARE,
        })

        self.assertEqual(stray.project_id, mine)
        self.assertEqual(loose_unit.project_id, mine)

    def test_a_descendant_already_in_another_project_is_left_there(self):
        mine, other = self._project(), self._project()
        stray = self.Property.create({
            'name': 'Mixed Block', 'property_code': 'MIXED-B',
            'hierarchy_level': 'building', 'company_id': self.company.id,
        })
        foreign_unit = self._unit(other, released=False, parent_id=stray.id)

        self.BuildingRegion.create({
            'project_id': mine.id, 'property_id': stray.id, 'polygon': SQUARE,
        })

        self.assertEqual(stray.project_id, mine)
        self.assertEqual(foreign_unit.project_id, other)

    def test_a_region_is_named_after_its_label_or_property(self):
        project = self._project()
        building = self._building(project)
        labelled = self.BuildingRegion.create({
            'project_id': project.id, 'property_id': building.id,
            'polygon': SQUARE, 'label': 'North Tower',
        })
        unlabelled = self.BuildingRegion.create({
            'project_id': project.id,
            'property_id': self._building(project).id, 'polygon': SQUARE,
        })

        self.assertEqual(labelled.display_name, 'North Tower')
        self.assertEqual(unlabelled.display_name,
                         unlabelled.property_id.display_name)


@tagged('post_install', '-at_install')
class TestRegionRouteStaysInItsProject(VisualCommon, HttpCase):
    """The viewer's save route accepted any building and any region id."""

    def setUp(self):
        super().setUp()
        # A Visual Manager who may also edit projects: the routes check the
        # project's own access rights before anything else.
        self.env.ref('base.user_admin').groups_id |= (
            self.env.ref('real_estate_maquette.group_visual_manager')
            | self.env.ref('real_estate_developer.group_dev_manager'))
        self.mine, self.other = self._project(), self._project()
        self.authenticate('admin', 'admin')

    def _save(self, **params):
        payload = {'project_id': self.mine.id,
                   'polygon': [[1, 1], [20, 1], [20, 20]]}
        payload.update(params)
        return self.make_jsonrpc_request('/maquette/regions/save', payload)

    def test_a_building_of_another_project_is_refused(self):
        foreign = self._building(self.other)

        result = self._save(building_id=foreign.id)

        self.assertEqual(result, {'error': 'foreign_property'})
        self.assertEqual(foreign.project_id, self.other)
        self.assertFalse(self.BuildingRegion.search(
            [('property_id', '=', foreign.id)]))

    def test_a_region_of_another_project_is_not_found(self):
        building = self._building(self.other)
        region = self.BuildingRegion.create({
            'project_id': self.other.id, 'property_id': building.id,
            'polygon': SQUARE,
        })

        result = self._save(building_id=building.id, region_id=region.id)

        self.assertEqual(result, {'error': 'not_found'})
        self.assertEqual(region.project_id, self.other)


@tagged('post_install', '-at_install')
class TestArchivedGalleryIsRevalidated(VisualCommon):
    """Archive, swap in a different file, publish: it went live unvalidated."""

    def test_replacing_a_file_while_archived_returns_to_draft(self):
        project, _units = self._ready_project()
        project.action_visual_publish()
        project.action_visual_archive()

        self._attach_glb(project, ('SOMETHING_ELSE',))

        self.assertEqual(project.visual_publication_state, 'draft')
        with self.assertRaises(UserError):
            project.action_visual_publish()

    def test_an_untouched_archive_can_still_be_republished(self):
        project, _units = self._ready_project()
        project.action_visual_publish()
        project.action_visual_archive()

        project.action_visual_publish()

        self.assertEqual(project.visual_publication_state, 'published')


@tagged('post_install', '-at_install')
class TestLifecycleStateGuards(VisualCommon):
    """Unpublish and Archive were hidden by state in the view only."""

    def test_only_a_published_gallery_can_be_unpublished(self):
        draft = self._project()
        with self.assertRaises(UserError):
            draft.action_visual_unpublish()
        self.assertEqual(draft.visual_publication_state, 'draft')

        project, _units = self._ready_project()
        with self.assertRaises(UserError):
            project.action_visual_unpublish()
        project.action_visual_publish()
        project.action_visual_unpublish()
        self.assertEqual(project.visual_publication_state, 'ready')

    def test_only_a_ready_or_published_gallery_can_be_archived(self):
        draft = self._project()
        with self.assertRaises(UserError):
            draft.action_visual_archive()
        self.assertEqual(draft.visual_publication_state, 'draft')

        project, _units = self._ready_project()
        project.action_visual_archive()
        self.assertEqual(project.visual_publication_state, 'archived')
        with self.assertRaises(UserError):
            project.action_visual_archive()


@tagged('post_install', '-at_install')
class TestStatsFollowTheGallery(VisualCommon):
    """Building preview and floor counters read the legacy `property.state`."""

    def setUp(self):
        super().setUp()
        self.project = self._project()
        self.building = self._building(self.project)
        self.released = self._unit(self.project, parent_id=self.building.id,
                                   floor_number=3)
        self.unreleased = self._unit(self.project, released=False,
                                     parent_id=self.building.id,
                                     floor_number=3)
        self.assertEqual(self.unreleased.visual_state, 'unreleased')

    def test_building_preview_counts_only_units_on_sale(self):
        preview = self.env['realestate.building.preview'].create(
            {'property_id': self.building.id})

        self.assertEqual(preview.units_total, 2)
        self.assertEqual(preview.units_available, 1)

    def test_floor_counts_only_units_on_sale(self):
        floor = self.env['realestate.building.floor'].create({
            'building_id': self.building.id, 'unit_id': self.released.id})

        self.assertEqual(floor.units_total, 2)
        self.assertEqual(floor.units_available, 1)


@tagged('post_install', '-at_install')
class TestGalleryLifecycleIsReachable(VisualCommon):
    """The lifecycle methods existed, but no button on any form called them."""

    def test_the_project_form_offers_the_lifecycle(self):
        publisher = self._visual_user(
            'real_estate_maquette.group_visual_publisher',
            'real_estate_developer.group_dev_manager')
        author = self._visual_user(
            'real_estate_maquette.group_visual_author',
            'real_estate_developer.group_dev_manager')

        def arch_for(user):
            return self.Project.with_user(user).get_views(
                [(False, 'form')])['views']['form']['arch']

        arch = arch_for(publisher)
        author_arch = arch_for(author)
        for name in ('action_visual_validate', 'action_visual_publish',
                     'action_visual_unpublish', 'action_visual_archive'):
            self.assertIn('name="%s"' % name, arch)
            # Shown to the role the methods themselves require.
            self.assertNotIn('name="%s"' % name, author_arch)
        for field in ('visual_publication_state', 'visual_public_enabled',
                      'visual_readiness_score'):
            self.assertIn('name="%s"' % field, arch)



@tagged('post_install', '-at_install')
class TestPriceFilterFollowsTheShownPrice(VisualCommon):
    """The price range searched `base_price`, the internal figure.

    A public or broker audience is shown `_public_price()` (0.0 for a unit not
    on the market), so a range search matched units on a price nobody in that
    audience may see -- and repeated ranges would reveal it by bisection.
    """

    def test_a_public_price_range_does_not_match_on_the_internal_price(self):
        project = self._project()
        on_sale = self._unit(project, price=800000.0)
        held_back = self._unit(project, released=False, price=1500000.0)
        Gallery = self.env['realestate.visual.gallery']

        public = Gallery.search_units(
            project, {'price_min': 1000000.0}, audience='public')

        self.assertNotIn(held_back.id, public['matching_ids'])
        self.assertEqual(public['matching_ids'], [])
        cheap = Gallery.search_units(
            project, {'price_max': 900000.0}, audience='public')
        self.assertEqual(cheap['matching_ids'], [on_sale.id])
