# -*- coding: utf-8 -*-
"""Keep quality overdue flags current as the days pass.

``is_overdue`` on observations and NCRs is stored, because the registers filter
on it, but it compares a due date with today, and a stored compute only reruns
when its record changes. Without this daily job an NCR past its target
completion date stayed "not overdue" until somebody edited it.
"""

from odoo import api, fields, models


class ConstructionQualityDateRefresh(models.AbstractModel):
    _name = 'realestate.construction.quality.refresh'
    _description = "Construction quality: refresh values that depend on today's date"

    @api.model
    def _cron_refresh_dates(self):
        env = self.env(su=True)
        today = fields.Date.context_today(self)
        Observation = env['realestate.construction.quality.observation']
        NCR = env['realestate.construction.ncr']
        observations = Observation.search([
            ('state', 'not in', ('closed', 'void')),
            ('due_date', '<', today),
            ('is_overdue', '=', False),
        ])
        ncrs = NCR.search([
            ('state', 'in', NCR.OPEN_STATES),
            ('target_completion_date', '<', today),
            ('is_overdue', '=', False),
        ])
        env.add_to_compute(Observation._fields['is_overdue'], observations)
        env.add_to_compute(NCR._fields['is_overdue'], ncrs)
        env.flush_all()
        return {'observations': len(observations), 'ncrs': len(ncrs)}
