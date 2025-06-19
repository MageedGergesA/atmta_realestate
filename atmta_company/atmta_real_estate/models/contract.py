from odoo import models, fields, _, api
from odoo.tools import date_utils
from dateutil.relativedelta import relativedelta
import datetime

class RealEstateContract(models.Model):
    _name = 'realestate.contract'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Real Estate Contract'

    name = fields.Char(string="Contract Reference", required=True, copy=False, readonly=True, default='New', tracking=True)
    partner_id = fields.Many2one('res.partner', string="Tenant", required=True, tracking=True)
    start_date = fields.Date(string="Start Date", required=True, tracking=True)
    end_date = fields.Date(string="End Date", required=True, tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
    ], default='draft', string="Status", tracking=True)
    notes = fields.Text(string="Terms and Conditions", tracking=True)
    line_ids = fields.One2many('realestate.contract.line', 'contract_id', string="Contract Lines")
    payment_ids = fields.One2many('realestate.contract.payment', 'contract_id', string="Scheduled Payments")
    payment_count = fields.Integer(compute='get_payment_count', default=0)
    last_generated = fields.Datetime(string="Last Payment Generation", readonly=True)
    contract_payment_ids = fields.One2many('realestate.contract.payment', 'contract_id', string="Payments")

    @api.depends('payment_ids')
    def get_payment_count(self):
        for rec in self:
            rec.payment_count = len(rec.payment_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
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
            # Skip if nothing changed since last generation
            if contract.last_generated and contract.write_date <= contract.last_generated and all(
                    line.write_date <= contract.last_generated for line in contract.line_ids
            ):
                continue

            # Unlink only non-invoiced payments
            safe_to_delete = contract.contract_payment_ids.filtered(lambda p: not p.move_id)
            safe_to_delete.unlink()

            for line in contract.line_ids:
                if not line.payment_plan_ids:
                    continue

                start = contract.start_date
                end = contract.end_date
                current_date = start

                while current_date <= end:
                    current_price = line.price
                    month_diff = (current_date.year - start.year) * 12 + (current_date.month - start.month)

                    valid_plans = line.payment_plan_ids.filtered(lambda r: r.start_month <= month_diff)
                    if not valid_plans:
                        current_date += relativedelta(months=1)
                        continue

                    plan_rule = max(valid_plans, key=lambda r: r.start_month)
                    delta = relativedelta(**{plan_rule.unit + 's': plan_rule.interval})
                    due_date = current_date

                    increase_total = 0.0
                    discount_total = 0.0
                    applied_increment_ids = []
                    applied_discount_ids = []

                    # Apply increments
                    active_increments = line.increment_rule_ids.filtered(
                        lambda r: r.start_month <= month_diff and (
                                r.duration_months == 0 or month_diff < r.start_month + r.duration_months
                        )
                    ).sorted(key=lambda r: r.priority)

                    for rule in active_increments:
                        if rule.increase_type == 'fixed':
                            increase = abs(rule.increase_value)
                            current_price += increase
                            increase_total += increase
                            applied_increment_ids.append(rule.id)
                        elif rule.increase_type == 'percent':
                            increase = current_price * abs(rule.increase_value) / 100
                            current_price += increase
                            increase_total += increase
                            applied_increment_ids.append(rule.id)

                    # Apply discounts
                    active_discounts = line.discount_rule_ids.filtered(
                        lambda r: r.start_month <= month_diff and (
                                r.duration_months == 0 or month_diff < r.start_month + r.duration_months
                        )
                    ).sorted(key=lambda r: r.priority)

                    for rule in active_discounts:
                        if rule.increase_type == 'fixed':
                            discount = abs(rule.increase_value)
                            current_price -= discount
                            discount_total += discount
                            applied_discount_ids.append(rule.id)
                        elif rule.increase_type == 'percent':
                            discount = current_price * abs(rule.increase_value) / 100
                            current_price -= discount
                            discount_total += discount
                            applied_discount_ids.append(rule.id)

                    # Don't recreate existing or protected entries
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

                    current_date += delta

            contract.last_generated = now
