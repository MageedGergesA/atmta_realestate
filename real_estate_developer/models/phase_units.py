# -*- coding: utf-8 -*-
"""The unit side of a phase — see ``project_units.py`` for why it is not in core.

``realestate.phase`` is defined in ``atmta_project_core``. Its counters read
``realestate.property``, which Project Core must not name, so they are declared
here. Unlike the project counters, this compute needed no correction: it already
declared every field it reads.
"""

from odoo import _, api, fields, models


class PhaseUnits(models.Model):
    _inherit = 'realestate.phase'

    property_ids = fields.One2many('realestate.property', 'phase_id', string='Units')
    unit_count = fields.Integer(compute='_compute_counters')
    available_unit_count = fields.Integer(compute='_compute_counters')
    sold_unit_count = fields.Integer(compute='_compute_counters')

    @api.depends('property_ids', 'property_ids.state', 'property_ids.is_sold')
    def _compute_counters(self):
        for rec in self:
            rec.unit_count = len(rec.property_ids)
            rec.available_unit_count = len(rec.property_ids.filtered(lambda p: p.state == 'available'))
            rec.sold_unit_count = len(rec.property_ids.filtered(lambda p: p.is_sold))

    def action_view_units(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Units'),
            'res_model': 'realestate.property',
            'view_mode': 'kanban,list,form',
            'domain': [('phase_id', '=', self.id)],
            'context': {'default_phase_id': self.id, 'default_project_id': self.project_id.id},
        }
