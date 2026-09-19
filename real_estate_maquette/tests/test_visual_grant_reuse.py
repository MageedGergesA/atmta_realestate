# -*- coding: utf-8 -*-
"""A public project's pages reuse a live grant instead of minting one per URL.

``grant_for_public_project`` always created a new ``realestate.visual.grant``.
The public API calls it for every gated image and model URL it serialises, so
one anonymous catalogue request for a project with a few hundred units wrote
hundreds of rows, and anyone could repeat the request. A grant that is still
valid for at least half its lifetime, for the same project, kinds, source and
origin, is now returned instead. Revoked, deactivated and nearly expired grants
are never reused.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import tagged

from .test_visual_access import VisualAccessCommon


@tagged('post_install', '-at_install')
class TestVisualGrantReuse(VisualAccessCommon):

    def _grants(self, project):
        return self.env['realestate.visual.grant'].sudo().with_context(
            active_test=False).search([('project_id', '=', project.id)])

    def test_repeated_calls_reuse_one_grant(self):
        project, _unit = self._published_public_project()
        first = self._grant_for(project)
        for _i in range(20):
            self.assertEqual(self._grant_for(project), first)
        self.assertEqual(len(self._grants(project)), 1)

    def test_a_revoked_grant_is_not_reused(self):
        project, _unit = self._published_public_project()
        first = self._grant_for(project)
        first.action_revoke()
        second = self._grant_for(project)
        self.assertNotEqual(second, first)
        self.assertTrue(second.is_valid)

    def test_a_grant_close_to_expiry_is_not_reused(self):
        project, _unit = self._published_public_project()
        first = self._grant_for(project)
        first.sudo().expires_at = fields.Datetime.now() + timedelta(minutes=30)
        second = self._grant_for(project)
        self.assertNotEqual(second, first)
        self.assertGreater(second.expires_at, fields.Datetime.now() + timedelta(hours=4))

    def test_other_kinds_or_origins_get_their_own_grant(self):
        project, _unit = self._published_public_project()
        full = self._grant_for(project)
        plans = self._grant_for(project, kinds=['plan_image'])
        embedded = self._grant_for(project, origin='https://partner.example')
        self.assertEqual(len(full | plans | embedded), 3)
        self.assertEqual(self._grant_for(project, kinds=['plan_image']), plans)
        self.assertEqual(self._grant_for(project, origin='https://partner.example'), embedded)
