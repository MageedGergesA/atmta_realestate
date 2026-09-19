# -*- coding: utf-8 -*-
"""Registering a bank return.

0.1's wizard carried an "Issue Penalty Invoice Now" tickbox that raised a
customer invoice as a side effect of recording a bank event. Billing a customer
should never be a side effect (M11), so the tickbox is gone: the wizard records
what the bank did, and the penalty is a separate, deliberate act on the bounce
record.

What the wizard *does* show, before anything is written, is the accounting
consequence — which invoices go back to outstanding, and whether the case needs
manual handling because the bank receipt had already been reconciled.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.check_states import BOUNCE_REASON, CHECK_AT_BANK


class BounceWizard(models.TransientModel):
    _name = 'realestate.check.bounce.wizard'
    _description = 'Register Check Bounce'

    check_id = fields.Many2one(
        'realestate.check', required=True, ondelete='cascade',
    )
    company_id = fields.Many2one(related='check_id.company_id', readonly=True)
    presentation_id = fields.Many2one(
        'realestate.check.presentation', string='Attempt',
        compute='_compute_context', readonly=True)
    reason = fields.Selection(
        BOUNCE_REASON, required=True, default='insufficient',
    )
    bounce_date = fields.Date(default=fields.Date.context_today, required=True)
    bank_reference = fields.Char(string='Bank Return Reference')
    returned_document = fields.Binary(string='Return Advice', attachment=True)
    returned_document_name = fields.Char()

    bank_charge_amount = fields.Monetary(
        string='Bank Charge',
        help="What the bank charged US. Our cost, not the customer's.")
    penalty_amount = fields.Monetary(
        string='Customer Penalty',
        help="What we intend to charge the drawer. Recorded now; invoiced "
             "separately and deliberately from the bounce record.")
    currency_id = fields.Many2one(
        related='check_id.currency_id', readonly=True,
    )
    notes = fields.Text()

    # ---------- the preview (M10) ----------
    accounting_preview = fields.Text(
        compute='_compute_context', readonly=True,
        string='What will happen to the ledger')
    needs_manual_accounting = fields.Boolean(compute='_compute_context')

    @api.depends('check_id')
    def _compute_context(self):
        for wiz in self:
            check = wiz.check_id
            wiz.presentation_id = check.current_presentation_id
            payment = check.payment_id
            if not payment:
                wiz.needs_manual_accounting = False
                wiz.accounting_preview = _(
                    "No payment has been registered for this cheque, so there "
                    "is nothing to unwind. The obligation was never shown as "
                    "settled and stays outstanding.")
                continue
            invoices = payment.reconciled_invoice_ids
            # `is_matched` alone, and for exactly the reason
            # `check._is_cash_confirmed` documents: Odoo 18 promotes a payment
            # to `paid` as soon as the invoices it settles read paid, with no
            # bank transaction involved. Testing `state` here made the PREVIEW
            # announce the manual-accounting path for every ordinary bounce —
            # the opposite of what `_restore_receivable` then did. A wizard
            # that predicts the wrong outcome is worse than one that predicts
            # nothing.
            if payment.is_matched:
                wiz.needs_manual_accounting = True
                wiz.accounting_preview = _(
                    "Payment %(payment)s has ALREADY been matched against a "
                    "bank transaction, so the original receipt genuinely "
                    "happened.\n\n"
                    "This bounce will NOT unwind it automatically — doing so "
                    "would break the bank reconciliation you have already "
                    "agreed. The cheque will be marked bounced and the record "
                    "will carry step-by-step instructions for Accounting.",
                    payment=payment.display_name)
            else:
                wiz.needs_manual_accounting = False
                wiz.accounting_preview = _(
                    "Payment %(payment)s (%(amount)s) will be unreconciled and "
                    "then cancelled through Odoo.\n\n"
                    "%(count)s invoice(s) return to outstanding: %(names)s\n\n"
                    "Nothing is deleted: the payment's journal entry is "
                    "reversed and stays visible.",
                    payment=payment.display_name,
                    amount=check.currency_id.format(payment.amount),
                    count=len(invoices),
                    names=', '.join(invoices.mapped('name')) or _('none'))

    def action_register_bounce(self):
        self.ensure_one()
        self.check_id._assert_group(
            'real_estate_checks.group_checks_treasurer',
            _('register a bounce'))
        if self.check_id.state not in CHECK_AT_BANK:
            raise UserError(_(
                "Only a cheque that is at the bank can bounce. "
                "%(name)s is %(state)s.",
                name=self.check_id.name, state=self.check_id.state))

        bounce = self.env['realestate.check.bounce'].create({
            'check_id': self.check_id.id,
            'presentation_id': self.presentation_id.id or False,
            'reason': self.reason,
            'bounce_date': self.bounce_date,
            'bank_reference': self.bank_reference,
            'returned_document': self.returned_document,
            'returned_document_name': self.returned_document_name,
            'bank_charge_amount': self.bank_charge_amount,
            'penalty_amount': self.penalty_amount,
            'notes': self.notes,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Bounce'),
            'res_model': 'realestate.check.bounce',
            'res_id': bounce.id,
            'view_mode': 'form',
        }
