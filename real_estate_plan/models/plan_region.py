import json

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PlanRegion(models.Model):
    """One clickable polygon overlay on a property's 2D plan image, tied to a
    child property. Coordinates are percentages of the image so they survive
    any display size."""
    _name = 'realestate.plan.region'
    _description = '2D Plan Region'
    _order = 'parent_property_id, sequence, id'

    sequence = fields.Integer(default=10)
    parent_property_id = fields.Many2one(
        'realestate.property', required=True, ondelete='cascade', index=True,
        help="The property whose plan image carries this region.",
    )
    target_property_id = fields.Many2one(
        'realestate.property', required=True, ondelete='cascade', index=True,
        help="The child property this region links to.",
    )
    label = fields.Char(
        string='Hover Label',
        help="Short text shown on hover. Defaults to the target's name.",
    )
    color = fields.Char(
        string='Fill Color', default='#3b82f6',
        help="Hex color used to render the polygon overlay (semi-transparent).",
    )
    polygon = fields.Char(
        string='Polygon (JSON)', required=True,
        help='JSON list of [x%, y%] points relative to the plan image, '
             'e.g. "[[10, 12], [40, 12], [40, 35], [10, 35]]".',
    )

    target_state = fields.Selection(
        related='target_property_id.state', store=True, readonly=True,
    )
    target_hierarchy_level = fields.Selection(
        related='target_property_id.hierarchy_level', store=True, readonly=True,
    )

    _sql_constraints = [
        ('unique_target_per_parent',
         'UNIQUE(parent_property_id, target_property_id)',
         'A child property can only have one region on its parent plan.'),
    ]

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

    @api.constrains('parent_property_id', 'target_property_id')
    def _check_target_is_child(self):
        for r in self:
            if not r.target_property_id or not r.parent_property_id:
                continue
            if r.target_property_id == r.parent_property_id:
                raise ValidationError(_(
                    "A region cannot point back to its own parent property."))
            if r.target_property_id.parent_id != r.parent_property_id:
                raise ValidationError(_(
                    "Region target '%(t)s' must be a direct child of '%(p)s'.",
                    t=r.target_property_id.display_name,
                    p=r.parent_property_id.display_name,
                ))

    def action_open_target(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.property',
            'res_id': self.target_property_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_delete(self):
        """Close the dialog cleanly after deletion so the caller refreshes."""
        self.unlink()
        return {'type': 'ir.actions.act_window_close'}
