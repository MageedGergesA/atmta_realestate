from odoo import models, fields

class AccountMove(models.Model):
    _inherit = 'account.move'

    contract_id = fields.Many2one(
        'realestate.contract',
        string="Contract",
        ondelete='set null',
        index=True
    )
