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

    def _refuse_legacy_deposit_action(self):
        """The legacy deposit buttons changed a status and nothing else.

        They recorded, refunded and forfeited deposits without posting any
        accounting, and "Deposit Held" never counted them. Deposits are handled
        on the lease's Security Deposit records, which post to the deposit
        journal and keep these legacy fields in sync as read-only history.
        """
        raise UserError(_(
            "Deposits are recorded, refunded and forfeited on the lease's "
            "Security Deposit records (Deposits smart button), which post the "
            "accounting. The legacy deposit fields are kept as read-only history."))

    def action_record_deposit(self):
        return self._refuse_legacy_deposit_action()

    def action_refund_deposit(self):
        return self._refuse_legacy_deposit_action()

    def action_partial_refund_deposit(self):
        return self._refuse_legacy_deposit_action()

    def action_forfeit_deposit(self):
        return self._refuse_legacy_deposit_action()

    def action_reset_deposit(self):
        return self._refuse_legacy_deposit_action()
