# -*- coding: utf-8 -*-
"""The one reason-wizard mode that only this module can answer.

Quality owns the reason prompt and registers its own two modes. The third,
amending a closed daily report, belongs to daily reporting -- and daily reports
are in `atmta_construction_site`, which sits *above* quality. Quality can never
declare it, so it is registered here, from the one module above both.

Wave 23 moved the NCR change-event link down to quality, where it belongs.
This did not follow it, and cannot.
"""
from odoo import api, models


class ReasonWizardDailyReport(models.TransientModel):
    _inherit = 'realestate.construction.reason.wizard'

    @api.model
    def _reason_dispatch(self):
        dispatch = super()._reason_dispatch()
        dispatch['amend_daily_report'] = (
            'realestate.construction.daily.report', 'action_amend')
        return dispatch
