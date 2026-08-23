"""Where a purchase line came from — Wave 6 / AD-008.

A purchase order line that was raised from a requisition should say so, and
that link is demand metadata: it names a request line and nothing else. It is
declared here so that a purchase order can be traced back to the demand that
caused it without the tender, award and receipt machinery being installed.

Sourcing keeps its own link on the same model; the two are independent.
"""
from odoo import fields, models


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    re_material_request_line_id = fields.Many2one(
        'realestate.material.request.line', string='Material Request Line',
        ondelete='set null', index=True,
        help='Source line in the material request that spawned this PO line.',
    )
    re_material_request_id = fields.Many2one(
        'realestate.material.request',
        related='re_material_request_line_id.request_id', store=True, readonly=True,
    )
