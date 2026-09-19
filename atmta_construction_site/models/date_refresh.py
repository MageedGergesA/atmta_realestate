# -*- coding: utf-8 -*-
"""Mark milestones delayed once their expected end date has passed.

A milestone's ``state`` is stored and becomes "delayed" when work is under way
and today is past the expected end date, but a stored compute only reruns when
its record changes: a milestone nobody touched stayed "in progress" after its
end date. The comparison still uses the date of whoever triggers the
recompute; this daily job only makes sure one happens.
"""

from odoo import api, fields, models


class ConstructionSiteDateRefresh(models.AbstractModel):
    _name = 'realestate.construction.site.refresh'
    _description = "Construction site: refresh values that depend on today's date"

    @api.model
    def _cron_refresh_dates(self):
        env = self.env(su=True)
        today = fields.Date.context_today(self)
        Milestone = env['realestate.construction.milestone']
        # Both in progress and not started become "delayed" once the expected
        # end date has passed; completed and cancelled milestones never move.
        milestones = Milestone.search([
            ('state', 'in', ('not_started', 'in_progress')),
            ('expected_end_date', '<', today),
        ])
        env.add_to_compute(Milestone._fields['state'], milestones)
        env.flush_all()
        return {'milestones': len(milestones)}
