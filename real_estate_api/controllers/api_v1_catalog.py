"""Public catalog REST endpoints.

URL map (read-only, anonymous unless noted)::

    GET  /api/v1/developers
    GET  /api/v1/developers/<int:dev_id>
    GET  /api/v1/projects
    GET  /api/v1/developers/<int:dev_id>/projects
    GET  /api/v1/projects/<int:project_id>
    GET  /api/v1/projects/<int:project_id>/buildings
    GET  /api/v1/projects/<int:project_id>/plan-2d
    GET  /api/v1/projects/<int:project_id>/maquette-3d
    GET  /api/v1/properties/<int:property_id>
    GET  /api/v1/properties/<int:property_id>/plan-2d
    GET  /api/v1/buildings/<int:building_id>/units
    GET  /api/v1/units/<int:unit_id>

All listing endpoints honour ``?limit=`` (max 100) and ``?offset=``,
and return an ``X-Total-Count`` header.
"""

import werkzeug.exceptions

from odoo import _, http
from odoo.http import request

from ._base import (
    json_endpoint,
    json_response,
    not_found_if_missing,
    parse_pagination,
)


class CatalogApiV1(http.Controller):

    # ---- developers ------------------------------------------------------

    @http.route('/api/v1/developers', type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def list_developers(self, **kw):
        limit, offset = parse_pagination(kw)
        env = request.env

        # A developer = a partner who is the developer_id of at least one
        # publicly-listed project. We compute that set via a sub-query.
        Project = env['realestate.project'].sudo()
        project_developer_ids = Project.search(
            Project._api_public_domain()
        ).mapped('developer_id').ids
        if not project_developer_ids:
            return json_response({'results': [], 'limit': limit, 'offset': offset},
                                 headers={'X-Total-Count': '0'})

        Partner = env['res.partner'].sudo()
        domain = [('id', 'in', project_developer_ids)]
        total = Partner.search_count(domain)
        partners = Partner.search(domain, limit=limit, offset=offset, order='name')
        return json_response(
            {
                'results': [p._to_api_dict_developer_v1('summary') for p in partners],
                'limit': limit, 'offset': offset,
            },
            headers={'X-Total-Count': str(total)},
        )

    @http.route('/api/v1/developers/<int:dev_id>', type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def get_developer(self, dev_id, **kw):
        env = request.env
        # Confirm the partner is genuinely a developer (has projects).
        partner = env['res.partner'].sudo().browse(dev_id)
        not_found_if_missing(partner, 'developer')
        Project = env['realestate.project'].sudo()
        has_project = Project.search_count([
            ('developer_id', '=', partner.id),
            ('state', 'in', Project._api_public_states()),
        ])
        if not has_project:
            raise werkzeug.exceptions.NotFound(_("Developer not found."))
        return json_response(partner._to_api_dict_developer_v1('detail'))

    @http.route('/api/v1/developers/<int:dev_id>/projects', type='http',
                auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def list_developer_projects(self, dev_id, **kw):
        limit, offset = parse_pagination(kw)
        env = request.env
        Project = env['realestate.project'].sudo()
        domain = Project._api_public_domain() + [('developer_id', '=', dev_id)]
        total = Project.search_count(domain)
        records = Project.search(domain, limit=limit, offset=offset, order='name')
        return json_response(
            {'results': [r._to_api_dict_v1('summary') for r in records],
             'limit': limit, 'offset': offset},
            headers={'X-Total-Count': str(total)},
        )

    # ---- projects --------------------------------------------------------

    @http.route('/api/v1/projects', type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def list_projects(self, **kw):
        limit, offset = parse_pagination(kw)
        env = request.env
        Project = env['realestate.project'].sudo()
        domain = list(Project._api_public_domain())

        # Whitelisted filters — raw domains in query string would be an
        # ORM injection risk.
        if kw.get('city'):
            domain.append(('city', 'ilike', kw['city'][:60]))
        if kw.get('country'):
            domain.append(('country_id.code', '=', kw['country'][:4].upper()))
        if kw.get('developer_id'):
            try:
                domain.append(('developer_id', '=', int(kw['developer_id'])))
            except (TypeError, ValueError):
                raise werkzeug.exceptions.BadRequest(_("developer_id must be int."))
        if kw.get('q'):
            domain.append(('name', 'ilike', kw['q'][:80]))

        total = Project.search_count(domain)
        records = Project.search(domain, limit=limit, offset=offset, order='name')
        return json_response(
            {'results': [r._to_api_dict_v1('summary') for r in records],
             'limit': limit, 'offset': offset},
            headers={'X-Total-Count': str(total)},
        )

    @http.route('/api/v1/projects/<int:project_id>', type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def get_project(self, project_id, **kw):
        Project = request.env['realestate.project'].sudo()
        project = Project.browse(project_id)
        not_found_if_missing(project, 'project')
        if project.state not in Project._api_public_states():
            raise werkzeug.exceptions.NotFound(_("Project not found."))
        return json_response(project._to_api_dict_v1('detail'))

    # ---- buildings / units ----------------------------------------------

    @http.route('/api/v1/projects/<int:project_id>/buildings', type='http',
                auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def list_project_buildings(self, project_id, **kw):
        limit, offset = parse_pagination(kw)
        env = request.env
        # Make sure the project itself is public-visible.
        Project = env['realestate.project'].sudo()
        project = Project.browse(project_id)
        not_found_if_missing(project, 'project')
        if project.state not in Project._api_public_states():
            raise werkzeug.exceptions.NotFound(_("Project not found."))

        Property = env['realestate.property'].sudo()
        domain = [
            ('project_id', '=', project_id),
            ('hierarchy_level', '=', 'building'),
            ('state', 'in', ['available', 'reserved', 'sold']),
        ]
        total = Property.search_count(domain)
        records = Property.search(domain, limit=limit, offset=offset,
                                  order='property_code')
        return json_response(
            {'results': [r._to_api_dict_v1('summary') for r in records],
             'limit': limit, 'offset': offset},
            headers={'X-Total-Count': str(total)},
        )

    @http.route('/api/v1/buildings/<int:building_id>/units', type='http',
                auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def list_building_units(self, building_id, **kw):
        limit, offset = parse_pagination(kw)
        env = request.env
        building = env['realestate.property'].sudo().browse(building_id)
        not_found_if_missing(building, 'building')
        if building.hierarchy_level != 'building':
            raise werkzeug.exceptions.NotFound(_("Building not found."))

        Property = env['realestate.property'].sudo()
        # All descendants that are units or rooms (skip intermediate floors
        # — they're not consumer-facing).
        domain = [
            ('id', 'child_of', building_id),
            ('id', '!=', building_id),
            ('hierarchy_level', 'in', ('unit', 'room')),
            ('state', 'in', ('available', 'reserved', 'sold')),
        ]
        if kw.get('status'):
            # Whitelisted status filter on the public-facing label.
            from ..models.realestate_property import PUBLIC_SALE_STATUS
            reverse = {v: [k for k, vv in PUBLIC_SALE_STATUS.items() if vv == v]
                       for v in set(PUBLIC_SALE_STATUS.values())}
            if kw['status'] not in reverse:
                raise werkzeug.exceptions.BadRequest(_("Unknown status filter."))
            domain.append(('sale_status', 'in', reverse[kw['status']]))
        if kw.get('min_bedrooms'):
            try:
                domain.append(('bedroom_count', '>=', int(kw['min_bedrooms'])))
            except (TypeError, ValueError):
                raise werkzeug.exceptions.BadRequest(
                    _("min_bedrooms must be an integer."))

        total = Property.search_count(domain)
        records = Property.search(domain, limit=limit, offset=offset,
                                  order='floor_number, property_code')
        return json_response(
            {'results': [r._to_api_dict_v1('summary') for r in records],
             'limit': limit, 'offset': offset},
            headers={'X-Total-Count': str(total)},
        )

    @http.route('/api/v1/units/<int:unit_id>', type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def get_unit(self, unit_id, **kw):
        unit = request.env['realestate.property'].sudo().browse(unit_id)
        not_found_if_missing(unit, 'unit')
        if unit.hierarchy_level not in ('unit', 'room'):
            raise werkzeug.exceptions.NotFound(_("Unit not found."))
        if unit.state not in ('available', 'reserved', 'sold'):
            raise werkzeug.exceptions.NotFound(_("Unit not found."))
        return json_response(unit._to_api_dict_v1('detail'))

    @http.route('/api/v1/properties/<int:property_id>', type='http',
                auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def get_property(self, property_id, **kw):
        prop = request.env['realestate.property'].sudo().browse(property_id)
        not_found_if_missing(prop, 'property')
        if prop.state not in ('available', 'reserved', 'sold'):
            raise werkzeug.exceptions.NotFound(_("Property not found."))
        return json_response(prop._to_api_dict_v1('detail'))

    # ---- maps (2D) -------------------------------------------------------

    @http.route('/api/v1/projects/<int:project_id>/plan-2d', type='http',
                auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def get_project_plan_2d(self, project_id, **kw):
        project = request.env['realestate.project'].sudo().browse(project_id)
        not_found_if_missing(project, 'project')
        tree = project._to_api_plan_2d_v1()
        if tree is None:
            raise werkzeug.exceptions.NotFound(
                _("This project has no 2D plan configured.")
            )
        return json_response(tree)

    @http.route('/api/v1/properties/<int:property_id>/plan-2d', type='http',
                auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def get_property_plan_2d(self, property_id, **kw):
        prop = request.env['realestate.property'].sudo().browse(property_id)
        not_found_if_missing(prop, 'property')
        tree = prop._to_api_plan_2d_v1()
        if tree is None:
            raise werkzeug.exceptions.NotFound(
                _("This property has no 2D plan image.")
            )
        return json_response(tree)

    # ---- maquette (3D) ---------------------------------------------------

    @http.route('/api/v1/projects/<int:project_id>/maquette-3d', type='http',
                auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @json_endpoint(methods=['GET'])
    def get_project_maquette_3d(self, project_id, **kw):
        project = request.env['realestate.project'].sudo().browse(project_id)
        not_found_if_missing(project, 'project')
        descriptor = project._to_api_maquette_3d_v1()
        if descriptor is None:
            raise werkzeug.exceptions.NotFound(
                _("This project has no 3D maquette uploaded.")
            )
        return json_response(descriptor)
