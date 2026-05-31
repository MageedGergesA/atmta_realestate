from odoo import _, api, fields, models
from odoo.exceptions import UserError


class Offer(models.Model):
    _name = 'realestate.offer'
    _description = 'Property Sale Offer'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'amount desc, offer_date desc'

    name = fields.Char(string='Reference', copy=False, required=True, readonly=True, default=lambda self: _('New'))
    listing_id = fields.Many2one(
        'realestate.listing', string='Listing', required=True, tracking=True, ondelete='cascade',
    )
    property_id = fields.Many2one(related='listing_id.property_id', store=True, readonly=True)
    lead_id = fields.Many2one('realestate.lead', string='Lead', tracking=True)
    partner_id = fields.Many2one('res.partner', string='Buyer', tracking=True, required=True)
    agent_id = fields.Many2one(
        'res.users', string='Selling Agent',
        domain=[('is_realestate_agent', '=', True)],
        default=lambda self: self.env.user, tracking=True,
    )

    amount = fields.Monetary(string='Offer Amount', required=True, tracking=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )
    offer_date = fields.Date(string='Offer Date', default=fields.Date.context_today, tracking=True)
    expiry_date = fields.Date(string='Valid Until', tracking=True)

    # ----- Pricing references (read-only context for negotiation) -----
    listing_list_price = fields.Monetary(
        string='Asking Price', related='listing_id.list_price', readonly=True,
    )
    listing_base_price = fields.Monetary(
        string='Property Base Price', related='listing_id.property_base_price', readonly=True,
    )
    offer_vs_list = fields.Float(
        string='% of Asking', compute='_compute_offer_vs_list', readonly=True,
        help='Offer amount as a fraction of asking price. 1.0 = at asking, 0.95 = 5% below asking.',
    )
    offer_vs_base = fields.Float(
        string='vs Base (%)', compute='_compute_offer_vs_base', readonly=True,
        help='Offer deviation from property base price.',
    )

    @api.depends('amount', 'listing_list_price')
    def _compute_offer_vs_list(self):
        for rec in self:
            rec.offer_vs_list = (rec.amount / rec.listing_list_price) if rec.listing_list_price else 0.0

    @api.depends('amount', 'listing_base_price')
    def _compute_offer_vs_base(self):
        for rec in self:
            if rec.listing_base_price:
                rec.offer_vs_base = (rec.amount - rec.listing_base_price) / rec.listing_base_price
            else:
                rec.offer_vs_base = 0.0

    state = fields.Selection([
        ('submitted', 'Submitted'),
        ('countered', 'Countered'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('withdrawn', 'Withdrawn'),
        ('expired', 'Expired'),
    ], default='submitted', tracking=True, required=True)

    conditions = fields.Text(string='Conditions', help='E.g. subject to financing, inspection, etc.')
    deposit_amount = fields.Monetary(string='Earnest Deposit')
    proposed_closing_date = fields.Date(string='Proposed Closing Date')
    rejection_reason = fields.Char(string='Rejection Reason', readonly=True)

    # ------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.offer') or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------
    def action_accept(self):
        for rec in self:
            if rec.state not in ('submitted', 'countered'):
                raise UserError(_("Only submitted or countered offers can be accepted."))
            if rec.listing_id.state not in ('active', 'under_offer'):
                raise UserError(_("Listing is not in a state that accepts offers."))
            # Reject sibling open offers
            siblings = rec.listing_id.offer_ids.filtered(
                lambda o: o.id != rec.id and o.state in ('submitted', 'countered')
            )
            siblings.action_reject(reason='other_offer_accepted')
            rec.state = 'accepted'
            rec.listing_id.state = 'under_offer'
            if rec.lead_id and rec.lead_id.state != 'converted':
                rec.lead_id.state = 'offer'

    def action_reject(self, reason=None):
        for rec in self:
            if rec.state not in ('submitted', 'countered'):
                continue
            rec.state = 'rejected'
            if reason:
                rec.rejection_reason = reason

    def action_withdraw(self):
        for rec in self:
            if rec.state not in ('submitted', 'countered'):
                raise UserError(_("Only open offers can be withdrawn."))
            rec.state = 'withdrawn'

    def action_create_transaction(self):
        """Promote an accepted offer to a transaction."""
        self.ensure_one()
        if self.state != 'accepted':
            raise UserError(_("Only accepted offers can be promoted to a transaction."))
        if self.listing_id.transaction_id:
            return self.listing_id.action_view_transaction()
        Transaction = self.env['realestate.transaction']
        txn = Transaction.create({
            'listing_id': self.listing_id.id,
            'offer_id': self.id,
            'buyer_id': self.partner_id.id,
            'sale_price': self.amount,
            'deposit_amount': self.deposit_amount,
            'closing_date': self.proposed_closing_date,
            'selling_agent_id': self.agent_id.id,
        })
        self.listing_id.transaction_id = txn.id
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.transaction',
            'view_mode': 'form',
            'res_id': txn.id,
        }

    # ------------------------------------------------------------
    # Crons
    # ------------------------------------------------------------
    @api.model
    def cron_expire_offers(self):
        today = fields.Date.today()
        expired = self.search([
            ('state', 'in', ('submitted', 'countered')),
            ('expiry_date', '!=', False),
            ('expiry_date', '<', today),
        ])
        expired.write({'state': 'expired'})
