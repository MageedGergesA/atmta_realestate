from odoo import models, fields, api

class RealEstateContractPayment(models.Model):
    _name = 'realestate.contract.payment'
    _order = 'date_due ASC'
    _description = 'Scheduled Contract Payment'

    contract_id = fields.Many2one('realestate.contract', string="Contract", required=True, ondelete='cascade')
    partner_id = fields.Many2one(related='contract_id.partner_id', string='Tenant/Partner')
    contract_line_id = fields.Many2one('realestate.contract.line', string="Contract Line", required=True, ondelete='cascade')
    date_due = fields.Date(string="Due Date", required=True)
    amount = fields.Float(string="Amount", required=True)
    state = fields.Selection([
        ('draft', 'Unpaid'),
        ('invoiced', 'Invoiced'),
        ('paid', 'Paid'),
    ], default='draft', string="Status")
    move_id = fields.Many2one('account.move', string="Invoice")
    increase_amount = fields.Float(string="Increase Amount", readonly=True)
    discount_amount = fields.Float(string="Discount Amount", readonly=True)

    payment_plan_id = fields.Many2one(
        'realestate.payment.plan',
        string="Payment Plan Rule",
        ondelete='set null'
    )
    increment_rule_ids = fields.Many2many(
        'realestate.contract.increment.rule',
        'rel_payment_increment_rule',
        'payment_id',
        'rule_id',
        string="Applied Increment Rules",
        domain=[('discount', '=', False)]
    )

    discount_rule_ids = fields.Many2many(
        'realestate.contract.increment.rule',
        'rel_payment_discount_rule',
        'payment_id',
        'rule_id',
        string="Applied Discount Rules",
        domain=[('discount', '=', True)]
    )
    discount_rule_id = fields.Many2one(
        'realestate.contract.increment.rule',
        string="Applied Discount Rule",
        domain="[('discount', '=', True)]",
        ondelete='set null'
    )
    name = fields.Char(string="Label", compute="_compute_name", store=True)

    @api.depends('contract_line_id', 'date_due')
    def _compute_name(self):
        for rec in self:
            prop_name = rec.contract_line_id.property_id.display_name or "Property"
            date_str = rec.date_due.strftime('%Y-%m-%d') if rec.date_due else 'N/A'
            rec.name = f"Rent for {prop_name} on {date_str}"
