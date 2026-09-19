import json

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BuildingRegion(models.Model):
    """One clickable polygon overlay on a project's 2D master plan, tied to a
    building (a property with hierarchy_level='building'). Coordinates are
    stored as percentages of the image so they survive any display size."""
    _name = 'realestate.building.region'
    _description = '2D Master Plan Building Region'

    _order = 'project_id, sequence, id'

    sequence = fields.Integer(default=10)
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        related='project_id.company_id', store=True, index=True, readonly=True,
        help="Stored related, so a region can never disagree with the project "
             "it is drawn on. Phase 0 found no company field on any visual "
             "model and no record rule anywhere.")
    property_id = fields.Many2one(
        'realestate.property', string='Property',
        required=True, ondelete='cascade',
        domain="['|', ('project_id', '=', project_id), ('project_id', '=', False)]",
        help="The property this region represents on the 2D master plan. "
             "Buildings, villas, blocks — any property level is fine.",
    )
    label = fields.Char(
        string='Hover Label',
        help="Short text shown on hover. Defaults to the building name.",
    )
    color = fields.Char(
        string='Fill Color', default='#3b82f6',
        help="Hex color used to render the polygon overlay (semi-transparent).",
    )
    polygon = fields.Char(
        string='Polygon (JSON)', required=True,
        help='JSON list of [x%, y%] points relative to the master plan image, '
             'e.g. "[[10, 12], [40, 12], [40, 35], [10, 35]]".',
    )

    @api.constrains('polygon')
    def _check_polygon(self):
        for rec in self:
            try:
                pts = json.loads(rec.polygon or '[]')
            except json.JSONDecodeError as exc:
                raise ValidationError(_("Polygon must be valid JSON: %s") % exc)
            if not isinstance(pts, list) or len(pts) < 3:
                raise ValidationError(_("A region needs at least 3 vertices."))
            for p in pts:
                if (not isinstance(p, (list, tuple)) or len(p) != 2
                        or not all(isinstance(c, (int, float)) for c in p)):
                    raise ValidationError(_(
                        "Each vertex must be [x, y] with numeric coordinates."))
                if not (0 <= p[0] <= 100 and 0 <= p[1] <= 100):
                    raise ValidationError(_(
                        "Vertex coordinates must be percentages in [0, 100]."))

    @api.constrains('project_id', 'property_id')
    def _check_property_project(self):
        """A region may only point at a property of its own project, or at one
        that has no project yet (which it then adopts).

        Pinning a building that already belongs to another project used to
        rewrite that building's project — and all its units' — so drawing a
        polygon on one master plan silently took inventory away from another
        project."""
        for rec in self:
            prop = rec.property_id
            if prop and prop.project_id and prop.project_id != rec.project_id:
                raise ValidationError(_(
                    "%(property)s belongs to project %(other)s and cannot be "
                    "pinned on the master plan of %(project)s.",
                    property=prop.display_name,
                    other=prop.project_id.display_name,
                    project=rec.project_id.display_name))

    @api.depends('label', 'property_id.display_name')
    def _compute_display_name(self):
        # `name_get` is no longer consulted in Odoo 18; without this the
        # region read "realestate.building.region,7" everywhere it was shown.
        for rec in self:
            rec.display_name = (
                rec.label or rec.property_id.display_name
                or _('Region %s', rec.id))

    def _sync_property_project(self):
        """When a region links a property that has no project yet, attach the
        property — and those of its descendants that have no project either —
        to this project. That's how the project's Properties / hierarchy
        starts showing the buildings the user pinned on the master plan.

        Only project-less properties are adopted: a property that already
        belongs to a project is that project's inventory, and moving it is
        not a side effect a polygon may have."""
        for rec in self:
            if not rec.property_id or not rec.project_id:
                continue
            family = self.env['realestate.property'].search([
                ('id', 'child_of', rec.property_id.id),
            ])
            to_link = family.filtered(lambda p: not p.project_id)
            if to_link:
                to_link.write({'project_id': rec.project_id.id})

    @api.model
    def _backfill_property_projects(self):
        """One-time data fix for regions created before the auto-sync hook
        existed. Idempotent — safe to call on every upgrade."""
        self.search([]).filtered(
            lambda r: r.property_id and not r.property_id.project_id
        )._sync_property_project()

    @api.model_create_multi
    def create(self, vals_list):
        regions = super().create(vals_list)
        regions._sync_property_project()
        return regions

    def write(self, vals):
        result = super().write(vals)
        if 'property_id' in vals or 'project_id' in vals:
            self._sync_property_project()
        return result

    def action_delete(self):
        """Delete + close the dialog cleanly so the caller (the 2D preview)
        re-fetches regions via its onClose handler."""
        self.unlink()
        return {'type': 'ir.actions.act_window_close'}
