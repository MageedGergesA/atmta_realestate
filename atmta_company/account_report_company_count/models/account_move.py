
# -*- coding: utf-8 -*-

from odoo import models, fields, api


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'
    _order = 'sequence, id'

    cheque_number = fields.Char(string='Cheque Number', tracking=True)
    description = fields.Char(string='Description')  # Editable field
    account_type = fields.Selection(related='account_id.account_type')
    sequence = fields.Integer()

class AccountMove(models.Model):
    _inherit = 'account.move'

    ref = fields.Char(
        string='General Description',
        copy=False,
        tracking=True,
        index='trigram',
    )

    @api.onchange('ref')
    def _onchange_ref_sync_description(self):
        for move in self:
            for line in move.line_ids:
                line.description = move.ref

    @api.onchange('line_ids')
    def _onchange_line_ids_fill_description(self):
        for move in self:
            for line in move.line_ids:
                if not line.description:
                    line.description = move.ref
