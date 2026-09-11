# -*- coding: utf-8 -*-
"""Wave 15 — the two things quality could not take with it.

Inspections, NCRs and observations moved to `atmta_construction_quality`. Two
ties point back up here and therefore stay:

* an NCR may raise a **change event**, and change events are declared in this
  module. The link and the action that creates it are added onto the NCR from
  here. It was always a deliberate action somebody takes, and it still is;
* the reason wizard's **amend a daily report** mode. Daily reports are a model
  of this module, so its dispatch entry is registered through the seam the
  wizard exposes rather than hard-coded below, where the model would not exist.
"""
from odoo import _, api, fields, models
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


class ReasonWizardDailyReport(models.TransientModel):
    _inherit = 'realestate.construction.reason.wizard'

    @api.model
    def _reason_dispatch(self):
        dispatch = super()._reason_dispatch()
        dispatch['amend_daily_report'] = (
            'realestate.construction.daily.report', 'action_amend')
        return dispatch
