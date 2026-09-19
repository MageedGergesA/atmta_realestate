# -*- coding: utf-8 -*-
"""M10 / M11 — the bounce, and putting the receivable back.

This is the most dangerous path in the module, and it only became dangerous in
0.2. In 0.1 no accounting existed before a treasurer pressed "Mark Cleared", so
a bounce wrote a state and a chatter line and touched no ledger — safe, but only
by accident, and a bank return *after* clearance could not be recorded at all.

Now that presenting a deposit registers a real payment (M7), a bounce has to
undo real accounting. The rules it obeys:

* **Never delete a payment.** `unlink()` on a posted payment is not an
  accounting operation, it is destruction of evidence.
* **Never delete or edit an invoice.** The customer was correctly billed. The
  cheque failing does not un-bill them.
* **Never touch `payment_state` or a residual by hand.** Un-reconciling is what
  restores the residual, and Odoo does the arithmetic.

So the sequence is: **unreconcile, then cancel the payment.** Unreconciling
returns the invoice's receivable line to its full residual — the obligation is
outstanding again, which is exactly the truth. Cancelling the payment reverses
its journal entry through Odoo's own `action_cancel`, leaving the entry visible
rather than vanished.

### The already-bank-reconciled case

If the bank statement had already been matched to the payment and the bank later
posts a returned-cheque debit, the original receipt *did* happen and pretending
otherwise would leave the bank account unreconcilable. That case is detected and
routed to a controlled workflow: the module refuses to silently unwind a matched
bank transaction and instead tells Treasury exactly what to do. See
`_restore_receivable`.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .check_states import (
    BOUNCE_REASON,
    BOUNCE_RESOLUTION,
    CHECK_AT_BANK,
)

_logger = logging.getLogger(__name__)

#: 0.1 exported this name.
BOUNCE_REASONS = BOUNCE_REASON


class CheckBounce(models.Model):
    """A bank return on a presented cheque."""
    _name = 'realestate.check.bounce'
    _description = 'Check Bounce'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'bounce_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index=True,
    )
    check_id = fields.Many2one(
        'realestate.check', string='Check', required=True,
        ondelete='cascade', tracking=True, index=True,
    )
    presentation_id = fields.Many2one(
        'realestate.check.presentation', string='Presentation Attempt',
        readonly=True, ondelete='set null', index=True,
        help="Which trip to the bank failed. A cheque presented twice has two "
             "attempts and at most one bounce each.")
    company_id = fields.Many2one(
        related='check_id.company_id', store=True, index=True, readonly=True)
    partner_id = fields.Many2one(
        related='check_id.partner_id', string='Drawer', store=True,
        readonly=True,
    )
    amount = fields.Monetary(
        related='check_id.amount', store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related='check_id.currency_id', store=True, readonly=True,
    )
    sale_contract_id = fields.Many2one(
        related='check_id.sale_contract_id', store=True, readonly=True,
        index=True)
    project_id = fields.Many2one(
        related='check_id.project_id', store=True, readonly=True, index=True)

    bounce_date = fields.Date(
        default=fields.Date.context_today, required=True, tracking=True,
        index=True,
    )
    reason = fields.Selection(
        BOUNCE_REASON, required=True, default='insufficient', tracking=True,
    )
    bank_reference = fields.Char(
        string='Bank Return Reference',
        help="The bank's own reference for the return advice.")
    returned_document = fields.Binary(
        string='Return Advice', attachment=True)
    returned_document_name = fields.Char()

    # ------------------------------------------------------------------
    # Two different amounts, for two different parties (M11)
    # ------------------------------------------------------------------
    bank_charge_amount = fields.Monetary(
        string='Bank Charge',
        help="What the BANK charged us for handling the return. Our cost. "
             "Recorded here for the record; billing it on to the customer, if "
             "policy allows, is what the penalty is for.")
    bank_charge_move_id = fields.Many2one(
        'account.move', string='Bank Charge Entry', readonly=True, copy=False,
        help="Optional vendor bill / miscellaneous entry for the bank's fee.")
    penalty_amount = fields.Monetary(
        string='Customer Penalty', tracking=True,
        help="What WE charge the drawer for the returned cheque. A commercial "
             "decision, not a bank fee — 0.1 conflated the two into one "
             "'penalty' field.")
    penalty_invoice_id = fields.Many2one(
        'account.move', string='Penalty Invoice', readonly=True, copy=False,
    )

    # ------------------------------------------------------------------
    # Accounting effect, recorded rather than assumed
    # ------------------------------------------------------------------
    payment_id = fields.Many2one(
        'account.payment', string='Reversed Payment', readonly=True,
        copy=False,
        help="The payment that had been registered for the failed "
             "presentation, and which this bounce unwound.")
    accounting_handled = fields.Boolean(
        string='Receivable Restored', readonly=True, copy=False,
        help="True when the payment was unreconciled and cancelled, so the "
             "invoice is outstanding again.")
    accounting_note = fields.Text(
        readonly=True,
        help="What was actually done to the ledger, or what could not be done "
             "and why. Never blank when `accounting_handled` is false.")
    requires_manual_accounting = fields.Boolean(
        readonly=True, copy=False,
        help="Set when the bank had already matched the receipt, so unwinding "
             "it automatically would corrupt the bank reconciliation.")

    # ------------------------------------------------------------------
    # Resolution (M10)
    # ------------------------------------------------------------------
    resolution = fields.Selection(
        BOUNCE_RESOLUTION, default='pending', required=True, tracking=True,
        index=True)
    resolved = fields.Boolean(
        string='Resolved', compute='_compute_resolved', store=True,
        tracking=True,
        help="0.1 field, kept. Now derived from the resolution so the two "
             "cannot disagree.")
    resolved_date = fields.Date(readonly=True, copy=False)
    replacement_check_id = fields.Many2one(
        'realestate.check', string='Replacement Cheque', readonly=True,
        ondelete='set null')
    notes = fields.Text()

    _sql_constraints = [
        ('bounce_presentation_uniq', 'unique(presentation_id)',
         'A presentation attempt can only bounce once.'),
    ]

    @api.depends('resolution')
    def _compute_resolved(self):
        for rec in self:
            rec.resolved = rec.resolution != 'pending'

    @api.constrains('bank_charge_amount', 'penalty_amount')
    def _check_amounts_not_negative(self):
        for rec in self:
            if rec.bank_charge_amount < 0 or rec.penalty_amount < 0:
                raise ValidationError(_(
                    "Bank charges and penalties cannot be negative."))

    # ==================================================================
    # Create — the bounce itself
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                check = self.env['realestate.check'].browse(vals.get('check_id'))
                vals['name'] = self.env['ir.sequence'].with_company(
                    check.company_id.id or self.env.company.id).next_by_code(
                        'realestate.check.bounce') or 'BNC/NEW'
        records = super().create(vals_list)
        records._apply_bounce()
        return records

    def _apply_bounce(self):
        """Move the instrument and the ledger, in that order.

        0.1 did this inline in `create()` with no state guard at all, so a row
        created by import or by any user with the (over-broad) ACL flipped a
        cheque to `bounced` from *any* state — including `cleared` and
        `cancelled`.
        """
        for rec in self:
            check = rec.check_id
            if check.state not in CHECK_AT_BANK:
                raise UserError(_(
                    "Cheque %(name)s is '%(state)s'. Only a cheque that is at "
                    "the bank can be returned by it.",
                    name=check.name, state=check.state))

            attempt = rec.presentation_id or check.current_presentation_id
            if attempt and not rec.presentation_id:
                rec.presentation_id = attempt.id
            if attempt:
                attempt.write({'state': 'bounced',
                               'bounced_date': rec.bounce_date,
                               'bounce_id': rec.id})
                rec.payment_id = attempt.payment_id.id or False
            elif check.payment_id:
                rec.payment_id = check.payment_id.id

            rec._restore_receivable()

            check.write({'state': 'bounced', 'bounce_id': rec.id,
                         'bounce_date': rec.bounce_date})
            # The paper comes back from the bank. Recording that is what makes
            # a re-presentation later a real movement rather than a fiction.
            self.env['realestate.check.custody'].transfer(
                check, to_custodian=self.env.user,
                to_location=check.company_id.check_default_location_id or False,
                reason='return_from_bank',
                note=_('Returned by the bank: %s.') % dict(
                    BOUNCE_REASON).get(rec.reason, rec.reason))

            check.message_post(body=_(
                "Cheque bounced — %(reason)s. See bounce record %(ref)s.",
                reason=dict(BOUNCE_REASON)[rec.reason], ref=rec.name))
            if check.deposit_id:
                check.deposit_id._recompute_final_state()
        return True

    def _restore_receivable(self):
        """Put the customer's outstanding balance back. Safely.

        The order matters. Unreconciling first is what restores the invoice's
        residual; cancelling the payment afterwards reverses its entry through
        Odoo's own API. Doing it the other way round, or deleting anything,
        would leave the invoice reading as settled by a payment that no longer
        exists.
        """
        self.ensure_one()
        payment = self.payment_id
        if not payment:
            self.write({
                'accounting_handled': True,
                'accounting_note': _(
                    "No payment had been registered for this presentation, so "
                    "there was nothing to unwind. The obligation was never "
                    "shown as settled."),
            })
            return True

        move = payment.move_id

        # ---- the already-bank-matched case ----
        #
        # `is_matched` alone. Odoo 18 promotes a payment to `paid` as soon as
        # the invoices it settles read `paid`, with no bank transaction
        # involved (see `check._is_cash_confirmed`), so `state == 'paid'` here
        # would route every ordinary bounce down the manual path and leave the
        # receivable un-restored.
        if payment.is_matched:
            self.write({
                'accounting_handled': False,
                'requires_manual_accounting': True,
                'accounting_note': _(
                    "Payment %(payment)s has already been matched against a "
                    "bank transaction, so the original receipt genuinely "
                    "happened.\n\n"
                    "This module will not silently unwind a matched bank line: "
                    "doing so would leave the bank account unreconcilable "
                    "against the statement you already agreed.\n\n"
                    "In Accounting:\n"
                    "1. Record the bank's returned-cheque debit as a new bank "
                    "transaction.\n"
                    "2. Reconcile it against a reversal of payment %(payment)s "
                    "(Payments > %(payment)s > Reverse), or against the "
                    "customer's receivable directly.\n"
                    "3. The invoice returns to outstanding through that "
                    "reversal.\n\n"
                    "The cheque has been marked bounced and the obligation is "
                    "flagged; the ledger is untouched and awaits the entries "
                    "above.",
                    payment=payment.display_name),
            })
            self.message_post(body=self.accounting_note)
            _logger.info(
                "real_estate_checks: bounce %s left to manual accounting — "
                "payment %s is already bank-matched.", self.name, payment.name)
            return False

        # ---- the normal case: registered, not yet bank-matched ----
        notes = []
        if move and move.state == 'posted':
            reconciled = move.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable'
                and l.reconciled)
            invoices = payment.reconciled_invoice_ids
            if reconciled:
                reconciled.remove_move_reconcile()
                notes.append(_(
                    "Unreconciled payment %(payment)s from %(count)s "
                    "invoice(s): %(names)s. Their residuals are restored.",
                    payment=payment.display_name, count=len(invoices),
                    names=', '.join(invoices.mapped('name')) or '—'))
        try:
            payment.action_cancel()
            notes.append(_(
                "Payment %s cancelled; its journal entry is reversed, not "
                "deleted.") % payment.display_name)
        except UserError as err:
            self.write({
                'accounting_handled': False,
                'requires_manual_accounting': True,
                'accounting_note': _(
                    "The payment was unreconciled — the invoice is outstanding "
                    "again — but it could not be cancelled automatically: "
                    "%(error)s\n\nCancel or reverse payment %(payment)s in "
                    "Accounting.", error=err, payment=payment.display_name),
            })
            self.message_post(body=self.accounting_note)
            return False

        self.write({
            'accounting_handled': True,
            'accounting_note': '\n'.join(notes) or _(
                "The payment had not reached the ledger; nothing to unwind."),
        })
        self.message_post(body=self.accounting_note)
        return True

    # ==================================================================
    # Fees (M11) — preview, then confirm. Never silently on Bounce.
    # ==================================================================
    def action_issue_penalty_invoice(self):
        """Bill the customer for the returned cheque.

        Deliberately a separate, explicit act. 0.1's wizard had an "Issue
        Penalty Invoice Now" tickbox that raised an invoice as a side effect of
        pressing Bounce — invoicing a customer should not be a side effect of
        recording a bank event.
        """
        self.ensure_one()
        self.check_id._assert_group(
            'real_estate_checks.group_checks_treasurer',
            _('issue a bounce penalty invoice'))
        if self.penalty_invoice_id:
            raise UserError(_(
                "Penalty invoice %s already exists.") % self.penalty_invoice_id.name)
        if self.penalty_amount <= 0:
            raise UserError(_("Enter a positive penalty amount first."))

        product = self.company_id.check_bounce_penalty_product_id
        if not product:
            raise UserError(_(
                "No bounce penalty product is configured for %s.\n\n"
                "Set one under Settings > Real Estate Checks. No account is "
                "hard-coded — the product's category drives the income "
                "account, exactly as for any other sale."
            ) % self.company_id.display_name)

        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'invoice_date': fields.Date.context_today(self),
            'invoice_origin': self.check_id.sale_contract_id.name or self.check_id.name,
            'ref': _('Bounce %(bounce)s / cheque %(check)s',
                     bounce=self.name, check=self.check_id.name),
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id,
                'name': _('Returned cheque penalty — cheque %(number)s, '
                          '%(reason)s',
                          number=self.check_id.check_number,
                          reason=dict(BOUNCE_REASON)[self.reason]),
                'quantity': 1.0,
                'price_unit': self.penalty_amount,
            })],
        })
        self.penalty_invoice_id = invoice.id
        self.message_post(body=_(
            "Penalty invoice %(name)s raised for %(amount)s. It is in draft — "
            "post it in Accounting when you are ready to bill it.",
            name=invoice.name,
            amount=self.currency_id.format(self.penalty_amount)))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Penalty Invoice'),
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
        }

    # ==================================================================
    # Resolution (M10 / M12)
    # ==================================================================
    def action_mark_resolved(self, resolution=None):
        """0.1's method, kept. Now it has to say *how* it was resolved."""
        for rec in self:
            rec.write({
                'resolution': resolution or (
                    rec.resolution if rec.resolution != 'pending'
                    else 'settled_cash'),
                'resolved_date': fields.Date.context_today(rec),
            })
        return True

    def action_mark_accounting_done(self):
        """Close a bounce that was left for manual accounting. (M10)

        `accounting_handled` is read-only, and before this nothing set it once
        `_restore_receivable` had refused to unwind a bank-matched receipt: the
        bounce could never leave the manual queue and the cheque could never be
        re-presented. Treasury now confirms that Accounting has done its part.

        Confirmation, not assertion: the original payment must no longer settle
        the customer's receivable -- cancelled, reversed, or unreconciled from
        the invoices. Otherwise re-presenting would register the same money
        twice, which is exactly what the manual path exists to prevent.
        """
        self.ensure_one()
        self.check_id._assert_group(
            'real_estate_checks.group_checks_treasurer',
            _('close the manual accounting of a bounce'))
        if not self.requires_manual_accounting or self.accounting_handled:
            raise UserError(_(
                "Bounce %s is not awaiting manual accounting.") % self.name)
        if self._payment_still_settles_receivable():
            raise UserError(_(
                "Payment %(payment)s is still reconciled against the "
                "customer's receivable, so the original receipt still stands "
                "in the ledger. Reverse or cancel it, or unreconcile it from "
                "the invoices, in Accounting first.\n\n%(note)s",
                payment=self.payment_id.display_name,
                note=self.accounting_note or ''))
        note = _(
            "Manual accounting confirmed by %(user)s on %(date)s: payment "
            "%(payment)s no longer settles the receivable.",
            user=self.env.user.name,
            date=fields.Date.context_today(self),
            payment=self.payment_id.display_name or '—')
        self.write({
            'accounting_handled': True,
            'accounting_note': '\n\n'.join(
                part for part in (self.accounting_note, note) if part),
        })
        self.message_post(body=note)
        return True

    def _payment_still_settles_receivable(self):
        """Is the bounced payment still matched against a receivable?

        A reversal done the accountant's way (`Reverse` with cancellation)
        reconciles the payment's receivable line with the reversal entry; that
        match settles nothing and does not count. Any other counterpart -- an
        invoice, typically -- means the receipt still stands.
        """
        self.ensure_one()
        payment = self.payment_id
        if not payment or payment.state in ('canceled', 'rejected'):
            return False
        move = payment.move_id
        if not move or move.state != 'posted':
            return False
        receivable = move.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable')
        counterparts = (receivable.matched_debit_ids.debit_move_id
                        | receivable.matched_credit_ids.credit_move_id)
        reversals = move.reversal_move_ids
        return bool(counterparts.filtered(
            lambda l: l.move_id != move and l.move_id not in reversals))

    def action_authorize_representation(self):
        """M12 — send the same cheque back to the bank.

        Business policy sometimes allows a second attempt, typically after the
        drawer confirms funds. The first bounce is never destroyed: the cheque
        returns to `registered` and gets a *new* presentation attempt, so the
        history reads attempt 1 bounced, attempt 2 cleared.
        """
        self.ensure_one()
        self.check_id._assert_group(
            'real_estate_checks.group_checks_treasurer',
            _('authorise a re-presentation'))
        check = self.check_id
        if check.state != 'bounced':
            raise UserError(_(
                "Cheque %(name)s is '%(state)s', not bounced.",
                name=check.name, state=check.state))
        if self.requires_manual_accounting and not self.accounting_handled:
            raise UserError(_(
                "Bounce %s is still awaiting manual accounting. Re-presenting "
                "the cheque before the previous receipt is unwound would "
                "register the same money twice.\n\n%s"
            ) % (self.name, self.accounting_note or ''))

        check.write({
            'state': 'registered',
            'deposit_id': False,
            'payment_id': False,
        })
        self.write({
            'resolution': 're_presented',
            'resolved_date': fields.Date.context_today(self),
        })
        check.message_post(body=_(
            "Re-presentation authorised after bounce %s. The cheque is "
            "available for a new deposit slip; attempt %s is preserved."
        ) % (self.name, self.presentation_id.attempt if self.presentation_id else 1))
        return True

    def action_open_replacement_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Replace Bounced Cheque'),
            'res_model': 'realestate.check.replace.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_original_check_id': self.check_id.id,
                'default_bounce_id': self.id,
            },
        }

    @api.model
    def _cron_bounce_followup(self):
        """M34 — a reminder, never an accounting action."""
        pending = self.search([('resolution', '=', 'pending')])
        if not pending:
            return True
        todo = self.env.ref('mail.mail_activity_data_todo',
                            raise_if_not_found=False)
        if not todo:
            return True
        model_id = self.env['ir.model']._get_id('realestate.check.bounce')
        already = set(self.env['mail.activity'].search([
            ('res_model_id', '=', model_id),
            ('res_id', 'in', pending.ids),
        ]).mapped('res_id'))
        for rec in pending:
            if rec.id in already:
                continue
            self.env['mail.activity'].create({
                'res_model_id': model_id,
                'res_id': rec.id,
                'activity_type_id': todo.id,
                'summary': _('Unresolved bounce %s') % rec.name,
                'note': _(
                    "Cheque %(check)s bounced on %(date)s (%(reason)s) and has "
                    "no resolution. Re-present, replace, or refer it.",
                    check=rec.check_id.name, date=rec.bounce_date,
                    reason=dict(BOUNCE_REASON)[rec.reason]),
                'date_deadline': fields.Date.context_today(rec),
                'user_id': (rec.check_id.custodian_id or rec.create_uid).id,
            })
        return True
