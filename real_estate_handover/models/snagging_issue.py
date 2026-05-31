from odoo import _, api, fields, models


class SnaggingIssue(models.Model):
    _name = 'realestate.snagging.issue'
    _description = 'Snagging / Defect Issue'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'severity desc, reported_date desc'

    name = fields.Char(string='Reference', copy=False, required=True, readonly=True, default=lambda self: _('New'))
    handover_id = fields.Many2one('realestate.handover', string='Handover', ondelete='cascade')
    property_id = fields.Many2one(related='handover_id.property_id', store=True, readonly=True)
    sale_contract_id = fields.Many2one(related='handover_id.sale_contract_id', store=True, readonly=True)

    description = fields.Text(required=True)
    photo = fields.Binary()
    location_on_unit = fields.Char(string='Location on Unit', help='E.g. Master bathroom, Living room ceiling')

    severity = fields.Selection([
        ('minor', 'Minor'),
        ('major', 'Major'),
        ('critical', 'Critical'),
    ], default='minor', required=True, tracking=True)
    state = fields.Selection([
        ('open', 'Open'),
        ('assigned', 'Assigned'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('verified', 'Verified'),
        ('rejected', 'Rejected'),
    ], default='open', required=True, tracking=True)

    contractor_id = fields.Many2one('realestate.contractor', string='Assigned Contractor', tracking=True)
    reporter_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    reported_date = fields.Date(default=fields.Date.context_today, required=True)
    target_resolution_date = fields.Date()
    resolved_date = fields.Date()
    verified_date = fields.Date()
    notes = fields.Html()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.snagging.issue') or _('New')
        return super().create(vals_list)

    def action_assign(self):
        for rec in self:
            rec.state = 'assigned'

    def action_start(self):
        for rec in self:
            rec.state = 'in_progress'

    def action_resolve(self):
        for rec in self:
            rec.write({'state': 'resolved', 'resolved_date': fields.Date.today()})

    def action_verify(self):
        for rec in self:
            rec.write({'state': 'verified', 'verified_date': fields.Date.today()})

    def action_reject(self):
        for rec in self:
            rec.state = 'rejected'

    def action_reopen(self):
        for rec in self:
            rec.write({'state': 'open', 'resolved_date': False, 'verified_date': False})
