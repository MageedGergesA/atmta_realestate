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

    # ------------------------------------------------------------------
    # Asset delivery
    #
    # These three keep their URLs for backward compatibility, but no longer
    # authorise or stream anything themselves: they delegate to
    # `realestate.visual.access`, the one gate every visual asset passes
    # through. Three near-identical copies of "check access, base64-decode,
    # set headers" is three places to get authorisation wrong, and the audit
    # found two more of them in other modules.
    # ------------------------------------------------------------------
    def _delegate(self, kind, record_id):
        from ..controllers.visual_asset import VisualAssetController
        return VisualAssetController().visual_asset(kind, record_id)

    @http.route(
        '/maquette/glb/<int:project_id>',
        type='http', auth='user', methods=['GET'], csrf=False,
    )
    def serve_glb(self, project_id, **kw):
        return self._delegate('maquette_glb', project_id)

    @http.route(
        '/maquette/hdr/<int:project_id>',
        type='http', auth='user', methods=['GET'], csrf=False,
    )
    def serve_hdr(self, project_id, **kw):
        return self._delegate('maquette_hdr', project_id)

    @http.route(
        '/maquette/interior/<int:property_id>',
        type='http', auth='user', methods=['GET'], csrf=False,
    )
    def serve_interior(self, property_id, **kw):
        return self._delegate('interior_glb', property_id)

    @http.route(
        '/maquette/units/<int:project_id>',
        type='json', auth='user', methods=['POST'],
    )
    def get_units(self, project_id, **kw):
        """Everything the viewer needs to render and react to clicks.

        Three things changed from 0.4, all of them consequences of the audit:

        * the payload comes from `realestate.visual.commercial`, so colour,
          price and the Reserve gate agree with Developer rather than with
          `property.state`;
        * the audience is decided here rather than by the caller, so an
          internal panel and a public embed cannot accidentally be handed the
          same dictionary;
        * a legend and a fallback descriptor ride along, because M25 forbids
          colour as the only signal and Rule 4 forbids 3D being the only path.
        """
        project = request.env['realestate.project'].browse(project_id)
        try:
            project.check_access_rights('read')
            project.check_access_rule('read')
        except AccessError:
            return {'error': 'forbidden'}
        if not project.exists():
            return {'error': 'not_found'}

        audience = ('internal' if request.env.user._is_internal()
                    else 'public')
        units = project.get_maquette_units_data(audience=audience)
        Commercial = request.env['realestate.visual.commercial']
        return {
            'project_id': project.id,
            'project_name': project.display_name,
            'units': units,
            'legend': Commercial.legend({u['visual_state'] for u in units}),
            'default_camera': project.maquette_default_camera or '',
            'has_glb': bool(project.maquette_glb),
            'has_hdr': bool(project.maquette_env_hdr),
            'mesh_naming_hint': project.maquette_mesh_naming_hint or '',
            # Rule 4 — what to fall back to when 3D cannot run. Sent with the
            # payload rather than fetched after a failure, so the fallback is
            # available at the moment the failure happens.
            'fallback': project._visual_fallback_descriptor(),
            # Cache-busting on publish, not on `write_date`. 0.4 invalidated
            # every browser's copy of a multi-megabyte model whenever anybody
            # edited the project's phone number.
            'glb_version': project.visual_version,
            'asset_config': request.env[
                'realestate.visual.assets'].viewer_config(),
            'publication_state': project.visual_publication_state,
            'is_live': project.visual_is_live,
        }

    @http.route(
        '/maquette/floor_plan/<int:property_id>',
        type='http', auth='user', methods=['GET'], csrf=False,
    )
    def serve_floor_plan(self, property_id, **kw):
        """Serve a unit's floor plan image, through the one gate."""
        return self._delegate('floor_plan_image', property_id)

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
        Region = request.env['realestate.building.region']
        region = Region
        if region_id:
            region = Region.browse(int(region_id))
            # A region of another project is not this project's to rewrite:
            # the write below would move it here.
            if not region.exists() or region.project_id != project:
                return {'error': 'not_found'}
        # Only this project's properties, or ones with no project yet. The
        # route took any id, and saving the region then moved a building of
        # another project (and its units) onto this one. The model refuses it
        # too; answering here keeps the viewer's error a message, not a crash.
        building = request.env['realestate.property'].browse(
            int(building_id)).exists()
        if not building:
            return {'error': 'not_found'}
        if building.project_id and building.project_id != project:
            return {'error': 'foreign_property'}
        vals = {
            'project_id': project.id,
            'property_id': building.id,
            'polygon': json.dumps(polygon),
        }
        if label is not None:
            vals['label'] = label
        if color:
            vals['color'] = color
        if region:
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
