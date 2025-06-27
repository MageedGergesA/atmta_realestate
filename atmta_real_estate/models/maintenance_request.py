from odoo import fields, models, api, _
from odoo.exceptions import UserError


class MaintenanceRequest(models.Model):
    _name = 'realestate.maintenance.request'
    _description = 'Maintenance Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Request", required=True, default="New", tracking=True)
    property_id = fields.Many2one('product.product', domain="[('is_property','=',True)]", required=True, string="Property", tracking=True)
    description = fields.Text(string="Issue Description", tracking=True)
    request_date = fields.Date(default=fields.Date.today, string="Request Date", tracking=True)
    scheduled_date = fields.Date(string="Scheduled Date", tracking=True)
    completion_date = fields.Date(string="Completion Date", tracking=True)
    cost = fields.Float(string="Estimated Cost", tracking=True)
    actual_cost = fields.Float(string="Actual Cost", tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('scheduled', 'Scheduled'),
        ('in_progress', 'In Progress'),
        ('done', 'Completed'),
        ('cancelled', 'Cancelled')
    ], default='draft', tracking=True)
    attachment_ids = fields.Many2many('ir.attachment', 'request_attachment_rel', 'request_id',
                                      'maintenance_request_id', 'Attachments',
                                      help="You may attach files to this template, to be added to all "
                                           "emails created from this template", tracking=True)


    assigned_to = fields.Many2one('res.users', string="Assigned Technician", tracking=True)

    def action_schedule(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError("Can only schedule from draft.")
            rec.state = 'scheduled'

    def action_start_progress(self):
        for rec in self:
            rec.state = 'in_progress'
            if rec.property_id:
                rec.property_id.state = 'maintenance'

    def action_complete(self):
        for rec in self:
            rec.state = 'done'
            if rec.property_id:
                other_active = self.search([
                    ('property_id', '=', rec.property_id.id),
                    ('id', '!=', rec.id),
                    ('state', '=', 'in_progress')
                ])
                if not other_active:
                    rec.property_id.state = 'available'

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancelled'
            if rec.property_id:
                other_active = self.search([
                    ('property_id', '=', rec.property_id.id),
                    ('id', '!=', rec.id),
                    ('state', '=', 'in_progress')
                ])
                if not other_active:
                    rec.property_id.state = 'available'

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = 'draft'
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _("New")) == _("New"):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.maintenance.request') or 'New'
        return super().create(vals_list)
