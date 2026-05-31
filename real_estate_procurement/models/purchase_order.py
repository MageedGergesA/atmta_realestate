from odoo import _, api, fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    re_project_id = fields.Many2one(
        'realestate.project', string='Real Estate Project',
        help='Project this PO is allocated to. Drives committed/budget rollups.',
    )
    re_source_model = fields.Selection([
        ('realestate.material.request', 'Material Request'),
        ('realestate.contractor', 'Subcontract'),
        ('realestate.listing', 'Listing'),
        ('realestate.project', 'Project (Direct)'),
    ], string='RE Source Type')
    re_source_id = fields.Integer(string='RE Source ID', help='Polymorphic ID into the source record.')
    re_source_display = fields.Char(string='RE Source', compute='_compute_re_source_display')
    is_realestate_po = fields.Boolean(
        compute='_compute_is_realestate_po', store=True,
        help='True when the PO is linked to any real-estate project or source.',
    )

    @api.depends('re_project_id', 're_source_model', 're_source_id')
    def _compute_is_realestate_po(self):
        for rec in self:
            rec.is_realestate_po = bool(rec.re_project_id or (rec.re_source_model and rec.re_source_id))

    def button_confirm(self):
        res = super().button_confirm()
        self._re_route_receipts()
        return res

    def _re_route_receipts(self):
        """Send incoming receipts of a project-scoped PO into that project's
        stock location, so material is filed per project."""
        for po in self:
            project = po.re_project_id
            # Project location lives in the developer module; route only when
            # that capability is present.
            if not project or not hasattr(project, '_get_stock_location'):
                continue
            if 'picking_ids' not in po._fields:
                continue
            location = project._get_stock_location()
            incoming = po.picking_ids.filtered(
                lambda p: p.picking_type_code == 'incoming' and p.state not in ('done', 'cancel'))
            for picking in incoming:
                moves = picking.move_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
                moves.write({'location_dest_id': location.id})
                moves.move_line_ids.write({'location_dest_id': location.id})
                picking.location_dest_id = location.id

    def _compute_re_source_display(self):
        for rec in self:
            if not rec.re_source_model or not rec.re_source_id:
                rec.re_source_display = ''
                continue
            try:
                src = self.env[rec.re_source_model].browse(rec.re_source_id).exists()
                rec.re_source_display = src.display_name if src else _('Missing source')
            except KeyError:
                rec.re_source_display = _('Unknown source')


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    re_material_request_line_id = fields.Many2one(
        'realestate.material.request.line', string='Material Request Line',
        ondelete='set null',
        help='Source line in the material request that spawned this PO line.',
    )
    re_material_request_id = fields.Many2one(
        'realestate.material.request',
        related='re_material_request_line_id.request_id', store=True, readonly=True,
    )

    def write(self, vals):
        res = super().write(vals)
        if 'qty_received' in vals:
            requests = self.mapped('re_material_request_id')
            requests._refresh_state_from_lines()
        return res
