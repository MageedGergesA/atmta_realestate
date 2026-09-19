from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SnaggingIssue(models.Model):
    _name = 'realestate.snagging.issue'
    _description = 'Snagging / Defect Issue'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    # Not 'severity desc': that sorts the selection keys alphabetically and
    # lists minor before critical.
    _order = 'severity_rank desc, reported_date desc'

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
    severity_rank = fields.Integer(compute='_compute_severity_rank', store=True, index=True,
                                   help='1 = minor, 2 = major, 3 = critical. Used to sort worst first.')
    state = fields.Selection([
        ('open', 'Open'),
        ('assigned', 'Assigned'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('verified', 'Verified'),
        ('rejected', 'Rejected'),
    ], default='open', required=True, tracking=True)

    # A snag is handed to a contractor by the Handover team, so the role is
    # given read on the contractor directory (security/ir.model.access.csv):
    # the field is on this form and in the dashboard's snag panel, and without
    # the right both died with an AccessError on the user's own screen.
    contractor_id = fields.Many2one('realestate.contractor', string='Assigned Contractor', tracking=True)
    reporter_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    reported_date = fields.Date(default=fields.Date.context_today, required=True)
    target_resolution_date = fields.Date()
    resolved_date = fields.Date()
    verified_date = fields.Date()
    notes = fields.Html()

    @api.depends('severity')
    def _compute_severity_rank(self):
        ranks = {'minor': 1, 'major': 2, 'critical': 3}
        for rec in self:
            rec.severity_rank = ranks.get(rec.severity, 0)

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
            # Rejecting means turning down a reported fix; there is none
            # before the issue is resolved.
            if rec.state != 'resolved':
                raise UserError(_("Only a resolved issue's fix can be rejected."))
            rec.state = 'rejected'

    def action_reopen(self):
        for rec in self:
            rec.write({'state': 'open', 'resolved_date': False, 'verified_date': False})
