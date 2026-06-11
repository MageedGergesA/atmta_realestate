"""Extend the maquette's realestate.building.floor so it works with the
property → floor → unit hierarchy used by real_estate_plan.

The maquette ships with unit_id.domain = [parent_id == building_id] and
_compute_unit_ids = [parent_id == building_id, hierarchy_level == 'unit'].
That assumes units sit directly under buildings. When units are nested under
floor records (`unit.parent_id = floor; floor.parent_id = building`) the
picker comes up empty and unit stats roll up to zero.

We swap the direct-child checks for `child_of` so any descendant unit of the
building counts, regardless of how deep the hierarchy is."""
from odoo import api, fields, models


class BuildingFloor(models.Model):
    _inherit = 'realestate.building.floor'

    unit_id = fields.Many2one(
        'realestate.property',
        domain="['&', ('id', 'child_of', building_id), ('hierarchy_level', '=', 'unit')]",
    )

    @api.depends('building_id', 'floor_number')
    def _compute_unit_ids(self):
        Property = self.env['realestate.property']
        for rec in self:
            bid = rec.building_id.id if rec.building_id else False
            if not bid or not isinstance(bid, int):
                rec.unit_ids = Property
                continue
            rec.unit_ids = Property.search([
                ('id', 'child_of', bid),
                ('hierarchy_level', '=', 'unit'),
                ('floor_number', '=', rec.floor_number),
            ])
