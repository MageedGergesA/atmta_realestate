# -*- coding: utf-8 -*-
"""M6 / M7 — modes, broker authorisation and deep links.

The rule these all share: **an identifier is not an authorisation, and a mode
is not a security boundary.** Hiding Odoo's chrome, or knowing a record id, or
being handed a well-formed URL, changes what is *assembled* — never what may be
*read*.
"""

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import VisualCommon


@tagged('post_install', '-at_install')
class TestPresentationMode(VisualCommon):
    """An internal showroom, with the same server permissions as the backend."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Modes = cls.env['realestate.visual.modes']

    def _lead(self, name='Showroom Visitor'):
        self._skip_without_models('crm.lead')
        partner = self.env['res.partner'].create({'name': name})
        return self.env['crm.lead'].create({
            'name': '%s — opportunity' % name, 'type': 'opportunity',
            'partner_id': partner.id})

    def test_a_project_opens_without_any_opportunity(self):
        """A visitor before anybody has taken a name."""
        project = self._project()

        ctx = self.Modes.presentation_context(project.id)

        self.assertEqual(ctx['mode'], 'presentation')
        self.assertEqual(ctx['project_id'], project.id)
        self.assertFalse(ctx['crm_lead_id'])
        self.assertEqual(ctx['shortlist'], [])

    def test_an_opportunity_brings_its_shortlist(self):
        project = self._project()
        unit = self._unit(project)
        lead = self._lead()
        self.env['realestate.visual.gallery'].shortlist_add(lead.id, unit.id)

        ctx = self.Modes.presentation_context(project.id, lead.id)

        self.assertEqual(ctx['crm_lead_id'], lead.id)
        self.assertEqual(len(ctx['shortlist']), 1)
        self.assertEqual(ctx['shortlist'][0]['id'], unit.id)

    def test_a_user_without_brokerage_access_is_told_so_not_thrown_at(self):
        """The defect the browser gate caught, in one assertion.

        A showroom user who may read projects and properties but holds no
        Brokerage sales role used to reach `shortlist_for()`, fail
        `realestate.property.match`'s model ACL, and get a raw red Access Error
        dialog on the screen a customer was looking at. The feature is now
        reported as unavailable and the shortlist comes back empty, so the
        client hides it instead of advertising something that then explodes.
        """
        project = self._project()
        unit = self._unit(project)
        showroom = self._showroom_user_without_brokerage()
        # Assigned to the showroom user, so the only thing this test can fail
        # on is the shortlist. An opportunity belonging to somebody else is a
        # different rule, asserted separately.
        lead = self._lead()
        lead.user_id = showroom
        self.env['realestate.visual.gallery'].shortlist_add(lead.id, unit.id)

        Gallery = self.env['realestate.visual.gallery'].with_user(showroom)

        self.assertFalse(
            Gallery.shortlist_available(),
            "A user who cannot read the match model must be told the "
            "shortlist is unavailable.")
        self.assertEqual(
            Gallery.shortlist_for(lead.id), [],
            "An unreadable shortlist must be empty, never an exception.")

        ctx = self.env['realestate.visual.modes'].with_user(
            showroom).presentation_context(project.id, lead.id)
        self.assertFalse(ctx['shortlist_available'])
        self.assertEqual(ctx['shortlist'], [])
        self.assertEqual(ctx['project_id'], project.id,
                         "The gallery itself must still open.")

    def _showroom_user_without_brokerage(self):
        """Reads projects and properties; holds no Brokerage sales role."""
        user = self.env['res.users'].create({
            'name': 'Showroom Without Brokerage',
            'login': 'showroom_no_brokerage',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('atmta_real_estate.group_realestate_user').id,
                self.env.ref('real_estate_developer.group_dev_manager').id,
                self.env.ref('sales_team.group_sale_salesman').id,
            ])],
        })
        Match = self.env.get('realestate.property.match')
        if Match is not None:
            self.assertFalse(
                Match.with_user(user).check_access_rights(
                    'read', raise_exception=False),
                "This test is only meaningful while the user genuinely "
                "cannot read the shortlist model.")
        return user

    def test_presentation_is_an_internal_audience(self):
        """Customer-facing layout, internal data. The two are separate."""
        project = self._project()

        self.assertEqual(self.Modes.audience_for('presentation'), 'internal')
        self.assertEqual(
            self.Modes.presentation_context(project.id)['audience'],
            'internal')

    def test_hiding_the_chrome_does_not_relax_record_rules(self):
        """The check runs as the real user, exactly as the backend does."""
        project = self._project()
        other_company = self.env['res.company'].create({'name': 'Rival Show'})
        stranger = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Rival Employee',
                'login': 'rival.show@test.example',
                'company_id': other_company.id,
                'company_ids': [(6, 0, [other_company.id])],
                'groups_id': [(6, 0, [self.env.ref('base.group_user').id])]})

        with self.assertRaises(Exception):
            self.Modes.with_user(stranger).presentation_context(project.id)

    def test_a_portal_user_cannot_open_presentation_mode(self):
        project = self._project()
        portal_user = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Portal Visitor',
                'login': 'portal.show@test.example',
                'groups_id': [(6, 0, [self.env.ref('base.group_portal').id])]})

        with self.assertRaises(AccessError):
            self.Modes.with_user(portal_user).presentation_context(project.id)

    def test_somebody_elses_opportunity_cannot_be_pulled_up_by_id(self):
        """The lead is read as the real user, not with sudo."""
        project = self._project()
        lead = self._lead()
        agent = self._visual_user('real_estate_maquette.group_visual_viewer')

        # The agent may not read this lead; presentation mode must refuse
        # rather than hand it over because an id was supplied.
        with self.assertRaises(Exception):
            self.Modes.with_user(agent).presentation_context(
                project.id, lead.id)

    def test_the_fallback_descriptor_travels_with_the_context(self):
        """Rule 4 applies in the showroom exactly as in the backend."""
        project = self._project()

        ctx = self.Modes.presentation_context(project.id)

        self.assertIn('fallback', ctx)
        self.assertIn('list_available', ctx['fallback'])


@tagged('post_install', '-at_install')
class TestBrokerMode(VisualCommon):
    """Brokerage answers every question. This module asks."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Modes = cls.env['realestate.visual.modes']

    def _broker(self, name='Gallery Broker', **kwargs):
        self._skip_without_module('real_estate_brokerage')
        vals = {
            'name': name,
            'is_realestate_broker': True,
            'broker_type': 'agency',
            'broker_state': 'active',
            'broker_kyc_state': 'verified',
            'broker_license_expiry': fields.Date.add(
                fields.Date.today(), days=365),
        }
        vals.update(kwargs)
        return self.env['res.partner'].create(vals)

    def _agreement(self, broker, projects=None, activate=True, **kwargs):
        vals = {
            'broker_partner_id': broker.id,
            'company_id': self.company.id,
            'protection_days': 90,
        }
        if projects is not None:
            vals['allowed_project_ids'] = [(6, 0, projects.ids)]
        vals.update(kwargs)
        agreement = self.env['realestate.broker.agreement'].create(vals)
        if activate:
            agreement.action_activate()
        return agreement

    def test_an_authorised_broker_sees_the_project(self):
        project = self._project()
        self._unit(project)
        broker = self._broker()
        self._agreement(broker)

        ctx = self.Modes.broker_context(broker.id, project.id)

        self.assertTrue(ctx['allowed'])
        self.assertEqual(ctx['audience'], 'broker')
        self.assertTrue(ctx['units'])

    def test_a_project_outside_the_agreement_is_refused(self):
        """Brokerage's `_covers_project`, not a rule re-implemented here."""
        allowed_project = self._project()
        other_project = self._project()
        broker = self._broker()
        self._agreement(broker, projects=allowed_project)

        ctx = self.Modes.broker_context(broker.id, other_project.id)

        self.assertFalse(ctx['allowed'])
        self.assertEqual(ctx['units'], [])

    def test_a_suspended_broker_is_refused_with_brokerages_own_words(self):
        project = self._project()
        broker = self._broker()
        self._agreement(broker)
        broker.action_suspend_broker()

        ctx = self.Modes.broker_context(broker.id, project.id)

        self.assertFalse(ctx['allowed'])
        self.assertIn('suspend', ctx['reason'].lower())

    def test_an_expired_licence_is_refused(self):
        project = self._project()
        broker = self._broker()
        self._agreement(broker)
        broker.broker_license_expiry = fields.Date.subtract(
            fields.Date.today(), days=1)

        ctx = self.Modes.broker_context(broker.id, project.id)

        self.assertFalse(ctx['allowed'])
        self.assertIn('licence', ctx['reason'].lower())

    def test_a_broker_with_no_agreement_is_refused(self):
        project = self._project()
        broker = self._broker()

        ctx = self.Modes.broker_context(broker.id, project.id)

        self.assertFalse(ctx['allowed'])

    def test_a_broker_never_sees_the_internal_price(self):
        """Broker pricing is Developer's public price, like the embed's."""
        project = self._project()
        unit = self._unit(project, price=1000000.0)
        broker = self._broker()
        self._agreement(broker)

        ctx = self.Modes.broker_context(broker.id, project.id)
        entry = next(u for u in ctx['units'] if u['id'] == unit.id)

        self.assertEqual(entry['price'], unit._public_price())
        self.assertNotIn('unavailable_reason', entry)
        self.assertNotIn('commercial_status', entry)

    def test_a_broker_only_sees_released_inventory(self):
        project = self._project()
        released = self._unit(project)
        held_back = self._unit(project, released=False)
        broker = self._broker()
        self._agreement(broker)

        ctx = self.Modes.broker_context(broker.id, project.id)
        ids = [u['id'] for u in ctx['units'] if u['matches_filter']]

        self.assertIn(released.id, ids)
        self.assertNotIn(held_back.id, ids)

    def test_reserving_defers_to_brokerages_registration_workflow(self):
        """No second 'visual broker customer' model."""
        project = self._project()
        broker = self._broker()
        self._agreement(broker)

        allowed, message = self.Modes.broker_may_reserve(broker, project)

        self.assertTrue(allowed)
        self.assertIn('registration', message.lower())

    def test_an_unauthorised_broker_cannot_reserve(self):
        allowed_project = self._project()
        other_project = self._project()
        broker = self._broker()
        self._agreement(broker, projects=allowed_project)

        allowed, _message = self.Modes.broker_may_reserve(
            broker, other_project)

        self.assertFalse(allowed)

    def test_this_module_defines_no_broker_models_of_its_own(self):
        """Rule: consume Brokerage's authorisation, never rebuild it."""
        import os
        models_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'models')
        offenders = []
        for name in sorted(os.listdir(models_dir)):
            if not name.endswith('.py'):
                continue
            with open(os.path.join(models_dir, name)) as handle:
                for line in handle:
                    stripped = line.strip()
                    if stripped.startswith('_name =') and 'broker' in stripped:
                        offenders.append('%s: %s' % (name, stripped))
        self.assertFalse(
            offenders,
            "The visual layer must not define broker models: %s" % offenders)


