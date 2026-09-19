# -*- coding: utf-8 -*-
"""Keep document overdue flags current as the days pass.

``is_overdue`` and the overdue day counts on RFIs, submittals and transmittals
are stored, because the registers filter, group and sort on them, but they are
computed from today's date, and a stored compute only reruns when its record
changes. Without this daily job an RFI whose response date passed stayed "not
overdue" until somebody edited it.

Only open documents whose date has passed are recomputed: the ones whose value
can still move.
"""

from odoo import api, fields, models


class ConstructionDocumentsDateRefresh(models.AbstractModel):
    _name = 'realestate.construction.documents.refresh'
    _description = "Construction documents: refresh values that depend on today's date"

    @api.model
    def _cron_refresh_dates(self):
        env = self.env(su=True)
        today = fields.Date.context_today(self)
        RFI = env['realestate.construction.rfi']
        Submittal = env['realestate.construction.submittal']
        Transmittal = env['realestate.construction.transmittal']
        rfis = RFI.search([
            ('state', 'not in', ('closed', 'void')),
            ('required_response_date', '<', today),
            ('actual_response_date', '=', False),
        ])
        submittals = Submittal.search([
            ('state', 'not in', ('closed', 'void')),
            '|',
            '&', ('required_submission_date', '<', today), ('submitted_date', '=', False),
            '&', ('required_approval_date', '<', today), ('final_response_date', '=', False),
        ])
        transmittals = Transmittal.search([
            ('state', '=', 'sent'),
            ('acknowledgement_required', '=', True),
            ('acknowledged_date', '=', False),
            ('response_due_date', '<', today),
        ])
        env.add_to_compute(RFI._fields['is_overdue'], rfis)
        env.add_to_compute(Submittal._fields['is_overdue'], submittals)
        env.add_to_compute(Transmittal._fields['is_acknowledgement_overdue'], transmittals)
        env.flush_all()
        return {'rfis': len(rfis), 'submittals': len(submittals),
                'transmittals': len(transmittals)}
