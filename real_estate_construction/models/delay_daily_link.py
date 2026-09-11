# -*- coding: utf-8 -*-
"""Wave 17 — the daily-report half of a delay event.

Delay events moved to `atmta_construction_claims`, which sits above documents,
quality and change because a claim cites all three. Daily reporting did not
move: it is still here, tangled with labour, equipment and cost lines.

So the relation between a delay event and the daily records that evidence it
is declared here, and the two seams the claims module exposes are answered
here. A delay event with no daily records reports none, which is what it would
report anyway; what changes is only which module can see them.
"""
from odoo import _, api, fields, models


class DelayEventDailyLink(models.Model):
    _inherit = 'realestate.construction.delay.event'

    daily_delay_ids = fields.One2many(
        'realestate.construction.daily.delay', 'delay_event_id',
        string='Daily Records')

    @api.depends('claim_ids', 'daily_delay_ids')
    def _compute_counts(self):
        return super()._compute_counts()

    def _daily_record_count(self):
        self.ensure_one()
        return len(self.daily_delay_ids)

    def _daily_delay_chronology(self):
        self.ensure_one()
        return [
            (record.report_id.report_date, record.report_id.name,
             _('Daily report records the delay'))
            for record in self.daily_delay_ids
        ]


class ClaimEvidenceSources(models.Model):
    _inherit = 'realestate.construction.claim.evidence'

    @api.model
    def _record_ref_selection(self):
        """Add the record types this module owns.

        Daily reports, labour logs, equipment records and payment
        certificates are the site and money evidence a claim leans on hardest,
        and all four are still declared here.
        """
        return super()._record_ref_selection() + [
            ('realestate.construction.daily.report', 'Daily Site Report'),
            ('realestate.construction.labor.log', 'Labour Log'),
            ('realestate.construction.daily.equipment', 'Equipment Record'),
            ('realestate.construction.payment.certificate', 'Payment Certificate'),
        ]
