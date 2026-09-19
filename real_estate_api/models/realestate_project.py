"""Public serializer for ``realestate.project``.

Sensitive fields explicitly NOT exposed:
* ``expected_budget`` / ``expected_revenue`` (commercial)
* ``notes`` (internal Html)
* ``manager_id`` (internal user)
* ``state == 'cancelled'`` records are hidden from the public catalog
"""

from odoo import api, fields, models
from odoo.exceptions import AccessError

from odoo.addons.real_estate_maquette.models.visual_access import (
    VISUAL_ASSET_KINDS,
)

#: The kinds that actually moved behind the grant, matching
#: `VISUAL_ASSET_REDIRECT` in `real_estate_api.controllers.api_v1_image`.
#: Deliberately not every key of `VISUAL_ASSET_KINDS`: `unit_image` and
#: `gallery_image` describe ordinary marketing photos that are still served
#: on the open image route, and gating them here would 404 every unit photo
#: in the public catalogue.
GATED_KINDS = (
    'maquette_glb', 'maquette_hdr', 'master_plan_2d',
    'plan_image', 'floor_plan_image', 'elevation_sheet', 'interior_glb',
)

#: (model, field) -> gated asset kind, inverted from the authoritative map in
#: `realestate.visual.access` rather than restated here. Every field in it is
#: served only by `/visual/asset/...` behind a grant, so a serializer that
#: hands out a bare `/api/v1/image/...` URL for one of them produces a link
#: that 404s.
GATED_FIELD_KINDS = {
    (model, field): kind for kind, (model, field) in VISUAL_ASSET_KINDS.items()
    if kind in GATED_KINDS
}


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

    # True when the /api/v1/projects/<id>/plan-2d endpoint has anything to
    # show — including "picker" mode where the project has no master plan
    # image of its own but has top-level properties with plan images the
    # user can drill into. Stored so the list endpoint doesn't run one
    # sub-search per row.
    has_drillable_2d = fields.Boolean(
        compute='_compute_has_drillable_2d', store=True, index=True,
    )

    @api.depends('has_master_plan_2d', 'main_property_id',
                 'property_ids.has_plan_image', 'property_ids.parent_id')
    def _compute_has_drillable_2d(self):
        for proj in self:
            if proj.has_master_plan_2d or proj.main_property_id:
                proj.has_drillable_2d = True
                continue
            proj.has_drillable_2d = bool(proj.property_ids.filtered(
                lambda p: not p.parent_id and p.has_plan_image
            ))

    @api.model
    def _api_public_states(self):
        return list(PUBLIC_PROJECT_STATUS.keys())

    @api.model
    def _api_public_domain(self):
        """Domain restricting the catalog to publicly visible projects."""
        return [('state', 'in', self._api_public_states())]

    def _visual_asset_url(self, kind, record_id, unique=None):
        """A grant-bearing URL for a visual asset.

        The descriptor used to hand out `/api/v1/image/...` URLs that needed no
        authorisation at all. It now mints one grant per descriptor and scopes
        every URL to it, so the URLs expire and cannot be edited into a
        different project's.
        """
        try:
            grant = self.env['realestate.visual.access'].sudo(
                ).grant_for_public_project(
                    self,
                    kinds=['maquette_glb', 'maquette_hdr', 'master_plan_2d',
                           'plan_image', 'floor_plan_image', 'elevation_sheet',
                           'interior_glb'],
                    source='embed_token', source_ref='api/maquette-3d')
        except AccessError:
            # The project is not published. A serializer must answer "there is
            # no URL", not raise: the route's own contract is a 404, and the
            # six descriptors that call this build whole payloads around it.
            return None
        url = '/visual/asset/%s/%s?t=%s' % (kind, record_id, grant.token)
        return '%s&unique=%s' % (url, unique) if unique else url

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

        # A gated asset is never reachable on the open image route, whatever
        # size is asked for. Mint the grant and hand back the gated URL.
        kind = GATED_FIELD_KINDS.get(('realestate.project', field))
        if kind:
            return self._visual_asset_url(kind, self.id, unique=unique)

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

        # Price range across the units actually on the market. Units that are
        # unreleased, blocked or committed have no public price, and folding
        # their internal `base_price` into the range would publish it anyway.
        Property = self.env['realestate.property'].sudo()
        unit_prices = [
            price for price in (
                unit._public_price() for unit in Property.search([
                    ('project_id', '=', self.id),
                    ('is_available_for_sale', '=', True),
                ]))
            if price > 0
        ]
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
            'has_2d_plan': self.has_drillable_2d,
            'has_3d_maquette': bool(self.has_maquette),
            # 3D mesh linkage — lets callers distinguish "GLB uploaded but
            # nothing clickable yet" from "fully interactive scene" without
            # a second round-trip to /maquette-3d.
            'maquette_status': self.maquette_status or 'not_uploaded',
            'maquette_unit_count': self.maquette_unit_count or 0,
            'maquette_total_units': self.maquette_total_units or 0,
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

        Three shapes, depending on how the project is configured:

        1. ``has_master_plan_2d``: the project's image is the root
           level; its regions overlay it; drill continues by region click.
        2. ``main_property_id`` set: delegate straight to that property's
           own ``_to_api_plan_2d_v1`` — the project becomes a breadcrumb
           ancestor with no level of its own.
        3. Neither: return a **picker tree** — no image, but a list of
           the project's top-level properties so the viewer can render
           cards for the user to pick where to start drilling.

        Returns ``None`` only when even the picker would be empty (no
        top-level property has a plan image and no master plan / main
        property is configured). The controller maps that to a strict
        404 — no silent fallback.
        """
        self.ensure_one()
        if self.has_master_plan_2d:
            return {
                'root_kind': 'project',
                'root_id': self.id,
                'name': self.name or '',
                'image_url': self._api_image_url('master_plan_2d', size='1920x1080'),
                'regions': [self._building_region_to_api_dict(r)
                            for r in self.region_ids],
                # Project level has no image gallery; keep the field present
                # so the viewer's render path doesn't need a conditional.
                'gallery': [],
                'breadcrumbs': [{'id': self.id, 'kind': 'project', 'name': self.name}],
            }
        if self.main_property_id:
            return self.main_property_id._to_api_plan_2d_v1(
                breadcrumbs=[{'id': self.id, 'kind': 'project', 'name': self.name}]
            )

        # Picker mode: surface any top-level property that has its own
        # plan image so the user can click one card to enter the drill.
        Property = self.env['realestate.property'].sudo()
        top_props = Property.search([
            ('project_id', '=', self.id),
            ('parent_id', '=', False),
            ('has_plan_image', '=', True),
        ])
        if not top_props:
            return None
        return {
            'root_kind': 'project',
            'root_id': self.id,
            'name': self.name or '',
            'image_url': None,                              # no image at this level
            'regions': [],
            'gallery': [],
            'drillable_children': [
                {
                    'id': p.id,
                    'name': p.name or '',
                    'hierarchy_level': p.hierarchy_level or '',
                    'thumb_url': p._api_image_url('plan_image', size='400x300'),
                    'maquette_mesh_name': p.maquette_mesh_name or '',
                }
                for p in top_props
            ],
            'is_picker': True,
            'breadcrumbs': [{'id': self.id, 'kind': 'project', 'name': self.name}],
        }

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
            'target_mesh_name': (target.maquette_mesh_name or '') if target else '',
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
            'glb_url': self._visual_asset_url('maquette_glb', self.id, unique),
            'glb_filename': self.maquette_glb_filename or 'maquette.glb',
            'env_hdr_url': (
                self._visual_asset_url('maquette_hdr', self.id, unique)
                if self.maquette_env_hdr else None
            ),
            'default_camera': self.maquette_default_camera or '',
            'units': self.get_maquette_units_data(audience='public'),
        }
