from odoo import api, models


class RealEstatePropertyStock(models.Model):
    """Inventory lifecycle for property units.

    Each unit-level property is a storable product carrying exactly one unit of
    on-hand stock. Its quant is relocated directly (no pickings) between
    state-driven locations:

        available / rented / maintenance / inactive -> home (project) location
        reserved                                    -> Reserved Units
        sold                                        -> Sold Units (customer)

    Because the sync is driven off ``state`` writes, every module that flips a
    property's state (rental, developer reservations, brokerage transactions,
    resale) keeps stock in step through this single code path.
    """
    _inherit = 'realestate.property'

    # ---- hooks (overridden by the developer module for per-project homes) ----
    def _is_stock_tracked(self):
        """Only leaf units are treated as sellable inventory items."""
        self.ensure_one()
        return self.hierarchy_level == 'unit'

    def _re_home_location(self):
        """Internal location an available unit lives in. The developer module
        overrides this to return the unit's project location."""
        self.ensure_one()
        return self.env.ref('atmta_real_estate.stock_location_re_unassigned')

    def _re_state_location(self):
        self.ensure_one()
        if self.state == 'sold':
            return self.env.ref('atmta_real_estate.stock_location_re_sold')
        if self.state == 'reserved':
            return self.env.ref('atmta_real_estate.stock_location_re_reserved')
        return self._re_home_location()

    def _sync_unit_quant(self):
        """Ensure the unit holds exactly one quant at the location matching its
        state. Infra guard only: if the stock tree is missing we no-op rather
        than break property CRUD."""
        root = self.env.ref('atmta_real_estate.stock_location_re_root', raise_if_not_found=False)
        if not root:
            return
        Quant = self.env['stock.quant'].sudo()
        for rec in self:
            if not rec._is_stock_tracked() or not rec.product_variant_id:
                continue
            product = rec.product_variant_id
            target = rec._re_state_location()
            quants = Quant.search([
                ('product_id', '=', product.id),
                ('location_id', 'child_of', root.id),
            ])
            for q in quants:
                if q.quantity:
                    Quant._update_available_quantity(product, q.location_id, -q.quantity)
            Quant._update_available_quantity(product, target, 1)

    @api.model
    def _backfill_unit_stock(self):
        """Make existing units storable and place their quants. Safe to re-run;
        called on install (post_init_hook) and on upgrade (migration)."""
        units = self.search([('hierarchy_level', '=', 'unit')])
        not_storable = units.filtered(lambda u: not u.is_storable)
        if not_storable:
            not_storable.write({'is_storable': True})
        wrong_policy = units.filtered(lambda u: u.invoice_policy != 'order')
        if wrong_policy:
            wrong_policy.write({'invoice_policy': 'order'})
        units._sync_unit_quant()

    def action_view_unit_stock(self):
        """Open the quant(s) for this unit so you can see where it sits."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.display_name,
            'res_model': 'stock.quant',
            'view_mode': 'list,form',
            'domain': [('product_id', '=', self.product_variant_id.id)],
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Unit-level properties are storable goods (1 on hand each).
            # 'order' invoice policy lets each installment SO line be invoiced
            # 1:1 by ordered quantity (see the developer sale-contract bridge).
            if vals.get('hierarchy_level', 'unit') == 'unit':
                vals.setdefault('is_storable', True)
                vals.setdefault('invoice_policy', 'order')
        records = super().create(vals_list)
        records._sync_unit_quant()
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'state' in vals or 'hierarchy_level' in vals or 'project_id' in vals:
            self._sync_unit_quant()
        return res
