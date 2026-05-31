from odoo import api, fields, models


class SaleOrder(models.Model):
    """Bridge real-estate deals onto sale.order for pipeline & reporting.

    A deal is one order with a single unit line at the full price; the sale
    contract creates one invoice from it whose payment term splits the
    receivable into the installment schedule. The order never delivers — the
    unit's quant is moved by the property state lifecycle instead.
    """
    _inherit = 'sale.order'

    re_source_model = fields.Char(string='RE Source Model', copy=False)
    re_source_id = fields.Integer(string='RE Source ID', copy=False)
    is_re_bridge = fields.Boolean(
        string='Real Estate Bridge Order', copy=False,
        help="Commercial record mirroring a real-estate sale/resale. Stock is "
             "handled by the property lifecycle; invoicing by the installment flow.")

    @api.model
    def _create_re_bridge_order(self, partner, origin, source, line_vals):
        """Create and confirm a bridge order.

        :param line_vals: list of order-line value dicts (no (0, 0, ...) wrap).
        :returns: the order (empty recordset if partner/lines missing).
        """
        if not partner or not line_vals:
            return self.browse()
        order = self.create({
            'partner_id': partner.id,
            'origin': origin,
            'is_re_bridge': True,
            're_source_model': source._name,
            're_source_id': source.id,
            'order_line': [(0, 0, lv) for lv in line_vals],
        })
        order.action_confirm()
        return order


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _action_launch_stock_rule(self, previous_product_uom_qty=False):
        """Bridge orders never generate deliveries — the unit's quant is moved
        by the property state lifecycle instead."""
        non_bridge = self.filtered(lambda line: not line.order_id.is_re_bridge)
        if not non_bridge:
            return True
        return super(SaleOrderLine, non_bridge)._action_launch_stock_rule(
            previous_product_uom_qty=previous_product_uom_qty)
