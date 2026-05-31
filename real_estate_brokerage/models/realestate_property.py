from odoo import api, fields, models


class RealEstateProperty(models.Model):
    _inherit = 'realestate.property'

    listing_ids = fields.One2many('realestate.listing', 'property_id', string='Sales Listings')
    active_listing_id = fields.Many2one(
        'realestate.listing', string='Active Listing',
        compute='_compute_active_listing', store=True,
    )
    for_sale = fields.Boolean(
        string='For Sale', compute='_compute_active_listing', store=True,
    )
    transaction_ids = fields.One2many('realestate.transaction', 'property_id', string='Sale Transactions')
    last_transaction_id = fields.Many2one(
        'realestate.transaction', string='Last Sale',
        compute='_compute_last_transaction', store=True,
    )
    is_sold = fields.Boolean(
        string='Sold', compute='_compute_last_transaction', store=True,
    )
    listing_count = fields.Integer(compute='_compute_brokerage_counts')
    transaction_count = fields.Integer(compute='_compute_brokerage_counts')

    @api.depends('listing_ids', 'transaction_ids')
    def _compute_brokerage_counts(self):
        for rec in self:
            rec.listing_count = len(rec.listing_ids)
            rec.transaction_count = len(rec.transaction_ids)

    def action_view_listings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Listings',
            'res_model': 'realestate.listing',
            'view_mode': 'list,form',
            'domain': [('property_id', '=', self.id)],
            'context': {'default_property_id': self.id},
        }

    def action_view_transactions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Transactions',
            'res_model': 'realestate.transaction',
            'view_mode': 'list,form',
            'domain': [('property_id', '=', self.id)],
            'context': {'default_property_id': self.id},
        }

    @api.depends('listing_ids', 'listing_ids.state')
    def _compute_active_listing(self):
        for prop in self:
            active = prop.listing_ids.filtered(lambda l: l.state in ('active', 'under_offer'))
            prop.active_listing_id = active[:1]
            prop.for_sale = bool(active)

    @api.depends('transaction_ids', 'transaction_ids.state', 'transaction_ids.closing_date')
    def _compute_last_transaction(self):
        for prop in self:
            closed = prop.transaction_ids.filtered(lambda t: t.state == 'closed').sorted(
                key=lambda t: t.closing_date or fields.Date.today(), reverse=True,
            )
            prop.last_transaction_id = closed[:1]
            prop.is_sold = bool(closed)

    @api.depends('listing_ids.state')
    def _compute_sale_status(self):
        super()._compute_sale_status()
        for rec in self:
            if rec.sale_status == 'not_listed' and rec.listing_ids.filtered(lambda l: l.state == 'active'):
                rec.sale_status = 'for_sale'
