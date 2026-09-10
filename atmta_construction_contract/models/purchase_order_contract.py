# -*- coding: utf-8 -*-
"""The link from a purchase order to the package it executes.

`realestate.construction.contract.package` declares
`purchase_order_ids` as the inverse of this field, so the field has to be
declared beside the model that inverts it: Odoo resolves a One2many by looking
its inverse up on the comodel at setup time, and a missing inverse is a
registry error, not a runtime one.

Only the field lives here. The onchange that fills the project and the partner
from the package stays in `real_estate_construction`, because it writes
`re_project_id`, which the procurement chain declares. Moving it would make the
construction floor depend on the whole of procurement to set one default.
"""

from odoo import fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    re_package_id = fields.Many2one(
        'realestate.construction.contract.package',
        string='Construction Package', index=True, ondelete='set null',
        help="The commercial package this order executes. An order that "
             "belongs to a package is *the* commitment for it — the package's "
             "own value is then a comparison, not an addition.",
    )
