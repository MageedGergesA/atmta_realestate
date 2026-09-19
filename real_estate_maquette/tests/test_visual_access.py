# -*- coding: utf-8 -*-
"""M4.5-A — every visual asset request is authorised, or it is a 404.

The defect: a project's entire 3D model, its floor plans and its interior
models were fetchable by anybody who could count to the next integer. Two
separate route families did it — `/api/v1/image/...` and the portal's
`/projects/<id>/glb` — and fixing only one would have left the other as a
bypass.

Every test here goes through a real HTTP request. Asserting that a Python
method raises proves the method; it does not prove that no URL reaches the
bytes another way, and "no alternative route bypasses the protected
controller" is the actual requirement.
"""

import base64

from odoo.exceptions import AccessError
from odoo.tests.common import HttpCase, tagged

from .common import VisualCommon, build_glb


class VisualAccessCommon(VisualCommon, HttpCase):
    """Fixtures: a published public project, and a private one."""

    def setUp(self):
        super().setUp()
        self.Access = self.env['realestate.visual.access']
        self.Grant = self.env['realestate.visual.grant']

    def _published_public_project(self, mesh_names=('U1',)):
        project = self._project(visual_public_enabled=True)
        unit = self._unit(project, mesh=mesh_names[0])
        self._attach_glb(project, list(mesh_names))
        self._attach_master_plan(project)
        unit.floor_plan_image = base64.b64encode(b'plan-bytes')
        project.action_visual_validate()
        project.action_visual_publish()
        return project, unit

    def _private_project(self):
        """Has assets, is not published publicly."""
        project = self._project(visual_public_enabled=False)
        unit = self._unit(project, mesh='P1')
        self._attach_glb(project, ['P1'])
        unit.floor_plan_image = base64.b64encode(b'private-plan')
        return project, unit

    def _grant_for(self, project, kinds=None, origin=None):
        return self.Access.grant_for_public_project(
            project, kinds=kinds, origin=origin)

    def _get(self, url):
        return self.url_open(url)


@tagged('post_install', '-at_install')
class TestAssetEnumeration(VisualAccessCommon):
    """The hole, closed. These are the requests that used to succeed."""

    def test_an_anonymous_request_with_no_token_gets_nothing(self):
        project, _unit = self._published_public_project()

        response = self._get('/visual/asset/maquette_glb/%s' % project.id)

        self.assertEqual(response.status_code, 404)

    def test_sequential_enumeration_finds_nothing(self):
        """Walking the id space is what the old route made cheap."""
        project, _unit = self._published_public_project()

        for candidate in range(max(1, project.id - 3), project.id + 3):
            with self.subTest(id=candidate):
                response = self._get(
                    '/visual/asset/maquette_glb/%s' % candidate)
                self.assertEqual(response.status_code, 404)

    def test_the_old_api_url_no_longer_serves_the_model(self):
        """`/api/v1/image/realestate.project/<id>/maquette_glb`."""
        project, _unit = self._published_public_project()

        response = self._get(
            '/api/v1/image/realestate.project/%s/maquette_glb' % project.id)

        self.assertEqual(response.status_code, 404)

    def test_the_old_portal_url_no_longer_serves_the_model(self):
        """`/projects/<id>/glb` — the family the first audit missed."""
        project, _unit = self._published_public_project()

        response = self._get('/projects/%s/glb' % project.id)

        self.assertEqual(response.status_code, 404)

    def test_the_portal_floor_plan_url_is_gated_too(self):
        project, unit = self._published_public_project()

        response = self._get(
            '/projects/portal/property/%s/floor_plan' % unit.id)

        self.assertEqual(response.status_code, 404)

    def test_a_missing_record_and_a_forbidden_one_look_identical(self):
        """Otherwise the route is an existence oracle."""
        project, _unit = self._published_public_project()

        real = self._get('/visual/asset/maquette_glb/%s' % project.id)
        fake = self._get('/visual/asset/maquette_glb/99999999')

        self.assertEqual(real.status_code, fake.status_code)
        self.assertEqual(real.status_code, 404)


