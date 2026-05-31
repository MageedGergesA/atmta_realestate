from odoo import fields, models


class LostReason(models.Model):
    _name = 'realestate.lost.reason'
    _description = 'Real Estate Lead Lost Reason'
    _order = 'sequence, name'

    name = fields.Char(string='Reason', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
