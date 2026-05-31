from odoo import _, api, fields, models
from odoo.exceptions import UserError


class Lead(models.Model):
    _name = 'realestate.lead'
    _description = 'Real Estate Buyer Lead'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Reference', copy=False, required=True, readonly=True, default=lambda self: _('New'))
    partner_id = fields.Many2one('res.partner', string='Contact', tracking=True, ondelete='restrict')
    partner_name = fields.Char(related='partner_id.name', readonly=False, string='Name')
    phone = fields.Char(related='partner_id.phone', readonly=False)
    email = fields.Char(related='partner_id.email', readonly=False)

    source = fields.Selection([
        ('walk_in', 'Walk-in'),
        ('website', 'Website'),
        ('referral', 'Referral'),
        ('portal', 'Property Portal'),
        ('call', 'Phone Call'),
        ('other', 'Other'),
    ], default='other', tracking=True)

    state = fields.Selection([
        ('new', 'New'),
        ('qualified', 'Qualified'),
        ('matched', 'Matched'),
        ('viewing_scheduled', 'Viewing Scheduled'),
        ('offer', 'Offer Made'),
        ('converted', 'Converted'),
        ('lost', 'Lost'),
    ], default='new', tracking=True, required=True)

    agent_id = fields.Many2one(
        'res.users', string='Assigned Agent',
        domain=[('is_realestate_agent', '=', True)],
        default=lambda self: self.env.user, tracking=True,
    )

    # Preferences
    budget_min = fields.Monetary(string='Budget Min')
    budget_max = fields.Monetary(string='Budget Max')
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )
    area_min = fields.Float(string='Area Min (sqm)')
    area_max = fields.Float(string='Area Max (sqm)')
    bedroom_count_min = fields.Integer(string='Bedrooms Min')
    property_type_ids = fields.Many2many('property.type', string='Preferred Property Types')
    preferred_country_id = fields.Many2one('res.country', string='Preferred Country')
    preferred_state_id = fields.Many2one(
        'res.country.state', string='Preferred State',
        domain="[('country_id', '=', preferred_country_id)]",
    )
    preferred_city = fields.Char(string='Preferred City')
    preferred_district = fields.Char(string='Preferred District')

    matched_listing_ids = fields.Many2many(
        'realestate.listing',
        'realestate_lead_listing_rel', 'lead_id', 'listing_id',
        string='Matched Listings',
    )
    viewing_ids = fields.One2many('realestate.viewing', 'lead_id', string='Viewings')
    offer_ids = fields.One2many('realestate.offer', 'lead_id', string='Offers')

    loss_reason_id = fields.Many2one('realestate.lost.reason', string='Loss Reason')
    notes = fields.Html()
    color = fields.Integer()
    priority = fields.Selection([
        ('0', 'Normal'),
        ('1', 'Low'),
        ('2', 'Medium'),
        ('3', 'High'),
        ('4', 'Very High'),
        ('5', 'Critical'),
    ], default='0')

    # ------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.lead') or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------
    def action_qualify(self):
        for rec in self:
            if rec.state != 'new':
                raise UserError(_("Only new leads can be qualified."))
            if not rec.partner_id:
                raise UserError(_("Set a contact on the lead before qualifying."))
            rec.state = 'qualified'

    def action_match(self):
        """Search for listings that fit this lead's preferences and link them."""
        Listing = self.env['realestate.listing']
        for rec in self:
            # Listing must be actively marketed AND the underlying property must be available.
            domain = [
                ('state', '=', 'active'),
                ('property_id.state', '=', 'available'),
            ]
            if rec.budget_max:
                domain.append(('list_price', '<=', rec.budget_max))
            if rec.budget_min:
                domain.append(('list_price', '>=', rec.budget_min))
            if rec.property_type_ids:
                domain.append(('property_id.property_type_id', 'in', rec.property_type_ids.ids))
            if rec.preferred_state_id:
                domain.append(('property_id.state_id', '=', rec.preferred_state_id.id))
            if rec.bedroom_count_min:
                domain.append(('property_id.bedroom_count', '>=', rec.bedroom_count_min))
            if rec.area_min:
                domain.append(('property_id.area_sqm', '>=', rec.area_min))
            if rec.area_max:
                domain.append(('property_id.area_sqm', '<=', rec.area_max))
            matches = Listing.search(domain)
            rec.matched_listing_ids = [(6, 0, matches.ids)]
            if rec.state in ('new', 'qualified') and matches:
                rec.state = 'matched'
        return True

    def action_mark_lost(self):
        for rec in self:
            rec.state = 'lost'

    def action_reopen(self):
        for rec in self:
            if rec.state != 'lost':
                raise UserError(_("Only lost leads can be reopened."))
            rec.state = 'qualified'
            rec.loss_reason_id = False

    def action_schedule_viewing(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Schedule Viewing'),
            'res_model': 'realestate.viewing',
            'view_mode': 'form',
            'context': {
                'default_lead_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_agent_id': self.agent_id.id,
            },
        }
