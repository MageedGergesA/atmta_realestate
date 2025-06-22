from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class RealEstateContractLine(models.Model):
    _name = 'realestate.contract.line'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'property_id'
    _description = 'Contract Property Line'

    contract_id = fields.Many2one('realestate.contract', string="Contract", required=True, ondelete='cascade')
    partner_id = fields.Many2one(related='contract_id.partner_id', string='Tenant/Partner')
    property_id = fields.Many2one('product.product', string="Property", domain="[('is_property', '=', True)]", required=True)
    payment_plan_ids = fields.Many2many(
        'realestate.payment.plan',
        'rel_contract_line_payment_plan',  # relation table name
        'contract_line_id',  # this model's column
        'payment_plan_id',  # related model's column
        string='Payment Plans'
    )
    increment_rule_ids = fields.Many2many(
        'realestate.contract.increment.rule',
        'rel_contract_line_increment_rule_inc',
        'contract_line_id',
        'rule_id',
        string="Increment Rules",
        domain=[('discount', '=', False)]
    )

    discount_rule_ids = fields.Many2many(
        'realestate.contract.increment.rule',
        'rel_contract_line_increment_rule_disc',
        'contract_line_id',
        'rule_id',
        string="Discount Rules",
        domain=[('discount', '=', True)]
    )
    price = fields.Float(string="Base Rent", required=True)
    notes = fields.Text(string="Line Notes")

    start_date = fields.Date(string="Line Start Date", required=True)
    end_date = fields.Date(string="Line End Date", required=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('ready', 'Ready'),
        ('confirmed', 'Confirmed'),
        ('invoiced', 'Invoiced'),
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('terminated', 'Terminated'),
    ], default='draft', string='Line Status', tracking=True)

    def _cron_update_line_statuses(self):
        today = fields.Date.today()
        lines = self.search([
            ('state', 'in', ['confirmed', 'invoiced', 'active']),
            ('contract_id.state', '=', 'active')
        ])

        for line in lines:
            if line.state != 'terminated':
                if line.start_date <= today <= line.end_date:
                    line.state = 'active'
                elif today > line.end_date:
                    line.state = 'expired'

    def action_terminate_line(self):
        for line in self:
            line.state = 'terminated'

    @api.onchange('contract_id')
    def _onchange_contract_id(self):
        for rec in self:
            if rec.contract_id:
                rec.start_date = rec.contract_id.start_date
                rec.end_date = rec.contract_id.end_date


    @api.constrains('start_date', 'end_date', 'contract_id')
    def _check_dates_within_contract(self):
        for line in self:
            contract = line.contract_id
            if not contract:
                continue
            if line.start_date < contract.start_date:
                raise ValidationError(_("Line start date cannot be before contract start date."))
            if line.end_date > contract.end_date:
                raise ValidationError(_("Line end date cannot be after contract end date."))
            if line.start_date > line.end_date:
                raise ValidationError(_("Line start date must be before end date."))

