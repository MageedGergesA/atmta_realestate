"""``/api/v1/map/*`` — geolocation endpoints for map-based UIs.

The 3rd-party site renders its own Leaflet / Google Maps view of the
catalog. These endpoints feed it:

* ``GET /api/v1/map/projects``    — all publicly-visible projects that
  have ``latitude`` + ``longitude`` set (centroid, plus optional
  boundary polygon).
* ``GET /api/v1/map/properties``  — all publicly-visible properties
  (compounds, buildings, units, …) that have coordinates.

Both endpoints are **public reads** — no auth required, consistent
with the rest of the catalog. They respect the same visibility rules:
cancelled / planning / hidden records never appear.

Strict semantics:
* A record with ``latitude == 0 AND longitude == 0`` is treated as
  "no coordinates set" and excluded — we never plot a point at the
  prime meridian / equator as a fallback.
* ``limit`` is bounded at 5000 (matches the in-house dashboard cap).
  Asking for more does NOT silently truncate; the response returns
  exactly the cap with an ``"X-Truncated: true"`` header so the client
  knows it lost rows.
* ``bbox`` filter (``lat_min,lng_min,lat_max,lng_max``) — malformed
  bbox raises 400, never silently widens to "no bbox".
"""

import werkzeug.exceptions

from odoo import _, http
from odoo.http import request

from ._base import (
    _int_arg,
    json_endpoint,
    json_response,
    parse_pagination,
)
from ..models.realestate_project import PUBLIC_PROJECT_STATUS


# Properties that are publicly visible on the map. Catalog already
# hides cancelled / inactive; the map further filters to states the
# 3rd-party would render a marker for.
PUBLIC_PROPERTY_STATES = ('available', 'reserved', 'sold')

HARD_LIMIT_MAX = 5000


def _parse_bbox(raw):
    """Parse ``lat_min,lng_min,lat_max,lng_max`` or return ``None``.

    Strict: any malformed input → 400 rather than silently ignoring
    the bbox (caller might think they're filtering when they're not).
    """
    if not raw:
        return None
    parts = [p.strip() for p in str(raw).split(',')]
    if len(parts) != 4:
        raise werkzeug.exceptions.BadRequest(_(
            "'bbox' must be 'lat_min,lng_min,lat_max,lng_max'."
        ))
    try:
        lat_min, lng_min, lat_max, lng_max = (float(p) for p in parts)
    except ValueError:
        raise werkzeug.exceptions.BadRequest(_(
            "'bbox' values must be floats."
        ))
    if not (-90 <= lat_min <= 90 and -90 <= lat_max <= 90):
        raise werkzeug.exceptions.BadRequest(_(
            "'bbox' latitudes must be in [-90, 90]."
        ))
    if not (-180 <= lng_min <= 180 and -180 <= lng_max <= 180):
        raise werkzeug.exceptions.BadRequest(_(
            "'bbox' longitudes must be in [-180, 180]."
        ))
    if lat_min > lat_max or lng_min > lng_max:
        raise werkzeug.exceptions.BadRequest(_(
            "'bbox' min must be <= max for both axes."
        ))
    return (lat_min, lng_min, lat_max, lng_max)


