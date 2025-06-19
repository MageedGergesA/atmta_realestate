from odoo import models, fields

class PropertyType(models.Model):
    _name = 'property.type'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Real Estate Property Type'

    name = fields.Char(string='Name',required=True, tracking=True)
    description = fields.Text(string='Description', tracking=True)
