from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class MaterialRequestLine(models.Model):
    _name = 'realestate.material.request.line'
    _description = 'Real Estate Material Request Line'
    _order = 'request_id, id'

    request_id = fields.Many2one(
        'realestate.material.request', required=True, ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product', string='Product', required=True,
        domain="['|', '|', '|', '|', "
               "('is_construction_material', '=', True), "
               "('is_property_fitting', '=', True), "
               "('is_marketing_asset', '=', True), "
               "('is_maintenance_consumable', '=', True), "
               "('type', '=', 'service')]",
    )
    description = fields.Char(string='Description')
    qty = fields.Float(string='Quantity', required=True, default=1.0)
    uom_id = fields.Many2one(
        'uom.uom', string='UoM', required=True,
        compute='_compute_uom', store=True, readonly=False,
    )
    unit_price_hint = fields.Float(
        string='Last Price',
        compute='_compute_price_hint',
        help='Latest seller price for hint only; the PO carries the binding price.',
    )
    estimated_cost = fields.Monetary(
        string='Estimated', compute='_compute_estimated_cost', store=True,
    )
    currency_id = fields.Many2one(
        related='request_id.currency_id', store=True, readonly=True,
    )

    po_line_id = fields.Many2one(
        'purchase.order.line', string='PO Line', readonly=True, copy=False,
    )
    received_qty = fields.Float(
        string='Received', compute='_compute_received', store=True,
    )

    @api.depends('product_id')
    def _compute_uom(self):
        for ln in self:
            if ln.product_id and not ln.uom_id:
                ln.uom_id = ln.product_id.uom_id

    @api.depends('product_id', 'qty')
    def _compute_price_hint(self):
        for ln in self:
            if not ln.product_id:
                ln.unit_price_hint = 0.0
                continue
            seller = ln.product_id.seller_ids[:1]
            ln.unit_price_hint = seller.price if seller else ln.product_id.standard_price

    @api.depends('unit_price_hint', 'qty')
    def _compute_estimated_cost(self):
        for ln in self:
            ln.estimated_cost = (ln.unit_price_hint or 0.0) * (ln.qty or 0.0)

    @api.depends('po_line_id.qty_received')
    def _compute_received(self):
        for ln in self:
            ln.received_qty = ln.po_line_id.qty_received if ln.po_line_id else 0.0
            if ln.received_qty and ln.request_id:
                ln.request_id._refresh_state_from_lines()

    @api.constrains('qty')
    def _check_qty(self):
        for ln in self:
            if ln.qty <= 0:
                raise ValidationError(_("Quantity must be greater than zero."))

    def _get_preferred_supplier(self):
        """Return the partner of the first `seller_ids` row, if any."""
        self.ensure_one()
        if self.product_id.seller_ids:
            return self.product_id.seller_ids[0].partner_id
        return False

    def _unit_price(self):
        """Price the PO line should carry — preferred seller price, fallback to standard cost."""
        self.ensure_one()
        seller = self.product_id.seller_ids[:1]
        return seller.price if seller else self.product_id.standard_price
