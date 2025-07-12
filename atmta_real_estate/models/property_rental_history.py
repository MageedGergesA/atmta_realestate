from odoo import fields,models, api, _


class RealEstateContractLineHistory(models.Model):
    _name = 'realestate.property.rental.history'
    _description = 'realestate property rental History'
    _order = 'start_date desc'

    contract_id = fields.Many2one('realestate.contract', string='Contract', required=True, ondelete='cascade')
    property_id = fields.Many2one('product.product', string='Property', required=True, ondelete='cascade')
    tenant_id = fields.Many2one(related='contract_id.partner_id', string="Tenant", store=True)
    start_date = fields.Date(string='Start Date')
    end_date = fields.Date(string='End Date')
    is_multi = fields.Boolean(string="From Multi-unit Line", readonly=True)
    contract_line_id = fields.Many2one('realestate.contract.line', string='Contract Line')
