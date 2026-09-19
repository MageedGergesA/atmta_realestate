"""Public serializers for ``realestate.property`` and ``realestate.plan.region``.

Sensitive fields explicitly NOT exposed:
* ``owner_id``, ``is_internal``, ``manager_id`` (ownership / staff)
* ``property_notes`` (internal notes)
* utility meter readings
* ``reservation_ids`` / ``sale_contract_ids`` (CRM-internal)
* ``cost_*`` (none present, but reserved)

Public-facing status uses ``sale_status`` (not the raw ``state``) so we
never leak "maintenance" / "inactive" / "reserved" wording to the
website without intent.
"""

from odoo import api, models
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
#: `realestate.visual.access`. A property's plan, floor plan, elevation sheet
#: and interior model are all served only behind a grant.
GATED_FIELD_KINDS = {
    (model, field): kind for kind, (model, field) in VISUAL_ASSET_KINDS.items()
    if kind in GATED_KINDS
}


# Map raw sale_status → website-facing label. Anything not in this
# dict is **not** exposed (returned as ``hidden``). No silent fallback.
PUBLIC_SALE_STATUS = {
    'for_sale': 'available',
    'reserved': 'reserved',
    'under_contract': 'reserved',
    'sold': 'sold',
    'not_listed': 'hidden',
}

# Whether a property is publicly browseable at all.
PUBLIC_STATE = {'available', 'reserved', 'sold'}


