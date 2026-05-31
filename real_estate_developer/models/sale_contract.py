from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleContract(models.Model):
    _name = 'realestate.sale.contract'
    _description = 'Property Sale Contract'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'contract_date desc, id desc'

    name = fields.Char(string='Reference', copy=False, required=True, readonly=True, default=lambda self: _('New'))
    partner_id = fields.Many2one('res.partner', string='Buyer', required=True, tracking=True)
    property_id = fields.Many2one(
        'realestate.property', string='Unit',
        required=True, ondelete='restrict', tracking=True,
    )
    project_id = fields.Many2one(related='property_id.project_id', store=True, readonly=True)
    phase_id = fields.Many2one(related='property_id.phase_id', store=True, readonly=True)

    reservation_id = fields.Many2one('realestate.unit.reservation', string='Origin Reservation', readonly=True)
    agent_id = fields.Many2one('res.users', string='Sales Agent', default=lambda self: self.env.user, tracking=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('signed', 'Signed'),
        ('handed_over', 'Handed Over'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True, required=True)

    contract_date = fields.Date(string='Contract Date', default=fields.Date.context_today, tracking=True)
    signing_date = fields.Date(string='Signing Date', tracking=True)
    expected_handover_date = fields.Date(string='Expected Handover', tracking=True)
    handover_date = fields.Date(string='Handover Date', tracking=True)

    sale_price = fields.Monetary(string='Sale Price', required=True, tracking=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    payment_term_id = fields.Many2one(
        'account.payment.term', string='Payment Plan / Terms', tracking=True,
        help="Installment schedule applied to the single sale invoice.")
    invoice_id = fields.Many2one('account.move', string='Invoice', readonly=True, copy=False)
    # Legacy per-contract installments, kept for historical contracts.
    installment_ids = fields.One2many('realestate.sale.installment', 'sale_contract_id', string='Installments')

    paid_amount = fields.Monetary(string='Paid', compute='_compute_balances', store=True)
    invoiced_amount = fields.Monetary(string='Invoiced', compute='_compute_balances', store=True)
    balance_due = fields.Monetary(string='Balance Due', compute='_compute_balances', store=True)
    progress = fields.Float(string='Progress (%)', compute='_compute_balances', store=True)

    cancellation_reason = fields.Char(string='Cancellation Reason')
    notes = fields.Html()

    sale_order_id = fields.Many2one(
        'sale.order', string='Sale Order', readonly=True, copy=False,
        help="Commercial record on the Sales pipeline. Invoicing stays on the "
             "installment schedule.")

    @api.depends('invoice_id.amount_total', 'invoice_id.amount_residual',
                 'invoice_id.payment_state', 'invoice_id.state', 'sale_price')
    def _compute_balances(self):
        for rec in self:
            inv = rec.invoice_id
            if inv and inv.state == 'posted':
                total = inv.amount_total
                paid = total - inv.amount_residual
                rec.invoiced_amount = total
                rec.paid_amount = paid
                rec.balance_due = inv.amount_residual
                rec.progress = (paid / total * 100.0) if total else 0.0
            else:
                rec.invoiced_amount = 0.0
                rec.paid_amount = 0.0
                rec.balance_due = rec.sale_price or 0.0
                rec.progress = 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.sale.contract') or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_sign(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft contracts can be signed."))
            if not rec.payment_term_id:
                raise UserError(_("Set a payment plan (payment terms) before signing."))
            if not rec.property_id.product_variant_id:
                raise UserError(_("The unit has no linked product."))
            rec.write({'state': 'signed', 'signing_date': rec.signing_date or fields.Date.today()})
            # Committed via contract, not yet delivered.
            if rec.property_id.state == 'available':
                rec.property_id.state = 'reserved'
            rec._create_sale_and_invoice()

    def _create_sale_and_invoice(self):
        """One sale order (single unit line) + one customer invoice whose payment
        term splits the receivable into the installment schedule."""
        self.ensure_one()
        if self.invoice_id:
            return
        order = self.sale_order_id
        if not order:
            order = self.env['sale.order']._create_re_bridge_order(
                partner=self.partner_id, origin=self.name, source=self,
                line_vals=[{
                    'product_id': self.property_id.product_variant_id.id,
                    'name': _('Sale — %s') % self.property_id.display_name,
                    'product_uom_qty': 1,
                    'price_unit': self.sale_price,
                }],
            )
            if not order:
                return
            self.sale_order_id = order.id
        order.payment_term_id = self.payment_term_id.id
        # Apply discount + maintenance from the payment term BEFORE invoicing,
        # so the invoice total reflects them and the term splits the whole amount.
        self.payment_term_id.re_apply_extras_to_order(order, self.sale_price)
        # One invoice for the full price; the payment term spreads the due dates.
        invoices = order._create_invoices()
        if not invoices:
            return
        invoices.invoice_payment_term_id = self.payment_term_id.id
        self.env['realestate.account.tools'].post_moves(invoices)
        self.invoice_id = invoices[:1].id

    def action_view_sale_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': self.sale_order_id.id,
        }

    def action_view_invoice(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.invoice_id.id,
        }

    def action_handover(self):
        for rec in self:
            if rec.state != 'signed':
                raise UserError(_("Only signed contracts can be handed over."))
            rec.write({'state': 'handed_over', 'handover_date': fields.Date.today()})
            # Transfer ownership + lock property from further sales
            if rec.property_id and rec.partner_id:
                rec.property_id.owner_id = rec.partner_id
                rec.property_id.state = 'sold'

    def action_cancel(self):
        for rec in self:
            if rec.state == 'handed_over':
                raise UserError(_("Handed-over contracts cannot be cancelled; create a reversal instead."))
            rec.state = 'cancelled'
            # Free the property only if no other open holds remain
            prop = rec.property_id
            if prop and prop.state == 'reserved':
                still_held = prop.reservation_ids.filtered(lambda r: r.state in ('hold', 'booked')) \
                    or prop.sale_contract_ids.filtered(lambda c: c.state == 'signed' and c.id != rec.id)
                if not still_held:
                    prop.state = 'available'
