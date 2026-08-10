# -*- coding: utf-8 -*-
"""Reason prompts.

Three M6 actions refuse to run without a written reason — amending a closed
daily report, reopening a closed NCR, escalating an observation. The models
take that reason as an argument. A form button cannot pass one, so this asks
for it rather than letting the button call the method with `None` and hand the
user an error where a question belonged.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

REASON_MODES = [
    ('amend_daily_report', 'Amend Daily Report'),
    ('reopen_ncr', 'Reopen NCR'),
    ('escalate_observation', 'Escalate to NCR'),
]

# mode -> (model, method)
_DISPATCH = {
    'amend_daily_report': ('realestate.construction.daily.report',
                           'action_amend'),
    'reopen_ncr': ('realestate.construction.ncr', 'action_reopen'),
    'escalate_observation': ('realestate.construction.quality.observation',
                             'action_escalate_to_ncr'),
}


class ConstructionReasonWizard(models.TransientModel):
    _name = 'realestate.construction.reason.wizard'
    _description = 'Quality Reason Prompt'

    mode = fields.Selection(REASON_MODES, required=True)
    res_id = fields.Integer(required=True)
    reason = fields.Text(required=True)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        values.setdefault('res_id', self.env.context.get('active_id', 0))
        return values

    def action_confirm(self):
        self.ensure_one()
        if not self.reason or not self.reason.strip():
            raise UserError(_("Write the reason."))
        model, method = _DISPATCH[self.mode]
        record = self.env[model].browse(self.res_id).exists()
        if not record:
            raise UserError(_("The record is no longer there."))
        getattr(record, method)(reason=self.reason.strip())
        return {'type': 'ir.actions.act_window_close'}
