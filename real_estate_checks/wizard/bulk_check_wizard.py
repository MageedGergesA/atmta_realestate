# -*- coding: utf-8 -*-
"""Bulk cheque generation from a contract's schedule.

### The bug this file existed to demonstrate

0.1 did:

```python
start_num = int(self.first_check_number.strip())
...
'check_number': str(num),
```

So `000012345` was generated as `12345`. Leading zeros are printed on the paper
and are part of the cheque's identity; destroying them makes every generated
number wrong for a chequebook that uses them — which is most of them. And any
bank whose numbering is not purely numeric (`AB-0012`) could not be bulk-loaded
at all, because `int()` raised.

The fix preserves the *format* of whatever the user typed: the trailing digit
run is incremented and re-padded to its original width, and any prefix is
carried through. `000012345` → `000012346`; `AB-0012` → `AB-0013`; `7` → `8`.
"""

import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: Any prefix, then a run of digits at the end. The digits are what increments.
_NUMBER_RE = re.compile(r'^(?P<prefix>.*?)(?P<digits>\d+)$')


def next_check_number(reference, offset):
    """`('000012345', 3)` → `'000012348'`. Format-preserving.

    Widening is allowed (999 → 1000) but never narrowing, so a chequebook that
    rolls over keeps working and one that does not keeps its padding.
    """
    match = _NUMBER_RE.match((reference or '').strip())
    if not match:
        raise UserError(_(
            "Cheque number '%s' does not end in digits, so a sequence cannot "
            "be generated from it. Enter the numbers individually instead."
        ) % reference)
    prefix = match.group('prefix')
    digits = match.group('digits')
    return '%s%s' % (prefix, str(int(digits) + offset).zfill(len(digits)))


