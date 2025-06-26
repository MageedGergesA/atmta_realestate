from odoo import models, fields, _, api
from odoo.exceptions import UserError
from odoo.tools import date_utils
from dateutil.relativedelta import relativedelta
import datetime

class RealEstateContract(models.Model):
    _name = 'realestate.contract'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Real Estate Contract'

    name = fields.Char(string="Contract Reference", required=True, copy=False, readonly=False,
        index='trigram',
        default=lambda self: _('New'))
    partner_id = fields.Many2one('res.partner', string="Tenant", required=True, tracking=True)
    start_date = fields.Date(string="Start Date", required=True, tracking=True)
    end_date = fields.Date(string="End Date", required=True, tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('ready', 'Ready'),
        ('confirmed', 'Confirmed'),
        ('invoiced', 'Invoiced'),
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('terminated', 'Terminated'),
    ], string='Status', default='draft', tracking=True, readonly=True)
    notes = fields.Text(string="Terms and Conditions", tracking=True)
    line_ids = fields.One2many('realestate.contract.line', 'contract_id', string="Contract Lines")
    payment_ids = fields.One2many('realestate.contract.payment', 'contract_id', string="Scheduled Payments")
    payment_count = fields.Integer(compute='get_payment_count', default=0)
    move_ids = fields.One2many('account.move', 'contract_id', string='Invoices')
    invoice_count = fields.Integer(compute='get_invoice_count', default=0)
    last_generated = fields.Datetime(string="Last Payment Generation", readonly=True)
    contract_payment_ids = fields.One2many('realestate.contract.payment', 'contract_id', string="Payments")
    attachment_ids = fields.Many2many('ir.attachment', 'contract_attachment_rel', 'contract_id',
                                      'attachment_contract_id', 'Attachments',
                                      help="You may attach files to this template, to be added to all "
                                           "emails created from this template")
    is_renewed = fields.Boolean(string='Is Renewed Contract')
    old_contract_id = fields.Many2one('realestate.contract', string='Old Contract')
    # New computed fields
    total_scheduled = fields.Monetary(
        string="Total Scheduled",
        compute="_compute_totals",
        store=True
    )
    total_paid = fields.Monetary(
        string="Total Paid",
        compute="_compute_totals",
        store=True
    )
    balance_due = fields.Monetary(
        string="Remaining Balance",
        compute="_compute_totals",
        store=True
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id.id,
        required=True
    )

    utility_line_ids = fields.One2many('realestate.contract.utility.line', 'contract_id', string="Utilities")
    total_utilities = fields.Monetary(string="Total Utilities", compute='_compute_utilities')
    paid_utilities = fields.Monetary(string="Paid Utilities", compute='_compute_utilities')
    net_income = fields.Monetary(string="Net Income", compute='_compute_utilities')

    @api.depends('utility_line_ids.amount', 'utility_line_ids.bill_paid')
    def _compute_utilities(self):
        for contract in self:
            utilities = contract.utility_line_ids
            contract.total_utilities = sum(utilities.mapped('amount'))
            contract.paid_utilities = sum(utilities.filtered(lambda l: l.bill_paid).mapped('amount'))
            contract.net_income = contract.total_paid - contract.paid_utilities

    def action_generate_payment_lines(self):
        self.ensure_one()
        self.action_generate_payment_schedule()
        if self.payment_ids:
            self.state = 'ready'
            self.line_ids.write({'state':'ready'})

    def action_reset_to_draft(self):
        for contract in self:
            contract.state = 'draft'
            contract.line_ids.write({'state': 'draft'})

    def action_confirm(self):
        self.ensure_one()
        if self.state != 'ready':
            raise UserError("You must generate payment lines before confirming the contract.")
        self.state = 'confirmed'
        self.line_ids.write({'state':'confirmed'})

    def action_generate_invoices(self):
        self.ensure_one()
        if self.state != 'confirmed':
            raise UserError("You must confirm the contract before generating invoices.")
        self.action_create_invoices()
        if self.move_ids:
            self.state = 'invoiced'
            self.line_ids.write({'state':'invoiced'})


    def action_activate(self):
        for contract in self:
            if contract.state != 'invoiced':
                raise UserError("You must generate invoices before activating the contract.")
            contract.state = 'active'

            # Auto-activate lines that match contract start date
            for line in contract.line_ids:
                if (
                        line.state not in ['terminated', 'expired']
                        and line.start_date == contract.start_date
                ):
                    line.state = 'active'

    def action_terminate(self):
        for contract in self:
            if contract.state not in ['confirmed', 'invoiced', 'active']:
                raise UserError("Only confirmed, invoiced, or active contracts can be terminated.")

            # Cancel only draft invoices
            draft_moves = contract.contract_payment_ids.mapped('move_id').filtered(lambda m: m.state == 'draft')
            for move in draft_moves:
                move.button_cancel()

            # Update contract and its lines
            contract.state = 'terminated'
            contract.line_ids.filtered(lambda l: l.state != 'expired').write({'state': 'terminated'})

    def check_contract_expiry(self):
        for contract in self.search([('state', '=', 'active')]):
            if contract.end_date and contract.end_date < fields.Date.today():
                contract.state = 'expired'

    @api.depends('contract_payment_ids.amount', 'contract_payment_ids.move_state',
                 'contract_payment_ids.move_id.amount_total', 'contract_payment_ids.move_id.state')
    def _compute_totals(self):
        for contract in self:
            scheduled = 0.0
            paid = 0.0
            for line in contract.contract_payment_ids:
                scheduled += line.amount or 0.0
                if line.move_id and line.move_id.state == 'posted':
                    paid += line.amount or 0.0
            contract.total_scheduled = scheduled
            contract.total_paid = paid
            contract.balance_due = scheduled - paid

    @api.depends('payment_ids')
    def get_payment_count(self):
        for rec in self:
            rec.payment_count = len(rec.payment_ids)

    @api.depends('move_ids')
    def get_invoice_count(self):
        for rec in self:
            rec.invoice_count = len(rec.move_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _("New")) == _("New"):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.contract') or 'New'
        return super().create(vals_list)

    def action_open_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Scheduled Payments',
            'res_model': 'realestate.contract.payment',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {
                'default_contract_id': self.id,
                'group_by': 'contract_line_id',  # 👈 This triggers default grouping
            },        }

    def action_generate_payment_schedule(self):
        now = datetime.datetime.now()

        for contract in self:
            # Skip if nothing changed
            if contract.last_generated and contract.write_date <= contract.last_generated and all(
                    line.write_date <= contract.last_generated for line in contract.line_ids
            ):
                continue

            # Remove only non-invoiced lines
            contract.contract_payment_ids.filtered(lambda p: not p.move_id).unlink()

            for line in contract.line_ids:
                if not line.payment_plan_ids:
                    continue

                # Use contract line's custom date range
                start = line.start_date or contract.start_date
                end = line.end_date or contract.end_date
                current_date = start

                while current_date <= end:
                    current_price = line.price
                    due_date = current_date

                    # Compute time differences
                    delta = relativedelta(current_date, start)
                    days_passed = (current_date - start).days
                    months_passed = delta.years * 12 + delta.months

                    def to_days(unit, value):
                        return {
                            'day': value,
                            'week': value * 7,
                            'month': value * 30,
                            'year': value * 365,
                        }.get(unit, 0)

                    # Filter valid payment plans
                    valid_plans = line.payment_plan_ids.filtered(
                        lambda r: to_days(r.start_after_unit, r.start_after) <= days_passed
                    )
                    if not valid_plans:
                        current_date += relativedelta(days=1)
                        continue

                    # Pick the best plan
                    plan_rule = sorted(
                        valid_plans,
                        key=lambda r: to_days(r.start_after_unit, r.start_after),
                        reverse=True
                    )[0]

                    # Resolve interval
                    unit_map = {
                        'day': 'days',
                        'week': 'weeks',
                        'month': 'months',
                        'year': 'years',
                    }
                    unit_key = unit_map.get(plan_rule.unit)
                    if not unit_key:
                        raise UserError(f"Invalid interval unit '{plan_rule.unit}' in payment plan.")

                    interval_delta = relativedelta(**{unit_key: plan_rule.interval})

                    increase_total = 0.0
                    discount_total = 0.0
                    applied_increment_ids = []
                    applied_discount_ids = []

                    # Apply increment rules
                    active_increments = line.increment_rule_ids.filtered(
                        lambda r: r.start_month <= months_passed and (
                                r.duration_months == 0 or months_passed < r.start_month + r.duration_months
                        )
                    ).sorted(key=lambda r: r.priority)

                    for rule in active_increments:
                        value = abs(rule.increase_value)
                        if rule.increase_type == 'percent':
                            value = current_price * value / 100
                        current_price += value
                        increase_total += value
                        applied_increment_ids.append(rule.id)

                    # Apply discount rules
                    active_discounts = line.discount_rule_ids.filtered(
                        lambda r: r.start_month <= months_passed and (
                                r.duration_months == 0 or months_passed < r.start_month + r.duration_months
                        )
                    ).sorted(key=lambda r: r.priority)

                    for rule in active_discounts:
                        value = abs(rule.increase_value)
                        if rule.increase_type == 'percent':
                            value = current_price * value / 100
                        current_price -= value
                        discount_total += value
                        applied_discount_ids.append(rule.id)

                    # Avoid duplicates
                    exists = self.env['realestate.contract.payment'].search_count([
                        ('contract_id', '=', contract.id),
                        ('contract_line_id', '=', line.id),
                        ('date_due', '=', due_date),
                    ])
                    if not exists:
                        self.env['realestate.contract.payment'].create({
                            'contract_id': contract.id,
                            'contract_line_id': line.id,
                            'date_due': due_date,
                            'amount': current_price,
                            'payment_plan_id': plan_rule.id,
                            'increase_amount': increase_total,
                            'discount_amount': discount_total,
                            'increment_rule_ids': [(6, 0, applied_increment_ids)],
                            'discount_rule_ids': [(6, 0, applied_discount_ids)],
                        })

                    current_date += interval_delta

            contract.last_generated = now

    def action_create_invoices(self):
        invoices = self.env['account.move']
        for contract in self:
            grouped = {}
            for payment in contract.contract_payment_ids.filtered(lambda p: not p.move_id):
                key = (payment.contract_id.id, payment.date_due)
                grouped.setdefault(key, []).append(payment)

            for (contract_id, date_due), payments in grouped.items():
                partner = contract.partner_id
                lines = []
                for p in payments:
                    lines.append((0, 0, {
                        'name': f'Rent for {p.contract_line_id.property_id.display_name} on {p.date_due}',
                        'quantity': 1,
                        'price_unit': p.amount,
                        'account_id': p.contract_line_id.property_id.categ_id.property_account_income_categ_id.id,
                    }))
                invoice = self.env['account.move'].create({
                    'move_type': 'out_invoice',
                    'partner_id': partner.id,
                    'contract_id': contract.id,
                    'invoice_date': date_due,
                    'invoice_line_ids': lines,
                })
                invoices |= invoice
                for p in payments:
                    p.move_id = invoice.id
                    p.state = 'invoiced'

    def action_get_invoices(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Invoices',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
        }

    def contract_xlsx_report(self):
        print('hello')