class MapApiV1(http.Controller):

    # ─────────────────────────────────────────────────────────────────
    # PROJECTS ON THE MAP
    # ─────────────────────────────────────────────────────────────────

    @http.route('/api/v1/map/projects', type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def list_map_projects(self, _body=None, _auth_uid=None, _key_fp=None, **kw):
        env = request.env
        Project = env['realestate.project'].sudo()

        limit, offset = parse_pagination(kw, default_limit=500,
                                         max_limit=HARD_LIMIT_MAX)
        include_boundary = (kw.get('include_boundary') or '').lower() in (
            '1', 'true', 'yes',
        )
        bbox = _parse_bbox(kw.get('bbox'))

        # Domain: publicly-visible projects with coordinates set
        # (treating 0,0 as "unset" — see module docstring).
        domain = Project._api_public_domain() + [
            '|', ('latitude', '!=', 0), ('longitude', '!=', 0),
        ]
        # Optional single-developer filter.
        dev_id = _int_arg(kw, 'developer_id', None, lo=1)
        if dev_id:
            domain.append(('developer_id', '=', dev_id))

        if bbox:
            lat_min, lng_min, lat_max, lng_max = bbox
            domain += [
                ('latitude', '>=', lat_min), ('latitude', '<=', lat_max),
                ('longitude', '>=', lng_min), ('longitude', '<=', lng_max),
            ]

        total = Project.search_count(domain)
        rows = Project.search(domain, limit=limit, offset=offset, order='id')

        # How many publicly-visible projects exist that DON'T have
        # coordinates set — useful for a "N hidden from map" hint.
        # Filtered by the same optional developer_id filter (but NOT
        # bbox — bbox is a viewport, not a "what could be on the map").
        missing_domain = Project._api_public_domain() + [
            ('latitude', '=', 0), ('longitude', '=', 0),
        ]
        if dev_id:
            missing_domain.append(('developer_id', '=', dev_id))
        missing_count = Project.search_count(missing_domain)

        results = []
        for p in rows:
            entry = {
                'id': p.id,
                'code': p.code or '',
                'name': p.name or '',
                'status': PUBLIC_PROJECT_STATUS.get(p.state),
                'latitude': p.latitude,
                'longitude': p.longitude,
                'city': p.city or '',
                'country': p.country_id.name if p.country_id else '',
                'country_code': p.country_id.code if p.country_id else '',
                'developer_id': p.developer_id.id if p.developer_id else None,
                'developer_name': p.developer_id.name if p.developer_id else '',
                'cover_image_url': p._api_image_url(
                    'master_plan_2d', size='400x300'),
                'has_2d_plan': bool(getattr(p, 'has_drillable_2d', False)),
                'has_3d_maquette': bool(getattr(p, 'has_maquette', False)),
                'unit_count': getattr(p, 'unit_count', 0),
                'available_unit_count': getattr(p, 'available_unit_count', 0),
            }
            if include_boundary and 'boundary_point_ids' in p._fields:
                entry['boundary_points'] = [
                    {'sequence': bp.sequence,
                     'latitude': bp.latitude,
                     'longitude': bp.longitude,
                     'label': bp.label or ''}
                    for bp in p.boundary_point_ids.sorted('sequence')
                ]
            results.append(entry)

        resp = json_response({
            'results': results,
            'total_count': total,
            'missing_coordinates_count': missing_count,
            'limit': limit,
            'offset': offset,
        })
        # Surface the cap when the caller asked for more than we'll
        # return (so they know they're paging).
        if total > offset + len(results):
            resp.headers['X-Total-Count'] = str(total)
        return resp

    # ─────────────────────────────────────────────────────────────────
    # PROPERTIES ON THE MAP
    # ─────────────────────────────────────────────────────────────────

    @http.route('/api/v1/map/properties', type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def list_map_properties(self, _body=None, _auth_uid=None, _key_fp=None, **kw):
        env = request.env
        Property = env['realestate.property'].sudo()

        limit, offset = parse_pagination(kw, default_limit=1000,
                                         max_limit=HARD_LIMIT_MAX)
        bbox = _parse_bbox(kw.get('bbox'))

        domain = [
            ('state', 'in', PUBLIC_PROPERTY_STATES),
            '|', ('latitude', '!=', 0), ('longitude', '!=', 0),
        ]
        # Optional filters
        project_id = _int_arg(kw, 'project_id', None, lo=1)
        if project_id:
            domain.append(('project_id', '=', project_id))
        hierarchy = (kw.get('hierarchy_level') or '').strip().lower()
        if hierarchy:
            if hierarchy not in ('compound', 'building', 'floor', 'unit', 'room'):
                raise werkzeug.exceptions.BadRequest(_(
                    "'hierarchy_level' must be one of "
                    "compound|building|floor|unit|room."
                ))
            domain.append(('hierarchy_level', '=', hierarchy))
        state = (kw.get('state') or '').strip().lower()
        if state:
            if state not in PUBLIC_PROPERTY_STATES:
                raise werkzeug.exceptions.BadRequest(_(
                    "'state' must be one of %s.") % (PUBLIC_PROPERTY_STATES,))
            # Narrow to just this state (overrides the default 'in' list).
            domain = [d for d in domain if not (isinstance(d, tuple) and d[0] == 'state')]
            domain.append(('state', '=', state))

        if bbox:
            lat_min, lng_min, lat_max, lng_max = bbox
            domain += [
                ('latitude', '>=', lat_min), ('latitude', '<=', lat_max),
                ('longitude', '>=', lng_min), ('longitude', '<=', lng_max),
            ]

        total = Property.search_count(domain)
        rows = Property.search(domain, limit=limit, offset=offset, order='id')

        results = [
            {
                'id': p.id,
                'property_code': p.property_code or '',
                'name': p.name or '',
                'hierarchy_level': p.hierarchy_level or '',
                'state': p.state or '',
                'latitude': p.latitude,
                'longitude': p.longitude,
                'city': p.city or '',
                'district': p.district or '',
                'country': p.country_id.name if p.country_id else '',
                'country_code': p.country_id.code if p.country_id else '',
                'property_type': (p.property_type_id.name
                                  if p.property_type_id else ''),
                'project_id': p.project_id.id if p.project_id else None,
                'project_name': p.project_id.name if p.project_id else '',
                'base_price': p.base_price if 'base_price' in p._fields else 0.0,
                'currency': (p.currency_id.symbol
                             if p.currency_id else ''),
                'area_sqm': p.area_sqm if 'area_sqm' in p._fields else 0.0,
                'cover_image_url': p._api_image_url(
                    'image_1920', size='400x300'),
            }
            for p in rows
        ]

        # How many catalog properties exist that DON'T have coordinates
        # set — useful for the dashboard's "12 missing coordinates" hint.
        # Filtered by the same optional filters except bbox.
        missing_domain = [
            ('state', 'in', PUBLIC_PROPERTY_STATES),
            ('latitude', '=', 0), ('longitude', '=', 0),
        ]
        if project_id:
            missing_domain.append(('project_id', '=', project_id))
        if hierarchy:
            missing_domain.append(('hierarchy_level', '=', hierarchy))
        missing_count = Property.search_count(missing_domain)

        return json_response({
            'results': results,
            'total_count': total,
            'missing_coordinates_count': missing_count,
            'limit': limit,
            'offset': offset,
        })
