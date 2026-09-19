# -*- coding: utf-8 -*-
"""M22 — the deposit workbench.

Treasury's actual job at the start of a day is: *which cheques have matured and
should go to the bank?* 0.1's answer was "open cheques one at a time and look".
This answers it in one screen, and every cheque it offers has already passed
the eligibility rules the deposit itself will enforce, so a batch built here
does not fail at confirmation.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.check_states import CHECK_DEPOSITABLE


class DepositWizard(models.TransientModel):
    """Group N registered cheques into one deposit slip."""
    _name = 'realestate.check.deposit.wizard'
    _description = 'Deposit Workbench'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    deposit_date = fields.Date(default=fields.Date.context_today, required=True)
    journal_id = fields.Many2one(
        'account.journal', string='Bank Journal', required=True,
        compute='_compute_journal', store=True, readonly=False,
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
        help="Where the batch is going. Defaults to the journal behind the "
             "company's configured cheque payment method, then to its first "
             "bank journal — a treasury with one bank should not have to "
             "restate it on every slip.",
    )
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
        help="One slip, one currency. Cheques in other currencies are not "
             "offered — a mixed batch produces a meaningless total.")
    payment_method_line_id = fields.Many2one(
        'account.payment.method.line', string='Payment Method',
        domain="[('payment_type', '=', 'inbound')]")
    bank_slip_ref = fields.Char()

    horizon_days = fields.Integer(
        string='Include Cheques Maturing Within (days)', default=0,
        help="0 offers only cheques that have already matured. Raise it to "
             "prepare tomorrow's batch — the company's early-presentation "
             "policy still applies at confirmation.")
    check_ids = fields.Many2many(
        'realestate.check', 'deposit_wizard_selected_rel', 'wizard_id',
        'check_id', string='Checks',
        default=lambda self: self._default_checks(),
    )
    eligible_check_ids = fields.Many2many(
        'realestate.check', 'deposit_wizard_eligible_rel', 'wizard_id',
        'check_id', string='Eligible Cheques',
        compute='_compute_eligible', readonly=True)
    total_amount = fields.Monetary(compute='_compute_total')
    check_count = fields.Integer(compute='_compute_total')

    @api.depends('company_id')
    def _compute_journal(self):
        for wiz in self:
            configured = wiz.company_id.check_payment_method_line_id.journal_id
            if configured:
                wiz.journal_id = configured
                continue
            wiz.journal_id = self.env['account.journal'].search([
                ('type', '=', 'bank'),
                ('company_id', '=', wiz.company_id.id),
            ], limit=1)

    def _default_checks(self):
        active_ids = self.env.context.get('active_ids') or []
        if self.env.context.get('active_model') == 'realestate.check' and active_ids:
            return [(6, 0, active_ids)]
        return False

    @api.depends('company_id', 'currency_id', 'horizon_days')
    def _compute_eligible(self):
        """What Treasury may actually present today.

        Deliberately the same predicates the deposit's `_validate_batch` uses,
        so nothing offered here is refused there.
        """
        today = fields.Date.context_today(self)
        for wiz in self:
            horizon = fields.Date.add(today, days=max(wiz.horizon_days, 0))
            checks = self.env['realestate.check'].search([
                ('company_id', '=', wiz.company_id.id),
                ('currency_id', '=', wiz.currency_id.id),
                ('state', 'in', list(CHECK_DEPOSITABLE)),
                ('deposit_id', '=', False),
                ('due_date', '<=', horizon),
            ])
            wiz.eligible_check_ids = checks.filtered(lambda c: not c.is_stale)

    @api.depends('check_ids.amount')
    def _compute_total(self):
        for wiz in self:
            wiz.check_count = len(wiz.check_ids)
            wiz.total_amount = sum(wiz.check_ids.mapped('amount'))

    def action_select_all_eligible(self):
        self.ensure_one()
        self.check_ids = [(6, 0, self.eligible_check_ids.ids)]
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_create_deposit(self):
        self.ensure_one()
        if not self.check_ids:
            raise UserError(_("Pick at least one check."))

        # 0.1 wrote `deposit_id` onto the cheques directly and then confirmed,
        # which skipped every validation except the state test. The slip is
        # created, populated and confirmed as one unit here, and confirmation
        # runs the full battery.
        deposit = self.env['realestate.check.deposit'].create({
            'company_id': self.company_id.id,
            'deposit_date': self.deposit_date,
            'journal_id': self.journal_id.id,
            'currency_id': self.currency_id.id,
            'bank_slip_ref': self.bank_slip_ref,
            'payment_method_line_id': self.payment_method_line_id.id or False,
            'check_ids': [(6, 0, self.check_ids.ids)],
        })
        if deposit._requires_other_confirmer():
            # Maker/checker: the person preparing a large batch may not also
            # present it, so confirming here could only ever be refused and
            # nobody could deposit through the workbench at all. The slip is
            # left in draft, prepared by this user, for someone else to
            # confirm. The batch is still validated now, so the preparer hears
            # about a problem before handing the slip on rather than after,
            # and only Treasury may prepare one, as confirming requires.
            deposit._assert_treasurer(_('prepare a deposit'))
            deposit._validate_batch()
            deposit.message_post(body=_(
                "Prepared by %s. Under this company's maker/checker policy "
                "another user must confirm it.") % self.env.user.name)
        else:
            deposit.action_confirm()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Deposit Slip'),
            'res_model': 'realestate.check.deposit',
            'res_id': deposit.id,
            'view_mode': 'form',
        }
