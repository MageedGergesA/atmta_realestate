# -*- coding: utf-8 -*-
"""Naming the vendors to enquire with.

A button cannot pass a vendor list, and `action_create_rfqs()` refuses to
invent one — so the enterprise flow needs somewhere for the buyer to say who
is being asked. That is all this wizard is: it makes the decision explicit and
attributable instead of letting a supplier row order sort itself into an
award.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MaterialRequestRfq(models.TransientModel):
    _name = 'realestate.material.request.rfq'
    _description = 'Create Requests for Quotation'

    request_id = fields.Many2one(
        'realestate.material.request', required=True, readonly=True)
    vendor_ids = fields.Many2many(
        'res.partner', string='Enquire With',
        domain="[('supplier_rank', '>', 0)]", required=True)
    suggested_vendor_ids = fields.Many2many(
        'res.partner', 'rfq_wizard_suggested_vendor_rel', 'wizard_id',
        'partner_id', string='Suggested by the Catalogue', readonly=True,
        help="Vendors already listed against the requested products. A "
             "suggestion is not a shortlist and a shortlist is not an award.")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        request = self.env['realestate.material.request'].browse(
            values.get('request_id') or self.env.context.get(
                'default_request_id'))
        if request:
            suggested = request.line_ids.mapped(
                'product_id.seller_ids.partner_id')
            values['suggested_vendor_ids'] = [(6, 0, suggested.ids)]
        return values

    def action_create_rfqs(self):
        self.ensure_one()
        if not self.vendor_ids:
            raise UserError(_("Name at least one vendor to enquire with."))
        orders = self.request_id.action_create_rfqs(vendors=self.vendor_ids)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Requests for Quotation'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'target': 'current',
            'domain': [('id', 'in', orders.ids)],
        }
