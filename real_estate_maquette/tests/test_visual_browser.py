# -*- coding: utf-8 -*-
"""M8 — the real-browser gate.

Tours verify that server and JavaScript work *together*. Everything below runs
in a real headless Chrome; nothing types a credential, because `start_tour`
authenticates server-side.
"""

import base64

from odoo.tests.common import HttpCase, tagged

from .common import VisualCommon, build_glb


class VisualBrowserCommon(VisualCommon, HttpCase):

    def setUp(self):
        super().setUp()
        self.browser_user = self.env.ref('base.user_admin')
        self._grant_visual()

    def _grant_visual(self, *extra):
        """The roles a real showroom operator holds.

        The gallery is a presentation layer: it reads properties, projects,
        opportunities and the brokerage shortlist as the logged-in user, and
        the visual roles govern *publishing visuals*, not access to the records
        underneath. Granting only the visual role produced a user who could
        open the gallery and read nothing in it — which is how the browser gate
        found the raw Access Error dialog. Model access is still enforced
        server-side for every one of these; this fixture only stops the test
        user being a person who could not do this job in the first place.

        Brokerage is optional at runtime (`_match_model()` is late-bound), so
        its group is resolved defensively — the visual stack must still install
        and test without it.
        """
        groups = self.env.ref('real_estate_maquette.group_visual_manager')
        base = ('real_estate_developer.group_dev_manager',
                'atmta_real_estate.group_realestate_user',
                'sales_team.group_sale_salesman',
                'real_estate_brokerage.group_realestate_sales_agent')
        for xmlid in base + extra:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                groups |= group
        self.browser_user.groups_id |= groups
        # Fixtures run as OdooBot, who holds no visual group; publishing and
        # validating are real gates and refuse without one.
        self.env.user.groups_id |= groups
        return self.browser_user

    def _gallery_project(self, mesh_names=('U1', 'U2'), publish=True):
        project = self._project()
        for mesh in mesh_names:
            self._unit(project, mesh=mesh, price=1000000.0)
        self._attach_glb(project, list(mesh_names))
        self._attach_master_plan(project)
        project.action_visual_validate()
        if publish:
            project.action_visual_publish()
        return project

    def _gallery_url(self, project, **params):
        query = ''.join('&%s=%s' % (k, v) for k, v in params.items() if v)
        return ('/odoo/action-real_estate_maquette.action_visual_gallery'
                '?project_id=%s%s' % (project.id, query))


@tagged('post_install', '-at_install', 'atmta_visual_browser')
class TestPresentationModeBrowser(VisualBrowserCommon):
    """The showroom, rendered."""

    def test_the_gallery_opens_as_a_showroom(self):
        project = self._gallery_project()

        self.start_tour(self._gallery_url(project),
                        're_visual_presentation_tour', login='admin')

    def test_a_customer_context_shows_the_customer(self):
        self._skip_without_models('crm.lead')
        project = self._gallery_project()
        partner = self.env['res.partner'].create({'name': 'Browser Customer'})
        lead = self.env['crm.lead'].create({
            'name': 'Browser Customer — opportunity',
            'type': 'opportunity', 'partner_id': partner.id})

        self.start_tour(
            self._gallery_url(project, crm_lead_id=lead.id),
            're_visual_presentation_crm_tour', login='admin')


