from odoo import models, fields, api

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
