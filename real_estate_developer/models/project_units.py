# -*- coding: utf-8 -*-
"""The unit side of a project, kept out of Project Core on purpose.

``realestate.project`` itself now lives in ``atmta_project_core``, which owns
the project's identity, location, plot geometry, timeline and lifecycle. What it
deliberately does not own is any knowledge of ``realestate.property``: the
target architecture puts ``atmta_property_core`` *above* Project Core, so a
one2many to Property declared on the core class would invert that edge and make
the core module impossible to install by itself.

So the inventory view of a project — how many units it has and what state they
are in — is declared here instead, in the module that already depends on the one
owning Property. Nothing about the behaviour changed in the move; the fields
have the same names, the same types and the same values.
"""

from odoo import _, api, fields, models


class ProjectUnits(models.Model):
    _inherit = 'realestate.project'

    property_ids = fields.One2many('realestate.property', 'project_id', string='Units / Properties')

    unit_count = fields.Integer(compute='_compute_unit_counters')
    available_unit_count = fields.Integer(compute='_compute_unit_counters')
    reserved_unit_count = fields.Integer(compute='_compute_unit_counters')
    sold_unit_count = fields.Integer(compute='_compute_unit_counters')

    @api.depends('property_ids', 'property_ids.state', 'property_ids.is_sold')
    def _compute_unit_counters(self):
        """Inventory counters for the project.

        ``property_ids.is_sold`` is declared here but was missing from the
        dependency list this compute was split out of, even though the body has
        always read ``p.is_sold``. The phase-level counter, computing the same
        thing, always declared it. A unit becoming sold without its ``state``
        changing therefore left ``sold_unit_count`` serving a stale cached value
        for the rest of the transaction. Nothing was persisted wrong — none of
        these fields are stored — but the two counters disagreed about their own
        inputs, and the extraction was the moment to make them agree.
        """
        for rec in self:
            rec.unit_count = len(rec.property_ids)
            rec.available_unit_count = len(rec.property_ids.filtered(lambda p: p.state == 'available'))
            rec.reserved_unit_count = len(rec.property_ids.filtered(lambda p: p.state == 'reserved'))
            rec.sold_unit_count = len(rec.property_ids.filtered(lambda p: p.is_sold))

    def action_view_units(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Units'),
            'res_model': 'realestate.property',
            'view_mode': 'kanban,list,form',
            'domain': [('project_id', '=', self.id)],
            'context': {'default_project_id': self.id},
        }