@tagged('post_install', '-at_install')
class TestGrantAuthorization(VisualAccessCommon):
    """A grant authorises exactly what it says and nothing adjacent."""

    def test_a_valid_grant_serves_the_asset(self):
        project, _unit = self._published_public_project()
        grant = self._grant_for(project)

        response = self._get('/visual/asset/maquette_glb/%s?t=%s'
                             % (project.id, grant.token))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'],
                         'model/gltf-binary')
        self.assertTrue(response.content.startswith(b'glTF'))

    def test_an_expired_grant_does_not(self):
        from odoo import fields
        project, _unit = self._published_public_project()
        grant = self._grant_for(project)
        grant.sudo().write({
            'expires_at': fields.Datetime.subtract(
                fields.Datetime.now(), hours=1)})

        response = self._get('/visual/asset/maquette_glb/%s?t=%s'
                             % (project.id, grant.token))

        self.assertEqual(response.status_code, 404)

    def test_a_revoked_grant_does_not(self):
        project, _unit = self._published_public_project()
        grant = self._grant_for(project)
        grant.action_revoke()

        response = self._get('/visual/asset/maquette_glb/%s?t=%s'
                             % (project.id, grant.token))

        self.assertEqual(response.status_code, 404)

    def test_a_malformed_token_does_not(self):
        project, _unit = self._published_public_project()

        for token in ('', 'not-a-token', 'x' * 200, '../../etc/passwd',
                      "' OR 1=1--"):
            with self.subTest(token=token):
                response = self._get('/visual/asset/maquette_glb/%s?t=%s'
                                     % (project.id, token))
                self.assertEqual(response.status_code, 404)

    def test_a_grant_for_project_a_cannot_fetch_project_b(self):
        """The single most important property of a scoped capability."""
        project_a, _ua = self._published_public_project(('A1',))
        project_b, _ub = self._published_public_project(('B1',))
        grant = self._grant_for(project_a)

        response = self._get('/visual/asset/maquette_glb/%s?t=%s'
                             % (project_b.id, grant.token))

        self.assertEqual(response.status_code, 404)

    def test_a_grant_covers_only_the_kinds_it_names(self):
        project, unit = self._published_public_project()
        grant = self._grant_for(project, kinds=['master_plan_2d'])

        allowed = self._get('/visual/asset/master_plan_2d/%s?t=%s'
                            % (project.id, grant.token))
        refused = self._get('/visual/asset/maquette_glb/%s?t=%s'
                            % (project.id, grant.token))

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(refused.status_code, 404)

    def test_an_unknown_kind_is_refused(self):
        project, _unit = self._published_public_project()
        grant = self._grant_for(project)

        response = self._get('/visual/asset/secret_field/%s?t=%s'
                             % (project.id, grant.token))

        self.assertEqual(response.status_code, 404)

    def test_unpublishing_stops_existing_grants_working(self):
        """A capability cannot outlive the decision that created it."""
        project, _unit = self._published_public_project()
        grant = self._grant_for(project)
        self.assertEqual(
            self._get('/visual/asset/maquette_glb/%s?t=%s'
                      % (project.id, grant.token)).status_code, 200)

        project.action_visual_unpublish()

        self.assertEqual(
            self._get('/visual/asset/maquette_glb/%s?t=%s'
                      % (project.id, grant.token)).status_code, 404)

    def test_a_grant_cannot_be_minted_for_a_private_project(self):
        project, _unit = self._private_project()

        with self.assertRaises(AccessError):
            self._grant_for(project)

    def test_use_is_recorded(self):
        project, _unit = self._published_public_project()
        grant = self._grant_for(project)

        self._get('/visual/asset/maquette_glb/%s?t=%s'
                  % (project.id, grant.token))

        self.assertTrue(grant.used_count)
        self.assertTrue(grant.last_used_at)


