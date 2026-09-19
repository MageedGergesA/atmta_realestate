# -*- coding: utf-8 -*-
"""M3 — pointing a cheque at what it pays.

The three shapes M3 asks for, in one screen:

* **Case A** — one cheque, one instalment. Select one line, take the default.
* **Case B** — two cheques, one instalment. Run this on each cheque; the
  obligation-side invariant lets both through as long as together they do not
  exceed what is owed.
* **Case C** — one cheque, several obligations. Select several lines; the
  wizard proposes a split by due date and refuses to exceed the face value.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AllocateWizard(models.TransientModel):
    _name = 'realestate.check.allocate.wizard'
    _description = 'Allocate a Cheque to Obligations'

    check_id = fields.Many2one(
        'realestate.check', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='check_id.company_id', readonly=True)
    currency_id = fields.Many2one(related='check_id.currency_id', readonly=True)
    partner_id = fields.Many2one(related='check_id.partner_id', readonly=True)
    check_amount = fields.Monetary(related='check_id.amount', readonly=True)
    already_allocated = fields.Monetary(
        related='check_id.allocated_amount', readonly=True)
    available_amount = fields.Monetary(
        related='check_id.unapplied_amount', readonly=True,
        string='Still Unapplied')

    line_ids = fields.One2many(
        'realestate.check.allocate.wizard.line', 'wizard_id',
        string='Obligations')
    to_allocate = fields.Monetary(compute='_compute_totals')
    remaining = fields.Monetary(compute='_compute_totals')

    @api.depends('line_ids.allocate_amount', 'available_amount')
    def _compute_totals(self):
        for wiz in self:
            total = sum(wiz.line_ids.filtered('selected').mapped(
                'allocate_amount'))
            wiz.to_allocate = total
            wiz.remaining = (wiz.currency_id or self.env.company.currency_id).round(
                (wiz.available_amount or 0.0) - total)

    @api.model
    def default_get(self, fields_list):
        """Propose the obligations this cheque plausibly pays.

        Sourced from the cheque's contract when it has one, oldest first, and
        pre-filled with a split that exactly consumes the unapplied balance.
        The user can override every number; nothing is created until they say
        so.
        """
        values = super().default_get(fields_list)
        check = self.env['realestate.check'].browse(
            values.get('check_id') or self.env.context.get('default_check_id'))
        if not check:
            return values
        obligations = check._candidate_obligations()
        currency = check.currency_id
        budget = check.unapplied_amount
        lines = []
        for obligation, outstanding in obligations:
            proposal = min(budget, outstanding) if budget > 0 else 0.0
            budget = currency.round(budget - proposal)
            lines.append((0, 0, {
                'sale_installment_id': (
                    obligation.id
                    if obligation._name == 'realestate.sale.installment'
                    else False),
                'rental_payment_id': (
                    obligation.id
                    if obligation._name == 'realestate.contract.payment'
                    else False),
                'outstanding_amount': outstanding,
                'allocate_amount': proposal,
                'selected': bool(proposal),
            }))
        values['line_ids'] = lines
        return values

    def action_allocate(self):
        self.ensure_one()
        selected = self.line_ids.filtered(
            lambda l: l.selected and l.allocate_amount > 0)
        if not selected:
            raise UserError(_("Select at least one obligation and an amount."))
        currency = self.currency_id
        if currency.compare_amounts(self.to_allocate, self.available_amount) > 0:
            raise UserError(_(
                "You are allocating %(total)s but only %(available)s of cheque "
                "%(check)s is unapplied. A cheque cannot pay more than it is "
                "worth.",
                total=currency.format(self.to_allocate),
                available=currency.format(self.available_amount),
                check=self.check_id.name))

        Allocation = self.env['realestate.check.allocation']
        Allocation.create([{
            'check_id': self.check_id.id,
            'sale_installment_id': line.sale_installment_id.id or False,
            'rental_payment_id': line.rental_payment_id.id or False,
            'allocated_amount': line.allocate_amount,
            'state': 'active',
        } for line in selected])

        self.check_id.message_post(body=_(
            "Allocated %(total)s across %(count)s obligation(s). Allocation is "
            "not payment: these obligations stay outstanding until the cheque "
            "clears and Odoo reconciles it.",
            total=currency.format(self.to_allocate), count=len(selected)))
        return {'type': 'ir.actions.act_window_close'}


class AllocateWizardLine(models.TransientModel):
    _name = 'realestate.check.allocate.wizard.line'
    _description = 'Cheque Allocation Proposal'
    _order = 'due_date, id'

    wizard_id = fields.Many2one(
        'realestate.check.allocate.wizard', required=True, ondelete='cascade')
    selected = fields.Boolean(default=True)
    sale_installment_id = fields.Many2one('realestate.sale.installment')
    rental_payment_id = fields.Many2one('realestate.contract.payment')
    description = fields.Char(compute='_compute_description')
    due_date = fields.Date(compute='_compute_description', store=True)
    outstanding_amount = fields.Monetary(readonly=True)
    allocate_amount = fields.Monetary(string='Allocate')
    currency_id = fields.Many2one(
        related='wizard_id.currency_id', readonly=True)

    @api.depends('sale_installment_id', 'rental_payment_id')
    def _compute_description(self):
        for line in self:
            obligation = line.sale_installment_id or line.rental_payment_id
            line.description = obligation.display_name if obligation else ''
            line.due_date = obligation.date_due if obligation else False
