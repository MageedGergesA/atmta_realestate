import json

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request


class PlanController(http.Controller):

    @http.route(
        '/real_estate_plan/regions/<int:property_id>',
        type='json', auth='user', methods=['POST'],
    )
    def list_regions(self, property_id, **kw):
        prop = request.env['realestate.property'].browse(property_id)
        try:
            prop.check_access_rights('read')
            prop.check_access_rule('read')
        except AccessError:
            return {'error': 'forbidden'}
        regions = request.env['realestate.plan.region'].search(
            [('parent_property_id', '=', prop.id)])
        out = []
        for r in regions:
            try:
                pts = json.loads(r.polygon or '[]')
            except (ValueError, TypeError):
                pts = []
            out.append({
                'id': r.id,
                'target_id': r.target_property_id.id,
                'target_name': r.target_property_id.display_name or '',
                'target_state': r.target_state or '',
                'target_hierarchy_level': r.target_hierarchy_level or '',
                'label': r.label or r.target_property_id.display_name or '',
                'color': r.color or '#3b82f6',
                'polygon': pts,
            })
        # Direct children that don't yet have a region — handy for the picker.
        children = prop.child_ids
        return {
            'regions': out,
            'children': [
                {
                    'id': c.id,
                    'name': c.display_name,
                    'property_code': c.property_code or '',
                    'hierarchy_level': c.hierarchy_level or '',
                    'state': c.state or '',
                    'has_region': any(r['target_id'] == c.id for r in out),
                }
                for c in children
            ],
        }

    @http.route(
        '/real_estate_plan/regions/save',
        type='json', auth='user', methods=['POST'],
    )
    def save_region(self, property_id, target_id, polygon, region_id=None,
                    label=None, color=None, **kw):
        prop = request.env['realestate.property'].browse(property_id)
        try:
            prop.check_access_rights('write')
            prop.check_access_rule('write')
        except AccessError:
            return {'error': 'forbidden'}
        if not isinstance(polygon, list) or len(polygon) < 3:
            return {'error': 'invalid_polygon'}
        vals = {
            'parent_property_id': prop.id,
            'target_property_id': int(target_id),
            'polygon': json.dumps(polygon),
        }
        if label is not None:
            vals['label'] = label
        if color:
            vals['color'] = color
        Region = request.env['realestate.plan.region']
        if region_id:
            region = Region.browse(int(region_id))
            if not region.exists():
                return {'error': 'not_found'}
            region.write(vals)
        else:
            region = Region.create(vals)
        return {'ok': True, 'region_id': region.id}

    @http.route(
        '/real_estate_plan/property_view/<int:property_id>',
        type='json', auth='user', methods=['POST'],
    )
    def property_view(self, property_id, **kw):
        """One-shot fetch used by the drill-down viewer dialog: returns the
        property, its regions, and its direct children + flag on each child
        saying whether it has its own plan image (= drillable further)."""
        prop = request.env['realestate.property'].browse(property_id)
        try:
            prop.check_access_rights('read')
            prop.check_access_rule('read')
        except AccessError:
            return {'error': 'forbidden'}
        if not prop.exists():
            return {'error': 'not_found'}
        # write_date is used as a cache-busting "unique" param on image URLs:
        # the URL stays stable for the same revision (so the browser caches),
        # and changes the moment the image is replaced (so we never serve a
        # stale image). Standard pattern from /web/static/src/views/fields.
        prop_unique = (prop.write_date or False) and prop.write_date.strftime(
            '%Y%m%d%H%M%S')
        regions = request.env['realestate.plan.region'].search(
            [('parent_property_id', '=', prop.id)])
        out_regions = []
        for r in regions:
            try:
                pts = json.loads(r.polygon or '[]')
            except (ValueError, TypeError):
                pts = []
            out_regions.append({
                'id': r.id,
                'target_id': r.target_property_id.id,
                'target_name': r.target_property_id.display_name or '',
                'target_state': r.target_state or '',
                'target_hierarchy_level': r.target_hierarchy_level or '',
                'label': r.label or r.target_property_id.display_name or '',
                'color': r.color or '#3b82f6',
                'polygon': pts,
            })
        children = []
        for c in prop.child_ids:
            children.append({
                'id': c.id,
                'name': c.display_name,
                'property_code': c.property_code or '',
                'hierarchy_level': c.hierarchy_level or '',
                'state': c.state or '',
                'has_plan_image': bool(c.plan_image),
            })
        return {
            'property': {
                'id': prop.id,
                'name': prop.display_name,
                'property_code': prop.property_code or '',
                'hierarchy_level': prop.hierarchy_level or '',
                'state': prop.state or '',
                'has_plan_image': bool(prop.plan_image),
                # Sized URL (max 1920×1080) + unique query param: Odoo's image
                # endpoint resizes on demand, caches the variant on disk, and
                # the unique parameter lets the browser cache aggressively
                # while still busting the cache when the image is replaced.
                'plan_image_url': (
                    f"/web/image/realestate.property/{prop.id}/plan_image/1920x1080"
                    f"?unique={prop_unique}"
                    if prop.plan_image else None
                ),
            },
            'regions': out_regions,
            'children': children,
        }

    @http.route(
        '/real_estate_plan/project_view/<int:project_id>',
        type='json', auth='user', methods=['POST'],
    )
    def project_view(self, project_id, **kw):
        """Drill-down viewer entry for a project: returns the master plan
        image + building.region polygons (from the maquette module). The
        viewer then drills into each clicked building via /property_view."""
        Project = request.env['realestate.project'].sudo(False)
        proj = Project.browse(project_id)
        try:
            proj.check_access_rights('read')
            proj.check_access_rule('read')
        except AccessError:
            return {'error': 'forbidden'}
        if not proj.exists():
            return {'error': 'not_found'}
        has_plan = bool(getattr(proj, 'master_plan_2d', False))
        proj_unique = (proj.write_date or False) and proj.write_date.strftime(
            '%Y%m%d%H%M%S')
        plan_url = (
            f"/web/image/realestate.project/{proj.id}/master_plan_2d/1920x1080"
            f"?unique={proj_unique}"
            if has_plan else None
        )
        out_regions = []
        # Master-plan polygons (maquette) — only when both maquette is
        # installed AND the project has a master_plan_2d image to overlay.
        if has_plan and 'realestate.building.region' in request.env:
            regions = request.env['realestate.building.region'].search(
                [('project_id', '=', proj.id)])
            for r in regions:
                try:
                    pts = json.loads(r.polygon or '[]')
                except (ValueError, TypeError):
                    pts = []
                out_regions.append({
                    'id': r.id,
                    'target_id': r.property_id.id,
                    'target_name': r.property_id.display_name or '',
                    'target_state': (r.property_id.state or ''),
                    'target_hierarchy_level': (r.property_id.hierarchy_level or ''),
                    'label': r.label or r.property_id.display_name or '',
                    'color': r.color or '#3b82f6',
                    'polygon': pts,
                })
        # Top-level properties under this project, regardless of hierarchy
        # level — this is what the user picks when there's no master_plan_2d
        # and they want to drill into a compound that has its own plan
        # configured.
        children = []
        seen_ids = set()
        top_props = request.env['realestate.property'].search([
            ('project_id', '=', proj.id),
            ('parent_id', '=', False),
        ])
        for p in top_props:
            children.append({
                'id': p.id,
                'name': p.display_name,
                'property_code': p.property_code or '',
                'hierarchy_level': p.hierarchy_level or '',
                'state': p.state or '',
                'has_plan_image': bool(p.plan_image),
            })
            seen_ids.add(p.id)
        # Plus any region target that isn't a top-level property (e.g. a
        # nested building polygon-targeted directly from the master plan).
        for r in out_regions:
            if r['target_id'] in seen_ids:
                continue
            target = request.env['realestate.property'].browse(r['target_id'])
            if target.exists():
                children.append({
                    'id': target.id,
                    'name': target.display_name,
                    'property_code': target.property_code or '',
                    'hierarchy_level': target.hierarchy_level or '',
                    'state': target.state or '',
                    'has_plan_image': bool(target.plan_image),
                })
                seen_ids.add(target.id)
        return {
            'property': {
                'id': proj.id,
                'name': proj.display_name,
                'property_code': getattr(proj, 'code', '') or '',
                'hierarchy_level': 'project',
                'state': '',
                'has_plan_image': has_plan,
                'plan_image_url': plan_url,
                'is_project': True,
            },
            'regions': out_regions,
            'children': children,
        }

    @http.route(
        '/real_estate_plan/regions/delete',
        type='json', auth='user', methods=['POST'],
    )
    def delete_region(self, region_id, **kw):
        region = request.env['realestate.plan.region'].browse(int(region_id))
        if not region.exists():
            return {'error': 'not_found'}
        try:
            region.unlink()
        except AccessError:
            return {'error': 'forbidden'}
        return {'ok': True}
