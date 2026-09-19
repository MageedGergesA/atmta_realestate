# -*- coding: utf-8 -*-
"""M6 — Public Mode.

Two rules meet here, and both are absolute.

**Nothing internal reaches an anonymous visitor.** Not the internal price
basis, not why a unit is blocked, not who is holding it, not what the minimum
acceptable price or the discount authority is. These are not filtered out at
the end of the assembly — they are never read, because the public context is
built from the public audience and nothing else.

**Browsing creates no CRM record.** A visitor who taps a heart on eleven
apartments is holding a list in their own browser. It becomes a real shortlist
only when they identify themselves by an explicit act, and there is exactly one
route from that act into the database.
"""

import base64

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import HttpCase

from .common import VisualCommon


class PublicModeCommon(VisualCommon):

    def setUp(self):
        super().setUp()
        self.Modes = self.env['realestate.visual.modes']
        self.Access = self.env['realestate.visual.access']

    def _public_project(self, mesh_names=('U1',)):
        project = self._project(visual_public_enabled=True)
        unit = self._unit(project, mesh=mesh_names[0])
        self._attach_glb(project, list(mesh_names))
        self._attach_master_plan(project)
        unit.floor_plan_image = base64.b64encode(b'plan-bytes')
        project.action_visual_validate()
        project.action_visual_publish()
        return project, unit

    def _grant(self, project, **kw):
        return self.Access.grant_for_public_project(project, **kw)


@tagged('post_install', '-at_install')
class TestPublicContextAuthorisation(PublicModeCommon):
    """The grant decides. Never the id, never the Public user's reach."""

    def test_a_valid_grant_opens_a_published_project(self):
        project, unit = self._public_project()
        grant = self._grant(project)

        ctx = self.Modes.public_context(project.id, grant_token=grant.token)

        self.assertEqual(ctx['mode'], 'public')
        self.assertEqual(ctx['project_id'], project.id)
        self.assertEqual(ctx['audience'], 'public')
        self.assertTrue(ctx['units'])

    def test_no_grant_is_not_an_authorisation(self):
        project, _unit = self._public_project()

        with self.assertRaises(AccessError):
            self.Modes.public_context(project.id)

    def test_a_grant_for_another_project_opens_nothing(self):
        project, _unit = self._public_project()
        other, _other_unit = self._public_project(mesh_names=('X1',))
        grant = self._grant(other)

        with self.assertRaises(AccessError):
            self.Modes.public_context(project.id, grant_token=grant.token)

    def test_an_expired_grant_stops_working(self):
        project, _unit = self._public_project()
        grant = self._grant(project)
        grant.sudo().expires_at = '2020-01-01 00:00:00'

        with self.assertRaises(AccessError):
            self.Modes.public_context(project.id, grant_token=grant.token)

    def test_a_revoked_grant_stops_working(self):
        project, _unit = self._public_project()
        grant = self._grant(project)
        grant.sudo().action_revoke()

        with self.assertRaises(AccessError):
            self.Modes.public_context(project.id, grant_token=grant.token)

    def test_unpublishing_closes_the_door_on_existing_grants(self):
        """A grant cannot outlive the decision to publish."""
        project, _unit = self._public_project()
        grant = self._grant(project)
        self.Modes.public_context(project.id, grant_token=grant.token)

        project.visual_public_enabled = False

        with self.assertRaises(AccessError):
            self.Modes.public_context(project.id, grant_token=grant.token)

    def test_a_project_that_does_not_exist_answers_the_same_way(self):
        """No existence oracle: a missing project and a forbidden one are
        indistinguishable from outside."""
        project, _unit = self._public_project()
        grant = self._grant(project)

        with self.assertRaises(AccessError):
            self.Modes.public_context(0, grant_token=grant.token)


@tagged('post_install', '-at_install')
class TestPublicContextLeaks(PublicModeCommon):
    """Everything the brief says a visitor must never see."""

    #: Field names that must not appear anywhere in a public payload.
    FORBIDDEN = (
        'list_price_developer', 'base_price', 'internal_price',
        'minimum_price', 'min_acceptable_price', 'discount_limit',
        'max_discount', 'approval', 'approved_by',
        'unavailable_reason', 'sale_unavailable_reason', 'commercial_status',
        'is_released', 'commission', 'partner_id', 'customer',
        'crm_lead_id', 'reserved_by', 'held_for',
    )

    def _block(self, unit, notes='VIP hold for Mr X, per the chairman'):
        """A real commercial block, with real internal notes on it."""
        return self.env['realestate.unit.block'].create({
            'property_id': unit.id,
            'reason': 'vip',
            'notes': notes,
        })

    def test_a_public_unit_carries_no_internal_key(self):
        project, unit = self._public_project()
        self._block(unit)
        grant = self._grant(project)

        ctx = self.Modes.public_context(project.id, grant_token=grant.token)

        for entry in ctx['units']:
            for key in self.FORBIDDEN:
                self.assertNotIn(
                    key, entry,
                    "Public payload carried %r, which is internal." % key)

    def test_the_reason_a_unit_is_blocked_never_travels(self):
        """"Blocked — VIP hold for Mr X" is the sentence M20 forbids."""
        project, unit = self._public_project()
        self._block(unit)
        grant = self._grant(project)

        ctx = self.Modes.public_context(project.id, grant_token=grant.token)

        rendered = str(ctx)
        self.assertNotIn('Mr X', rendered,
                         "A blocking note reached a public visitor.")
        self.assertNotIn('chairman', rendered)
        self.assertNotIn('vip', rendered.lower(),
                         "The *kind* of hold is internal too.")

    def test_public_mode_offers_no_reservation_and_no_crm_shortlist(self):
        project, _unit = self._public_project()
        grant = self._grant(project)

        ctx = self.Modes.public_context(project.id, grant_token=grant.token)

        self.assertFalse(ctx['can_reserve'])
        self.assertFalse(ctx['shortlist_available'])
        self.assertEqual(ctx['shortlist'], [])
        self.assertTrue(ctx['session_shortlist_only'])


