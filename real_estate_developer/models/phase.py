from odoo import _, api, fields, models


class Phase(models.Model):
    _name = 'realestate.phase'
    _description = 'Project Phase'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, sequence, name'

    name = fields.Char(string='Phase Name', required=True, tracking=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(string='Code', tracking=True)
    project_id = fields.Many2one('realestate.project', string='Project', required=True, ondelete='cascade', tracking=True)
    state = fields.Selection([
        ('planning', 'Planning'),
        ('construction', 'Under Construction'),
        ('ready', 'Ready'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ], default='planning', tracking=True, required=True)

    start_date = fields.Date(string='Start Date', tracking=True)
    expected_completion_date = fields.Date(string='Expected Completion', tracking=True)
    expected_delivery_date = fields.Date(string='Expected Delivery', tracking=True)

    property_ids = fields.One2many('realestate.property', 'phase_id', string='Units')
    unit_count = fields.Integer(compute='_compute_counters')
    available_unit_count = fields.Integer(compute='_compute_counters')
    sold_unit_count = fields.Integer(compute='_compute_counters')

    description = fields.Html()

    @api.depends('property_ids', 'property_ids.state', 'property_ids.is_sold')
    def _compute_counters(self):
        for rec in self:
            rec.unit_count = len(rec.property_ids)
            rec.available_unit_count = len(rec.property_ids.filtered(lambda p: p.state == 'available'))
            rec.sold_unit_count = len(rec.property_ids.filtered(lambda p: p.is_sold))

    def action_view_units(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Units'),
            'res_model': 'realestate.property',
            'view_mode': 'kanban,list,form',
            'domain': [('phase_id', '=', self.id)],
            'context': {'default_phase_id': self.id, 'default_project_id': self.project_id.id},
        }

    def action_set_construction(self):
        self.write({'state': 'construction'})

    def action_set_ready(self):
        self.write({'state': 'ready'})

    def action_set_delivered(self):
        self.write({'state': 'delivered'})
