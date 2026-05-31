from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ContractDeposit(models.Model):
    """Security deposit lifecycle on rental contracts."""
    _inherit = 'realestate.contract'

    deposit_amount = fields.Monetary(string='Security Deposit', tracking=True)
    deposit_state = fields.Selection([
        ('none', 'Not Collected'),
        ('held', 'Held'),
        ('refunded', 'Refunded'),
        ('forfeited', 'Forfeited'),
        ('partial_refund', 'Partially Refunded'),
    ], default='none', tracking=True, required=True)
    deposit_paid_date = fields.Date(string='Deposit Paid On', tracking=True)
    deposit_settled_date = fields.Date(string='Deposit Settled On', tracking=True)
    deposit_refund_amount = fields.Monetary(string='Refund Amount', tracking=True)
    deposit_forfeit_amount = fields.Monetary(
        string='Forfeit Amount',
        compute='_compute_deposit_forfeit_amount', store=True,
    )
    deposit_notes = fields.Text(string='Deposit Notes')

    @api.depends('deposit_state', 'deposit_amount', 'deposit_refund_amount')
    def _compute_deposit_forfeit_amount(self):
        for rec in self:
            if rec.deposit_state == 'forfeited':
                rec.deposit_forfeit_amount = rec.deposit_amount
            elif rec.deposit_state == 'partial_refund':
                rec.deposit_forfeit_amount = max(rec.deposit_amount - rec.deposit_refund_amount, 0)
            else:
                rec.deposit_forfeit_amount = 0.0

    def action_record_deposit(self):
        for rec in self:
            if rec.deposit_state not in ('none',):
                raise UserError(_("Deposit can only be recorded from the 'Not Collected' state."))
            if not rec.deposit_amount:
                raise UserError(_("Set a deposit amount before recording it."))
            rec.write({
                'deposit_state': 'held',
                'deposit_paid_date': fields.Date.today(),
            })

    def action_refund_deposit(self):
        for rec in self:
            if rec.deposit_state != 'held':
                raise UserError(_("Only held deposits can be refunded."))
            rec.write({
                'deposit_state': 'refunded',
                'deposit_refund_amount': rec.deposit_amount,
                'deposit_settled_date': fields.Date.today(),
            })

    def action_partial_refund_deposit(self):
        """Partial refund — keep deposit_refund_amount as set by the user."""
        for rec in self:
            if rec.deposit_state != 'held':
                raise UserError(_("Only held deposits can be partially refunded."))
            if not rec.deposit_refund_amount or rec.deposit_refund_amount >= rec.deposit_amount:
                raise UserError(_(
                    "Set a partial refund amount between 0 and the full deposit (%s).",
                    rec.deposit_amount,
                ))
            rec.write({
                'deposit_state': 'partial_refund',
                'deposit_settled_date': fields.Date.today(),
            })

    def action_forfeit_deposit(self):
        for rec in self:
            if rec.deposit_state != 'held':
                raise UserError(_("Only held deposits can be forfeited."))
            rec.write({
                'deposit_state': 'forfeited',
                'deposit_refund_amount': 0.0,
                'deposit_settled_date': fields.Date.today(),
            })

    def action_reset_deposit(self):
        for rec in self:
            rec.write({
                'deposit_state': 'none',
                'deposit_paid_date': False,
                'deposit_settled_date': False,
                'deposit_refund_amount': 0.0,
            })