class BulkCheckWizard(models.TransientModel):
    """Generate one cheque per instalment on a sale contract.

    Sequential cheque numbers map to instalments in due-date order, and each
    cheque's amount is the instalment's *uncovered* amount rather than its face
    value — an instalment already half secured by another cheque should not
    attract a second cheque for the whole thing.
    """
    _name = 'realestate.check.bulk.wizard'
    _description = 'Bulk-Create Checks from Installments'

    sale_contract_id = fields.Many2one(
        'realestate.sale.contract', string='Sale Contract', required=True,
    )
    company_id = fields.Many2one(
        related='sale_contract_id.company_id', readonly=True)
    partner_id = fields.Many2one(
        related='sale_contract_id.partner_id', readonly=True,
    )
    currency_id = fields.Many2one(
        related='sale_contract_id.currency_id', readonly=True)
    installment_ids = fields.Many2many(
        'realestate.sale.installment', string='Installments',
        compute='_compute_installments', store=True, readonly=False,
    )

    bank_id = fields.Many2one('res.bank', string='Drawer Bank', required=True)
    branch = fields.Char()
    account_number = fields.Char(string='Drawer Account')
    partner_bank_id = fields.Many2one(
        'res.partner.bank', string='Drawer Bank Account',
        domain="[('partner_id', '=', partner_id)]")
    first_check_number = fields.Char(
        string='First Check #', required=True,
        help="Whatever format the chequebook uses. Leading zeros are "
             "preserved: '000012345' generates '000012346', '000012347', …")
    issue_date = fields.Date(default=fields.Date.context_today, required=True)
    journal_id = fields.Many2one(
        'account.journal', string='Bank Journal',
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
        help='Journal used when each cheque is banked. Optional here.',
    )
    location_id = fields.Many2one(
        'realestate.check.location', string='Custody Location',
        domain="[('company_id', '=', company_id)]")

    skip_existing = fields.Boolean(
        string='Skip Covered Instalments', default=True,
        help='Do not generate a cheque for an instalment already fully '
             'secured by live cheques.',
    )
    preview_line_ids = fields.One2many(
        'realestate.check.bulk.wizard.line', 'wizard_id', string='Preview',
        readonly=True)

    @api.depends('sale_contract_id')
    def _compute_installments(self):
        for wiz in self:
            contract = wiz.sale_contract_id
            wiz.installment_ids = contract.installment_ids.filtered(
                lambda i: not i.is_cancelled) if contract else False

    def _eligible_installments(self):
        """Instalments that still need paper, in due order."""
        self.ensure_one()
        installments = self.installment_ids.sorted(
            key=lambda i: (i.date_due or fields.Date.today(), i.id))
        currency = self.currency_id or self.company_id.currency_id
        rows = []
        for inst in installments:
            outstanding = (inst.current_amount or 0.0) - (inst.paid_amount or 0.0)
            uncovered = outstanding - (inst.secured_by_checks_amount or 0.0)
            amount = uncovered if self.skip_existing else outstanding
            if currency.compare_amounts(amount, 0) <= 0:
                continue
            rows.append((inst, currency.round(amount)))
        return rows

    def _cheque_due_date(self, installment):
        """`(due_date, note)` for the cheque that will cover `installment`.

        A cheque cannot fall due before it was issued, so an instalment already
        past due when the paper is taken gets a cheque dated the issue date --
        the earliest date it can be presented -- rather than an invalid row
        that aborted the whole batch. It is still generated, because that is
        exactly the instalment Treasury most wants paper for; the preview says
        which rows were moved and why.
        """
        self.ensure_one()
        due = installment.date_due
        if due and self.issue_date and due < self.issue_date:
            return self.issue_date, _(
                "Instalment was due %(due)s, before the issue date; the "
                "cheque is dated %(issue)s.",
                due=due, issue=self.issue_date)
        return due, False

    def action_preview(self):
        """Show what would be created, before creating it.

        0.1 created the cheques straight from the button. A wizard that raises
        twenty financial instruments should say what they will be first.
        """
        self.ensure_one()
        self.preview_line_ids.unlink()
        rows = self._eligible_installments()
        if not rows:
            raise UserError(_(
                "Nothing to generate: every selected instalment is already "
                "fully secured by cheques, paid, or cancelled."))
        lines = []
        for index, (inst, amount) in enumerate(rows):
            due, note = self._cheque_due_date(inst)
            lines.append({
                'wizard_id': self.id,
                'sequence': index,
                'installment_id': inst.id,
                'check_number': next_check_number(
                    self.first_check_number, index),
                'due_date': due,
                'amount': amount,
                'note': note,
            })
        self.env['realestate.check.bulk.wizard.line'].create(lines)
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_generate(self):
        self.ensure_one()
        if not self.installment_ids:
            raise UserError(_("Pick at least one installment."))
        rows = self._eligible_installments()
        if not rows:
            raise UserError(_(
                "No checks generated — every selected instalment is already "
                "covered."))

        Check = self.env['realestate.check']
        currency = self.currency_id or self.company_id.currency_id
        created = Check
        for index, (inst, amount) in enumerate(rows):
            due, _note = self._cheque_due_date(inst)
            created |= Check.create({
                'partner_id': self.partner_id.id,
                'company_id': self.company_id.id,
                'sale_contract_id': self.sale_contract_id.id,
                'sale_installment_id': inst.id,
                'bank_id': self.bank_id.id,
                'branch': self.branch,
                'account_number': self.account_number,
                'partner_bank_id': self.partner_bank_id.id or False,
                'check_number': next_check_number(
                    self.first_check_number, index),
                'amount': amount,
                'issue_date': self.issue_date,
                'received_date': self.issue_date,
                'due_date': due,
                'currency_id': (inst.currency_id or currency).id,
                'journal_id': self.journal_id.id or False,
                'state': 'registered',
            })
        if self.location_id:
            self.env['realestate.check.custody'].transfer(
                created, to_custodian=self.env.user,
                to_location=self.location_id, reason='receipt',
                note=_('Bulk-generated from contract %s.')
                % self.sale_contract_id.name)

        self.sale_contract_id.message_post(body=_(
            "%(count)s cheque(s) registered, %(total)s face value, numbers "
            "%(first)s–%(last)s. Cheques received — not money received.",
            count=len(created),
            total=currency.format(sum(created.mapped('amount'))),
            first=created[0].check_number, last=created[-1].check_number))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Generated Checks'),
            'res_model': 'realestate.check',
            'view_mode': 'list,form',
            'domain': [('id', 'in', created.ids)],
        }


class BulkCheckWizardLine(models.TransientModel):
    _name = 'realestate.check.bulk.wizard.line'
    _description = 'Bulk Cheque Preview Line'
    _order = 'sequence'

    wizard_id = fields.Many2one(
        'realestate.check.bulk.wizard', required=True, ondelete='cascade')
    sequence = fields.Integer()
    installment_id = fields.Many2one('realestate.sale.installment')
    check_number = fields.Char()
    due_date = fields.Date()
    amount = fields.Monetary()
    note = fields.Char()
    currency_id = fields.Many2one(
        related='wizard_id.currency_id', readonly=True)
