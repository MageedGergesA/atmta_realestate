"""What the receiving chain looks like to the procurement integrity audit.

The audit moved to `atmta_procurement_evaluation` in Wave 8 and the award
checks moved to `atmta_procurement_award`. These four read receipts, vendor
bills and construction cost codes — capabilities this module still owns — so
they stay here and are appended the same way. When Receipt is extracted they
go with it.

Every method below was moved from `evaluation_audit.py` unchanged.
"""
from odoo import _, api, models


class EvaluationAuditReceipt(models.AbstractModel):
    _inherit = 'realestate.procurement.evaluation.audit'

    @api.model
    def _audit_checks(self):
        return super()._audit_checks() + [
            self._check_billed_more_than_accepted,
            self._check_rejected_material_received,
            self._check_uninspected_receipt_under_policy,
            self._check_tender_order_without_cost_code,
        ]


    # ------------------------------------------------------------------
    # M8 — the receiving chain.
    #
    # These four ask the questions the gates cannot: a gate refuses the next
    # bad act, an audit finds the ones already in the database. Every one of
    # them is reachable by a record that predates M8, which is the point —
    # switching a control on does not clean up what happened before it.
    # ------------------------------------------------------------------
    def _check_billed_more_than_accepted(self, rounds):
        """Posted bills exceeding what the site accepted.

        The M8 three-way match refuses this at posting. Anything found here
        was posted before the gate existed, or through a path that bypasses
        `_post()`, and is money already owed against material nobody accepted.
        """
        Line = self.env['purchase.order.line'].sudo()
        domain = [('order_id.re_project_id', '!=', False),
                  ('order_id.state', 'in', ('purchase', 'done'))]
        bad = Line.browse()
        for line in Line.search(domain):
            received = line.qty_received or 0.0
            billed = line.qty_invoiced or 0.0
            tolerance = (line.company_id.sudo(
            ).procurement_amount_tolerance_pct or 0.0)
            if billed - received - (received * tolerance / 100.0) > 0.000001:
                bad |= line
        if not bad:
            return []
        return [self._finding(
            'billed_over_accepted', 'critical',
            _("%d purchase line(s) are billed for more than the site "
              "accepted.") % len(bad),
            bad.order_id,
            _("Credit the difference or record the missing receipt. Until "
              "one of the two happens the project owes money for material "
              "it never took."))]

    def _check_rejected_material_received(self, rounds):
        """Inspections whose rejection never reached the stock ledger.

        M8 applies the accepted quantity at validation. A receipt validated
        before M8 — or one whose inspection was recorded after it was already
        done — books the whole load in while the sheet says half of it was
        refused. The sheet and the ledger then disagree, and the ledger is
        what gets paid.
        """
        Inspection = self.env[
            'realestate.procurement.receipt.inspection'].sudo()
        bad = Inspection.browse()
        for inspection in Inspection.search([('state', 'in',
                                              ('partial', 'failed'))]):
            if inspection.picking_id.state != 'done':
                continue
            moved = sum(inspection.picking_id.move_ids.mapped('quantity'))
            if moved - inspection.accepted_qty > 0.000001:
                bad |= inspection
        if not bad:
            return []
        return [self._finding(
            'rejected_material_received', 'critical',
            _("%d inspection(s) rejected material that the stock ledger "
              "shows as received.") % len(bad),
            bad,
            _("The sheet and the ledger disagree about the same delivery, "
              "and the ledger is the one that gets paid."))]

    def _check_uninspected_receipt_under_policy(self, rounds):
        """Receipts validated with no inspection where the project requires one.

        Expected to find records: the policy defaults to off, so anything
        received before somebody switched it on is legitimately uninspected.
        Reported `low` for exactly that reason — a statement about history,
        not an accusation. (The audit's severities are critical / high /
        medium / low; an earlier draft invented `warning`, which the wizard's
        finding model rejected at write time and the upgrade gate caught.)
        """
        Picking = self.env['stock.picking'].sudo()
        Control = self.env['realestate.procurement.control']
        Inspection = self.env[
            'realestate.procurement.receipt.inspection'].sudo()
        bad = Picking.browse()
        candidates = Picking.search([('state', '=', 'done'),
                                     ('picking_type_id.code', '=', 'incoming')])
        inspected = set(Inspection.search([
            ('picking_id', 'in', candidates.ids),
            ('state', 'in', ('passed', 'partial', 'failed')),
        ]).picking_id.ids)
        for picking in candidates:
            if picking.id in inspected:
                continue
            project = picking._re_inspection_project()
            if not project:
                continue
            if Control.receipt_inspection_for(
                    project, picking.company_id) != 'required':
                continue
            bad |= picking
        if not bad:
            return []
        return [self._finding(
            'uninspected_receipt', 'low',
            _("%d receipt(s) were validated with no inspection on a project "
              "that now requires one.") % len(bad),
            bad,
            _("Most will predate the policy. Those that do not are material "
              "accepted by nobody in particular."))]

    def _check_tender_order_without_cost_code(self, rounds):
        """Tender orders carrying a project and no cost code.

        The commitment reaches the cost report as Unassigned and the analytic
        distribution is never stamped, so no bill against the order can ever
        become actual cost. M8 carries the coding at RFQ time; this finds the
        orders raised before it did.
        """
        Line = self.env['purchase.order.line'].sudo()
        if 're_cost_code_id' not in Line._fields:
            return []
        bad = Line.search([
            ('order_id.re_sourcing_event_id', '!=', False),
            ('order_id.re_project_id', '!=', False),
            ('order_id.state', 'in', ('purchase', 'done')),
            ('re_cost_code_id', '=', False),
            ('display_type', '=', False),
        ])
        if not bad:
            return []
        return [self._finding(
            'tender_order_uncoded', 'medium',
            _("%d confirmed tender line(s) carry a project and no cost "
              "code.") % len(bad),
            bad.order_id,
            _("Their commitment sits under Unassigned and no bill against "
              "them can become actual cost. Code them, and the analytic "
              "distribution follows."))]
