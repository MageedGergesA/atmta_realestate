"""What an incoming receipt means to procurement.

Native Odoo owns the receipt: `stock.picking`, its moves and its validation are
Inventory's, and nothing here duplicates them. What this module adds is the
procurement question asked *about* that receipt — was an inspection required,
was one recorded, and what did it conclude.

Moved unchanged from the monolith's purchase bridge in Wave 9; only its address
changed.
"""
import logging

from odoo import _, api, fields, models
from odoo.addons.atmta_procurement_core.models.procurement_policy import (
    RECEIPT_INSPECTION_POLICY,
)
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)




class StockPicking(models.Model):
    _inherit = 'stock.picking'

    re_inspection_id = fields.Many2one(
        'realestate.procurement.receipt.inspection', string='Inspection',
        compute='_compute_re_inspection', search='_search_re_inspection',
        help="The inspection sheet for this receipt, if one has been opened.")
    re_inspection_state = fields.Selection(
        related='re_inspection_id.state', string='Inspection Result')
    re_inspection_policy = fields.Selection(
        RECEIPT_INSPECTION_POLICY, compute='_compute_re_inspection',
        string='Inspection Policy')

    def _compute_re_inspection(self):
        """Read as the system: a storekeeper is not a project reader.

        The M7 Confirmation Gate was exactly this mistake one model along —
        a compute that only ever wanted a selection field raising an
        `AccessError` at somebody holding the right to do the thing.
        """
        Inspection = self.env['realestate.procurement.receipt.inspection']
        Control = self.env['realestate.procurement.control']
        found = {
            inspection.picking_id.id: inspection
            for inspection in Inspection.sudo().search(
                [('picking_id', 'in', self.ids)])
        } if self.ids else {}
        for picking in self:
            inspection = found.get(picking.id)
            picking.re_inspection_id = inspection.id if inspection else False
            picking.re_inspection_policy = Control.receipt_inspection_for(
                picking._re_inspection_project(), picking.company_id)

    def _search_re_inspection(self, operator, value):
        inspections = self.env[
            'realestate.procurement.receipt.inspection'].sudo().search(
                [('id', operator, value)])
        return [('id', 'in', inspections.picking_id.ids)]

    def _re_inspection_project(self):
        """The project this delivery is for, or nothing.

        Nothing is a legitimate answer: plenty of receipts are not against a
        project at all, and inspection is a project control.
        """
        self.ensure_one()
        order = self.sudo().move_ids.purchase_line_id.order_id[:1]
        if order and 're_project_id' in order._fields:
            return order.re_project_id
        return self.env['realestate.project']

    def _re_inspection_required(self):
        """Whether this particular receipt is under the inspection policy."""
        self.ensure_one()
        if self.picking_type_id.code != 'incoming':
            return 'off'
        if not self._re_inspection_project():
            return 'off'
        return self.env['realestate.procurement.control'
                        ].receipt_inspection_for(
                            self._re_inspection_project(), self.company_id)

    def action_open_inspection(self):
        """Open — creating if needed — this receipt's inspection sheet."""
        self.ensure_one()
        inspection = self.env[
            'realestate.procurement.receipt.inspection']._for_picking(self)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.procurement.receipt.inspection',
            'res_id': inspection.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def button_validate(self):
        """Refuse an uninspected receipt where the project requires one.

        The refusal sits on `button_validate()` rather than `_action_done()`
        because this is a decision a person is making at a screen, and the
        message has to reach them while they can still act on it. `_action_done`
        runs for backorders, scrap and internal transfers too, where the
        message would arrive attached to something nobody chose to do.
        """
        for picking in self:
            policy = picking._re_inspection_required()
            if policy == 'off':
                continue
            inspection = picking.re_inspection_id
            done = inspection and inspection.state in (
                'passed', 'partial', 'failed')
            if done:
                continue
            if policy == 'warn':
                picking.message_post(body=_(
                    "Validated without a material inspection. The project's "
                    "policy records the gap rather than refusing it."))
                _logger.info(
                    "Receipt %s validated with no inspection under a warn "
                    "policy.", picking.name)
                continue
            raise UserError(_(
                "%(picking)s delivers material to %(project)s, which requires "
                "inspection before a receipt is validated.\n\nOpen the "
                "inspection, record what arrived and what of it is fit to "
                "use, then validate. Rejecting material here is not a "
                "judgement about the vendor — it is a statement about this "
                "load.",
                picking=picking.name,
                project=picking._re_inspection_project().display_name))
        self._apply_inspection_outcome()
        return super().button_validate()

    def _apply_inspection_outcome(self):
        """What was accepted is what enters stock.

        Until this runs, an inspection is a document beside the receipt saying
        half the load was cracked while the receipt books all of it in — which
        is worse than no inspection at all, because it produces a record that
        looks like a control and changes nothing. The rejected quantity never
        enters stock, so it never reaches `qty_received`, so it never rolls up
        to the requisition and is never billable.

        Only quantities are touched, and only downwards. Nothing here creates a
        return, a claim or a debit note: what happens to rejected material is a
        commercial conversation, and inventing a stock move for it would be
        this module deciding an outcome that is not its to decide.
        """
        for picking in self:
            inspection = picking.re_inspection_id
            if not inspection or inspection.state not in ('partial', 'failed'):
                continue
            if not inspection.accepted_qty:
                raise UserError(_(
                    "%(picking)s was inspected and nothing on it was "
                    "accepted.\n\nValidating would book rejected material "
                    "into stock. Cancel the receipt, or arrange the return "
                    "with the vendor — either way it is a decision somebody "
                    "makes, not one this screen makes for them.",
                    picking=picking.name))
            by_move = {
                line.move_id.id: line.accepted_qty
                for line in inspection.sudo().line_ids
            }
            for move in picking.move_ids:
                if move.id in by_move:
                    move.quantity = by_move[move.id]
            picking.message_post(body=_(
                "Inspection %(name)s: %(accepted)s accepted, %(rejected)s "
                "rejected. The rejected quantity is not received and is not "
                "billable.",
                name=inspection.name,
                accepted=inspection.accepted_qty,
                rejected=inspection.rejected_qty))

    def _action_done(self):
        """Roll receipts up to the requisition when a receipt is validated.

        This used to happen inside a compute on the requisition line, which
        meant the request's state depended on when the ORM happened to
        invalidate a cache. Inventory validating a receipt is the actual
        event, so that is where the rollup is triggered from.
        """
        res = super()._action_done()
        requests = self.move_ids.purchase_line_id.re_material_request_id
        if requests:
            requests.invalidate_recordset()
            requests._refresh_state_from_lines()
        return res
