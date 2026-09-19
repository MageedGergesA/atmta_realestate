# -*- coding: utf-8 -*-
"""Phase 20 — structured reservation cancellation.

Cancellation is a commercial event with money attached, not a state change. The
wizard exists so the reason, the refund and the forfeiture are captured at the
moment the decision is made, rather than reconstructed from memory later.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ReservationCancelWizard(models.TransientModel):
    _name = 'realestate.reservation.cancel'
    _description = 'Cancel Reservation'

    reservation_id = fields.Many2one(
        'realestate.unit.reservation', required=True, readonly=True)
    currency_id = fields.Many2one(
        related='reservation_id.currency_id', readonly=True)
    property_id = fields.Many2one(
        related='reservation_id.property_id', readonly=True)
    booking_received = fields.Monetary(
        related='reservation_id.booking_amount_received', readonly=True,
        string='Booking Amount Received')
    booking_is_refundable = fields.Boolean(
        related='reservation_id.booking_is_refundable', readonly=True)

    reason_code = fields.Selection(
        related='reservation_id.cancellation_reason_code', readonly=False,
        required=True, string='Reason')
    note = fields.Text(string='Details')
    by_customer = fields.Boolean(string='Customer-Requested')

    refund_amount = fields.Monetary(string='Refund')
    forfeited_amount = fields.Monetary(string='Forfeit')
    unallocated = fields.Monetary(
        compute='_compute_unallocated', string='Unallocated',
        help="Booking money neither refunded nor forfeited. It should normally "
             "be zero — otherwise nobody has decided what happens to it.")

    @api.depends('refund_amount', 'forfeited_amount', 'booking_received')
    def _compute_unallocated(self):
        for wiz in self:
            wiz.unallocated = (wiz.booking_received or 0.0) \
                - (wiz.refund_amount or 0.0) - (wiz.forfeited_amount or 0.0)

    @api.onchange('reservation_id', 'by_customer')
    def _onchange_defaults(self):
        """Propose the contractual outcome; never decide it silently."""
        for wiz in self:
            received = wiz.booking_received or 0.0
            if not received:
                wiz.refund_amount = 0.0
                wiz.forfeited_amount = 0.0
            elif wiz.booking_is_refundable:
                wiz.refund_amount = received
                wiz.forfeited_amount = 0.0
            else:
                # A non-refundable booking that the customer walks away from is
                # normally forfeited; a developer-side cancellation normally is
                # not. Both are proposals the user can override.
                wiz.refund_amount = 0.0 if wiz.by_customer else received
                wiz.forfeited_amount = received if wiz.by_customer else 0.0

    def action_confirm(self):
        self.ensure_one()
        if self.unallocated:
            raise UserError(_(
                "%s of the booking amount is neither refunded nor forfeited. "
                "Decide what happens to it before cancelling — an unexplained "
                "balance becomes a dispute."
            ) % self.unallocated)
        self.reservation_id._cancel(
            reason_code=self.reason_code,
            note=self.note,
            by_customer=self.by_customer,
            refund_amount=self.refund_amount,
            forfeited_amount=self.forfeited_amount,
        )
        return {'type': 'ir.actions.act_window_close'}
