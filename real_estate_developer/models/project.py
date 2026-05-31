from odoo import _, api, fields, models


PROJECT_TYPES = [
    ('residential', 'Residential'),
    ('commercial', 'Commercial'),
    ('mixed', 'Mixed-use'),
    ('land', 'Land / Plots'),
    ('industrial', 'Industrial / Warehouse'),
]


class Project(models.Model):
    _name = 'realestate.project'
    _description = 'Real Estate Development Project'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    name = fields.Char(string='Project Name', required=True, copy=False, tracking=True)
    code = fields.Char(
        string='Code', copy=False, required=True, index='trigram',
        default=lambda self: _('New'),
    )
    project_type = fields.Selection(PROJECT_TYPES, default='residential', required=True, tracking=True)
    state = fields.Selection([
        ('planning', 'Planning'),
        ('construction', 'Under Construction'),
        ('marketing', 'Marketing'),
        ('handover', 'Handover'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ], default='planning', tracking=True, required=True)

    developer_id = fields.Many2one('res.partner', string='Developer/Company', tracking=True)
    manager_id = fields.Many2one('res.users', string='Project Manager', tracking=True)

    # Location
    country_id = fields.Many2one('res.country', string='Country', tracking=True)
    state_id = fields.Many2one(
        'res.country.state', string='State',
        domain="[('country_id', '=', country_id)]",
    )
    city = fields.Char(string='City', tracking=True)
    district = fields.Char(string='District', tracking=True)
    address_line = fields.Char(string='Address')
    latitude = fields.Float(string='Latitude', digits=(10, 7))
    longitude = fields.Float(string='Longitude', digits=(10, 7))
    boundary_point_ids = fields.One2many(
        'realestate.project.boundary.point', 'project_id',
        string='Plot Boundary Points',
        help='Polygon vertices defining the project plot. Order matters; the last point connects back to the first.',
    )
    boundary_point_count = fields.Integer(compute='_compute_boundary_point_count')

    @api.depends('boundary_point_ids')
    def _compute_boundary_point_count(self):
        for rec in self:
            rec.boundary_point_count = len(rec.boundary_point_ids)

    def _recompute_centroid_from_boundary(self):
        """Set lat/lng to the arithmetic centroid of the boundary points."""
        for rec in self:
            pts = rec.boundary_point_ids
            if not pts:
                continue
            rec.latitude = sum(p.latitude for p in pts) / len(pts)
            rec.longitude = sum(p.longitude for p in pts) / len(pts)

    def action_add_boundary_point(self, latitude, longitude):
        self.ensure_one()
        last_seq = max(self.boundary_point_ids.mapped('sequence') or [0])
        point = self.env['realestate.project.boundary.point'].create({
            'project_id': self.id,
            'latitude': latitude,
            'longitude': longitude,
            'sequence': last_seq + 10,
        })
        return point.id

    def action_move_boundary_point(self, point_id, latitude, longitude):
        self.ensure_one()
        point = self.env['realestate.project.boundary.point'].browse(point_id)
        if point.project_id != self:
            return False
        point.write({'latitude': latitude, 'longitude': longitude})
        return True

    def action_remove_boundary_point(self, point_id):
        self.ensure_one()
        point = self.env['realestate.project.boundary.point'].browse(point_id)
        if point.project_id != self:
            return False
        point.unlink()
        return True

    def action_clear_boundary(self):
        self.ensure_one()
        self.boundary_point_ids.unlink()
        return True

    # Timeline
    start_date = fields.Date(string='Start Date', tracking=True)
    expected_completion_date = fields.Date(string='Expected Completion', tracking=True)
    actual_completion_date = fields.Date(string='Actual Completion', tracking=True)

    # Scale
    total_land_area = fields.Float(string='Total Land Area (sqm)', tracking=True)
    total_built_up_area = fields.Float(string='Total Built-up Area (sqm)', tracking=True)
    expected_budget = fields.Monetary(string='Expected Budget', tracking=True)
    expected_revenue = fields.Monetary(string='Expected Revenue', tracking=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    description = fields.Html()
    notes = fields.Html()

    # Relations
    phase_ids = fields.One2many('realestate.phase', 'project_id', string='Phases')
    property_ids = fields.One2many('realestate.property', 'project_id', string='Units / Properties')

    # Computed inventory counters
    phase_count = fields.Integer(compute='_compute_counters')
    unit_count = fields.Integer(compute='_compute_counters')
    available_unit_count = fields.Integer(compute='_compute_counters')
    reserved_unit_count = fields.Integer(compute='_compute_counters')
    sold_unit_count = fields.Integer(compute='_compute_counters')

    @api.depends('phase_ids', 'property_ids', 'property_ids.state')
    def _compute_counters(self):
        for rec in self:
            rec.phase_count = len(rec.phase_ids)
            rec.unit_count = len(rec.property_ids)
            rec.available_unit_count = len(rec.property_ids.filtered(lambda p: p.state == 'available'))
            rec.reserved_unit_count = len(rec.property_ids.filtered(lambda p: p.state == 'reserved'))
            rec.sold_unit_count = len(rec.property_ids.filtered(lambda p: p.is_sold))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code', _('New')) == _('New'):
                vals['code'] = self.env['ir.sequence'].next_by_code('realestate.project') or _('New')
        return super().create(vals_list)

    def action_view_phases(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Phases'),
            'res_model': 'realestate.phase',
            'view_mode': 'list,form',
            'domain': [('project_id', '=', self.id)],
            'context': {'default_project_id': self.id},
        }

    def action_view_units(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Units'),
            'res_model': 'realestate.property',
            'view_mode': 'kanban,list,form',
            'domain': [('project_id', '=', self.id)],
            'context': {'default_project_id': self.id},
        }

    def action_set_construction(self):
        self.write({'state': 'construction'})

    def action_set_marketing(self):
        self.write({'state': 'marketing'})

    def action_set_handover(self):
        self.write({'state': 'handover'})

    def action_complete(self):
        self.write({'state': 'completed', 'actual_completion_date': fields.Date.today()})

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset_planning(self):
        self.write({'state': 'planning'})
