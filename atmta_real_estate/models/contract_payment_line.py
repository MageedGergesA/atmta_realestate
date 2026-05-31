from odoo import models, fields


class RealEstateContractPaymentLine(models.Model):
    _name = 'realestate.contract.payment.line'
    _description = 'Additional Charge on a Scheduled Payment'
    _order = 'payment_id, id'

    payment_id = fields.Many2one(
        'realestate.contract.payment', string="Payment",
        required=True, ondelete='cascade', index=True)
    contract_id = fields.Many2one(related='payment_id.contract_id', store=True, index=True)
    charge_type = fields.Selection([
        ('maintenance', 'Maintenance'),
        ('utility', 'Utility'),
        ('penalty', 'Penalty / Late Fee'),
        ('service', 'Service'),
        ('other', 'Other'),
    ], string="Charge Type", default='other', required=True)
    name = fields.Char(string="Description", required=True)
    amount = fields.Float(string="Amount", required=True)
    product_id = fields.Many2one(
        'product.product', string="Product",
        help="Optional. Drives the income account and taxes on the invoice line. "
             "Falls back to the property's product when left empty.")
    maintenance_request_id = fields.Many2one(
        'realestate.maintenance.request', string="Maintenance Request",
        ondelete='set null', copy=False,
        help="Source maintenance request, when this charge was billed from one.")
