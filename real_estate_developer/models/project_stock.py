from odoo import _, fields, models


class ProjectStock(models.Model):
    """Each project owns one internal stock location (under the Real Estate
    tree) that holds its available units and any materials received for it."""
    _inherit = 'realestate.project'

    stock_location_id = fields.Many2one(
        'stock.location', string='Stock Location', readonly=True, copy=False,
        help="Internal location holding this project's available units and "
             "received materials. Created on first use.")

    def _get_stock_location(self):
        """Return (creating on first use) this project's internal location."""
        self.ensure_one()
        if self.stock_location_id:
            return self.stock_location_id
        root = self.env.ref('atmta_real_estate.stock_location_re_root')
        location = self.env['stock.location'].sudo().create({
            'name': self.name or self.code or _('Project %s') % self.id,
            'usage': 'internal',
            'location_id': root.id,
        })
        self.stock_location_id = location.id
        return location

    def action_view_stock(self):
        """Open on-hand quants for everything filed under this project."""
        self.ensure_one()
        location = self._get_stock_location()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Stock — %s') % self.name,
            'res_model': 'stock.quant',
            'view_mode': 'list,form',
            'domain': [('location_id', 'child_of', location.id)],
            'context': {'search_default_internal_loc': 1},
        }


class PropertyProjectHome(models.Model):
    """Route a unit's home stock location to its project's location."""
    _inherit = 'realestate.property'

    def _re_home_location(self):
        self.ensure_one()
        if self.project_id:
            return self.project_id._get_stock_location()
        return super()._re_home_location()