@tagged('post_install', '-at_install')
class TestOriginRestriction(VisualAccessCommon):
    """Origin is enforced where it is configured, and only there."""

    def test_a_matching_origin_is_allowed(self):
        project, _unit = self._published_public_project()
        grant = self._grant_for(project, origin='https://partner.example')

        response = self.url_open(
            '/visual/asset/maquette_glb/%s?t=%s' % (project.id, grant.token),
            headers={'Origin': 'https://partner.example'})

        self.assertEqual(response.status_code, 200)

    def test_a_mismatched_origin_is_refused(self):
        project, _unit = self._published_public_project()
        grant = self._grant_for(project, origin='https://partner.example')

        response = self.url_open(
            '/visual/asset/maquette_glb/%s?t=%s' % (project.id, grant.token),
            headers={'Origin': 'https://attacker.example'})

        self.assertEqual(response.status_code, 404)

    def test_a_missing_origin_is_refused_when_one_is_required(self):
        project, _unit = self._published_public_project()
        grant = self._grant_for(project, origin='https://partner.example')

        response = self._get('/visual/asset/maquette_glb/%s?t=%s'
                             % (project.id, grant.token))

        self.assertEqual(response.status_code, 404)

    def test_an_unrestricted_grant_accepts_any_origin(self):
        """Correct for a first-party portal page; wrong for a third party."""
        project, _unit = self._published_public_project()
        grant = self._grant_for(project)

        response = self.url_open(
            '/visual/asset/maquette_glb/%s?t=%s' % (project.id, grant.token),
            headers={'Origin': 'https://anywhere.example'})

        self.assertEqual(response.status_code, 200)


@tagged('post_install', '-at_install')
class TestInternalAuthorization(VisualAccessCommon):
    """Internal users are authorised as themselves, before any sudo."""

    def test_an_authorised_internal_user_gets_the_asset(self):
        project, _unit = self._private_project()
        # Reading a project needs a Developer group; the visual roles govern
        # the gallery, not the underlying inventory. Granting both is what an
        # actual internal viewer would hold.
        self.env.ref('base.user_admin').groups_id |= (
            self.env.ref('real_estate_maquette.group_visual_viewer')
            | self.env.ref('real_estate_developer.group_dev_readonly'))
        self.authenticate('admin', 'admin')

        response = self._get('/visual/asset/maquette_glb/%s' % project.id)

        self.assertEqual(response.status_code, 200)

    def test_the_check_runs_as_the_real_user_not_as_sudo(self):
        """Record rules — company isolation included — apply here."""
        project, _unit = self._private_project()
        other_company = self.env['res.company'].create({'name': 'Rival Visual'})
        stranger = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Rival Employee',
                'login': 'rival.visual@test.example',
                'password': 'rival.visual.pw.1',
                'company_id': other_company.id,
                'company_ids': [(6, 0, [other_company.id])],
                'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
            })

        with self.assertRaises(AccessError):
            self.Access.with_user(stranger).authorize(
                'maquette_glb', project.id)

    def test_a_portal_user_is_treated_as_public(self):
        """`_is_internal()` is the test, so a portal login is not a way in."""
        project, _unit = self._private_project()
        portal_user = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Portal Visitor',
                'login': 'portal.visitor@test.example',
                'groups_id': [(6, 0, [self.env.ref('base.group_portal').id])],
            })

        with self.assertRaises(AccessError):
            self.Access.with_user(portal_user).authorize(
                'maquette_glb', project.id)


