from odoo import _, api, fields, models
from odoo.exceptions import UserError


class Commission(models.Model):
    _name = 'realestate.commission'
    _description = 'Transaction Commission Line'
    _order = 'transaction_id, sequence, id'

    transaction_id = fields.Many2one(
        'realestate.transaction', string='Transaction',
        required=True, ondelete='cascade',
    )
    sequence = fields.Integer(default=10)

    partner_id = fields.Many2one('res.partner', string='Recipient', required=True)
    role = fields.Selection([
        ('lister', 'Lister Agent'),
        ('selling', 'Selling Agent'),
        ('referrer', 'Referrer'),
        ('brokerage', 'Brokerage'),
        ('other', 'Other'),
    ], default='lister', required=True)

    calculation_method = fields.Selection([
        ('percentage', 'Percentage of Sale Price'),
        ('fixed', 'Fixed Amount'),
    ], default='percentage', required=True)
    percentage = fields.Float(string='Percentage (%)')
    fixed_amount = fields.Monetary(string='Fixed Amount')

    amount = fields.Monetary(
        string='Amount',
        compute='_compute_amount', store=True,
    )
    currency_id = fields.Many2one(related='transaction_id.currency_id', store=True, readonly=True)

    bill_id = fields.Many2one(
        'account.move', string='Vendor Bill', readonly=True, copy=False,
        help="Posted vendor bill that pays this commission to the recipient.")
    payment_state = fields.Selection(
        related='bill_id.payment_state', string='Payment Status', store=True)
    paid = fields.Boolean(
        string='Paid', compute='_compute_paid', store=True,
        help="Derived from the vendor bill's reconciliation status.")
    payment_date = fields.Date(string='Paid On')
    payment_move_id = fields.Many2one('account.move', string='Payment Journal Entry')
    notes = fields.Char()

    @api.depends('calculation_method', 'percentage', 'fixed_amount', 'transaction_id.sale_price')
    def _compute_amount(self):
        for rec in self:
            if rec.calculation_method == 'fixed':
                rec.amount = rec.fixed_amount
            else:
                rec.amount = (rec.transaction_id.sale_price or 0.0) * (rec.percentage or 0.0) / 100.0

    @api.depends('bill_id.payment_state')
    def _compute_paid(self):
        for rec in self:
            rec.paid = rec.bill_id.payment_state in ('paid', 'in_payment', 'reversed')

    def action_create_vendor_bill(self):
        """Raise (and post) a vendor bill to the commission recipient."""
        for rec in self:
            if rec.bill_id:
                raise UserError(_("A vendor bill already exists for this commission."))
            if not rec.partner_id:
                raise UserError(_("Set the commission recipient before billing."))
            if rec.amount <= 0:
                raise UserError(_("Commission amount must be positive to create a bill."))
            role_label = dict(rec._fields['role'].selection).get(rec.role, '')
            bill = self.env['account.move'].create({
                'move_type': 'in_invoice',
                'partner_id': rec.partner_id.id,
                'invoice_date': fields.Date.context_today(rec),
                'ref': _('Commission — %s (%s)') % (rec.transaction_id.name, role_label),
                'invoice_line_ids': [(0, 0, {
                    'name': _('%s commission on %s') % (role_label, rec.transaction_id.name),
                    'quantity': 1,
                    'price_unit': rec.amount,
                })],
            })
            rec.bill_id = bill.id
            self.env['realestate.account.tools'].post_moves(bill)
        return True

    def action_mark_paid(self):
        """Bill (if needed) then register & reconcile a real payment."""
        for rec in self:
            if not rec.bill_id:
                rec.action_create_vendor_bill()
            self.env['realestate.account.tools'].post_moves(rec.bill_id)
            self.env['realestate.account.tools'].register_payment(rec.bill_id)
            if rec.bill_id.payment_state in ('paid', 'in_payment', 'reversed') and not rec.payment_date:
                rec.payment_date = fields.Date.today()

    def action_mark_unpaid(self):
        raise UserError(_(
            "Payment status is derived from the vendor bill. "
            "Reverse the payment on the linked bill to undo it."))
