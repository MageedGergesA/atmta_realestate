"""Public serializer for ``realestate.project``.

Sensitive fields explicitly NOT exposed:
* ``expected_budget`` / ``expected_revenue`` (commercial)
* ``notes`` (internal Html)
* ``manager_id`` (internal user)
* ``state == 'cancelled'`` records are hidden from the public catalog
"""

from odoo import api, models


# Public-facing status mapping. Anything not in this map is hidden from
# the public catalog — no silent fallback, no "guess a status".
PUBLIC_PROJECT_STATUS = {
    'planning': 'planned',
    'construction': 'under_construction',
    'marketing': 'selling',
    'handover': 'handover',
    'completed': 'completed',
}


class RealEstateProject(models.Model):
    _inherit = 'realestate.project'

    @api.model
    def _api_public_states(self):
        return list(PUBLIC_PROJECT_STATUS.keys())

    @api.model
    def _api_public_domain(self):
        """Domain restricting the catalog to publicly visible projects."""
        return [('state', 'in', self._api_public_states())]

    def _api_image_url(self, field, size=None):
        """Build a public image URL.

        Returns ``None`` when the field is empty — never a placeholder URL.
        Cache-busting via ``unique=write_date``. URLs point at our public
        ``/api/v1/image/...`` proxy so anonymous visitors don't get
        Odoo's stock placeholder PNG (which is what ``/web/image`` serves
        when the parent record isn't readable by the public user).
        """
        self.ensure_one()
        has_image = bool(self[field]) if field in self._fields else False
        if not has_image:
            return None
        unique = self.write_date.strftime('%Y%m%d%H%M%S') if self.write_date else ''
        qs = [f"unique={unique}"]
        if size and 'x' in size:
            w, h = size.split('x', 1)
            qs += [f"w={w}", f"h={h}"]
        return f"/api/v1/image/realestate.project/{self.id}/{field}?{'&'.join(qs)}"

    def _to_api_dict_v1(self, depth='summary'):
        """Serialize one project. Caller passes ``depth='detail'`` for the
        single-record endpoint, ``'summary'`` for lists.
        """
        self.ensure_one()
        public_status = PUBLIC_PROJECT_STATUS.get(self.state)
        # Currency: emit the project's native currency code, plus base_price
        # range in that currency. The website is expected to convert if it
        # needs another display currency.
        currency_name = self.currency_id.name or ''
        currency_symbol = self.currency_id.symbol or ''

        # Price range across publicly-listed units in this project.
        Property = self.env['realestate.property'].sudo()
        unit_prices = Property.search([
            ('project_id', '=', self.id),
            ('hierarchy_level', 'in', ('unit', 'room')),
            ('base_price', '>', 0),
        ]).mapped('base_price')
        price_min = min(unit_prices) if unit_prices else 0.0
        price_max = max(unit_prices) if unit_prices else 0.0

        data = {
            'id': self.id,
            'code': self.code or '',
            'name': self.name or '',
            'project_type': self.project_type or '',
            'status': public_status,
            'developer_id': self.developer_id.id if self.developer_id else None,
            'developer_name': self.developer_id.name if self.developer_id else '',
            'city': self.city or '',
            'district': self.district or '',
            'country': self.country_id.name if self.country_id else '',
            'cover_image_url': self._api_image_url('master_plan_2d', size='1280x720'),
            'has_2d_plan': bool(self.has_master_plan_2d or self.main_property_id),
            'has_3d_maquette': bool(self.has_maquette),
            'unit_count': self.unit_count,
            'available_unit_count': self.available_unit_count,
            'starting_price': price_min,
            'currency': currency_name,
            'currency_symbol': currency_symbol,
        }
        if depth == 'detail':
            data.update({
                'description_html': self.description or '',
                'address_line': self.address_line or '',
                'latitude': self.latitude or 0.0,
                'longitude': self.longitude or 0.0,
                'start_date': self.start_date.isoformat() if self.start_date else None,
                'expected_completion_date': (
                    self.expected_completion_date.isoformat()
                    if self.expected_completion_date else None
                ),
                'total_land_area_sqm': self.total_land_area or 0.0,
                'total_built_up_area_sqm': self.total_built_up_area or 0.0,
                'reserved_unit_count': self.reserved_unit_count,
                'sold_unit_count': self.sold_unit_count,
                'price_max': price_max,
                'boundary_polygon': [
                    {'sequence': p.sequence,
                     'latitude': p.latitude,
                     'longitude': p.longitude}
                    for p in self.boundary_point_ids.sorted('sequence')
                ],
            })
        return data

    def _to_api_plan_2d_v1(self):
        """JSON tree for the 2D drill viewer.

        Returns the entry node (project's master plan OR ``main_property_id``)
        with its regions + immediate child stubs. Deeper levels are fetched
        on demand by the viewer via /properties/<id>/plan-2d.
        """
        self.ensure_one()
        # If the project has its own master plan, that's the root. Otherwise
        # we delegate to the configured ``main_property_id``.
        if self.has_master_plan_2d:
            return {
                'root_kind': 'project',
                'root_id': self.id,
                'name': self.name or '',
                'image_url': self._api_image_url('master_plan_2d', size='1920x1080'),
                'regions': [self._building_region_to_api_dict(r)
                            for r in self.region_ids],
                'breadcrumbs': [{'id': self.id, 'kind': 'project', 'name': self.name}],
            }
        if self.main_property_id:
            return self.main_property_id._to_api_plan_2d_v1(
                breadcrumbs=[{'id': self.id, 'kind': 'project', 'name': self.name}]
            )
        # No silent fallback: caller (controller) decides what to do.
        return None

    @api.model
    def _building_region_to_api_dict(self, region):
        """Serialize one ``realestate.building.region`` (master-plan region).

        The master-plan region model lives in ``real_estate_maquette`` and
        targets ``property_id`` (not ``target_property_id`` like the
        sub-property plan regions). We serialize it inline rather than
        inheriting the model for one method.
        """
        target = region.property_id
        return {
            'id': region.id,
            'target_id': target.id if target else None,
            'target_name': target.name if target else '',
            'target_kind': target.hierarchy_level if target else '',
            'target_has_plan': bool(target.has_plan_image) if target else False,
            'target_status': target._public_sale_status() if target else None,
            'label': region.label or (target.name if target else ''),
            'color': region.color or '#3b82f6',
            'polygon': region.polygon or '[]',
        }

    def _to_api_maquette_3d_v1(self):
        """Descriptor the 3D viewer needs to render the GLB scene."""
        self.ensure_one()
        if not self.has_maquette:
            return None
        unique = (self.write_date.strftime('%Y%m%d%H%M%S')
                  if self.write_date else '')
        return {
            'project_id': self.id,
            'name': self.name or '',
            'glb_url': (f"/api/v1/image/realestate.project/{self.id}/maquette_glb"
                        f"?unique={unique}"),
            'glb_filename': self.maquette_glb_filename or 'maquette.glb',
            'env_hdr_url': (
                f"/api/v1/image/realestate.project/{self.id}/maquette_env_hdr"
                f"?unique={unique}"
                if self.maquette_env_hdr else None
            ),
            'default_camera': self.maquette_default_camera or '',
            'units': self.get_maquette_units_data(),
        }