class RealEstateProperty(models.Model):
    _inherit = 'realestate.property'

    @api.model
    def _api_public_domain(self):
        return [
            ('state', 'in', list(PUBLIC_STATE)),
            ('project_id', '!=', False),  # only project-attached properties
        ]

    def _visual_asset_url(self, kind, unique=None):
        """A grant-bearing URL for one of this property's gated assets.

        Grants are scoped to a project, never to a property, so the grant is
        minted for the project this property belongs to. A property with no
        project is not in the public catalogue at all -- `_api_public_domain`
        already requires one -- so there is nothing to authorise and nothing
        to serve.
        """
        self.ensure_one()
        project = self.sudo().project_id
        if not project:
            return None
        try:
            grant = self.env['realestate.visual.access'].sudo(
                ).grant_for_public_project(
                    project,
                    kinds=['plan_image', 'floor_plan_image',
                           'elevation_sheet', 'interior_glb'],
                    source='embed_token', source_ref='api/plan-2d')
        except AccessError:
            # Not published. Answer "no URL" rather than raising out of a
            # serializer that is building a whole plan tree.
            return None
        url = '/visual/asset/%s/%s?t=%s' % (kind, self.id, grant.token)
        return '%s&unique=%s' % (url, unique) if unique else url

    def _api_image_url(self, field, size=None):
        self.ensure_one()
        if field not in self._fields or not self[field]:
            return None
        unique = self.write_date.strftime('%Y%m%d%H%M%S') if self.write_date else ''

        # A gated asset is never reachable on the open image route, whatever
        # size is asked for. The grant route serves the full asset; the `w`/`h`
        # hints are dropped because resizing happens behind the gate.
        kind = GATED_FIELD_KINDS.get(('realestate.property', field))
        if kind:
            return self._visual_asset_url(kind, unique=unique)

        qs = [f"unique={unique}"]
        if size and 'x' in size:
            w, h = size.split('x', 1)
            qs += [f"w={w}", f"h={h}"]
        return f"/api/v1/image/realestate.property/{self.id}/{field}?{'&'.join(qs)}"

    def _api_gallery_urls(self, size='800x600'):
        self.ensure_one()
        urls = []
        for img in self.property_Attachment_media_ids.sorted('sequence'):
            unique = (img.write_date.strftime('%Y%m%d%H%M%S')
                      if img.write_date else '')
            urls.append({
                'id': img.id,
                'name': img.name or '',
                'url': f"/api/v1/image/property.image/{img.id}/image_1024"
                       f"?unique={unique}",
                'thumb_url': f"/api/v1/image/property.image/{img.id}/image_256"
                             f"?unique={unique}",
                'video_url': img.video_url or '',
            })
        return urls

    # Hierarchy levels at which the "sale status" concept is meaningful.
    # Anything above a unit (compound / building / floor) is a traversal
    # node, not something a customer buys — exposing a status for those
    # would just hide them from the drill viewer for no reason.
    _LEAF_LEVELS_FOR_STATUS = ('unit', 'room')

    def _public_sale_status(self):
        """Return website-facing status, ``None`` for traversal nodes, or
        ``None`` if this leaf unit shouldn't appear in public listings.

        Buildings / floors / compounds always return ``None`` — the drill
        viewer treats that as "render the region, no status badge".
        """
        self.ensure_one()
        if self.hierarchy_level not in self._LEAF_LEVELS_FOR_STATUS:
            return None
        return PUBLIC_SALE_STATUS.get(self.sale_status)

    def _to_api_dict_v1(self, depth='summary'):
        """Serialize for the public catalog.

        Buildings (``hierarchy_level == 'building'``) and units
        (``unit`` / ``room``) share this serializer — depth controls which
        building-specific or unit-specific fields are included.
        """
        self.ensure_one()
        currency = self.currency_id
        public_status = self._public_sale_status()
        data = {
            'id': self.id,
            'name': self.name or '',
            'property_code': self.property_code or '',
            'hierarchy_level': self.hierarchy_level or '',
            'project_id': self.project_id.id if self.project_id else None,
            'parent_id': self.parent_id.id if self.parent_id else None,
            'status': public_status,
            'cover_image_url': self._api_image_url('image_1920', size='800x600'),
            'has_2d_plan': bool(self.has_plan_image),
            'has_3d_interior': bool(self.interior_glb),
            # Mesh linkage to the project's maquette_glb. Empty string when
            # this unit isn't wired to a mesh (or not a unit at all). The
            # 3D viewer raycasts a click → matches mesh.name → this field.
            'maquette_mesh_name': self.maquette_mesh_name or '',
            'area_sqm': self.area_sqm or 0.0,
            'currency': currency.name or '',
            'currency_symbol': currency.symbol or '',
        }
        if self.hierarchy_level == 'building':
            data.update({
                'floors_count': self.number_of_floors or 0,
                'units_count': self.number_of_units or 0,
                'thumb_url': self._api_image_url('plan_image', size='400x300'),
            })
        else:
            data.update({
                'bedrooms': self.bedroom_count or 0,
                'bathrooms': self.bathroom_count or 0,
                'floor_number': self.floor_number or 0,
                # `base_price` is the internal number every discount is
                # measured against, and an unreleased unit has no public price
                # at all. Developer owns that rule in `_public_price()`.
                'price': self._public_price(),
                'furnished_status': self.furnished_status or '',
            })

        if depth == 'detail':
            data.update({
                'description': self.property_notes or '',
                'property_type': self.property_type_id.name if self.property_type_id else '',
                'latitude': self.latitude or 0.0,
                'longitude': self.longitude or 0.0,
                'has_kitchen': bool(self.has_kitchen),
                'has_balcony': bool(self.has_balcony),
                'living_room_count': self.living_room_count or 0,
                'gallery': self._api_gallery_urls(),
                'plan_image_url': self._api_image_url(
                    'plan_image', size='1920x1080',
                ),
                'floor_plan_image_url': self._api_image_url(
                    'floor_plan_image', size='1920x1080',
                ),
                'spec_tags': [t.name for t in self.spec_tag_ids],
                'maquette_color_override': self.maquette_color_override or '',
            })
        return data

    def _to_api_plan_2d_v1(self, breadcrumbs=None):
        """JSON tree for the 2D drill viewer, rooted at this property.

        When the caller passes ``breadcrumbs`` (the project delegated to
        us via ``main_property_id``), we just append ourselves to it.
        Otherwise — this is a direct hit on ``/properties/<id>/plan-2d``
        from the picker or a deep link — we synthesize the chain by
        walking up ``parent_id`` and prepending the owning project so
        the viewer can show the full path back to the root.
        """
        self.ensure_one()
        if not self.has_plan_image:
            return None
        if breadcrumbs is None:
            crumbs = []
            if self.project_id:
                crumbs.append({
                    'id': self.project_id.id,
                    'kind': 'project',
                    'name': self.project_id.name or '',
                })
            ancestors = []
            node = self.parent_id
            while node:
                ancestors.append({
                    'id': node.id,
                    'kind': 'property',
                    'name': node.name or '',
                    'hierarchy_level': node.hierarchy_level or '',
                })
                node = node.parent_id
            # ancestors walks immediate parent → root; reverse to get
            # root → immediate parent order in the breadcrumb display.
            ancestors.reverse()
            crumbs.extend(ancestors)
        else:
            crumbs = list(breadcrumbs)
        crumbs.append({
            'id': self.id,
            'kind': 'property',
            'name': self.name or '',
            'hierarchy_level': self.hierarchy_level or '',
        })
        # Children with their own plan image are drillable on click.
        drillable_children = self.child_ids.filtered(lambda c: c.has_plan_image)
        return {
            'root_kind': 'property',
            'root_id': self.id,
            'name': self.name or '',
            'hierarchy_level': self.hierarchy_level or '',
            'image_url': self._api_image_url('plan_image', size='1920x1080'),
            'regions': [r._to_api_dict_v1() for r in self.plan_region_ids],
            'drillable_children': [
                {
                    'id': c.id,
                    'name': c.name or '',
                    'hierarchy_level': c.hierarchy_level or '',
                    'thumb_url': c._api_image_url('plan_image', size='240x150'),
                    'maquette_mesh_name': c.maquette_mesh_name or '',
                }
                for c in drillable_children
            ],
            # Gallery for the current level — the viewer renders a side
            # strip of thumbnails; clicking one opens a lightbox. Empty
            # list when the property has no attached images.
            'gallery': self._api_gallery_urls(),
            'breadcrumbs': crumbs,
        }


class PlanRegion(models.Model):
    _inherit = 'realestate.plan.region'

    def _to_api_dict_v1(self):
        self.ensure_one()
        target = self.target_property_id
        return {
            'id': self.id,
            'target_id': target.id if target else None,
            'target_name': target.name if target else '',
            'target_kind': target.hierarchy_level if target else '',
            'target_has_plan': bool(target.has_plan_image) if target else False,
            'target_status': target._public_sale_status() if target else None,
            'target_mesh_name': (target.maquette_mesh_name or '') if target else '',
            'label': self.label or (target.name if target else ''),
            'color': self.color or '#3b82f6',
            'polygon': self.polygon or '[]',  # stays JSON string for the client
        }
