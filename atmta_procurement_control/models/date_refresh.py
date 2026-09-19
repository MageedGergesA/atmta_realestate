# -*- coding: utf-8 -*-
"""Keep waiting days and reservation expiry anomalies current as the days pass.

A requisition's ``days_waiting``, an approval step's ``waiting_days`` and a
reservation's ``has_anomaly`` are stored, so registers can sort and filter on
them, but they depend on the current date, and a stored compute only reruns
when its record changes. Without this daily job a requisition waiting a week
for approval still showed 0 days, and a reservation past its expiry date was
not flagged.
"""

from odoo import api, fields, models


class ProcurementControlDateRefresh(models.AbstractModel):
    _name = 'realestate.procurement.control.refresh'
    _description = "Procurement control: refresh values that depend on today's date"

    @api.model
    def _cron_refresh_dates(self):
        env = self.env(su=True)
        today = fields.Date.context_today(self)
        Step = env['realestate.procurement.approval.step']
        Request = env['realestate.material.request']
        Reservation = env['realestate.procurement.reservation']
        steps = Step.search([
            ('decision', '=', 'pending'),
            ('requested_on', '!=', False),
        ])
        requests = Request.search([
            ('state', '=', 'submitted'),
            ('waiting_since', '!=', False),
        ])
        reservations = Reservation.search([
            ('state', '=', 'reserved'),
            ('expiry_date', '<', today),
            ('has_anomaly', '=', False),
        ])
        env.add_to_compute(Step._fields['waiting_days'], steps)
        env.add_to_compute(Request._fields['days_waiting'], requests)
        env.add_to_compute(Reservation._fields['has_anomaly'], reservations)
        env.flush_all()
        return {'steps': len(steps), 'requests': len(requests),
                'reservations': len(reservations)}