@tagged('post_install', '-at_install')
class TestDeepLinks(VisualCommon):
    """A link says which unit. It never says what that unit costs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Link = cls.env['realestate.visual.deeplink']

    def _published_public_project(self):
        project = self._project(visual_public_enabled=True)
        unit = self._unit(project, mesh='U1')
        self._attach_glb(project, ['U1'])
        self._attach_master_plan(project)
        project.action_visual_validate()
        project.action_visual_publish()
        return project, unit

    def test_an_internal_link_resolves_the_whole_ancestry(self):
        project = self._project()
        building = self._building(project)
        unit = self._unit(project, parent_id=building.id, floor_number=7)

        path = self.Link.resolve('unit', unit.id)

        self.assertEqual(path['project_id'], project.id)
        self.assertEqual(path['building_id'], building.id)
        self.assertEqual(path['floor_number'], 7)
        self.assertEqual(path['unit_id'], unit.id)

    def test_no_commercial_data_travels_in_the_path(self):
        """A link accurate on Tuesday shows Thursday's truth on Thursday."""
        project = self._project()
        unit = self._unit(project, price=1000000.0)

        path = self.Link.resolve('unit', unit.id)

        for forbidden in ('price', 'visual_state', 'available', 'status'):
            self.assertNotIn(forbidden, path)

    def test_the_project_is_walked_upwards_not_taken_from_the_caller(self):
        """A link cannot claim a unit belongs to a project it does not."""
        project = self._project()
        unit = self._unit(project)

        path = self.Link.resolve('unit', unit.id)

        self.assertEqual(path['project_id'], project.id)

    def test_an_unknown_record_is_not_found(self):
        with self.assertRaises(AccessError):
            self.Link.resolve('unit', 999999999)

    def test_an_unknown_target_is_not_found(self):
        project = self._project()
        with self.assertRaises(AccessError):
            self.Link.resolve('secret', project.id)

    def test_a_public_link_needs_a_grant(self):
        project, unit = self._published_public_project()

        with self.assertRaises(AccessError):
            self.Link.with_user(
                self.env.ref('base.public_user')).resolve('unit', unit.id)

    def test_a_valid_grant_resolves_a_public_link(self):
        project, unit = self._published_public_project()
        grant = self.env['realestate.visual.access'].grant_for_public_project(
            project)

        path = self.Link.resolve('unit', unit.id, grant_token=grant.token)

        self.assertEqual(path['unit_id'], unit.id)

    def test_a_grant_for_another_project_does_not_resolve(self):
        project_a, _unit_a = self._published_public_project()
        _project_b, unit_b = self._published_public_project()
        grant = self.env['realestate.visual.access'].grant_for_public_project(
            project_a)

        with self.assertRaises(AccessError):
            self.Link.resolve('unit', unit_b.id, grant_token=grant.token)

    def test_an_expired_grant_does_not_resolve(self):
        project, unit = self._published_public_project()
        grant = self.env['realestate.visual.access'].grant_for_public_project(
            project)
        grant.sudo().expires_at = fields.Datetime.subtract(
            fields.Datetime.now(), hours=1)

        with self.assertRaises(AccessError):
            self.Link.resolve('unit', unit.id, grant_token=grant.token)

    def test_unpublishing_kills_existing_public_links(self):
        project, unit = self._published_public_project()
        grant = self.env['realestate.visual.access'].grant_for_public_project(
            project)
        project.action_visual_unpublish()

        with self.assertRaises(AccessError):
            self.Link.resolve('unit', unit.id, grant_token=grant.token)

    def test_share_links_offer_no_public_url_for_a_private_project(self):
        """Returning one that 404s would look like a bug, not a decision."""
        project = self._project()
        unit = self._unit(project)

        links = self.Link.share_links(unit, audience='public')

        self.assertFalse(links['public'])
        self.assertIn('not published', links['public_reason'].lower())

    def test_share_links_mint_a_scoped_grant_for_a_published_project(self):
        project, unit = self._published_public_project()

        links = self.Link.share_links(unit, audience='public')

        self.assertTrue(links['public'])
        self.assertIn('/visual/p/', links['public'])
        self.assertTrue(links['expires_at'])

    def test_the_token_is_in_the_path_not_the_query(self):
        """It survives a copy-paste that strips query parameters."""
        project, unit = self._published_public_project()

        links = self.Link.share_links(unit, audience='public')

        self.assertNotIn('?', links['public'])

    def test_share_links_produce_urls_and_nothing_else(self):
        """No outbound messaging grew here."""
        project, unit = self._published_public_project()

        links = self.Link.share_links(unit, audience='public')

        self.assertEqual(
            set(links) - {'internal', 'public', 'public_reason',
                          'expires_at'}, set())