@tagged('post_install', '-at_install', 'atmta_visual_browser')
class TestJourneyBrowser(VisualBrowserCommon):
    """Select a unit, read its terms, save it — and check the row landed.

    The last assertion is the one that matters: a heart that lights up in the
    browser but writes nothing an agent will ever see is worse than no heart at
    all. Rule 3 says the shortlist lives in Brokerage, so that is where this
    looks for it.
    """

    def _journey_project(self, units=2):
        """A project on the degraded experience, deliberately.

        The unit *list* is the experience a weak device gets, and it is also
        the only one a headless browser can click deterministically — picking a
        specific mesh out of a WebGL canvas means guessing at pixels. Selection
        converges on `_setSelectedUnit()` whichever way it was made, so the
        journey below exercises the same path a mesh click takes, on the
        experience that most needs to work.
        """
        project = self._project()
        for index in range(units):
            self._unit(project, mesh='U%d' % index, price=1000000.0 + index)
        # No master plan either: with one, the degraded experience renders the
        # plan image, and the unit list — the last rung of the chain — is what
        # this journey needs to click.
        project.visual_3d_enabled = False
        project.action_visual_validate()
        project.action_visual_publish()
        return project

    def test_saving_a_unit_creates_the_brokerage_shortlist_row(self):
        self._skip_without_models('crm.lead', 'realestate.property.match')
        project = self._journey_project()
        partner = self.env['res.partner'].create({'name': 'Journey Customer'})
        lead = self.env['crm.lead'].create({
            'name': 'Journey Customer — opportunity',
            'type': 'opportunity', 'partner_id': partner.id})

        self.start_tour(self._gallery_url(project, crm_lead_id=lead.id),
                        're_visual_journey_tour', login='admin')

        rows = self.env['realestate.property.match'].search([
            ('crm_lead_id', '=', lead.id), ('shortlisted', '=', True)])
        self.assertEqual(
            len(rows), 1,
            "Saving in the gallery did not produce a shortlist row in "
            "Brokerage.")
        self.assertEqual(rows.property_id.project_id, project)

    def test_two_units_compare_side_by_side(self):
        project = self._journey_project()

        self.start_tour(self._gallery_url(project),
                        're_visual_compare_tour', login='admin')


@tagged('post_install', '-at_install', 'atmta_visual_browser')
class TestFallbackBrowser(VisualBrowserCommon):
    """Rule 4, in a browser. A customer must never reach a dead end."""

    def test_a_project_with_no_model_falls_back_rather_than_erroring(self):
        """The commonest real case, and it needs no WebGL trickery."""
        project = self._project()
        self._unit(project, price=1000000.0)
        self._attach_master_plan(project)
        project.visual_3d_enabled = False
        project.action_visual_validate()
        project.action_visual_publish()

        self.start_tour(self._gallery_url(project),
                        're_visual_fallback_tour', login='admin')

    def test_a_malformed_model_falls_back(self):
        project = self._project()
        self._unit(project, mesh='U1', price=1000000.0)
        self._attach_master_plan(project)
        # A .zip renamed to .glb — the shape of a real upload mistake.
        project.maquette_glb = base64.b64encode(b'PK\x03\x04 not a glb at all')

        self.start_tour(self._gallery_url(project),
                        're_visual_fallback_tour', login='admin')


@tagged('post_install', '-at_install', 'atmta_visual_browser')
class TestLifecycleBrowser(VisualBrowserCommon):
    """M8 — repeated open/close must not accumulate renderers."""

    def test_repeated_open_and_close_leaves_nothing_behind(self):
        project = self._gallery_project()

        self.start_tour(self._gallery_url(project),
                        're_visual_lifecycle_tour', login='admin')


@tagged('post_install', '-at_install', 'atmta_visual_browser')
class TestRtlBrowser(VisualBrowserCommon):
    """The UI mirrors. The 3D world does not."""

    def setUp(self):
        super().setUp()
        self.arabic = self.env['res.lang']._activate_lang('ar_001')
        if not self.arabic:
            self.env['res.lang'].with_context(active_test=False).search(
                [('code', '=', 'ar_001')]).write({'active': True})
            self.arabic = self.env['res.lang']._lang_get('ar_001')

    def test_the_gallery_mirrors_without_mirroring_the_world(self):
        if not self.arabic or self.arabic.direction != 'rtl':
            self.skipTest("No RTL Arabic language in this database.")
        project = self._gallery_project()
        self.browser_user.lang = self.arabic.code

        self.start_tour(self._gallery_url(project), 're_visual_rtl_tour',
                        login='admin')


@tagged('post_install', '-at_install', 'atmta_visual_browser')
class TestTabletBrowser(VisualBrowserCommon):
    """A salesperson holding a tablet while talking."""
    browser_size = '768x1024'
    touch_enabled = True

    def test_the_showroom_fits_a_tablet(self):
        project = self._gallery_project()

        self.start_tour(self._gallery_url(project),
                        're_visual_presentation_tour', login='admin')
