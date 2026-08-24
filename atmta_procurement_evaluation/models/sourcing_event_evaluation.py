"""What a tender looks like once it can be evaluated.

Wave 7 parked these in the monolith because a sourcing event that knew about
`realestate.procurement.evaluation.round` would sit above Evaluation instead of
below it. Now that Evaluation is its own capability they come here, where the
model they point at is declared.

The classifier describes; it never creates an evaluation. Moved unchanged.
"""
from odoo import fields, models


class SourcingEventEvaluation(models.Model):
    _inherit = 'realestate.procurement.sourcing.event'


    evaluation_round_ids = fields.One2many(
        'realestate.procurement.evaluation.round', 'sourcing_event_id')


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