@tagged('post_install', '-at_install')
class TestAttachmentBypass(VisualAccessCommon):
    """No alternative route reaches the bytes."""

    #: A GLB always starts with the four bytes "glTF". Asserting on the body
    #: rather than the status code is the assertion that actually matters:
    #: Odoo's `/web/image` answers 200 with a placeholder PNG when it cannot
    #: read a record, and `auth='user'` routes answer 200 with a login page
    #: once redirects are followed. A status-code check calls both of those a
    #: leak, and would call a real leak a pass if the route ever answered 206.
    GLB_MAGIC = b'glTF'

    def _assert_no_glb(self, url):
        response = self._get(url)
        self.assertFalse(
            response.content.startswith(self.GLB_MAGIC),
            "%s served a GLB to an anonymous caller" % url)
        return response

    def test_odoos_own_web_image_route_does_not_serve_a_public_glb(self):
        project, _unit = self._published_public_project()

        self._assert_no_glb(
            '/web/image/realestate.project/%s/maquette_glb' % project.id)

    def test_web_content_does_not_serve_a_public_glb(self):
        project, _unit = self._published_public_project()

        self._assert_no_glb(
            '/web/content/realestate.project/%s/maquette_glb' % project.id)

    def test_an_interior_model_is_not_publicly_reachable(self):
        project, unit = self._published_public_project()
        unit.interior_glb = base64.b64encode(build_glb(['ROOM']))

        for url in ('/visual/asset/interior_glb/%s' % unit.id,
                    '/maquette/interior/%s' % unit.id,
                    '/web/content/realestate.property/%s/interior_glb'
                    % unit.id,
                    '/web/image/realestate.property/%s/interior_glb'
                    % unit.id):
            with self.subTest(url=url):
                self._assert_no_glb(url)

    def test_the_gated_route_really_does_serve_the_bytes_with_a_grant(self):
        """The counterpart: proof the checks above are not passing vacuously."""
        project, _unit = self._published_public_project()
        grant = self._grant_for(project)

        response = self._get('/visual/asset/maquette_glb/%s?t=%s'
                             % (project.id, grant.token))

        self.assertTrue(response.content.startswith(self.GLB_MAGIC))


@tagged('post_install', '-at_install')
class TestPublicPayloadLeak(VisualAccessCommon):
    """The regression the audit's own fix introduced for one release."""

    def test_the_portal_units_endpoint_is_public_audience(self):
        # `/projects/...` are real_estate_portal's routes.
        self._skip_without_module('real_estate_portal')
        """It briefly served internal prices because it inherited a default."""
        project, unit = self._published_public_project()

        response = self._get('/projects/%s/units.json' % project.id)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        entry = payload['units'][0] if isinstance(payload, dict) else payload[0]
        self.assertNotIn('unavailable_reason', entry)
        self.assertNotIn('commercial_status', entry)
        self.assertNotIn('is_released', entry)

    def test_the_default_audience_is_the_least_privileged_one(self):
        """A caller that forgets to say who is asking gets the safe answer."""
        project, _unit = self._published_public_project()

        payload = project.get_maquette_units_data()

        self.assertNotIn('unavailable_reason', payload[0])


@tagged('post_install', '-at_install')
class TestPortalStillWorks(VisualAccessCommon):
    """Security that breaks the product is not security.

    The gate refuses anonymous asset requests. The portal is anonymous. So the
    page has to mint a grant and hand it to the viewer, and these assert that
    it does — otherwise M4.5-A would have closed the hole by turning the
    public gallery off.
    """

    def test_a_published_project_page_renders_and_carries_a_grant(self):
        # `/projects/...` are real_estate_portal's routes.
        self._skip_without_module('real_estate_portal')
        project, _unit = self._published_public_project()

        response = self._get('/projects/%s' % project.id)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-visual-grant', response.text)

    def test_the_token_on_the_page_actually_fetches_the_model(self):
        """End to end: page → grant → asset, as a visitor experiences it."""
        import re
        self._skip_without_module('real_estate_portal')
        project, _unit = self._published_public_project()

        page = self._get('/projects/%s' % project.id)
        match = re.search(r'data-visual-grant="([^"]+)"', page.text)
        self.assertTrue(match, "The page did not carry a grant token")

        asset = self._get('/visual/asset/maquette_glb/%s?t=%s'
                          % (project.id, match.group(1)))

        self.assertEqual(asset.status_code, 200)
        self.assertTrue(asset.content.startswith(b'glTF'))

    def test_an_unpublished_project_has_no_public_page_at_all(self):
        project, _unit = self._private_project()

        response = self._get('/projects/%s' % project.id)

        self.assertEqual(response.status_code, 404)

    def test_the_units_endpoint_still_answers_for_a_published_project(self):
        # `/projects/...` are real_estate_portal's routes.
        self._skip_without_module('real_estate_portal')
        project, _unit = self._published_public_project()

        response = self._get('/projects/%s/units.json' % project.id)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json())

    def test_the_units_endpoint_is_closed_for_an_unpublished_one(self):
        project, _unit = self._private_project()

        response = self._get('/projects/%s/units.json' % project.id)

        self.assertEqual(response.status_code, 404)
