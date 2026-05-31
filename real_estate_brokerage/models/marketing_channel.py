from odoo import fields, models


class MarketingChannel(models.Model):
    _name = 'realestate.marketing.channel'
    _description = 'Real Estate Marketing Channel'
    _order = 'sequence, name'

    name = fields.Char(string='Channel', required=True)
    sequence = fields.Integer(default=10)
    base_url = fields.Char(string='Base URL', help='Public site where listings are published, e.g. https://bayut.com')
    icon = fields.Char(help='Font Awesome class, e.g. "fa-globe"')
    active = fields.Boolean(default=True)
