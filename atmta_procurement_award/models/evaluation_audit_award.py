"""What the award chain looks like to the procurement integrity audit.

`atmta_procurement_evaluation` owns the audit and asks the evaluation half of
the question. These four checks ask the award half, and they belong here
because they read `realestate.procurement.award` — a model this module
declares and Evaluation must not know about.

Every method below was moved from `evaluation_audit.py` unchanged.
"""
from odoo import _, api, models


class EvaluationAuditAward(models.AbstractModel):
    _inherit = 'realestate.procurement.evaluation.audit'

    @api.model
    def _audit_checks(self):
        return super()._audit_checks() + [
            self._check_award_surface,
            self._check_confirmed_tender_order_without_award,
            self._check_demand_reserved_and_committed,
            self._check_award_approved_by_its_author,
            self._check_award_over_tender,
        ]


    def _check_award_surface(self, rounds):
        """M6 must expose nothing that awards. Structural, not data."""
        Round = self.env['realestate.procurement.evaluation.round']
        exposed = [name for name in
                   ('action_award', 'action_confirm_award',
                    'action_create_winning_po', 'award_partner_id')
                   if hasattr(Round, name)]
        if not exposed:
            return []
        return [self._finding(
            'award_surface_in_m6', 'critical',
            _("M6 exposes an award interface: %s. Evaluation ranks; M7 "
              "awards.") % ', '.join(exposed),
            Round.browse(), _("Remove it. The award decision is a separate "
                              "authorised act."))]

    # ------------------------------------------------------------------
    # M7 — the award chain
    # ------------------------------------------------------------------
    def _check_confirmed_tender_order_without_award(self, rounds):
        """A tender RFQ that committed money with no award behind it.

        This is the bypass the whole confirmation boundary exists to close, and
        it is checked from the data rather than by trusting the guard: RPC,
        imports, another module and plain SQL all reach a confirmed state
        without passing `button_confirm`.
        """
        AwardLine = self.env['realestate.procurement.award.line'].sudo()
        bad = self.env['purchase.order'].sudo()
        for round_ in rounds:
            event = round_.sourcing_event_id
            orders = event.invitation_ids.purchase_order_id.filtered(
                lambda o: o.state in ('purchase', 'done'))
            for order in orders:
                authorised = AwardLine.search_count([
                    ('purchase_order_id', '=', order.id),
                    ('award_id.state', 'in', ('approved', 'issued')),
                ])
                if not authorised:
                    bad |= order
        if not bad:
            return []
        return [self._finding(
            'confirmed_tender_order_without_award', 'critical',
            _("A tender purchase order is confirmed with no approved award "
              "behind it, so a commitment exists that nobody authorised."),
            bad, _("Establish who confirmed it and under what authority. "
                   "Sourcing does not commit money; an award does."))]

    def _check_demand_reserved_and_committed(self, rounds):
        """The same demand held as a reservation *and* owed as a commitment.

        Phase 0's Q3. It happens when an order confirms without carrying the
        demand link M3 converts from: Construction reads the commitment off the
        order, the reservation goes on holding capacity, and the project is
        counted twice for one requisition.
        """
        bad = self.env['realestate.procurement.reservation'].sudo()
        for round_ in rounds:
            event = round_.sourcing_event_id
            orders = event.invitation_ids.purchase_order_id.filtered(
                lambda o: o.state in ('purchase', 'done'))
            for line in orders.mapped('order_line'):
                request_line = line.re_material_request_line_id
                if not request_line:
                    continue
                bad |= request_line.reservation_ids.filtered(
                    lambda r: r.state == 'reserved' and r.amount_active
                    and not r.amount_converted)
        if not bad:
            return []
        return [self._finding(
            'demand_reserved_and_committed', 'critical',
            _("Demand behind a confirmed order is still holding a reservation "
              "with nothing converted, so the same money is both reserved and "
              "committed."),
            bad, _("Convert or release the reservation. Two control numbers "
                   "for one obligation means the project's availability is "
                   "wrong by that amount."))]

    def _check_award_approved_by_its_author(self, rounds):
        """Maker and checker were the same person."""
        bad = self.env['realestate.procurement.award'].sudo().search([
            ('round_id', 'in', rounds.ids),
            ('state', 'in', ('approved', 'issued')),
        ]).filtered(
            lambda a: a.submitted_by_id and a.approved_by_id
            and a.submitted_by_id == a.approved_by_id)
        if not bad:
            return []
        return [self._finding(
            'award_approved_by_its_author', 'critical',
            _("An award was approved by the person who raised it."),
            bad, _("This is the one decision in the chain that commits money. "
                   "Establish how the second signature was bypassed."))]

    def _check_award_over_tender(self, rounds):
        """More awarded than was tendered."""
        Award = self.env['realestate.procurement.award'].sudo()
        bad = Award.browse()
        for award in Award.search([('round_id', 'in', rounds.ids),
                                   ('state', '!=', 'cancelled')]):
            allocations = award.line_ids.mapped('allocation_ids')
            for sourcing_line in allocations.mapped('sourcing_line_id'):
                tendered = sourcing_line.quantity or 0.0
                awarded = sum(allocations.filtered(
                    lambda a, s=sourcing_line: a.sourcing_line_id == s
                ).mapped('quantity'))
                if awarded - tendered > 0.000001:
                    bad |= award
                    break
        if not bad:
            return []
        return [self._finding(
            'award_over_tender', 'critical',
            _("An award commits more quantity than the tender authorised."),
            bad, _("The authorisation does not grow because the demand was "
                   "split between vendors."))]
