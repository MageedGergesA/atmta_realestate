# -*- coding: utf-8 -*-
"""Reopening an approved requisition, with the reason typed in."""

from odoo import _, fields, models


class MaterialRequestRevise(models.TransientModel):
    _name = 'realestate.material.request.revise'
    _description = 'Revise Procurement Requisition'

    request_id = fields.Many2one(
        'realestate.material.request', required=True, readonly=True)
    current_basis = fields.Text(readonly=True)
    reason = fields.Text(
        required=True,
        string='Why is the approved basis changing?',
        help="Read later by whoever asks why the quantity is not the one "
             "that was approved.")

    def action_revise(self):
        self.ensure_one()
        self.request_id.action_revise(reason=self.reason)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Requisition'),
            'res_model': 'realestate.material.request',
            'res_id': self.request_id.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }
