from odoo import api, fields, models


class BuildingFloor(models.Model):
    """A floor inside a building. The user picks one unit on the floor and
    everything else (floor number, usage) follows from that unit. All other
    units in the same building with a matching ``floor_number`` are surfaced
    via the computed ``unit_ids`` for the per-floor stats."""
    _name = 'realestate.building.floor'
    _description = 'Building Floor'
    _order = 'building_id, floor_number'

    building_id = fields.Many2one(
        'realestate.property', string='Building',
        required=True, ondelete='cascade', index=True,
        domain=[('hierarchy_level', 'in', ('block', 'building'))],
    )
    project_id = fields.Many2one(
        related='building_id.project_id', store=True, readonly=True,
    )

    unit_id = fields.Many2one(
        'realestate.property', string='Unit',
        required=True, ondelete='cascade',
        domain="[('parent_id', '=', building_id), ('hierarchy_level', '=', 'unit')]",
        help="Pick any unit on the floor — its Floor Number and Usage drive this row.",
    )
    floor_number = fields.Integer(
        related='unit_id.floor_number', store=True, readonly=True,
    )
    property_usage_id = fields.Many2one(
        related='unit_id.property_usage_id', store=True, readonly=True,
        string='Usage',
    )

    unit_ids = fields.Many2many(
        'realestate.property', string='Units on this floor',
        compute='_compute_unit_ids',
    )
    units_total = fields.Integer(compute='_compute_unit_stats')
    units_available = fields.Integer(compute='_compute_unit_stats')
    units_reserved = fields.Integer(compute='_compute_unit_stats')
    units_sold = fields.Integer(compute='_compute_unit_stats')

    display_name = fields.Char(compute='_compute_display_name', store=True)

    _sql_constraints = [
        ('unique_floor_per_building',
         'UNIQUE(building_id, floor_number)',
         'Each floor number can only appear once per building.'),
    ]

    @api.depends('floor_number', 'building_id.name')
    def _compute_display_name(self):
        for rec in self:
            num = str(rec.floor_number) if rec.floor_number else 'G'
            parts = [p for p in (rec.building_id.name, num) if p]
            rec.display_name = ' / '.join(parts)

    @api.depends('building_id', 'floor_number',
                 'building_id.child_ids', 'building_id.child_ids.floor_number')
    def _compute_unit_ids(self):
        Property = self.env['realestate.property']
        for rec in self:
            bid = rec.building_id.id if rec.building_id else False
            if not bid or not isinstance(bid, int):
                rec.unit_ids = Property
                continue
            rec.unit_ids = Property.search([
                ('parent_id', '=', bid),
                ('hierarchy_level', '=', 'unit'),
                ('floor_number', '=', rec.floor_number),
            ])

    @api.depends('unit_ids', 'unit_ids.state')
    def _compute_unit_stats(self):
        for rec in self:
            rec.units_total = len(rec.unit_ids)
            rec.units_available = len(rec.unit_ids.filtered(lambda u: u.state == 'available'))
            rec.units_reserved = len(rec.unit_ids.filtered(lambda u: u.state == 'reserved'))
            rec.units_sold = len(rec.unit_ids.filtered(lambda u: u.state == 'sold'))
