from odoo import _, api, fields, models
from odoo.exceptions import UserError


class Viewing(models.Model):
    _name = 'realestate.viewing'
    _description = 'Property Viewing'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'scheduled_at desc'

    name = fields.Char(string='Reference', copy=False, required=True, readonly=True, default=lambda self: _('New'))
    listing_id = fields.Many2one(
        'realestate.listing', string='Listing', required=True, tracking=True, ondelete='cascade',
    )
    property_id = fields.Many2one(related='listing_id.property_id', store=True, readonly=True)
    lead_id = fields.Many2one('realestate.lead', string='Lead', tracking=True)
    partner_id = fields.Many2one('res.partner', string='Attendee', tracking=True)
    agent_id = fields.Many2one(
        'res.users', string='Agent',
        domain=[('is_realestate_agent', '=', True)],
        default=lambda self: self.env.user, tracking=True,
    )

    scheduled_at = fields.Datetime(string='Scheduled At', required=True, tracking=True)
    duration = fields.Float(string='Duration (hours)', default=0.5)
    end_at = fields.Datetime(string='End', compute='_compute_end_at', store=True)

    state = fields.Selection([
        ('scheduled', 'Scheduled'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('no_show', 'No-show'),
    ], default='scheduled', tracking=True, required=True)

    feedback = fields.Text(string='Feedback')
    feedback_rating = fields.Selection([
        ('very_interested', 'Very Interested'),
        ('interested', 'Interested'),
        ('not_interested', 'Not Interested'),
    ], string='Interest Level')
    next_action = fields.Selection([
        ('follow_up_call', 'Follow-up Call'),
        ('send_proposal', 'Send Proposal'),
        ('second_viewing', 'Arrange Second Viewing'),
        ('expect_offer', 'Expect Offer'),
        ('none', 'No Further Action'),
    ], string='Next Action')

    @api.depends('scheduled_at', 'duration')
    def _compute_end_at(self):
        for rec in self:
            if rec.scheduled_at and rec.duration:
                rec.end_at = fields.Datetime.add(rec.scheduled_at, hours=rec.duration)
            else:
                rec.end_at = rec.scheduled_at

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.viewing') or _('New')
        records = super().create(vals_list)
        for rec in records:
            if rec.lead_id and rec.lead_id.state in ('new', 'qualified', 'matched'):
                rec.lead_id.state = 'viewing_scheduled'
        return records

    def action_mark_completed(self):
        for rec in self:
            rec.state = 'completed'

    def action_mark_no_show(self):
        for rec in self:
            rec.state = 'no_show'

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancelled'

    def action_create_offer(self):
        self.ensure_one()
        if not self.lead_id:
            raise UserError(_("Set a lead on the viewing first."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Submit Offer'),
            'res_model': 'realestate.offer',
            'view_mode': 'form',
            'context': {
                'default_listing_id': self.listing_id.id,
                'default_lead_id': self.lead_id.id,
                'default_partner_id': self.partner_id.id,
                'default_agent_id': self.agent_id.id,
            },
            'target': 'new',
        }
