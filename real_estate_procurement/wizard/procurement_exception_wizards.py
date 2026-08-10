# -*- coding: utf-8 -*-
"""Asking to pass a control, in the two places M3 has one.

Both wizards do the same small thing: put the position in front of the person
raising the request, make them type a reason, and create an exception record
that a manager then decides. Neither of them approves anything — a wizard that
both requested and granted an override would be the self-approval defect
rebuilt with a nicer form.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BudgetExceptionWizard(models.TransientModel):
    """Authority to approve demand that does not fit the remaining budget."""
    _name = 'realestate.procurement.budget.exception'
    _description = 'Request Budget Exception'

    request_id = fields.Many2one(
        'realestate.material.request', required=True, readonly=True)
    company_id = fields.Many2one(
        related='request_id.company_id', readonly=True)
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True)
    requested_amount = fields.Monetary(
        compute='_compute_position', readonly=True,
        help="The requisition's tax-exclusive control amount.")
    available_amount = fields.Monetary(compute='_compute_position',
                                       readonly=True)
    control_note = fields.Text(
        compute='_compute_position', readonly=True, string='Control Position',
        help="What the availability service says right now. Shown rather than "
             "summarised, because 'over budget' without the numbers is not "
             "something anybody can decide against.")
    reason = fields.Text(
        required=True,
        help="Why the project should spend past its available budget. This "
             "is the sentence the manager is agreeing to.")

    @api.depends('request_id')
    def _compute_position(self):
        """Read the position from the requisition, not from the context.

        An earlier version filled these in `default_get`, which works from the
        button — the context carries the requisition — and silently produced
        zeros for anything that passed `request_id` directly. A control
        document showing a confident 0.00 because it could not find its own
        source is worse than one that fails.
        """
        Control = self.env['realestate.procurement.control']
        for wizard in self:
            request = wizard.request_id
            positions = request._control_positions(
                ignore_own_reservations=True) if request else {}
            wizard.requested_amount = request._control_amount() if request \
                else 0.0
            wizard.available_amount = sum(
                position['available'] for position in positions.values())
            wizard.control_note = '\n\n'.join(
                Control.describe_position(position)
                for position in positions.values()) or _(
                    "No control position could be established for this "
                    "requisition.")

    def action_request(self):
        self.ensure_one()
        request = self.request_id
        exception = self.env[
            'realestate.procurement.control.exception'].create({
                'exception_type': (
                    'insufficient_data'
                    if request.control_status == 'insufficient_data'
                    else 'over_budget'),
                'company_id': request.company_id.id,
                'project_id': request.project_id.id,
                'request_id': request.id,
                'requested_amount': self.requested_amount,
                'available_amount': self.available_amount,
                'policy': self.env[
                    'realestate.procurement.control'].budget_policy_for(
                        request.project_id, request.company_id),
                'control_note': self.control_note,
                'reason': self.reason,
                'state': 'requested',
            })
        request.message_post(body=_(
            "Budget exception %s requested.") % exception.name)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.procurement.control.exception',
            'res_id': exception.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }


class PurchaseExceptionWizard(models.TransientModel):
    """Authority for a project purchase order with no requisition behind it."""
    _name = 'realestate.procurement.purchase.exception'
    _description = 'Request Direct Purchase Exception'

    order_id = fields.Many2one(
        'purchase.order', required=True, readonly=True)
    company_id = fields.Many2one(related='order_id.company_id', readonly=True)
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True)
    requested_amount = fields.Monetary(compute='_compute_order_amount',
                                       readonly=True)
    governance = fields.Char(compute='_compute_order_amount', readonly=True,
                             string='Policy')
    reason = fields.Text(
        required=True,
        help="Why this is being bought without going through a requisition — "
             "an emergency, a proprietary source, an existing agreement, a "
             "petty operational need. M3 does not judge which; it records "
             "which, so that somebody can.")

    @api.depends('order_id')
    def _compute_order_amount(self):
        for wizard in self:
            order = wizard.order_id
            wizard.requested_amount = sum(
                line._re_control_amount() for line in order.order_line)
            wizard.governance = dict(
                order._fields['re_governance'].selection).get(
                    order.re_governance, order.re_governance) if order else ''

    def action_request(self):
        self.ensure_one()
        order = self.order_id
        if order.state not in ('draft', 'sent'):
            raise UserError(_(
                "%s is already confirmed. An exception raised afterwards "
                "would be a note, not an authorisation.") % order.name)
        exception = self.env[
            'realestate.procurement.control.exception'].create({
                'exception_type': 'direct_purchase',
                'company_id': order.company_id.id,
                'project_id': order.re_project_id.id,
                'purchase_order_id': order.id,
                'requested_amount': self.requested_amount,
                'policy': order.re_governance,
                'reason': self.reason,
                'state': 'requested',
            })
        order.re_exception_id = exception
        order.message_post(body=_(
            "Direct purchase exception %s requested.") % exception.name)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.procurement.control.exception',
            'res_id': exception.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }


class ReservationReleaseWizard(models.TransientModel):
    """Releasing capacity by hand, with the reason attached."""
    _name = 'realestate.procurement.reservation.release'
    _description = 'Release Reservation'

    reservation_id = fields.Many2one(
        'realestate.procurement.reservation', required=True, readonly=True)
    amount_active = fields.Monetary(
        related='reservation_id.amount_active', readonly=True)
    currency_id = fields.Many2one(
        related='reservation_id.currency_id', readonly=True)
    reason = fields.Text(
        required=True,
        help="Why this demand no longer needs to hold the project's "
             "capacity. Capacity reappearing with no explanation is "
             "indistinguishable from a defect.")

    def action_release(self):
        self.ensure_one()
        if not self.env.user.has_group(
                'real_estate_procurement.group_procurement_manager'):
            raise UserError(_(
                "Releasing a reservation returns capacity to the project "
                "without anything having been bought. A manager decides "
                "that."))
        self.reservation_id._release(self.reason)
        return {'type': 'ir.actions.act_window_close'}
