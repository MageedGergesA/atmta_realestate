# -*- coding: utf-8 -*-
"""Keep issue and risk-action overdue values current as the days pass.

An issue's ``is_overdue`` and a risk's ``overdue_action_count`` are stored,
because the registers filter and sort on them, but they compare due dates with
today, and a stored compute only reruns when its record changes. Without this
daily job an issue past its due date stayed "not overdue", and a risk kept
counting no overdue actions, until somebody edited it.
"""

from odoo import api, fields, models


class ConstructionCostDateRefresh(models.AbstractModel):
    _name = 'realestate.construction.cost.refresh'
    _description = "Construction cost: refresh values that depend on today's date"

    @api.model
    def _cron_refresh_dates(self):
        env = self.env(su=True)
        today = fields.Date.context_today(self)
        Issue = env['realestate.construction.issue']
        Risk = env['realestate.construction.risk']
        issues = Issue.search([
            ('state', 'not in', ('closed', 'cancelled')),
            ('due_date', '<', today),
            ('is_overdue', '=', False),
        ])
        risks = env['realestate.construction.risk.action'].search([
            ('state', 'not in', ('done', 'cancelled')),
            ('due_date', '<', today),
        ]).risk_id
        env.add_to_compute(Issue._fields['is_overdue'], issues)
        env.add_to_compute(Risk._fields['overdue_action_count'], risks)
        env.flush_all()
        return {'issues': len(issues), 'risks': len(risks)}
