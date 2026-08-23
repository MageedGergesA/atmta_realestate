from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    is_construction_material = fields.Boolean(
        string='Construction Material',
        help='Building materials used during construction (cement, rebar, blocks, finishes, MEP, etc.).',
    )
    is_property_fitting = fields.Boolean(
        string='Property Fitting',
        help='Items shipped with a finished unit (appliances, sanitary, smart-home, furniture for furnished rentals).',
    )
    is_marketing_asset = fields.Boolean(
        string='Marketing Asset',
        help='Sales/marketing items: signage, brochures, drone sessions, ads.',
    )
    is_maintenance_consumable = fields.Boolean(
        string='Maintenance Consumable',
        help='Recurring maintenance materials: filters, lamps, plumbing parts, cleaning chemicals.',
    )
    is_realestate_product = fields.Boolean(
        compute='_compute_is_realestate_product', store=True,
        help='True when the product carries any real-estate flag — used for catalog filtering.',
    )

    # Physical RE goods that should be tracked in inventory (marketing assets
    # and services are excluded).
    _RE_STORABLE_FLAGS = (
        'is_construction_material', 'is_property_fitting', 'is_maintenance_consumable',
    )

    @api.depends(
        'is_construction_material',
        'is_property_fitting',
        'is_marketing_asset',
        'is_maintenance_consumable',
    )
    def _compute_is_realestate_product(self):
        for rec in self:
            rec.is_realestate_product = (
                rec.is_construction_material
                or rec.is_property_fitting
                or rec.is_marketing_asset
                or rec.is_maintenance_consumable
            )

    def _sync_re_storable(self):
        """Flag physical RE goods as storable so they're tracked in inventory."""
        for tmpl in self:
            physical = any(tmpl[f] for f in self._RE_STORABLE_FLAGS)
            if tmpl.type == 'consu' and physical and not tmpl.is_storable:
                tmpl.is_storable = True

    @api.model_create_multi
    def create(self, vals_list):
        templates = super().create(vals_list)
        templates._sync_re_storable()
        return templates

    def write(self, vals):
        res = super().write(vals)
        if any(f in vals for f in self._RE_STORABLE_FLAGS):
            self._sync_re_storable()
        return res


class ProductProduct(models.Model):
    _inherit = 'product.product'

    is_construction_material = fields.Boolean(related='product_tmpl_id.is_construction_material', store=True)
    is_property_fitting = fields.Boolean(related='product_tmpl_id.is_property_fitting', store=True)
    is_marketing_asset = fields.Boolean(related='product_tmpl_id.is_marketing_asset', store=True)
    is_maintenance_consumable = fields.Boolean(related='product_tmpl_id.is_maintenance_consumable', store=True)
    is_realestate_product = fields.Boolean(related='product_tmpl_id.is_realestate_product', store=True)
