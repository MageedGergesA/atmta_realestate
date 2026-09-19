"""Settle a held security deposit: keep part of it, return the rest.

The deposit form's Refund and Forfeit buttons always act on the whole amount
held, and the move-out's "Settle Deposit" only opened that form. So a move-out
with deductions could not be settled from the screens: the manager had to keep
all of the deposit or none of it. This dialog takes the two amounts and the
reason, then calls the deposit's own ``action_forfeit`` and ``action_refund``.
No accounting is done here; the deposit posts exactly as it always did.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_is_zero


class DepositSettlementWizard(models.TransientModel):
    _name = 'realestate.deposit.settlement.wizard'
    _description = 'Settle Security Deposit'

    deposit_id = fields.Many2one(
        'realestate.contract.deposit', string='Deposit', required=True,
        readonly=True, ondelete='cascade')
    move_out_id = fields.Many2one(
        'realestate.move.out', string='Move-Out', readonly=True)
    currency_id = fields.Many2one(related='deposit_id.currency_id')
    held_amount = fields.Monetary(related='deposit_id.held_amount', string='Held')
    deduction_amount = fields.Monetary(
        string='Keep (Deductions)',
        help="Recognised as income through the forfeited-deposit account.")
    refund_amount = fields.Monetary(
        string='Refund to Tenant', compute='_compute_refund_amount', store=True,
        readonly=False)
    reason = fields.Text(
        string='Reason',
        help="Required when anything is kept. The tenant is entitled to know "
             "what was withheld and why.")

    @api.depends('deduction_amount', 'held_amount')
    def _compute_refund_amount(self):
        for wiz in self:
            wiz.refund_amount = max((wiz.held_amount or 0.0) - (wiz.deduction_amount or 0.0), 0.0)

    def action_confirm(self):
        self.ensure_one()
        deposit = self.deposit_id
        rounding = deposit.currency_id.rounding if deposit.currency_id else 0.01
        keep = self.deduction_amount or 0.0
        refund = self.refund_amount or 0.0
        if keep < 0 or refund < 0:
            raise UserError(_("Amounts cannot be negative."))
        if float_compare(keep + refund, deposit.held_amount, precision_rounding=rounding) > 0:
            raise UserError(_(
                "%(keep)s kept plus %(refund)s refunded is more than the "
                "%(held)s held.", keep=keep, refund=refund, held=deposit.held_amount))
        if float_is_zero(keep + refund, precision_rounding=rounding):
            raise UserError(_("Enter an amount to keep or to refund."))
        reason = (self.reason or '').strip()
        if not float_is_zero(keep, precision_rounding=rounding):
            if not reason:
                raise UserError(_("Keeping part of a deposit requires a written reason."))
            deposit.action_forfeit(amount=keep, reason=reason)
        if not float_is_zero(refund, precision_rounding=rounding):
            deposit.action_refund(amount=refund, reason=reason or None)
        return {'type': 'ir.actions.act_window_close'}


class ContractDeposit(models.Model):
    _inherit = 'realestate.contract.deposit'

    def action_open_settlement(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Settle Deposit'),
            'res_model': 'realestate.deposit.settlement.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_deposit_id': self.id,
                'default_reason': self.settlement_reason or False,
            },
        }
