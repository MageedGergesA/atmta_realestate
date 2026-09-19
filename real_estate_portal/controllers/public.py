"""Public (anonymous) website endpoints for the real-estate portal.

Visitors can:
* Browse projects that have a 2D master plan or 3D maquette
* Open a project's page with the live 2D/3D viewer (clickable units)
* Submit an Expression of Interest (EOI) or a visit-request form
  → a crm.lead is created and tagged with the project / property

All routes are auth='public'; internally we sudo() so unauthenticated visitors
can still read public-marketing data without leaking unrelated records.
"""
import base64
import json
import logging
from datetime import datetime

from odoo import _, http
from odoo.http import request, Response

from odoo.addons.real_estate_maquette.controllers.visual_asset import (
    remember_public_lead,
)
from odoo.addons.real_estate_maquette.models.visual_states import (
    VISUAL_STATE_COUNTED_AS,
)

_logger = logging.getLogger(__name__)


_LIST_FIELDS = ['id', 'name', 'code', 'has_master_plan_2d', 'has_maquette']


class PublicRealEstate(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _public_project(self, project_id):
        """Return the project iff it may genuinely be shown to the public.

        Until M4.5 this asked only "does it exist and is it not cancelled",
        and every asset route below inherited that as its entire
        authorisation — so a sequential integer fetched a project's whole GLB.

        A project is now public because somebody decided it should be
        (`visual_public_enabled`) and published it (`visual_is_live`), which
        are the two fields the gallery lifecycle already maintains.
        """
        proj = request.env['realestate.project'].sudo().browse(
            int(project_id)).exists()
        if not proj or proj.state == 'cancelled':
            return None
        if not (proj.visual_public_enabled and proj.visual_is_live):
            return None
        return proj

    def _public_grant(self, project, kinds=None):
        """Mint a resource-scoped grant for a page that just passed the gate.

        The page is authorised; the assets it embeds are separately
        capability-checked. A leaked asset URL therefore expires, and cannot
        be edited into a URL for a different project.
        """
        return request.env['realestate.visual.access'].sudo(
            ).grant_for_public_project(
                project, kinds=kinds, source='portal_page',
                source_ref='/projects/%s' % project.id)

    def _asset_url(self, kind, record_id, grant):
        return '/visual/asset/%s/%s?t=%s' % (kind, record_id, grant.token)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------
    @http.route(['/projects'], type='http', auth='public', website=True, sitemap=True)
    def projects_index(self, **kw):
        # Only projects that have a public page: the same three conditions
        # `_public_project` applies. Listing every non-cancelled project sent
        # visitors to cards that answered 404. Visuals are optional; cards
        # without an image fall back to the SCSS placeholder block.
        projects = request.env['realestate.project'].sudo().search([
            ('state', '!=', 'cancelled'),
            ('visual_public_enabled', '=', True),
            ('visual_is_live', '=', True),
        ], order='name')
        return request.render('real_estate_portal.public_projects_index', {
            'projects': projects,
        })

    @http.route(['/projects/<int:project_id>'], type='http', auth='public', website=True, sitemap=True)
    def project_page(self, project_id, **kw):
        proj = self._public_project(project_id)
        if not proj:
            return request.not_found()
        Property = request.env['realestate.property'].sudo()
        units = Property.search([
            ('project_id', '=', proj.id),
            ('hierarchy_level', '=', 'unit'),
        ])
        # The 2D drill can fire when (a) the project has its own master
        # plan, (b) the configured entry property has a plan image, or
        # (c) some top-level property under the project has its own plan
        # image (picker mode — the viewer renders cards to choose from).
        has_2d_entry = bool(
            proj.has_master_plan_2d
            or (proj.main_property_id and proj.main_property_id.has_plan_image)
            or Property.search_count([
                ('project_id', '=', proj.id),
                ('parent_id', '=', False),
                ('has_plan_image', '=', True),
            ])
        )
        # One grant per page render, scoped to this project and the asset
        # kinds a portal visitor legitimately needs. The template passes its
        # token to the viewer mounts; every asset request then carries it.
        visual_grant = self._public_grant(proj, kinds=[
            'maquette_glb', 'maquette_hdr', 'master_plan_2d',
            'plan_image', 'floor_plan_image', 'elevation_sheet',
            'gallery_image',
        ])
        return request.render('real_estate_portal.public_project_page', {
            'visual_grant_token': visual_grant.token,
            'project': proj,
            'has_2d_entry': has_2d_entry,
            'units_total': len(units),
            # From the gallery status, like the units the viewer colours, not
            # the legacy `property.state` (an unreleased unit reads
            # `available` there).
            'units_available': len(units.filtered(
                lambda u: u.visual_state in VISUAL_STATE_COUNTED_AS['available'])),
            'units_reserved': len(units.filtered(
                lambda u: u.visual_state in VISUAL_STATE_COUNTED_AS['reserved'])),
            'units_sold': len(units.filtered(
                lambda u: u.visual_state in VISUAL_STATE_COUNTED_AS['sold'])),
        })

    # ------------------------------------------------------------------
    # Data endpoints consumed by the embedded OWL viewers
    # ------------------------------------------------------------------
    @http.route(['/projects/<int:project_id>/glb'], type='http',
                auth='public', csrf=False)
    def project_glb(self, project_id, t=None, **kw):
        """Kept for URL compatibility; authorisation is the shared gate's.

        Previously this streamed a project's entire 3D model to anybody who
        could count. It now requires a grant, exactly as `/visual/asset/...`
        does — this route simply forwards to it.
        """
        from odoo.addons.real_estate_maquette.controllers.visual_asset import (
            VisualAssetController)
        return VisualAssetController().visual_asset(
            'maquette_glb', project_id, t=t)

    @http.route(['/projects/<int:project_id>/units.json'],
                type='http', auth='public', csrf=False)
    def project_units(self, project_id, **kw):
        proj = self._public_project(project_id)
        if not proj:
            return Response("Not Found", status=404)
        # `audience='public'` explicitly. The default changed to public as a
        # fail-safe, but a public route stating its own audience is worth the
        # eight characters: this endpoint briefly served internal list prices,
        # unavailability reasons and commercial status to anonymous visitors
        # because it inherited a default.
        data = proj.get_maquette_units_data(audience='public')
        return Response(json.dumps(data), content_type='application/json')

    @http.route(['/projects/<int:project_id>/regions.json'],
                type='http', auth='public', csrf=False)
    def project_regions(self, project_id, **kw):
        proj = self._public_project(project_id)
        if not proj:
            return Response("Not Found", status=404)
        regions = request.env['realestate.building.region'].sudo().search(
            [('project_id', '=', proj.id)])
        out = []
        for r in regions:
            try:
                pts = json.loads(r.polygon or '[]')
            except (ValueError, TypeError):
                pts = []
            out.append({
                'id': r.id,
                'building_id': r.property_id.id,
                'building_name': r.property_id.display_name or '',
                'label': r.label or r.property_id.display_name or '',
                'color': r.color or '#3b82f6',
                'polygon': pts,
            })
        return Response(json.dumps({'regions': out}),
                        content_type='application/json')

    @http.route(['/projects/<int:project_id>/property/<int:property_id>.json'],
                type='http', auth='public', csrf=False)
    def project_property_info(self, project_id, property_id, **kw):
        """Detail JSON for a single clicked property (modal panel data)."""
        proj = self._public_project(project_id)
        if not proj:
            return Response("Not Found", status=404)
        prop = request.env['realestate.property'].sudo().browse(property_id).exists()
        if not prop or prop.project_id != proj:
            return Response("Not Found", status=404)
        images = []
        if prop.floor_plan_image:
            images.append({
                'id': 'fp',
                'src': f'/web/image/realestate.property/{prop.id}/floor_plan_image',
                'name': 'Floor Plan',
            })
        for img in prop.property_Attachment_media_ids:
            images.append({
                'id': img.id,
                'src': f'/web/image/property.image/{img.id}/image_1920',
                'name': img.name or '',
            })
        return Response(json.dumps({
            'id': prop.id,
            'name': prop.name or '',
            'property_code': prop.property_code or '',
            'property_type': prop.property_type_id.name or '',
            'state': prop.state or '',
            'state_label': dict(prop._fields['state'].selection).get(prop.state, ''),
            'area_sqm': prop.area_sqm or 0.0,
            'base_price': float(prop.base_price) if 'base_price' in prop._fields else 0.0,
            'currency': prop.currency_id.symbol or '',
            'bedrooms': prop.bedroom_count or 0,
            'bathrooms': prop.bathroom_count or 0,
            'description': prop.property_notes or '',
            'hierarchy_level': prop.hierarchy_level or '',
            'images': images,
        }), content_type='application/json')

    # ------------------------------------------------------------------
    # Building elevation (full drill-down) — used by BuildingElevation OWL
    # ------------------------------------------------------------------
    @http.route(['/projects/portal/building/<int:building_id>.json'],
                type='http', auth='public', csrf=False)
    def portal_building(self, building_id, **kw):
        prop = request.env['realestate.property'].sudo().browse(building_id).exists()
        if not prop or prop.hierarchy_level not in ('block', 'building'):
            return Response("Not Found", status=404)
        # Only a building of a published project. This route checked the level
        # and nothing else, so any building of any company -- unpublished
        # projects included -- could be read by counting ids.
        if not prop.project_id or not self._public_project(prop.project_id.id):
            return Response("Not Found", status=404)
        sheet_url = (f'/web/image/realestate.property/{prop.id}/elevation_sheet'
                     if prop.elevation_sheet else None)
        floors = []
        for f in prop.floor_ids.sorted(key=lambda x: x.floor_number):
            floors.append({
                'id': f.id,
                'floor_number': f.floor_number,
                'usage': f.property_usage_id.name or '',
                'units_total': f.units_total,
                'units_available': f.units_available,
                'units_reserved': f.units_reserved,
                'units_sold': f.units_sold,
            })
        specs = [{
            'id': t.id, 'name': t.name, 'icon': t.icon or '', 'color': t.color or 0,
        } for t in prop.spec_tag_ids]
        return Response(json.dumps({
            'building': {
                'id': prop.id,
                'name': prop.name or '',
                'property_code': prop.property_code or '',
                'number_of_floors': prop.number_of_floors or 0,
                'number_of_units': prop.number_of_units or 0,
                'city': prop.city or '',
                'district': prop.district or '',
                'area_sqm': prop.area_sqm or 0.0,
                'sheet_url': sheet_url,
            },
            'floors': floors,
            'specs': specs,
        }), content_type='application/json')

    @http.route(['/projects/portal/floor/<int:floor_id>/units.json'],
                type='http', auth='public', csrf=False)
    def portal_floor_units(self, floor_id, **kw):
        floor = request.env['realestate.building.floor'].sudo().browse(floor_id).exists()
        if not floor:
            return Response("Not Found", status=404)
        # Only a floor of a published project, and only what the public may
        # see. This route served any floor's units with their internal base
        # price and raw status, the leak `units.json` was fixed for.
        project = floor.building_id.project_id
        if not project or not self._public_project(project.id):
            return Response("Not Found", status=404)
        units = floor.unit_ids.sorted('property_code')
        payload = request.env['realestate.visual.commercial'].sudo().unit_payload(
            units, audience='public')
        out = [{
            'id': entry['id'],
            'name': entry['name'],
            'property_code': entry['property_code'],
            'state': entry['visual_state'],
            'area_sqm': entry['area_sqm'],
            'base_price': entry['price'],
            'has_floor_plan': entry['has_floor_plan'],
        } for entry in payload]
        return Response(json.dumps(out), content_type='application/json')

    @http.route(['/projects/portal/property/<int:property_id>/floor_plan'],
                type='http', auth='public', csrf=False)
    def portal_unit_floor_plan(self, property_id, t=None, **kw):
        """Serve a unit's floor plan, through the shared gate."""
        from odoo.addons.real_estate_maquette.controllers.visual_asset import (
            VisualAssetController)
        return VisualAssetController().visual_asset(
            'floor_plan_image', property_id, t=t)

    @http.route(['/projects/portal/property/<int:property_id>/images.json'],
                type='http', auth='public', csrf=False)
    def portal_property_images(self, property_id, **kw):
        prop = request.env['realestate.property'].sudo().browse(property_id).exists()
        if not prop:
            return Response("Not Found", status=404)
        project = prop.project_id
        if not project or not self._public_project(project.id):
            return Response("Not Found", status=404)
        grant = self._public_grant(project, kinds=['gallery_image'])
        imgs = []
        for img in prop.property_Attachment_media_ids:
            # Not `/web/image/property.image/<id>/image_1920`: that is Odoo's
            # own unauthenticated binary route, and handing a visitor a URL
            # shaped like that invites editing the id.
            imgs.append({
                'id': img.id,
                'src': self._asset_url('gallery_image', img.id, grant),
            })
        return Response(json.dumps(imgs), content_type='application/json')

    # ------------------------------------------------------------------
    # EOI + visit-request submission
    # ------------------------------------------------------------------
    def _create_lead(self, project, prop, post, source):
        """Create a CRM lead from a public form submission."""
        name = (post.get('name') or '').strip()
        email = (post.get('email') or '').strip()
        phone = (post.get('phone') or '').strip()
        message = (post.get('message') or '').strip()
        if not name or not (email or phone):
            return False, "Name and either email or phone are required."
        title = "EOI · " + (prop.display_name if prop else project.name)
        if source == 'visit':
            title = "Visit Request · " + (prop.display_name if prop else project.name)
        vals = {
            'name': title,
            'contact_name': name,
            'email_from': email,
            'phone': phone,
            'description': message,
            'type': 'lead',
            're_project_id': project.id,
            're_property_id': prop.id if prop else False,
            're_source': source,
            'source_id': self._utm_source_id() or False,
        }
        if source == 'visit':
            visit_str = (post.get('visit_date') or '').strip()
            vals['re_visit_requested'] = True
            if visit_str:
                # The form's `datetime-local` input posts ISO ('2026-09-20T10:30'),
                # which the ORM's '%Y-%m-%d %H:%M:%S' parser rejected with a
                # ValueError -- a 500 for every visitor who picked a date.
                # Stored as entered, as before (no visitor timezone is known).
                try:
                    vals['re_visit_date'] = datetime.fromisoformat(
                        visit_str).replace(tzinfo=None, microsecond=0)
                except ValueError:
                    return False, _("The preferred visit date is not a valid date and time.")
        lead = request.env['crm.lead'].sudo().create(vals)
        # This session created it, so this session may later fold a shortlist
        # onto it via `/visual/public/convert`. Nothing else may.
        remember_public_lead(lead.id)
        self._absorb_session_shortlist(project, lead, post, source)
        return lead, None

    def _absorb_session_shortlist(self, project, lead, post, source):
        """Fold the visitor's browser-held favourites onto the lead they just
        created.

        This is the conversion the anonymous-session rule allows: nothing was
        written while they browsed, and this runs only because they filled in a
        form and pressed a button. The ids are re-checked against the project
        rather than trusted from the post — a hidden field is a value somebody
        typed.

        A failure here never breaks the enquiry. The customer's message is the
        thing that must not be lost; a favourite that did not carry over is a
        smaller loss than an enquiry that vanished, and it is logged.
        """
        raw = (post.get('shortlist') or '').strip()
        if not raw or not lead:
            return
        try:
            wanted = {int(part) for part in raw.replace(' ', '').split(',')
                      if part.isdigit()}
        except ValueError:
            return
        if not wanted:
            return
        units = request.env['realestate.property'].sudo().browse(
            sorted(wanted)).exists().filtered(
                lambda u: u.project_id.id == project.id)
        if not units:
            return
        Modes = request.env.get('realestate.visual.modes')
        if Modes is None:
            return
        try:
            Modes.sudo().convert_public_session(
                lead.id, units.ids, source='public_%s' % source)
        except Exception:
            _logger.warning(
                "Could not carry a public shortlist onto lead %s", lead.id,
                exc_info=True)

    def _utm_source_id(self):
        rec = request.env.ref('real_estate_portal.utm_source_re_portal',
                              raise_if_not_found=False)
        return rec.id if rec else False

    @http.route(['/projects/<int:project_id>/eoi'],
                type='http', auth='public', methods=['POST'], website=True, csrf=True)
    def submit_eoi(self, project_id, **post):
        proj = self._public_project(project_id)
        if not proj:
            return request.not_found()
        prop = False
        if post.get('property_id'):
            prop = request.env['realestate.property'].sudo().browse(
                int(post['property_id'])).exists()
            if prop and prop.project_id != proj:
                prop = False
        lead, err = self._create_lead(proj, prop, post, source='eoi')
        if err:
            return request.render('real_estate_portal.public_eoi_error',
                                  {'project': proj, 'error': err})
        return request.render('real_estate_portal.public_eoi_thanks',
                              {'project': proj, 'property': prop, 'lead': lead})

    @http.route(['/projects/<int:project_id>/visit'],
                type='http', auth='public', methods=['POST'], website=True, csrf=True)
    def submit_visit(self, project_id, **post):
        proj = self._public_project(project_id)
        if not proj:
            return request.not_found()
        prop = False
        if post.get('property_id'):
            prop = request.env['realestate.property'].sudo().browse(
                int(post['property_id'])).exists()
            if prop and prop.project_id != proj:
                prop = False
        lead, err = self._create_lead(proj, prop, post, source='visit')
        if err:
            return request.render('real_estate_portal.public_eoi_error',
                                  {'project': proj, 'error': err})
        return request.render('real_estate_portal.public_eoi_thanks',
                              {'project': proj, 'property': prop, 'lead': lead,
                               'is_visit': True})
