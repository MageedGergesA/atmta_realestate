import base64
import json
import logging

from odoo import http
from odoo.http import request, Response
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class MaquetteController(http.Controller):
    """HTTP endpoints serving GLB binaries and unit metadata to the 3D viewer.

    Access is restricted by the user's read access to the underlying project.
    """

    @http.route(
        '/maquette/glb/<int:project_id>',
        type='http', auth='user', methods=['GET'], csrf=False,
    )
    def serve_glb(self, project_id, **kw):
        """Stream the project's GLB to the browser. Browser caches by ETag."""
        project = request.env['realestate.project'].browse(project_id)
        try:
            project.check_access_rights('read')
            project.check_access_rule('read')
        except AccessError:
            return Response("Forbidden", status=403)
        if not project.exists() or not project.maquette_glb:
            return Response("No GLB", status=404)
        binary = base64.b64decode(project.maquette_glb)
        headers = [
            ('Content-Type', 'model/gltf-binary'),
            ('Content-Length', str(len(binary))),
            ('Content-Disposition',
             'inline; filename="%s"' % (project.maquette_glb_filename or 'maquette.glb')),
            ('Cache-Control', 'private, max-age=300'),
        ]
        return Response(binary, headers=headers)

    @http.route(
        '/maquette/hdr/<int:project_id>',
        type='http', auth='user', methods=['GET'], csrf=False,
    )
    def serve_hdr(self, project_id, **kw):
        """Stream the project's HDR environment map, if any."""
        project = request.env['realestate.project'].browse(project_id)
        try:
            project.check_access_rights('read')
            project.check_access_rule('read')
        except AccessError:
            return Response("Forbidden", status=403)
        if not project.exists() or not project.maquette_env_hdr:
            return Response("No HDR", status=404)
        binary = base64.b64decode(project.maquette_env_hdr)
        headers = [
            ('Content-Type', 'image/vnd.radiance'),
            ('Content-Length', str(len(binary))),
            ('Cache-Control', 'private, max-age=300'),
        ]
        return Response(binary, headers=headers)

    @http.route(
        '/maquette/interior/<int:property_id>',
        type='http', auth='user', methods=['GET'], csrf=False,
    )
    def serve_interior(self, property_id, **kw):
        """Stream a unit's 3D interior GLB."""
        prop = request.env['realestate.property'].browse(property_id)
        try:
            prop.check_access_rights('read')
            prop.check_access_rule('read')
        except AccessError:
            return Response("Forbidden", status=403)
        if not prop.exists() or not prop.interior_glb:
            return Response("No interior", status=404)
        binary = base64.b64decode(prop.interior_glb)
        headers = [
            ('Content-Type', 'model/gltf-binary'),
            ('Content-Length', str(len(binary))),
            ('Cache-Control', 'private, max-age=300'),
        ]
        return Response(binary, headers=headers)

    @http.route(
        '/maquette/units/<int:project_id>',
        type='json', auth='user', methods=['POST'],
    )
    def get_units(self, project_id, **kw):
        """Return JSON of all units in the project with the data the viewer needs."""
        project = request.env['realestate.project'].browse(project_id)
        try:
            project.check_access_rights('read')
            project.check_access_rule('read')
        except AccessError:
            return {'error': 'forbidden'}
        if not project.exists():
            return {'error': 'not_found'}
        return {
            'project_id': project.id,
            'project_name': project.display_name,
            'units': project.get_maquette_units_data(),
            'default_camera': project.maquette_default_camera or '',
            'has_glb': bool(project.maquette_glb),
            'has_hdr': bool(project.maquette_env_hdr),
            'mesh_naming_hint': project.maquette_mesh_naming_hint or '',
        }

    @http.route(
        '/maquette/floor_plan/<int:property_id>',
        type='http', auth='user', methods=['GET'], csrf=False,
    )
    def serve_floor_plan(self, property_id, **kw):
        """Serve a unit's floor plan image. PDF route is separate."""
        prop = request.env['realestate.property'].browse(property_id)
        try:
            prop.check_access_rights('read')
            prop.check_access_rule('read')
        except AccessError:
            return Response("Forbidden", status=403)
        if not prop.exists() or not prop.floor_plan_image:
            return Response("No plan", status=404)
        binary = base64.b64decode(prop.floor_plan_image)
        headers = [
            ('Content-Type', 'image/png'),
            ('Content-Length', str(len(binary))),
            ('Cache-Control', 'private, max-age=300'),
        ]
        return Response(binary, headers=headers)

    @http.route(
        '/maquette/save_camera',
        type='json', auth='user', methods=['POST'],
    )
    def save_default_camera(self, project_id, camera_json):
        """Persist a user-chosen 'home view' so others land at the same angle."""
        project = request.env['realestate.project'].browse(project_id)
        try:
            project.check_access_rights('write')
            project.check_access_rule('write')
        except AccessError:
            return {'error': 'forbidden'}
        try:
            json.loads(camera_json)  # validate
        except (ValueError, TypeError):
            return {'error': 'invalid_json'}
        project.maquette_default_camera = camera_json
        return {'ok': True}

    @http.route(
        '/maquette/save_mesh_mapping',
        type='json', auth='user', methods=['POST'],
    )
    def save_mesh_mapping(self, project_id, mapping):
        """Persist a {property_code: mesh_name} dict back to property records."""
        project = request.env['realestate.project'].browse(project_id)
        try:
            project.check_access_rights('write')
            project.check_access_rule('write')
        except AccessError:
            return {'error': 'forbidden'}
        if not isinstance(mapping, dict):
            return {'error': 'invalid_payload'}
        Property = request.env['realestate.property']
        updated = 0
        for code, mesh in mapping.items():
            if not code or not mesh:
                continue
            prop = Property.search([
                ('property_code', '=', code),
                ('project_id', '=', project.id),
            ], limit=1)
            if prop:
                prop.maquette_mesh_name = mesh
                updated += 1
        return {'ok': True, 'updated': updated}

    # ------------------------------------------------------------------
    # 2D building regions (clickable polygons on the master plan)
    # ------------------------------------------------------------------
    @http.route(
        '/maquette/regions/<int:project_id>',
        type='json', auth='user', methods=['POST'],
    )
    def list_regions(self, project_id, **kw):
        project = request.env['realestate.project'].browse(project_id)
        try:
            project.check_access_rights('read')
            project.check_access_rule('read')
        except AccessError:
            return {'error': 'forbidden'}
        regions = request.env['realestate.building.region'].search(
            [('project_id', '=', project.id)])
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
        # Buildings on this project that don't have a region yet — handy for
        # the picker in the inline draw tool.
        buildings = request.env['realestate.property'].search([
            ('project_id', '=', project.id),
            ('hierarchy_level', '=', 'building'),
        ])
        return {
            'regions': out,
            'buildings': [
                {'id': b.id, 'name': b.display_name,
                 'has_region': any(r['building_id'] == b.id for r in out)}
                for b in buildings
            ],
        }

    @http.route(
        '/maquette/regions/save',
        type='json', auth='user', methods=['POST'],
    )
    def save_region(self, project_id, building_id, polygon, region_id=None,
                    label=None, color=None, **kw):
        """Upsert a region. Pass region_id to update, omit to create."""
        project = request.env['realestate.project'].browse(project_id)
        try:
            project.check_access_rights('write')
            project.check_access_rule('write')
        except AccessError:
            return {'error': 'forbidden'}
        if not isinstance(polygon, list) or len(polygon) < 3:
            return {'error': 'invalid_polygon'}
        vals = {
            'project_id': project.id,
            'property_id': int(building_id),
            'polygon': json.dumps(polygon),
        }
        if label is not None:
            vals['label'] = label
        if color:
            vals['color'] = color
        Region = request.env['realestate.building.region']
        if region_id:
            region = Region.browse(int(region_id))
            if not region.exists():
                return {'error': 'not_found'}
            region.write(vals)
        else:
            region = Region.create(vals)
        return {'ok': True, 'region_id': region.id}

    @http.route(
        '/maquette/regions/delete',
        type='json', auth='user', methods=['POST'],
    )
    def delete_region(self, region_id, **kw):
        region = request.env['realestate.building.region'].browse(int(region_id))
        if not region.exists():
            return {'error': 'not_found'}
        try:
            region.unlink()
        except AccessError:
            return {'error': 'forbidden'}
        return {'ok': True}
