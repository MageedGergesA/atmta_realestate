from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class Listing(models.Model):
    _name = 'realestate.listing'
    _description = 'Property Sales Listing'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'list_date desc, id desc'

    name = fields.Char(string='Reference', copy=False, required=True, readonly=True, default=lambda self: _('New'))
    property_id = fields.Many2one(
        'realestate.property', string='Property', required=True, tracking=True,
        ondelete='restrict',
    )
    property_type_id = fields.Many2one(
        'property.type', string='Property Type',
        related='property_id.property_type_id', store=True, readonly=True,
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('under_offer', 'Under Offer'),
        ('sold', 'Sold'),
        ('withdrawn', 'Withdrawn'),
        ('expired', 'Expired'),
    ], default='draft', tracking=True, required=True)

    list_price = fields.Monetary(string='List Price', tracking=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    # ----- Pricing references (read-only context for the agent) -----
    property_base_price = fields.Monetary(
        string='Property Base Price', related='property_id.base_price', readonly=True,
        help='Developer / valuation reference price on the property record.',
    )
    last_sold_price = fields.Monetary(
        string='Last Sold For', compute='_compute_last_sold', readonly=True,
        help='Most recent closed sale price for this property (across prior listings).',
    )
    last_sold_date = fields.Date(
        string='Last Sold On', compute='_compute_last_sold', readonly=True,
    )
    price_vs_base = fields.Float(
        string='vs Base (%)', compute='_compute_price_vs_base', readonly=True,
        help='How much asking price deviates from property base price.',
    )

    list_date = fields.Date(string='Listed On', default=fields.Date.context_today, tracking=True)
    expiry_date = fields.Date(string='Listing Expiry', tracking=True)
    sold_date = fields.Date(string='Sold On', readonly=True)
    withdrawn_date = fields.Date(string='Withdrawn On', readonly=True)

    days_on_market = fields.Integer(
        string='Days on Market',
        compute='_compute_days_on_market', store=True,
    )

    lister_agent_id = fields.Many2one(
        'res.users', string='Lister Agent',
        domain=[('is_realestate_agent', '=', True)],
        default=lambda self: self.env.user, tracking=True,
    )
    co_agent_ids = fields.Many2many(
        'res.users', 'realestate_listing_coagent_rel', 'listing_id', 'user_id',
        string='Co-agents', domain=[('is_realestate_agent', '=', True)],
    )

    marketing_channel_ids = fields.Many2many(
        'realestate.marketing.channel', string='Marketing Channels',
    )
    description_html = fields.Html(string='Marketing Description', sanitize=True)
    featured = fields.Boolean(string='Featured')

    viewing_ids = fields.One2many('realestate.viewing', 'listing_id', string='Viewings')
    viewing_count = fields.Integer(compute='_compute_counts')
    offer_ids = fields.One2many('realestate.offer', 'listing_id', string='Offers')
    offer_count = fields.Integer(compute='_compute_counts')
    accepted_offer_id = fields.Many2one(
        'realestate.offer', string='Accepted Offer',
        compute='_compute_accepted_offer', store=True,
    )
    transaction_id = fields.Many2one('realestate.transaction', string='Transaction', readonly=True, copy=False)

    final_sale_price = fields.Monetary(string='Final Sale Price', readonly=True)
    list_to_sale_ratio = fields.Float(
        string='List-to-sale Ratio (%)',
        compute='_compute_list_to_sale_ratio', store=True,
        help='Final sale price as % of original list price.',
    )

    notes = fields.Html(string='Internal Notes')
    color = fields.Integer(string='Color')

    # ------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------
    @api.depends('list_date', 'state', 'sold_date', 'withdrawn_date')
    def _compute_days_on_market(self):
        today = fields.Date.today()
        for rec in self:
            if not rec.list_date:
                rec.days_on_market = 0
                continue
            end = today
            if rec.state == 'sold' and rec.sold_date:
                end = rec.sold_date
            elif rec.state in ('withdrawn', 'expired') and rec.withdrawn_date:
                end = rec.withdrawn_date
            rec.days_on_market = (end - rec.list_date).days

    @api.depends('viewing_ids', 'offer_ids')
    def _compute_counts(self):
        for rec in self:
            rec.viewing_count = len(rec.viewing_ids)
            rec.offer_count = len(rec.offer_ids)

    @api.depends('offer_ids.state')
    def _compute_accepted_offer(self):
        for rec in self:
            rec.accepted_offer_id = rec.offer_ids.filtered(lambda o: o.state == 'accepted')[:1]

    @api.depends('property_id')
    def _compute_last_sold(self):
        Transaction = self.env['realestate.transaction']
        for rec in self:
            if not rec.property_id:
                rec.last_sold_price = 0.0
                rec.last_sold_date = False
                continue
            last_txn = Transaction.search([
                ('property_id', '=', rec.property_id.id),
                ('state', '=', 'closed'),
                ('listing_id', '!=', rec.id),
            ], order='closing_date desc', limit=1)
            rec.last_sold_price = last_txn.sale_price if last_txn else 0.0
            rec.last_sold_date = last_txn.closing_date if last_txn else False

    @api.depends('list_price', 'property_base_price')
    def _compute_price_vs_base(self):
        for rec in self:
            if rec.property_base_price:
                rec.price_vs_base = (rec.list_price - rec.property_base_price) / rec.property_base_price
            else:
                rec.price_vs_base = 0.0

    @api.depends('list_price', 'final_sale_price')
    def _compute_list_to_sale_ratio(self):
        for rec in self:
            rec.list_to_sale_ratio = (
                (rec.final_sale_price / rec.list_price * 100.0)
                if rec.list_price and rec.final_sale_price
                else 0.0
            )

    # ------------------------------------------------------------
    # Constrains
    # ------------------------------------------------------------
    @api.constrains('list_date', 'expiry_date')
    def _check_dates(self):
        for rec in self:
            if rec.list_date and rec.expiry_date and rec.expiry_date < rec.list_date:
                raise ValidationError(_("Expiry date cannot be before listing date."))

    # ------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.listing') or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------
    def action_activate(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft listings can be activated."))
            if not rec.list_price:
                raise UserError(_("Set a list price before activating."))
            if not rec.lister_agent_id:
                raise UserError(_("Assign a lister agent before activating."))
            rec.property_id._check_available_for_new_sale()
            rec.state = 'active'
            if not rec.list_date:
                rec.list_date = fields.Date.today()

    def action_withdraw(self):
        for rec in self:
            if rec.state not in ('active', 'under_offer'):
                raise UserError(_("Only active or under-offer listings can be withdrawn."))
            rec.state = 'withdrawn'
            rec.withdrawn_date = fields.Date.today()
            rec.offer_ids.filtered(lambda o: o.state in ('submitted', 'countered')).action_reject(reason='listing_withdrawn')

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state in ('sold',):
                raise UserError(_("Cannot reset a sold listing."))
            rec.state = 'draft'

    def action_view_viewings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Viewings'),
            'res_model': 'realestate.viewing',
            'view_mode': 'calendar,list,form',
            'domain': [('listing_id', '=', self.id)],
            'context': {'default_listing_id': self.id},
        }

    def action_view_offers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Offers'),
            'res_model': 'realestate.offer',
            'view_mode': 'list,form',
            'domain': [('listing_id', '=', self.id)],
            'context': {'default_listing_id': self.id},
        }

    def action_view_transaction(self):
        self.ensure_one()
        if not self.transaction_id:
            raise UserError(_("No transaction linked to this listing."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Transaction'),
            'res_model': 'realestate.transaction',
            'view_mode': 'form',
            'res_id': self.transaction_id.id,
        }

    # ------------------------------------------------------------
    # Crons
    # ------------------------------------------------------------
    @api.model
    def cron_expire_listings(self):
        today = fields.Date.today()
        expired = self.search([
            ('state', '=', 'active'),
            ('expiry_date', '!=', False),
            ('expiry_date', '<', today),
        ])
        expired.write({'state': 'expired', 'withdrawn_date': today})
