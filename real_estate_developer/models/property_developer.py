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

    is_contracted = fields.Boolean(
        string='Under Contract',
        compute='_compute_developer_sold_state', store=True,
        help="A sale contract has been signed but the unit has not been handed "
             "over yet.")

    @api.depends('sale_contract_ids.state')
    def _compute_developer_sold_state(self):
        """`is_sold` means handed over, and only that.

        The previous filter tested for `'contract_signed'`, which is not a value
        of `sale.contract.state` — a leftover from a rename. It was dead code,
        so the effective behaviour has always been "sold == handed over". That
        behaviour is preserved here rather than widened, because `is_sold` is
        read by the project counters, the dashboard and the public API, and
        quietly redefining it would restate every historical figure.

        Contracted-but-not-yet-handed-over is a real and different thing, so it
        gets its own field instead of being folded into this one.
        """
        for rec in self:
            states = set(rec.sale_contract_ids.mapped('state'))
            rec.is_sold = 'handed_over' in states
            rec.is_contracted = 'signed' in states and not rec.is_sold

    @api.depends('sale_contract_ids.state', 'reservation_ids.state',
                 'is_available_for_sale')
    def _compute_sale_status(self):
        """Pipeline position of the unit.

        `for_sale` used to be unreachable — the branch simply did not exist, so
        a unit actively on the market reported `not_listed`. It is now driven by
        the authoritative availability engine, which is the only thing that
        actually knows whether the unit is on the market.
        """
        for rec in self:
            states = set(rec.sale_contract_ids.mapped('state'))
            if 'handed_over' in states:
                rec.sale_status = 'sold'
            elif 'signed' in states:
                rec.sale_status = 'under_contract'
            elif rec.reservation_ids.filtered(lambda r: r.state in ('hold', 'booked')):
                rec.sale_status = 'reserved'
            elif rec.is_available_for_sale:
                rec.sale_status = 'for_sale'
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
        if self.project_id and not self.company_id:
            self.company_id = self.project_id.company_id

    @api.model_create_multi
    def create(self, vals_list):
        """A unit created under a project belongs to the project's company.

        Property Core takes the company from the product template, which
        defaults to none. A unit created from the property form therefore had
        no company, and everything scoped by company -- the active price book,
        bulk repricing, the dashboard -- silently left it out. A company given
        explicitly is kept.
        """
        Project = self.env['realestate.project']
        for vals in vals_list:
            if vals.get('project_id') and not vals.get('company_id'):
                company = Project.browse(vals['project_id']).company_id
                if company:
                    vals['company_id'] = company.id
        return super().create(vals_list)

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
