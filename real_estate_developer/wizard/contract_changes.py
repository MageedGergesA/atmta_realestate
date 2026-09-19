# -*- coding: utf-8 -*-
"""Phases 28–32 — wizards for post-signature changes.

Every one of these previews before it applies. That is not decoration: a
restructuring or a cancellation moves money on a signed contract, and the person
authorising it has to see the effect first. Each wizard raises a typed
`realestate.sale.contract.amendment`, so the change is approved and audited
through one architecture rather than five.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ContractChangeMixin(models.AbstractModel):
    """Shared plumbing: the contract, its money, and amendment creation."""
    _name = 'realestate.contract.change.mixin'
    _description = 'Contract Change Wizard Mixin'

    contract_id = fields.Many2one(
        'realestate.sale.contract', required=True, readonly=True)
    currency_id = fields.Many2one(
        related='contract_id.currency_id', readonly=True)
    company_id = fields.Many2one(
        related='contract_id.company_id', readonly=True)
    partner_id = fields.Many2one(
        related='contract_id.partner_id', readonly=True)
    property_id = fields.Many2one(
        related='contract_id.property_id', readonly=True)
    effective_date = fields.Date(
        required=True, default=fields.Date.context_today)
    reason = fields.Text(required=True)

    paid_amount = fields.Monetary(compute='_compute_position')
    outstanding_amount = fields.Monetary(compute='_compute_position')
    open_installment_count = fields.Integer(compute='_compute_position')

    @api.depends('contract_id')
    def _compute_position(self):
        for wiz in self:
            contract = wiz.contract_id
            wiz.paid_amount = contract._settled_amount() if contract else 0.0
            wiz.outstanding_amount = (
                contract._outstanding_amount() if contract else 0.0)
            wiz.open_installment_count = (
                len(contract._open_installments()) if contract else 0)

    def _create_amendment(self, amendment_type, financial_effect=0.0,
                          fee_amount=0.0, **extra):
        self.ensure_one()
        vals = {
            'contract_id': self.contract_id.id,
            'amendment_type': amendment_type,
            'effective_date': self.effective_date,
            'reason': self.reason,
            'financial_effect': financial_effect,
            'fee_amount': fee_amount,
        }
        vals.update(extra)
        return self.env['realestate.sale.contract.amendment'].create(vals)


class ContractRestructureWizard(models.TransientModel):
    """Phase 28 — re-cut the remaining schedule."""
    _name = 'realestate.contract.restructure'
    _description = 'Restructure Contract Payment Plan'
    _inherit = ['realestate.contract.change.mixin']

    new_plan_id = fields.Many2one(
        'realestate.payment.plan', string='New Payment Plan', required=True,
        domain="[('state', '=', 'active'), ('company_id', '=', company_id)]")
    state = fields.Selection([
        ('config', 'Configure'), ('preview', 'Preview')], default='config')
    line_ids = fields.One2many(
        'realestate.contract.restructure.line', 'wizard_id', readonly=True)
    new_total = fields.Monetary(compute='_compute_new_total')
    blocking_check_count = fields.Integer(compute='_compute_blocking')

    @api.depends('line_ids.amount')
    def _compute_new_total(self):
        for wiz in self:
            wiz.new_total = sum(wiz.line_ids.mapped('amount'))

    @api.depends('contract_id')
    def _compute_blocking(self):
        for wiz in self:
            contract = wiz.contract_id
            wiz.blocking_check_count = (
                len(contract._blocking_checks(contract._open_installments()))
                if contract else 0)

    def action_preview(self):
        """Show the proposed schedule before anything is written."""
        self.ensure_one()
        self.line_ids.unlink()
        contract = self.contract_id
        open_lines = contract._open_installments()
        if not open_lines:
            raise UserError(_(
                "Nothing is left to restructure — every remaining instalment "
                "is already invoiced or paid."))
        contract._assert_no_blocking_checks(open_lines)

        remaining = sum(open_lines.mapped('current_amount'))
        rows = self.new_plan_id._generate_schedule(
            total_price=remaining,
            booking_date=self.effective_date,
            contract_date=contract.contract_date,
            handover_date=(contract.expected_handover_date
                           or (contract.project_id.expected_handover_date
                               if contract.project_id else False)),
        )
        self.new_plan_id._validate_schedule_total(rows, remaining)
        self.env['realestate.contract.restructure.line'].create([{
            'wizard_id': self.id,
            'sequence': row['sequence'],
            'name': row['name'],
            'kind': row['kind'],
            'amount': row['amount'],
            'date_due': row['date_due'],
        } for row in rows])
        self.state = 'preview'
        return self._reopen()

    def action_apply(self):
        self.ensure_one()
        if self.state != 'preview':
            raise UserError(_(
                "Preview the new schedule before applying it."))
        amendment = self._create_amendment(
            'schedule_change',
            financial_effect=0.0,
            new_payment_plan_id=self.new_plan_id.id,
            old_payment_plan_id=self.contract_id.payment_plan_id.id,
        )
        amendment.action_submit()
        amendment.action_approve()
        self.contract_id._apply_restructure(
            amendment, self.new_plan_id, self.effective_date)
        amendment.write({'state': 'applied',
                         'applied_by_id': self.env.user.id,
                         'applied_on': fields.Datetime.now()})
        return {'type': 'ir.actions.act_window_close'}

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'views': [(False, 'form')],
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }


class ContractRestructureLine(models.TransientModel):
    _name = 'realestate.contract.restructure.line'
    _description = 'Restructure Preview Line'
    _order = 'sequence, date_due, id'

    wizard_id = fields.Many2one(
        'realestate.contract.restructure', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='wizard_id.currency_id', readonly=True)
    sequence = fields.Integer()
    name = fields.Char()
    kind = fields.Char()
    amount = fields.Monetary()
    date_due = fields.Date(string='Due')


class ContractSettlementWizard(models.TransientModel):
    """Phase 29 — clear the contract early."""
    _name = 'realestate.contract.settlement'
    _description = 'Contract Early Settlement'
    _inherit = ['realestate.contract.change.mixin']

    discount_amount = fields.Monetary(
        string='Settlement Discount',
        help="The incentive offered for paying early. Policy varies by "
             "developer, so it is entered rather than calculated.")
    fee_amount = fields.Monetary(string='Settlement Fee')
    net_settlement = fields.Monetary(compute='_compute_settlement')
    eligible_count = fields.Integer(compute='_compute_settlement')
    discount_percent = fields.Float(compute='_compute_settlement')

    @api.depends('contract_id', 'discount_amount', 'fee_amount')
    def _compute_settlement(self):
        for wiz in self:
            if not wiz.contract_id:
                wiz.net_settlement = 0.0
                wiz.eligible_count = 0
                wiz.discount_percent = 0.0
                continue
            preview = wiz.contract_id._settlement_preview(
                wiz.effective_date, wiz.discount_amount, wiz.fee_amount)
            wiz.net_settlement = preview['net_settlement']
            wiz.eligible_count = preview['eligible_count']
            wiz.discount_percent = (
                wiz.discount_amount / preview['outstanding_amount'] * 100.0
                if preview['outstanding_amount'] else 0.0)

    @api.constrains('discount_amount', 'fee_amount')
    def _check_amounts(self):
        for wiz in self:
            if wiz.discount_amount < 0 or wiz.fee_amount < 0:
                raise ValidationError(_(
                    "A settlement discount or fee cannot be negative."))
            if wiz.discount_amount > wiz.outstanding_amount:
                raise ValidationError(_(
                    "The discount exceeds the outstanding balance."))

    def action_apply(self):
        self.ensure_one()
        amendment = self._create_amendment(
            'early_settlement',
            financial_effect=-self.discount_amount + self.fee_amount,
            fee_amount=self.fee_amount)
        amendment.action_submit()
        # Measured on the discount percentage, which is what the authority
        # matrix bands are expressed in.
        amendment.financial_effect = self.discount_percent
        amendment.action_approve()
        amendment.financial_effect = -self.discount_amount + self.fee_amount
        self.contract_id._apply_settlement(
            amendment, self.discount_amount, self.fee_amount,
            self.effective_date)
        amendment.write({'state': 'applied',
                         'applied_by_id': self.env.user.id,
                         'applied_on': fields.Datetime.now()})
        return {'type': 'ir.actions.act_window_close'}


class ContractCancelWizard(models.TransientModel):
    """Phase 30 — cancellation with the financial effect shown first."""
    _name = 'realestate.contract.cancel'
    _description = 'Cancel Sale Contract'
    _inherit = ['realestate.contract.change.mixin']

    terminate = fields.Boolean(
        string='Terminate Rather Than Cancel',
        help="Termination is a developer-side ending of a live contract; "
             "cancellation is the buyer withdrawing. Both keep the history.")
    penalty_amount = fields.Monetary(string='Penalty')
    forfeit_amount = fields.Monetary(string='Forfeited')
    refundable_amount = fields.Monetary(compute='_compute_effect')
    posted_invoice_count = fields.Integer(compute='_compute_effect')
    future_installment_count = fields.Integer(compute='_compute_effect')
    release_property = fields.Boolean(
        string='Return Unit to the Market', default=True,
        help="Availability is recomputed from the authoritative engine; the "
             "unit is only freed if nothing else commits it.")

    @api.depends('contract_id', 'penalty_amount', 'forfeit_amount')
    def _compute_effect(self):
        for wiz in self:
            if not wiz.contract_id:
                wiz.refundable_amount = 0.0
                wiz.posted_invoice_count = 0
                wiz.future_installment_count = 0
                continue
            preview = wiz.contract_id._cancellation_preview(
                wiz.penalty_amount, wiz.forfeit_amount)
            wiz.refundable_amount = preview['refundable_amount']
            wiz.posted_invoice_count = len(preview['posted_invoices'])
            wiz.future_installment_count = len(preview['future_installments'])

    @api.constrains('penalty_amount', 'forfeit_amount')
    def _check_amounts(self):
        for wiz in self:
            if wiz.penalty_amount < 0 or wiz.forfeit_amount < 0:
                raise ValidationError(_("Amounts cannot be negative."))
            if wiz.penalty_amount + wiz.forfeit_amount > wiz.paid_amount + 0.0001:
                raise ValidationError(_(
                    "Penalty plus forfeiture (%s) exceeds what the buyer has "
                    "actually paid (%s)."
                ) % (wiz.penalty_amount + wiz.forfeit_amount, wiz.paid_amount))

    def action_apply(self):
        self.ensure_one()
        amendment = self._create_amendment(
            'cancellation',
            financial_effect=-self.outstanding_amount,
            fee_amount=self.penalty_amount)
        amendment.action_submit()
        amendment.action_approve()
        self.contract_id._apply_cancellation(
            amendment, reason_note=self.reason,
            penalty_amount=self.penalty_amount,
            forfeit_amount=self.forfeit_amount,
            release_property=self.release_property,
            terminate=self.terminate)
        amendment.write({'state': 'applied',
                         'applied_by_id': self.env.user.id,
                         'applied_on': fields.Datetime.now()})
        return {'type': 'ir.actions.act_window_close'}


class ContractUnitSwapWizard(models.TransientModel):
    """Phase 31 — move the deal to another unit."""
    _name = 'realestate.contract.unit.swap'
    _description = 'Swap Contract Unit'
    _inherit = ['realestate.contract.change.mixin']

    new_property_id = fields.Many2one(
        'realestate.property', string='New Unit', required=True,
        domain="[('is_available_for_sale', '=', True),"
               " ('company_id', '=', company_id)]")
    new_price = fields.Monetary(
        string='New Price',
        help="Leave empty to use the new unit's current list price.")
    price_difference = fields.Monetary(compute='_compute_swap')
    carried_forward = fields.Monetary(compute='_compute_swap')
    remaining_after = fields.Monetary(compute='_compute_swap')
    fee_amount = fields.Monetary(string='Swap Fee')

    @api.depends('contract_id', 'new_property_id', 'new_price')
    def _compute_swap(self):
        for wiz in self:
            if not wiz.contract_id or not wiz.new_property_id:
                wiz.price_difference = 0.0
                wiz.carried_forward = 0.0
                wiz.remaining_after = 0.0
                continue
            preview = wiz.contract_id._swap_preview(
                wiz.new_property_id, wiz.new_price or None)
            wiz.price_difference = preview['difference']
            wiz.carried_forward = preview['already_paid']
            wiz.remaining_after = max(
                preview['new_value'] - preview['already_paid'], 0.0)

    def action_apply(self):
        self.ensure_one()
        amendment = self._create_amendment(
            'unit_swap',
            financial_effect=self.price_difference,
            fee_amount=self.fee_amount,
            new_property_id=self.new_property_id.id,
            new_price=self.new_price or self.new_property_id.list_price_developer,
        )
        amendment.action_submit()
        amendment.action_approve()
        amendment.action_apply()
        return {'type': 'ir.actions.act_window_close'}


class ContractTransferWizard(models.TransientModel):
    """Phase 32 — change or add a buyer."""
    _name = 'realestate.contract.transfer'
    _description = 'Transfer Sale Contract'
    _inherit = ['realestate.contract.change.mixin']

    transfer_type = fields.Selection([
        ('correction', 'Buyer Correction'),
        ('add_co_buyer', 'Add Co-Buyer'),
        ('replace', 'Replace Buyer'),
        ('assignment', 'Assignment / Resale'),
    ], required=True, default='replace')
    new_partner_id = fields.Many2one(
        'res.partner', string='New Buyer', required=True)
    fee_amount = fields.Monetary(string='Transfer Fee')

    def action_apply(self):
        self.ensure_one()
        if self.transfer_type == 'add_co_buyer':
            # Adding a co-buyer does not change who holds the contract, so it
            # is a party addition rather than a transfer.
            self.env['realestate.sale.contract.party'].create({
                'contract_id': self.contract_id.id,
                'partner_id': self.new_partner_id.id,
                'role': 'co_buyer',
            })
            self.contract_id.message_post(body=_(
                "Co-buyer %s added.") % self.new_partner_id.display_name)
            return {'type': 'ir.actions.act_window_close'}

        amendment = self._create_amendment(
            'buyer_change',
            financial_effect=self.fee_amount,
            fee_amount=self.fee_amount,
            new_partner_id=self.new_partner_id.id,
        )
        amendment.action_submit()
        amendment.action_approve()
        amendment.action_apply()
        return {'type': 'ir.actions.act_window_close'}
