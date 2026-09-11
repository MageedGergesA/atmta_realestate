# -*- coding: utf-8 -*-
"""What an NCR costs, once somebody decides it costs something.

A non-conformance may raise a **change event**: corrective work that has to be
priced. Wave 15 could not declare this here, because change events were still
in `real_estate_construction`; Wave 16 extracted them and Wave 23 declared the
dependency, so the link now sits beside the NCR.

It stays a deliberate action. An NCR that raised change events by itself would
turn every finding into a commercial claim nobody decided to make.
"""
from odoo import _, fields, models
from odoo.exceptions import UserError


class NcrChangeEvent(models.Model):
    _inherit = 'realestate.construction.ncr'

    change_event_id = fields.Many2one(
        'realestate.construction.change.event', readonly=True, copy=False)

    def action_create_change_event(self):
        """Hand commercial consequences to M4. Never automatic."""
        self.ensure_one()
        if self.change_event_id:
            raise UserError(_("%s already has a change event.") % self.name)
        event = self.env['realestate.construction.change.event'].create({
            'title': _("NCR %(number)s — %(title)s",
                       number=self.name, title=self.title),
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'package_id': self.package_id.id or False,
            'contractor_id': self.contractor_id.id or False,
            'wbs_id': self.wbs_id.id or False,
            'cost_code_id': self.cost_code_id.id or False,
            'source': 'ncr_corrective_work',
            'source_reference': self.name,
            'source_model': self._name,
            'source_id': self.id,
            'estimated_cost_impact': self.estimated_rework_cost,
            'estimated_schedule_days': self.estimated_delay_days,
            'description': self.description,
        })
        self.change_event_id = event
        if self.cost_impact == 'potential':
            self.cost_impact = 'managed'
        return event
