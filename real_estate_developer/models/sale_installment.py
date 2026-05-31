from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleInstallment(models.Model):
    _name = 'realestate.sale.installment'
    _description = 'Sale Contract Installment'
    _order = 'sale_contract_id, sequence, date_due'

    sale_contract_id = fields.Many2one(
        'realestate.sale.contract', string='Contract',
        required=True, ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    kind = fields.Selection([
        ('down', 'Down Payment'),
        ('installment', 'Installment'),
        ('balloon', 'Balloon'),
    ], required=True, default='installment')

    amount = fields.Monetary(string='Amount', required=True)
    date_due = fields.Date(string='Due Date', required=True)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('invoiced', 'Invoiced'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ], default='pending', required=True)

    move_id = fields.Many2one('account.move', string='Invoice', readonly=True, copy=False)
    move_state = fields.Selection(related='move_id.state', string='Invoice Status', store=True)
    payment_state = fields.Selection(
        related='move_id.payment_state', string='Payment Status', store=True)
    sale_order_line_id = fields.Many2one(
        'sale.order.line', string='Sale Order Line', readonly=True, copy=False,
        help="Line on the bridge sale order this installment invoices 1:1.")

    currency_id = fields.Many2one(related='sale_contract_id.currency_id', store=True, readonly=True)
    partner_id = fields.Many2one(related='sale_contract_id.partner_id', store=True, readonly=True)
    property_id = fields.Many2one(related='sale_contract_id.property_id', store=True, readonly=True)

    def action_generate_invoice(self):
        for rec in self:
            if rec.state != 'pending':
                raise UserError(_("Only pending installments can be invoiced."))
            prop = rec.property_id
            if not prop or not prop.product_variant_id:
                raise UserError(_("The unit has no linked product variant."))
            label_map = {'down': 'Down payment', 'installment': 'Installment', 'balloon': 'Balloon payment'}
            line_vals = {
                'name': f'{label_map[rec.kind]} for {prop.display_name}',
                'product_id': prop.product_variant_id.id,
                'quantity': 1,
                'price_unit': rec.amount,
            }
            # Tie the invoice line to the bridge SO line so it rolls up under
            # the sale order's native Invoices button and invoice_status.
            if rec.sale_order_line_id:
                line_vals['sale_line_ids'] = [(6, 0, rec.sale_order_line_id.ids)]
            invoice = self.env['account.move'].create({
                'move_type': 'out_invoice',
                'partner_id': rec.partner_id.id,
                'invoice_date': rec.date_due,
                'invoice_origin': rec.sale_contract_id.sale_order_id.name or rec.sale_contract_id.name,
                'payment_reference': f'{rec.sale_contract_id.name} #{rec.sequence}',
                'invoice_line_ids': [(0, 0, line_vals)],
            })
            rec.write({'move_id': invoice.id, 'state': 'invoiced'})
            # Post so the receivable lands on the ledger.
            self.env['realestate.account.tools'].post_moves(invoice)

    def action_mark_paid(self):
        for rec in self:
            if rec.state not in ('invoiced', 'pending'):
                raise UserError(_("Only pending or invoiced installments can be marked paid."))
            # Ensure a posted invoice exists, then register & reconcile a real payment.
            if not rec.move_id:
                rec.action_generate_invoice()
            self.env['realestate.account.tools'].post_moves(rec.move_id)
            self.env['realestate.account.tools'].register_payment(rec.move_id)
            if rec.move_id.payment_state in ('paid', 'in_payment', 'reversed'):
                rec.state = 'paid'

    @api.model
    def cron_auto_invoice_due_installments(self):
        """Deprecated: sales now use one invoice whose payment term splits the
        receivable, so there is nothing to auto-invoice per installment. Kept as
        a no-op because the cron record is noupdate and still references it."""
        return
