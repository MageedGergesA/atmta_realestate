"""Where a tender stands on evaluation and awarding — described, never performed.

Wave 7 moved the sourcing capability out of this module. These two reverse
links and the two classifiers that read them stayed behind, because they point
*upward*: `realestate.procurement.evaluation.round` and
`realestate.procurement.award` are still declared here, and a sourcing event
that knew about them would put Sourcing above Evaluation and Award in the
dependency graph instead of below.

So the event keeps its identity in `atmta_procurement_sourcing`, and this
module — which owns Evaluation and Award until their own waves — contributes
the fields and the two read-only descriptions. Nothing here creates an
evaluation or an award; every branch reads records that already exist. When
Evaluation and Award are extracted, this file goes with them.
"""
from odoo import fields, models


class SourcingEventReadiness(models.Model):
    _inherit = 'realestate.procurement.sourcing.event'

    evaluation_round_ids = fields.One2many(
        'realestate.procurement.evaluation.round', 'sourcing_event_id')
    award_ids = fields.One2many(
        'realestate.procurement.award', 'sourcing_event_id')

    # ------------------------------------------------------------------
    def _classify_evaluation_readiness(self):
        """Describe this tender's evaluation status. Never create one.

        Read-only by construction: every branch looks at records that already
        exist. Nothing here scores a bid, and a tender that was evaluated
        outside ATMTA is labelled as exactly that rather than being given a
        fabricated internal history.
        """
        self.ensure_one()
        if self.evaluation_round_ids.filtered(
                lambda r: r.state == 'finalised'):
            return 'evaluated'
        if self.evaluation_round_ids:
            return 'evaluation_candidate'
        if self.state in ('draft', 'review', 'published'):
            return 'open_not_ready'
        if self.state == 'cancelled':
            return 'ambiguous'
        # Closed. Is there anything an evaluation could work on?
        evaluable = self.bid_response_ids.filtered(
            lambda r: r.state == 'received' and r.is_evaluable)
        if evaluable:
            return 'closed_unevaluated'
        if self.bid_response_ids:
            return 'legacy_external_evaluation'
        return 'ambiguous'

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
