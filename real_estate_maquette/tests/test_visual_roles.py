# -*- coding: utf-8 -*-
"""M7 — the role matrix, asserted rather than described.

Four roles, each a superset of the last:

```
    VIEWER      opens a published gallery. Changes nothing.
      ↑
    AUTHOR      uploads models, draws regions, maps meshes. Cannot publish.
      ↑
    PUBLISHER   validates, and decides a customer may see it.
      ↑
    MANAGER     everything, including revoking a public grant.
```

The line that matters most is between Author and Publisher: **drawing a
mapping and putting it in front of a customer are deliberately different
rights.** An author who could publish would be an author who could show a
half-mapped tower to a buyer.

Every assertion below goes through the real gate — `with_user()`, real groups,
real ACLs — never a `has_group()` check standing in for one.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import VisualCommon


@tagged('post_install', '-at_install')
class TestVisualRoleMatrix(VisualCommon):

    def setUp(self):
        super().setUp()
        # A ready-to-publish gallery, built by a user who is allowed to.
        self.env.user.groups_id |= self.env.ref(
            'real_estate_maquette.group_visual_manager')
        self.project = self._project()
        self.unit = self._unit(self.project, mesh='U1')
        self._attach_glb(self.project, ['U1'])
        self._attach_master_plan(self.project)

    def _user(self, role):
        groups = [self.env.ref('base.group_user').id,
                  self.env.ref('atmta_real_estate.group_realestate_user').id,
                  self.env.ref('real_estate_developer.group_dev_manager').id]
        if role:
            groups.append(
                self.env.ref('real_estate_maquette.%s' % role).id)
        return self.env['res.users'].create({
            'name': 'Role %s' % (role or 'none'),
            'login': 'role_%s' % (role or 'none'),
            'groups_id': [(6, 0, groups)],
        })

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def test_a_viewer_may_read_the_mapping(self):
        viewer = self._user('group_visual_viewer')
        region = self.env['realestate.building.region'].create({
            'project_id': self.project.id,
            'property_id': self.unit.id,
            'polygon': '[[0,0],[1,0],[1,1]]',
        })

        found = self.env['realestate.building.region'].with_user(
            viewer).search([('id', '=', region.id)])

        self.assertEqual(found, region)

    def test_any_internal_user_may_read_the_mapping_but_not_change_it(self):
        """Read is deliberately open to internal users.

        `access_building_region_base` grants `base.group_user` read and nothing
        else, because a project form has to render its regions for anybody who
        may see the project. The visual roles govern *changing* the mapping,
        which is the part that decides where a customer's click lands.
        """
        nobody = self._user(None)

        self.env['realestate.building.region'].with_user(nobody).search([])

        with self.assertRaises(AccessError):
            self.env['realestate.building.region'].with_user(nobody).create({
                'project_id': self.project.id,
                'property_id': self.unit.id,
                'polygon': '[[0,0],[1,0],[1,1]]',
            })

    # ------------------------------------------------------------------
    # Authoring
    # ------------------------------------------------------------------
    def test_a_viewer_may_not_draw_a_region(self):
        viewer = self._user('group_visual_viewer')

        with self.assertRaises(AccessError):
            self.env['realestate.building.region'].with_user(viewer).create({
                'project_id': self.project.id,
                'property_id': self.unit.id,
                'polygon': '[[0,0],[1,0],[1,1]]',
            })

    def test_an_author_may_draw_a_region(self):
        author = self._user('group_visual_author')

        region = self.env['realestate.building.region'].with_user(
            author).create({
                'project_id': self.project.id,
                'property_id': self.unit.id,
                'polygon': '[[0,0],[1,0],[1,1]]',
            })

        self.assertTrue(region.exists())

    # ------------------------------------------------------------------
    # The line between authoring and publishing
    # ------------------------------------------------------------------
    def test_an_author_may_check_their_own_work(self):
        """Running the validator is not the same as moving the project on.

        The report is a report: an author needs it to see whether their mesh
        names match the model they just uploaded, and producing one changes no
        publication state.
        """
        author = self._user('group_visual_author')

        report = self.env['realestate.visual.validation'].with_user(
            author)._validate_project(self.project)

        self.assertTrue(report.exists())
        self.assertEqual(self.project.visual_publication_state, 'draft',
                         "Checking a gallery must not advance it.")

    def test_an_author_may_not_advance_the_project_to_ready(self):
        """`action_visual_validate` moves DRAFT → READY, and that is a step
        along the road to a customer seeing it — so it is the Publisher's."""
        author = self._user('group_visual_author')

        with self.assertRaises(UserError):
            self.project.with_user(author).action_visual_validate()

        self.assertEqual(self.project.visual_publication_state, 'draft')

    def test_an_author_may_not_publish(self):
        """The line that matters. Drawing a mapping and putting it in front
        of a customer are different rights."""
        author = self._user('group_visual_author')
        self.project.action_visual_validate()

        with self.assertRaises(UserError):
            self.project.with_user(author).action_visual_publish()

        self.assertNotEqual(self.project.visual_publication_state, 'published')

    def test_a_publisher_may_publish(self):
        publisher = self._user('group_visual_publisher')
        self.project.action_visual_validate()

        self.project.with_user(publisher).action_visual_publish()

        self.assertEqual(self.project.visual_publication_state, 'published')

    def test_a_viewer_may_not_publish(self):
        viewer = self._user('group_visual_viewer')
        self.project.action_visual_validate()

        with self.assertRaises(UserError):
            self.project.with_user(viewer).action_visual_publish()

    # ------------------------------------------------------------------
    # Grants — the capability that lets bytes out of the building
    # ------------------------------------------------------------------
    def test_a_publisher_may_read_grants_but_not_revoke_one(self):
        publisher = self._user('group_visual_publisher')
        self.project.action_visual_validate()
        self.project.visual_public_enabled = True
        self.project.action_visual_publish()
        grant = self.env['realestate.visual.access'].grant_for_public_project(
            self.project)

        grant.with_user(publisher).read(['token'])

        with self.assertRaises(AccessError):
            grant.with_user(publisher).write({'active': False})

    def test_a_manager_may_revoke_a_grant(self):
        manager = self._user('group_visual_manager')
        self.project.action_visual_validate()
        self.project.visual_public_enabled = True
        self.project.action_visual_publish()
        grant = self.env['realestate.visual.access'].grant_for_public_project(
            self.project)

        grant.with_user(manager).action_revoke()

        self.assertFalse(grant.is_valid)

    def test_an_author_cannot_see_a_grant_at_all(self):
        """A capability that serves bytes is not authoring data."""
        author = self._user('group_visual_author')
        self.project.action_visual_validate()
        self.project.visual_public_enabled = True
        self.project.action_visual_publish()
        grant = self.env['realestate.visual.access'].grant_for_public_project(
            self.project)

        with self.assertRaises(AccessError):
            grant.with_user(author).read(['token'])

    # ------------------------------------------------------------------
    # Roles are cumulative, and that is deliberate
    # ------------------------------------------------------------------
    def test_each_role_contains_the_one_below_it(self):
        viewer = self.env.ref('real_estate_maquette.group_visual_viewer')
        author = self.env.ref('real_estate_maquette.group_visual_author')
        publisher = self.env.ref('real_estate_maquette.group_visual_publisher')
        manager = self.env.ref('real_estate_maquette.group_visual_manager')

        self.assertIn(viewer, author.implied_ids)
        self.assertIn(author, publisher.implied_ids)
        self.assertIn(publisher, manager.implied_ids)
