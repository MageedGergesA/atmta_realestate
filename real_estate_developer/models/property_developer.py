from odoo import api, fields, models


class PropertyDeveloperExt(models.Model):
    """Developer-specific extensions to the base property model."""
    _inherit = 'realestate.property'

    project_id = fields.Many2one('realestate.project', string='Project', ondelete='restrict', tracking=True)
    phase_id = fields.Many2one(
        'realestate.phase', string='Phase', ondelete='restrict', tracking=True,
        domain="[('project_id', '=', project_id)]",
    )
    base_price = fields.Monetary(string='Base Selling Price', tracking=True)

    reservation_ids = fields.One2many('realestate.unit.reservation', 'property_id', string='Reservations')
    active_reservation_id = fields.Many2one(
        'realestate.unit.reservation', string='Current Reservation',
        compute='_compute_active_reservation', store=True,
    )
    reservation_count = fields.Integer(compute='_compute_reservation_count')
    sale_contract_ids = fields.One2many('realestate.sale.contract', 'property_id', string='Sale Contracts')
    sale_contract_count = fields.Integer(compute='_compute_sale_contract_count')

    is_sold = fields.Boolean(
        string='Sold',
        compute='_compute_developer_sold_state', store=True,
    )

    sale_status = fields.Selection([
        ('not_listed', 'Not Listed'),
        ('for_sale', 'For Sale'),
        ('reserved', 'Reserved'),
        ('under_contract', 'Under Contract'),
        ('sold', 'Sold'),
    ], string='Sale Status', compute='_compute_sale_status', store=True,
        help='Where this unit stands in the sales pipeline.')

    construction_status = fields.Selection([
        ('planning', 'Planning'),
        ('under_construction', 'Under Construction'),
        ('ready', 'Ready to Deliver'),
        ('delivered', 'Delivered'),
    ], string='Construction Status', compute='_compute_construction_status', store=True,
        help='Physical readiness driven by project / phase state.')

    @api.depends('reservation_ids.state')
    def _compute_active_reservation(self):
        for rec in self:
            active = rec.reservation_ids.filtered(lambda r: r.state in ('hold', 'booked'))
            rec.active_reservation_id = active[:1]

    @api.depends('sale_contract_ids.state')
    def _compute_sale_contract_count(self):
        for rec in self:
            rec.sale_contract_count = len(rec.sale_contract_ids)

    @api.depends('reservation_ids')
    def _compute_reservation_count(self):
        for rec in self:
            rec.reservation_count = len(rec.reservation_ids)

    @api.depends('sale_contract_ids.state')
    def _compute_developer_sold_state(self):
        for rec in self:
            sold = rec.sale_contract_ids.filtered(lambda c: c.state in ('contract_signed', 'handed_over'))
            rec.is_sold = bool(sold)

    @api.depends('sale_contract_ids.state', 'reservation_ids.state')
    def _compute_sale_status(self):
        """Single-source-of-truth status for the sales pipeline."""
        for rec in self:
            if rec.sale_contract_ids.filtered(lambda c: c.state == 'handed_over'):
                rec.sale_status = 'sold'
            elif rec.sale_contract_ids.filtered(lambda c: c.state == 'signed'):
                rec.sale_status = 'under_contract'
            elif rec.reservation_ids.filtered(lambda r: r.state in ('hold', 'booked')):
                rec.sale_status = 'reserved'
            else:
                rec.sale_status = 'not_listed'

    @api.depends('project_id.state', 'phase_id.state')
    def _compute_construction_status(self):
        """Physical readiness based on phase first, falling back to project."""
        for rec in self:
            level = rec.phase_id.state if rec.phase_id else rec.project_id.state
            if level in ('delivered', 'completed', 'handover'):
                rec.construction_status = 'delivered'
            elif level == 'ready':
                rec.construction_status = 'ready'
            elif level in ('construction', 'marketing'):
                rec.construction_status = 'under_construction'
            else:
                rec.construction_status = 'planning'

    @api.onchange('project_id')
    def _onchange_project_id(self):
        if self.phase_id and self.phase_id.project_id != self.project_id:
            self.phase_id = False

    def action_view_reservations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Reservations',
            'res_model': 'realestate.unit.reservation',
            'view_mode': 'list,form',
            'domain': [('property_id', '=', self.id)],
            'context': {'default_property_id': self.id},
        }

    def action_view_sale_contracts(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Sale Contracts',
            'res_model': 'realestate.sale.contract',
            'view_mode': 'list,form',
            'domain': [('property_id', '=', self.id)],
            'context': {'default_property_id': self.id},
        }