@tagged('post_install', '-at_install')
class TestAnonymousSessionCreatesNothing(PublicModeCommon):
    """Browsing is not a commercial event."""

    def setUp(self):
        super().setUp()
        self._skip_without_models('crm.lead')

    def _lead(self):
        partner = self.env['res.partner'].create({'name': 'Enquirer'})
        return self.env['crm.lead'].create({
            'name': 'Enquiry', 'type': 'opportunity', 'partner_id': partner.id})

    def test_opening_the_gallery_writes_no_lead_and_no_contact(self):
        project, _unit = self._public_project()
        grant = self._grant(project)
        leads_before = self.env['crm.lead'].search_count([])
        partners_before = self.env['res.partner'].search_count([])

        self.Modes.public_context(project.id, grant_token=grant.token)

        self.assertEqual(self.env['crm.lead'].search_count([]), leads_before)
        self.assertEqual(
            self.env['res.partner'].search_count([]), partners_before)

    def test_a_shortlist_without_an_opportunity_is_refused_not_invented(self):
        project, unit = self._public_project()
        leads_before = self.env['crm.lead'].search_count([])

        with self.assertRaises(UserError):
            self.Modes.convert_public_session(False, [unit.id])

        self.assertEqual(self.env['crm.lead'].search_count([]), leads_before,
                         "A browsing session created a CRM record.")

    def test_conversion_writes_the_canonical_shortlist_rows(self):
        project, unit = self._public_project()
        lead = self._lead()

        self.Modes.convert_public_session(lead.id, [unit.id])

        Match = self.env['realestate.property.match']
        rows = Match.search([('crm_lead_id', '=', lead.id),
                             ('property_id', '=', unit.id)])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows.shortlisted)

    def test_converting_twice_does_not_duplicate(self):
        """A visitor who submits the form twice has one shortlist, not two."""
        project, unit = self._public_project()
        lead = self._lead()

        self.Modes.convert_public_session(lead.id, [unit.id])
        self.Modes.convert_public_session(lead.id, [unit.id])

        rows = self.env['realestate.property.match'].search([
            ('crm_lead_id', '=', lead.id), ('property_id', '=', unit.id)])
        self.assertEqual(len(rows), 1)


@tagged('post_install', '-at_install')
class TestPublicRoutes(PublicModeCommon, HttpCase):
    """The same rules, over HTTP, as an anonymous visitor."""

    def test_the_context_route_needs_a_grant(self):
        project, _unit = self._public_project()

        # No token at all.
        blind = self.opener.post(
            self.base_url() + '/visual/public/context',
            json={'params': {'project_id': project.id}}).json()
        self.assertEqual(blind['result'], {'error': 'not_found'})

        # A well-formed token that authorises nothing.
        forged = self.opener.post(
            self.base_url() + '/visual/public/context',
            json={'params': {'project_id': project.id,
                             't': 'a' * 32}}).json()
        self.assertEqual(forged['result'], {'error': 'not_found'})

    def test_the_context_route_answers_a_real_grant(self):
        project, _unit = self._public_project()
        grant = self._grant(project)

        result = self.opener.post(
            self.base_url() + '/visual/public/context',
            json={'params': {'project_id': project.id,
                             't': grant.token}}).json()['result']

        self.assertEqual(result['project_id'], project.id)
        self.assertEqual(result['audience'], 'public')

    def test_the_convert_route_will_not_invent_an_opportunity(self):
        self._skip_without_models('crm.lead')
        project, unit = self._public_project()
        leads_before = self.env['crm.lead'].search_count([])

        result = self.opener.post(
            self.base_url() + '/visual/public/convert',
            json={'params': {'crm_lead_id': False,
                             'property_ids': [unit.id]}}).json()['result']

        self.assertEqual(result, {'error': 'not_found'})
        self.assertEqual(self.env['crm.lead'].search_count([]), leads_before)
