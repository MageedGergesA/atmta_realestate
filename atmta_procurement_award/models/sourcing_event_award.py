"""What a tender looks like once it can be awarded.

The other half of the Wave 7 parking. `realestate.procurement.award` is
declared here, so the reverse link and the readiness description belong here
too, and the sourcing event stays unaware of both.

The classifier describes; it never awards anything. Moved unchanged.
"""
from odoo import fields, models


class SourcingEventAward(models.Model):
    _inherit = 'realestate.procurement.sourcing.event'

    award_ids = fields.One2many(
        'realestate.procurement.award', 'sourcing_event_id')


    def _classify_award_readiness(self):
        """Describe this tender's award status. Never create one.

        Read-only by construction: every branch looks at records that already
        exist. Nothing here awards anything, and a tender whose orders were
        confirmed without an award is labelled as exactly that rather than
        being given a retrospective authorisation — which would be forging the
        signature the control exists to require.
        """
        self.ensure_one()
        if self.state == 'cancelled':
            return 'cancelled'
        awards = self.award_ids.sudo()
        live = awards.filtered(
            lambda a: a.state != 'cancelled' and not a.superseded)
        if live.filtered(lambda a: a.state == 'issued'):
            return 'awarded'
        if live.filtered(lambda a: a.state == 'approved'):
            return 'award_approved'
        if live.filtered(lambda a: a.state == 'review'):
            return 'award_in_review'
        if live.filtered(lambda a: a.state == 'draft'):
            return 'award_drafted'

        # No live award. Did anything commit anyway?
        committed = self.invitation_ids.sudo().mapped(
            'purchase_order_id').filtered(
                lambda o: o.state in ('purchase', 'done'))
        if committed:
            return 'committed_without_award'
        if self.evaluation_round_ids.sudo().filtered(
                lambda r: r.state == 'finalised'):
            return 'awaiting_award'
        return 'not_evaluated'
