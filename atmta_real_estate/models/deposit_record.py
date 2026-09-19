"""Security deposits as real financial records (Phase 14).

The pre-upgrade design was a ``deposit_state`` selection plus a couple of
amount fields on the lease. That records an *opinion* about a deposit; it does
not record the money. There was no journal entry, no bank trace, no approval,
and a forfeited deposit never reached the P&L.

A security deposit is **money you owe back**. It is a liability from the day it
arrives until the day it is returned, applied or forfeited -- never revenue.
This module posts it that way:

===================== ==================================================
Event                 Accounting
===================== ==================================================
Received              ``account.payment`` (inbound) into the deposit
                      journal, landing on the **deposit liability**
                      account instead of the receivable
Refunded              ``account.payment`` (outbound) clearing the same
                      liability
Forfeited             journal entry: Dr Deposit Liability,
                      Cr Forfeited-Deposit Income -- the only point at
                      which a deposit ever becomes income
Applied to arrears    journal entry: Dr Deposit Liability,
                      Cr Receivable, then reconciled against the
                      tenant's open invoices by Odoo
===================== ==================================================

The legacy ``realestate.contract.deposit_state`` field is kept and kept in
sync, so existing views and any downstream reader continue to work.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

DEPOSIT_STATES = [
    ('draft', 'Draft'),
    ('requested', 'Requested'),
    ('received', 'Received'),
    ('held', 'Held'),
    ('partially_refunded', 'Partially Refunded'),
    ('refunded', 'Refunded'),
    ('forfeited', 'Forfeited'),
    ('applied', 'Applied to Arrears'),
    ('cancelled', 'Cancelled'),
]

#: New deposit state -> the legacy ``contract.deposit_state`` value.
LEGACY_DEPOSIT_MAP = {
    'draft': 'none',
    'requested': 'none',
    'received': 'held',
    'held': 'held',
    'partially_refunded': 'partial_refund',
    'refunded': 'refunded',
    'forfeited': 'forfeited',
    'applied': 'forfeited',
    'cancelled': 'none',
}


class ContractDepositRecord(models.Model):
    _name = 'realestate.contract.deposit'
    _description = 'Lease Security Deposit'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'contract_id, id desc'

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'),
    )
    contract_id = fields.Many2one(
        'realestate.contract', string='Lease', required=True,
        ondelete='cascade', index=True, tracking=True,
    )
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True, readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Tenant', required=True, tracking=True, index=True,
        compute='_compute_partner', store=True, readonly=False,
    )
    property_id = fields.Many2one(
        'realestate.property', string='Property', tracking=True,
        compute='_compute_partner', store=True, readonly=False,
    )
    currency_id = fields.Many2one(
        related='contract_id.currency_id', store=True, readonly=True,
    )

    # ---------------- Amounts ----------------
    requested_amount = fields.Monetary(
        string='Requested', required=True, tracking=True,
    )
    received_amount = fields.Monetary(string='Received', readonly=True, tracking=True)
    refunded_amount = fields.Monetary(string='Refunded', readonly=True, tracking=True)
    forfeited_amount = fields.Monetary(string='Forfeited', readonly=True, tracking=True)
    applied_amount = fields.Monetary(
        string='Applied to Arrears', readonly=True, tracking=True)
    held_amount = fields.Monetary(
        string='Currently Held', compute='_compute_held_amount', store=True,
        tracking=True,
        help="Received minus everything that has left the liability. This is "
             "what the company still owes the tenant.",
    )

    # ---------------- Accounting links ----------------
    journal_id = fields.Many2one(
        'account.journal', string='Journal',
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
        check_company=True,
        help="Defaults to the company's configured deposit journal.",
    )
    deposit_account_id = fields.Many2one(
        'account.account', string='Deposit Liability Account',
        check_company=True,
        domain="[('account_type', 'in', ('liability_current', 'liability_non_current')), "
               "('reconcile', '=', True)]",
        compute='_compute_deposit_account', store=True, readonly=False,
        help="Liability account holding the deposit. Never a revenue account.",
    )
    payment_ids = fields.Many2many(
        'account.payment', string='Payments', copy=False,
        help="Bank movements: the receipt and any refund.",
    )
    move_ids = fields.Many2many(
        'account.move', string='Journal Entries', copy=False,
        help="Forfeiture and application entries.",
    )
    payment_count = fields.Integer(compute='_compute_counts')
    move_count = fields.Integer(compute='_compute_counts')

    # ---------------- Lifecycle ----------------
    state = fields.Selection(
        DEPOSIT_STATES, default='draft', required=True, tracking=True,
        index=True, copy=False,
    )
    request_date = fields.Date(default=fields.Date.context_today, tracking=True)
    received_date = fields.Date(readonly=True, tracking=True)
    refund_date = fields.Date(readonly=True, tracking=True)
    settlement_reason = fields.Text(
        string='Settlement Reason',
        help="Why the deposit was refunded in part, forfeited or applied. "
             "Required for anything other than a full refund.",
    )
    approver_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False, tracking=True)
    approval_date = fields.Datetime(readonly=True, copy=False)
    attachment_ids = fields.Many2many(
        'ir.attachment', 'realestate_deposit_attachment_rel',
        'deposit_id', 'attachment_id', string='Attachments',
    )
    notes = fields.Text()

    _sql_constraints = [
        ('requested_positive', 'CHECK(requested_amount >= 0)',
         'The requested deposit cannot be negative.'),
    ]

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('contract_id')
    def _compute_partner(self):
        for rec in self:
            if rec.contract_id:
                rec.partner_id = rec.partner_id or rec.contract_id.partner_id
                rec.property_id = rec.property_id or rec.contract_id.property_id

    @api.depends('company_id')
    def _compute_deposit_account(self):
        for rec in self:
            if not rec.deposit_account_id:
                rec.deposit_account_id = rec.company_id.re_deposit_account_id

    @api.depends('received_amount', 'refunded_amount', 'forfeited_amount',
                 'applied_amount')
    def _compute_held_amount(self):
        for rec in self:
            rec.held_amount = max(
                rec.received_amount - rec.refunded_amount
                - rec.forfeited_amount - rec.applied_amount, 0.0)

    @api.depends('payment_ids', 'move_ids')
    def _compute_counts(self):
        for rec in self:
            rec.payment_count = len(rec.payment_ids)
            rec.move_count = len(rec.move_ids)

    # ==================================================================
    # Configuration guards
    # ==================================================================
    def _assert_accounting_ready(self):
        """Fail loudly and specifically rather than posting to a wrong account."""
        self.ensure_one()
        # The account is taken from Settings when the deposit is created. A
        # deposit requested before Settings were filled in kept an empty
        # account, and stayed unreceivable after the setup was done. Until money
        # has moved it has no account of its own yet, so it takes today's.
        if (not self.deposit_account_id and not self.received_amount
                and self.company_id.re_deposit_account_id):
            self.deposit_account_id = self.company_id.re_deposit_account_id
        if not self.deposit_account_id:
            raise UserError(_(
                "No security-deposit liability account is configured for "
                "company '%s'. Set it under Settings → Rental → Security Deposits.",
                self.company_id.display_name))
        if self.deposit_account_id.account_type not in (
                'liability_current', 'liability_non_current'):
            raise UserError(_(
                "Account '%s' is not a liability account. A security deposit is "
                "money owed back to the tenant and must never be posted to "
                "revenue.", self.deposit_account_id.display_name))
        if not self.deposit_account_id.reconcile:
            raise UserError(_(
                "Account '%s' must allow reconciliation so each tenant's "
                "receipt can be matched with their refund.",
                self.deposit_account_id.display_name))
        journal = self.journal_id or self.company_id.re_deposit_journal_id
        if not journal:
            raise UserError(_(
                "No deposit journal is configured for company '%s'.",
                self.company_id.display_name))
        return journal

    def _require_manager(self):
        """Refunds and forfeitures move real money -- gate them."""
        if self.env.su or self.env.user.has_group(
                'atmta_real_estate.group_rental_manager'):
            return True
        raise AccessError(_(
            "Approving a deposit refund, forfeiture or application requires "
            "the Rental Manager permission."))

    # ==================================================================
    # Workflow
    # ==================================================================
    def action_request(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft deposit can be requested."))
            if rec.requested_amount <= 0:
                raise UserError(_("Set the deposit amount before requesting it."))
            rec.state = 'requested'
            rec.message_post(body=_("Deposit of %s requested.", rec.requested_amount))
        return True

    def action_register_receipt(self, amount=None, date=None):
        """Record the deposit arriving. Posts an inbound payment to the
        liability account -- not to the receivable, and not to income."""
        for rec in self:
            if rec.state not in ('draft', 'requested'):
                raise UserError(_(
                    "Deposit %s has already been received.", rec.name))
            journal = rec._assert_accounting_ready()
            amount = amount if amount is not None else rec.requested_amount
            if amount <= 0:
                raise UserError(_("The received amount must be positive."))
            payment = rec._post_deposit_payment(
                amount, journal, 'inbound', date or fields.Date.context_today(rec))
            rec.write({
                'received_amount': rec.received_amount + amount,
                'received_date': date or fields.Date.context_today(rec),
                'state': 'held',
                'payment_ids': [(4, payment.id)],
            })
            rec.message_post(body=_(
                "Deposit of %(amount)s received and held as a liability "
                "(payment %(payment)s).",
                amount=amount, payment=payment.name))
        return True

    def action_refund(self, amount=None, reason=None):
        """Return the deposit (in full or in part) to the tenant."""
        self._require_manager()
        for rec in self:
            if rec.state not in ('held', 'received', 'partially_refunded'):
                raise UserError(_(
                    "Deposit %s is not in a state that can be refunded.", rec.name))
            journal = rec._assert_accounting_ready()
            amount = amount if amount is not None else rec.held_amount
            rec._check_settlement_amount(amount)
            if amount < rec.held_amount and not (reason or rec.settlement_reason):
                raise UserError(_(
                    "A partial refund needs a written reason -- the tenant is "
                    "entitled to know what was withheld."))
            payment = rec._post_deposit_payment(
                amount, journal, 'outbound', fields.Date.context_today(rec))
            refunded = rec.refunded_amount + amount
            remaining = rec.received_amount - refunded - rec.forfeited_amount - rec.applied_amount
            rec.write({
                'refunded_amount': refunded,
                'refund_date': fields.Date.context_today(rec),
                'settlement_reason': reason or rec.settlement_reason,
                'state': 'refunded' if remaining <= 0.01 else 'partially_refunded',
                'approver_id': self.env.user.id,
                'approval_date': fields.Datetime.now(),
                'payment_ids': [(4, payment.id)],
            })
            rec.message_post(body=_(
                "Refunded %(amount)s to %(tenant)s. %(reason)s",
                amount=amount, tenant=rec.partner_id.display_name,
                reason=reason or ''))
        return True

    def action_forfeit(self, amount=None, reason=None):
        """Recognise the deposit as income. The only path from liability to P&L."""
        self._require_manager()
        for rec in self:
            if rec.state not in ('held', 'received', 'partially_refunded'):
                raise UserError(_(
                    "Deposit %s is not in a state that can be forfeited.", rec.name))
            rec._assert_accounting_ready()
            income = rec.company_id.re_deposit_forfeit_income_account_id
            if not income:
                raise UserError(_(
                    "No forfeited-deposit income account is configured for "
                    "company '%s'.", rec.company_id.display_name))
            amount = amount if amount is not None else rec.held_amount
            rec._check_settlement_amount(amount)
            if not (reason or rec.settlement_reason):
                raise UserError(_(
                    "Forfeiting a deposit requires a written reason."))
            move = rec._post_liability_transfer(
                amount, income,
                _("Forfeited security deposit — %s") % rec.contract_id.display_name)
            forfeited = rec.forfeited_amount + amount
            remaining = rec.received_amount - rec.refunded_amount - forfeited - rec.applied_amount
            rec.write({
                'forfeited_amount': forfeited,
                'settlement_reason': reason or rec.settlement_reason,
                'state': 'forfeited' if remaining <= 0.01 else 'partially_refunded',
                'approver_id': self.env.user.id,
                'approval_date': fields.Datetime.now(),
                'move_ids': [(4, move.id)],
            })
            rec.message_post(body=_(
                "Forfeited %(amount)s to income. Reason: %(reason)s",
                amount=amount, reason=reason or rec.settlement_reason))
        return True

    def action_apply_to_arrears(self, amount=None, reason=None):
        """Use the deposit to settle the tenant's outstanding rent.

        Moves the liability to the receivable and lets Odoo reconcile it
        against the open invoices -- the deposit never touches revenue.
        """
        self._require_manager()
        for rec in self:
            if rec.state not in ('held', 'received', 'partially_refunded'):
                raise UserError(_(
                    "Deposit %s is not in a state that can be applied.", rec.name))
            rec._assert_accounting_ready()
            receivable = rec.partner_id.with_company(
                rec.company_id).property_account_receivable_id
            if not receivable:
                raise UserError(_(
                    "Tenant '%s' has no receivable account.",
                    rec.partner_id.display_name))
            outstanding = rec._outstanding_invoices()
            due = sum(outstanding.mapped('amount_residual'))
            amount = amount if amount is not None else min(rec.held_amount, due)
            rec._check_settlement_amount(amount)
            if amount <= 0:
                raise UserError(_(
                    "There is nothing outstanding on lease '%s' to apply the "
                    "deposit against.", rec.contract_id.display_name))
            move = rec._post_liability_transfer(
                amount, receivable,
                _("Security deposit applied to arrears — %s")
                % rec.contract_id.display_name)
            rec._reconcile_with_invoices(move, receivable, outstanding)
            applied = rec.applied_amount + amount
            remaining = rec.received_amount - rec.refunded_amount - rec.forfeited_amount - applied
            rec.write({
                'applied_amount': applied,
                'settlement_reason': reason or rec.settlement_reason,
                'state': 'applied' if remaining <= 0.01 else 'partially_refunded',
                'approver_id': self.env.user.id,
                'approval_date': fields.Datetime.now(),
                'move_ids': [(4, move.id)],
            })
            rec.message_post(body=_(
                "Applied %(amount)s of the deposit against outstanding rent.",
                amount=amount))
        return True

    def action_cancel(self):
        for rec in self:
            if rec.received_amount:
                raise UserError(_(
                    "Deposit %s has money against it and cannot be cancelled. "
                    "Refund or forfeit it instead.", rec.name))
            rec.state = 'cancelled'
        return True

    def _check_settlement_amount(self, amount):
        self.ensure_one()
        if amount <= 0:
            raise UserError(_("The amount must be positive."))
        if amount > self.held_amount + 0.01:
            raise UserError(_(
                "Only %(held)s is still held on deposit %(name)s -- you cannot "
                "settle %(amount)s.",
                held=self.held_amount, name=self.name, amount=amount))

    # ==================================================================
    # Accounting primitives
    # ==================================================================
    def _post_deposit_payment(self, amount, journal, direction, date):
        """Inbound/outbound payment whose counterpart is the deposit liability."""
        self.ensure_one()
        payment = self.env['account.payment'].with_company(self.company_id).create({
            'amount': amount,
            'date': date,
            'payment_type': direction,
            'partner_type': 'customer',
            'partner_id': self.partner_id.id,
            'journal_id': journal.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'destination_account_id': self.deposit_account_id.id,
            'memo': _("Security deposit — %s") % self.contract_id.display_name,
        })
        payment.action_post()
        return payment

    def _post_liability_transfer(self, amount, counterpart_account, label):
        """Dr deposit liability / Cr ``counterpart_account``.

        A plain journal entry through ``account.move`` -- the standard engine.
        Nothing here writes into a posted move or invents its own ledger.
        """
        self.ensure_one()
        journal = (self.company_id.re_deposit_journal_id
                   or self.journal_id
                   or self.env['account.journal'].search([
                       ('type', '=', 'general'),
                       ('company_id', '=', self.company_id.id)], limit=1))
        if not journal:
            raise UserError(_(
                "No journal available to post the deposit settlement for "
                "company '%s'.", self.company_id.display_name))
        move = self.env['account.move'].with_company(self.company_id).create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'company_id': self.company_id.id,
            'date': fields.Date.context_today(self),
            'ref': label,
            'line_ids': [
                (0, 0, {
                    'name': label,
                    'account_id': self.deposit_account_id.id,
                    'partner_id': self.partner_id.id,
                    'debit': amount,
                    'credit': 0.0,
                }),
                (0, 0, {
                    'name': label,
                    'account_id': counterpart_account.id,
                    'partner_id': self.partner_id.id,
                    'debit': 0.0,
                    'credit': amount,
                }),
            ],
        })
        move.action_post()
        return move

    def _outstanding_invoices(self):
        self.ensure_one()
        return self.env['account.move'].search([
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')),
            ('partner_id', '=', self.partner_id.id),
            ('company_id', '=', self.company_id.id),
            ('contract_id', '=', self.contract_id.id),
        ], order='invoice_date_due, id')

    def _reconcile_with_invoices(self, move, receivable, invoices):
        """Let Odoo match the transfer against the open invoices."""
        self.ensure_one()
        credit_line = move.line_ids.filtered(
            lambda line: line.account_id == receivable and line.credit > 0)
        invoice_lines = invoices.line_ids.filtered(
            lambda line: line.account_id == receivable and not line.reconciled)
        if credit_line and invoice_lines:
            (credit_line | invoice_lines).reconcile()

    # ==================================================================
    # ORM
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.contract.deposit') or _('New')
        deposits = super().create(vals_list)
        deposits._sync_legacy_deposit_state()
        return deposits

    def write(self, vals):
        res = super().write(vals)
        if 'state' in vals:
            self._sync_legacy_deposit_state()
        return res

    def _sync_legacy_deposit_state(self):
        """Keep the pre-upgrade ``contract.deposit_state`` truthful.

        The old field and its buttons still exist and still work; this makes
        sure they agree with the real financial record instead of drifting.
        """
        for rec in self:
            legacy = LEGACY_DEPOSIT_MAP.get(rec.state)
            if not legacy or not rec.contract_id:
                continue
            contract = rec.contract_id
            values = {'deposit_state': legacy}
            if rec.received_amount and not contract.deposit_amount:
                values['deposit_amount'] = rec.received_amount
            if rec.refunded_amount:
                values['deposit_refund_amount'] = rec.refunded_amount
            if rec.received_date:
                values['deposit_paid_date'] = rec.received_date
            if rec.refund_date:
                values['deposit_settled_date'] = rec.refund_date
            contract.with_context(re_deposit_sync=True).write(values)

    @api.constrains('deposit_account_id')
    def _check_deposit_account_type(self):
        for rec in self:
            account = rec.deposit_account_id
            if account and account.account_type not in (
                    'liability_current', 'liability_non_current'):
                raise ValidationError(_(
                    "'%s' is not a liability account. Security deposits are a "
                    "liability until settled.", account.display_name))

    # ==================================================================
    # Smart buttons
    # ==================================================================
    def action_view_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Deposit Payments'),
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.payment_ids.ids)],
        }

    def action_view_moves(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Deposit Journal Entries'),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.move_ids.ids)],
        }


class ContractDepositRecordMixin(models.Model):
    _inherit = 'realestate.contract'

    deposit_record_ids = fields.One2many(
        'realestate.contract.deposit', 'contract_id', string='Deposits',
    )
    deposit_record_count = fields.Integer(compute='_compute_deposit_records')
    deposit_held_total = fields.Monetary(
        string='Deposit Held', compute='_compute_deposit_records', store=True,
    )

    @api.depends('deposit_record_ids.held_amount', 'deposit_record_ids.state')
    def _compute_deposit_records(self):
        for rec in self:
            rec.deposit_record_count = len(rec.deposit_record_ids)
            rec.deposit_held_total = sum(rec.deposit_record_ids.mapped('held_amount'))

    def action_create_deposit(self):
        """Open a new deposit pre-filled from the lease."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Security Deposit'),
            'res_model': 'realestate.contract.deposit',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_contract_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_property_id': self.property_id.id,
                'default_requested_amount': self.deposit_amount or 0.0,
            },
        }

    def action_view_deposits(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Security Deposits'),
            'res_model': 'realestate.contract.deposit',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }
