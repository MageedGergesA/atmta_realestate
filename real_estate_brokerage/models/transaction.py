from odoo import _, api, fields, models
from odoo.exceptions import UserError


class Transaction(models.Model):
    _name = 'realestate.transaction'
    _description = 'Property Sale Transaction'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'closing_date desc, id desc'

    name = fields.Char(string='Reference', copy=False, required=True, readonly=True, default=lambda self: _('New'))

    listing_id = fields.Many2one('realestate.listing', string='Listing', required=True, ondelete='restrict', tracking=True)
    offer_id = fields.Many2one('realestate.offer', string='Accepted Offer', ondelete='restrict', tracking=True)
    property_id = fields.Many2one(related='listing_id.property_id', store=True, readonly=True)

    transaction_type = fields.Selection([
        ('in_house', 'In-house Sale'),
        ('brokerage', 'Brokerage'),
    ], default='brokerage', required=True, tracking=True,
        help='In-house: selling own property, no third-party commission. Brokerage: selling on behalf of an owner.')

    seller_id = fields.Many2one('res.partner', string='Seller', tracking=True)
    buyer_id = fields.Many2one('res.partner', string='Buyer', required=True, tracking=True)
    lister_agent_id = fields.Many2one(related='listing_id.lister_agent_id', store=True, readonly=True)
    selling_agent_id = fields.Many2one(
        'res.users', string='Selling Agent',
        domain=[('is_realestate_agent', '=', True)],
        tracking=True,
    )

    sale_price = fields.Monetary(string='Sale Price', required=True, tracking=True)
    deposit_amount = fields.Monetary(string='Deposit')
    deposit_paid_date = fields.Date(string='Deposit Paid On', tracking=True)
    sale_agreement_date = fields.Date(string='Agreement Signed On', tracking=True)
    closing_date = fields.Date(string='Closing Date', tracking=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    state = fields.Selection([
        ('draft', 'Draft'),
        ('deposit_received', 'Deposit Received'),
        ('contract_signed', 'Contract Signed'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True, required=True)

    commission_ids = fields.One2many('realestate.commission', 'transaction_id', string='Commissions')
    total_commission = fields.Monetary(
        string='Total Commission',
        compute='_compute_total_commission', store=True,
    )

    cancellation_reason = fields.Char(string='Cancellation Reason', readonly=True)
    notes = fields.Html()
    document_ids = fields.Many2many(
        'ir.attachment', 'realestate_transaction_attachment_rel', 'transaction_id', 'attachment_id',
        string='Documents',
    )
    sale_order_id = fields.Many2one(
        'sale.order', string='Sale Order', readonly=True, copy=False,
        help="Commercial record on the Sales pipeline for this resale.")
    payment_term_id = fields.Many2one(
        'account.payment.term', string='Payment Plan / Terms', tracking=True,
        help="Installment schedule applied to the in-house sale's invoice.")
    invoice_id = fields.Many2one('account.move', string='Invoice', readonly=True, copy=False)

    # ------------------------------------------------------------
    # Computes / defaults
    # ------------------------------------------------------------
    @api.depends('commission_ids.amount')
    def _compute_total_commission(self):
        for rec in self:
            rec.total_commission = sum(rec.commission_ids.mapped('amount'))

    @api.onchange('listing_id')
    def _onchange_listing_id(self):
        if self.listing_id and not self.seller_id:
            self.seller_id = self.listing_id.property_id.owner_id
        if self.listing_id and not self.selling_agent_id:
            self.selling_agent_id = self.listing_id.lister_agent_id

    # ------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.transaction') or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------
    def action_record_deposit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Deposit can only be recorded on draft transactions."))
            if not rec.deposit_amount:
                raise UserError(_("Set a deposit amount before recording."))
            rec.state = 'deposit_received'
            if not rec.deposit_paid_date:
                rec.deposit_paid_date = fields.Date.today()

    def action_sign_contract(self):
        for rec in self:
            if rec.state not in ('draft', 'deposit_received'):
                raise UserError(_("Contract can only be signed from draft or deposit-received state."))
            rec.state = 'contract_signed'
            if not rec.sale_agreement_date:
                rec.sale_agreement_date = fields.Date.today()

    def action_close(self):
        for rec in self:
            if rec.state not in ('contract_signed', 'deposit_received'):
                raise UserError(_("Transaction must have a signed contract before closing."))
            if rec.transaction_type == 'brokerage' and not rec.commission_ids:
                raise UserError(_("Add at least one commission line for a brokerage transaction before closing."))
            rec.state = 'closed'
            if not rec.closing_date:
                rec.closing_date = fields.Date.today()
            # Listing & property updates
            rec.listing_id.write({
                'state': 'sold',
                'sold_date': rec.closing_date,
                'final_sale_price': rec.sale_price,
            })
            # Transfer ownership to buyer + lock from further sales
            if rec.property_id and rec.buyer_id:
                rec.property_id.write({
                    'owner_id': rec.buyer_id.id,
                    'state': 'sold',
                })
            # Only in-house sales mirror onto the Sales pipeline: the company is
            # the seller and recognises the sale price. A brokerage deal is a
            # third-party sale — the company is only the broker (revenue = its
            # commission), so booking a full-price sale order would overstate it.
            if (rec.transaction_type == 'in_house'
                    and not rec.sale_order_id
                    and rec.property_id.product_variant_id):
                order = self.env['sale.order']._create_re_bridge_order(
                    partner=rec.buyer_id,
                    origin=rec.name,
                    source=rec,
                    line_vals=[{
                        'product_id': rec.property_id.product_variant_id.id,
                        'name': _('Sale — %s') % rec.property_id.display_name,
                        'product_uom_qty': 1,
                        'price_unit': rec.sale_price,
                    }],
                )
                if order:
                    rec.sale_order_id = order.id
                    if rec.payment_term_id:
                        order.payment_term_id = rec.payment_term_id.id
                        # Apply discount + maintenance lines to the SO before
                        # invoicing so the schedule covers the full price.
                        rec.payment_term_id.re_apply_extras_to_order(
                            order, rec.sale_price)
                    # One invoice for the full price; the payment term (if any)
                    # spreads it into installments.
                    invoices = order._create_invoices()
                    if invoices:
                        if rec.payment_term_id:
                            invoices.invoice_payment_term_id = rec.payment_term_id.id
                        self.env['realestate.account.tools'].post_moves(invoices)
                        rec.invoice_id = invoices[:1].id

    def action_view_invoice(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.invoice_id.id,
        }

    def action_view_sale_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': self.sale_order_id.id,
        }

    def action_cancel(self):
        for rec in self:
            if rec.state == 'closed':
                raise UserError(_("Closed transactions cannot be cancelled. Create a reversal instead."))
            rec.state = 'cancelled'
            # Free up the listing
            if rec.listing_id.state == 'under_offer' and rec.listing_id.transaction_id == rec:
                rec.listing_id.write({'state': 'active', 'transaction_id': False})

    def action_add_default_commissions(self):
        """Seed commission lines from agents' default share %."""
        for rec in self:
            if rec.commission_ids:
                continue
            lines = []
            for agent, role in [(rec.lister_agent_id, 'lister'), (rec.selling_agent_id, 'selling')]:
                if not agent:
                    continue
                share = agent.commission_share_default or 0.0
                if share <= 0:
                    continue
                lines.append((0, 0, {
                    'partner_id': agent.partner_id.id,
                    'role': role,
                    'calculation_method': 'percentage',
                    'percentage': share,
                }))
            if lines:
                rec.commission_ids = lines
