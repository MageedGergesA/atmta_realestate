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

# mode -> (model, method), for the modes this module owns.
#
# `amend_daily_report` is deliberately absent. Daily reports are a
# `real_estate_construction` model, above this one, and that module adds its
# entry through `_reason_dispatch`. The selection above still offers the mode,
# because the label is a fact about the prompt rather than about who answers
# it, and a mode nothing has registered is refused at confirm time with a
# sentence rather than a traceback.
_DISPATCH = {
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

    @api.model
    def _reason_dispatch(self):
        """mode -> (model, method).

        Seam. Modes belonging to models above this module register themselves
        by extending this, which is how `amend_daily_report` gets back in.
        """
        return dict(_DISPATCH)

    def action_confirm(self):
        self.ensure_one()
        if not self.reason or not self.reason.strip():
            raise UserError(_("Write the reason."))
        target = self._reason_dispatch().get(self.mode)
        if not target:
            raise UserError(_(
                "Nothing here answers for %s. The module that owns that "
                "record is not installed.") % self.mode)
        model, method = target
        record = self.env[model].browse(self.res_id).exists()
        if not record:
            raise UserError(_("The record is no longer there."))
        getattr(record, method)(reason=self.reason.strip())
        return {'type': 'ir.actions.act_window_close'}
