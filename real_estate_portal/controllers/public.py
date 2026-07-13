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

from odoo import http
from odoo.http import request, Response


_LIST_FIELDS = ['id', 'name', 'code', 'has_master_plan_2d', 'has_maquette']


class PublicRealEstate(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _public_project(self, project_id):
        """Return the project iff it exists. Visuals (2D plan, entry property,
        3D maquette) are no longer required — projects without any of those
        still get a portal page; the viewer tabs simply don't render.
        Cancelled / never-active projects are still hidden."""
        proj = request.env['realestate.project'].sudo().browse(int(project_id)).exists()
        if not proj:
            return None
        if proj.state == 'cancelled':
            return None
        return proj

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------
    @http.route(['/projects'], type='http', auth='public', website=True, sitemap=True)
    def projects_index(self, **kw):
        # List every non-cancelled project. Visuals are optional; cards
        # without an image fall back to the SCSS placeholder block.
        projects = request.env['realestate.project'].sudo().search([
            ('state', '!=', 'cancelled'),
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
        return request.render('real_estate_portal.public_project_page', {
            'project': proj,
            'has_2d_entry': has_2d_entry,
            'units_total': len(units),
            'units_available': len(units.filtered(lambda u: u.state == 'available')),
            'units_reserved': len(units.filtered(lambda u: u.state == 'reserved')),
            'units_sold': len(units.filtered(lambda u: u.state == 'sold')),
        })

    # ------------------------------------------------------------------
    # Data endpoints consumed by the embedded OWL viewers
    # ------------------------------------------------------------------
    @http.route(['/projects/<int:project_id>/glb'], type='http', auth='public', csrf=False)
    def project_glb(self, project_id, **kw):
        proj = self._public_project(project_id)
        if not proj or not proj.maquette_glb:
            return Response("Not Found", status=404)
        # Revalidate against write_date so a re-uploaded/deleted GLB isn't served
        # stale from the visitor's (or a shared proxy's) cache of the old model.
        etag = '"re-glb-%s-%s"' % (
            proj.id, int(proj.write_date.timestamp()) if proj.write_date else 0)
        if request.httprequest.headers.get('If-None-Match') == etag:
            return Response(status=304, headers=[
                ('ETag', etag), ('Cache-Control', 'no-cache')])
        binary = base64.b64decode(proj.maquette_glb)
        headers = [
            ('Content-Type', 'model/gltf-binary'),
            ('Content-Length', str(len(binary))),
            ('Cache-Control', 'no-cache'),
            ('ETag', etag),
        ]
        return Response(binary, headers=headers)

    @http.route(['/projects/<int:project_id>/units.json'],
                type='http', auth='public', csrf=False)
    def project_units(self, project_id, **kw):
        proj = self._public_project(project_id)
        if not proj:
            return Response("Not Found", status=404)
        data = proj.get_maquette_units_data()
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
        units = floor.unit_ids.sorted('property_code')
        out = []
        for u in units:
            out.append({
                'id': u.id,
                'name': u.name or '',
                'property_code': u.property_code or '',
                'state': u.state or '',
                'area_sqm': u.area_sqm or 0.0,
                'base_price': float(u.base_price) if 'base_price' in u._fields else 0.0,
                'has_floor_plan': bool(u.has_floor_plan_effective),
            })
        return Response(json.dumps(out), content_type='application/json')

    @http.route(['/projects/portal/property/<int:property_id>/floor_plan'],
                type='http', auth='public', csrf=False)
    def portal_unit_floor_plan(self, property_id, **kw):
        """Serve a unit's floor plan image."""
        prop = request.env['realestate.property'].sudo().browse(property_id).exists()
        if not prop or not prop.floor_plan_image:
            return Response("No plan", status=404)
        # Revalidate against write_date so a re-uploaded floor plan isn't served
        # stale from the visitor's (or a shared proxy's) cache of the old image.
        etag = '"re-fp-%s-%s"' % (
            prop.id, int(prop.write_date.timestamp()) if prop.write_date else 0)
        if request.httprequest.headers.get('If-None-Match') == etag:
            return Response(status=304, headers=[
                ('ETag', etag), ('Cache-Control', 'no-cache')])
        binary = base64.b64decode(prop.floor_plan_image)
        return Response(binary, headers=[
            ('Content-Type', 'image/png'),
            ('Content-Length', str(len(binary))),
            ('Cache-Control', 'no-cache'),
            ('ETag', etag),
        ])

    @http.route(['/projects/portal/property/<int:property_id>/images.json'],
                type='http', auth='public', csrf=False)
    def portal_property_images(self, property_id, **kw):
        prop = request.env['realestate.property'].sudo().browse(property_id).exists()
        if not prop:
            return Response("Not Found", status=404)
        imgs = []
        for img in prop.property_Attachment_media_ids:
            imgs.append({
                'id': img.id,
                'src': f'/web/image/property.image/{img.id}/image_1920',
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
                vals['re_visit_date'] = visit_str
        lead = request.env['crm.lead'].sudo().create(vals)
        return lead, None

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
