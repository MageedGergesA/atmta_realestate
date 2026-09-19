# -*- coding: utf-8 -*-
"""Keep notice status and delay durations current as the days pass.

A notice's ``status`` (due soon, overdue) and an ongoing delay's
``duration_days`` are stored, because the registers filter and sort on them,
but they depend on the current date, and a stored compute only reruns when its
record changes. Without this daily job an unissued notice past its deadline
never read "overdue", and a delay kept the duration it had when it was logged.
"""

from odoo import api, fields, models


class ConstructionClaimsDateRefresh(models.AbstractModel):
    _name = 'realestate.construction.claims.refresh'
    _description = "Construction claims: refresh values that depend on today's date"

    @api.model
    def _cron_refresh_dates(self):
        env = self.env(su=True)
        Notice = env['realestate.construction.notice']
        Delay = env['realestate.construction.delay.event']
        notices = Notice.search([
            ('notice_date', '=', False),
            ('has_deadline', '=', True),
            ('disputed', '=', False),
        ])
        delays = Delay.search([
            ('is_ongoing', '=', True),
            ('end_date', '=', False),
            ('start_date', '!=', False),
        ])
        env.add_to_compute(Notice._fields['status'], notices)
        env.add_to_compute(Delay._fields['duration_days'], delays)
        env.flush_all()
        return {'notices': len(notices), 'delays': len(delays)}
