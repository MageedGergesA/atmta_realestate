from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PaymentCertificate(models.Model):
    """Progress billing — certifies a contractor's % complete on a milestone (or a
    period of work on the whole project) and produces a vendor bill, withholding
    a retention percentage."""
    _name = 'realestate.construction.payment.certificate'
    _description = 'Contractor Payment Certificate'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, period_end desc, id desc'

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'),
    )
    project_id = fields.Many2one('realestate.project', string='Project', required=True, tracking=True, ondelete='cascade')
    milestone_id = fields.Many2one(
        'realestate.construction.milestone', string='Milestone',
        domain="[('project_id', '=', project_id)]",
        ondelete='set null', tracking=True,
        help='Leave blank for project-level certification.',
    )
    contractor_id = fields.Many2one('realestate.contractor', string='Contractor', required=True, tracking=True)
    period_start = fields.Date(string='Period Start', tracking=True)
    period_end = fields.Date(string='Period End', default=fields.Date.context_today, tracking=True, required=True)

    contract_value = fields.Monetary(
        string='Contract Value', tracking=True,
        help='Total contract value with this contractor for the milestone/project (used as the base for %).',
    )
    previous_certified_pct = fields.Float(
        string='Previously Certified (%)', tracking=True, default=0.0,
        help='Cumulative % already certified in earlier payment certificates.',
    )
    current_certified_pct = fields.Float(
        string='This Period (%)', tracking=True, required=True, default=0.0,
        help='Additional completion certified in this period.',
    )
    cumulative_certified_pct = fields.Float(
        string='Cumulative (%)', compute='_compute_amounts', store=True,
    )

    gross_amount = fields.Monetary(
        string='Gross This Period', compute='_compute_amounts', store=True,
    )
    retention_pct = fields.Float(
        string='Retention (%)', tracking=True,
        help='% withheld from this payment. Defaults from the contractor record.',
    )
    retention_amount = fields.Monetary(
        string='Retention Withheld', compute='_compute_amounts', store=True,
    )
    net_payable = fields.Monetary(
        string='Net Payable', compute='_compute_amounts', store=True,
    )

    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    state = fields.Selection([
        ('draft', 'Draft'),
        ('certified', 'Certified'),
        ('invoiced', 'Invoiced'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True)

    vendor_bill_id = fields.Many2one(
        'account.move', string='Vendor Bill', readonly=True, copy=False,
    )
    purchase_order_id = fields.Many2one(
        'purchase.order', string='Subcontract PO', tracking=True,
        domain="[('partner_id', '=', contractor_partner_id)]",
        help='Source purchase order — usually the contractor\'s master subcontract PO.',
    )
    purchase_order_line_id = fields.Many2one(
        'purchase.order.line', string='PO Line', tracking=True,
        domain="[('order_id', '=', purchase_order_id)]",
        help='Specific milestone line being certified. Drives qty_received updates.',
    )
    contractor_partner_id = fields.Many2one(
        related='contractor_id.partner_id', store=False, readonly=True,
    )
    is_po_linked = fields.Boolean(compute='_compute_is_po_linked', store=False)
    notes = fields.Html()

    @api.depends('purchase_order_line_id')
    def _compute_is_po_linked(self):
        for rec in self:
            rec.is_po_linked = bool(rec.purchase_order_line_id)

    @api.depends('current_certified_pct', 'previous_certified_pct', 'contract_value', 'retention_pct')
    def _compute_amounts(self):
        for rec in self:
            rec.cumulative_certified_pct = rec.previous_certified_pct + rec.current_certified_pct
            rec.gross_amount = (rec.contract_value or 0.0) * (rec.current_certified_pct or 0.0) / 100.0
            rec.retention_amount = rec.gross_amount * (rec.retention_pct or 0.0) / 100.0
            rec.net_payable = rec.gross_amount - rec.retention_amount

    @api.onchange('contractor_id')
    def _onchange_contractor_id(self):
        if self.contractor_id and not self.retention_pct:
            self.retention_pct = self.contractor_id.retention_pct
        # Auto-fill previous cumulative from prior certified rows for same contractor + milestone
        if self.contractor_id and self.milestone_id:
            prior = self.search([
                ('contractor_id', '=', self.contractor_id.id),
                ('milestone_id', '=', self.milestone_id.id),
                ('state', 'in', ('certified', 'invoiced', 'paid')),
                ('id', '!=', self._origin.id if self._origin else 0),
            ])
            self.previous_certified_pct = sum(prior.mapped('current_certified_pct'))

    @api.onchange('milestone_id')
    def _onchange_milestone_id(self):
        if self.milestone_id:
            if not self.contract_value:
                self.contract_value = self.milestone_id.budget_amount
            self._onchange_contractor_id()

    @api.onchange('purchase_order_id')
    def _onchange_purchase_order_id(self):
        if not self.purchase_order_id:
            self.purchase_order_line_id = False
            return
        if self.purchase_order_id.partner_id and self.contractor_id and \
                self.purchase_order_id.partner_id != self.contractor_id.partner_id:
            self.purchase_order_id = False
            self.purchase_order_line_id = False
            return {'warning': {
                'title': _('Mismatch'),
                'message': _("The selected PO belongs to a different vendor than this contractor."),
            }}

    @api.onchange('purchase_order_line_id')
    def _onchange_purchase_order_line_id(self):
        line = self.purchase_order_line_id
        if not line:
            return
        # Line carries: qty=1, price_unit=milestone budget — adopt as contract_value
        if not self.contract_value:
            self.contract_value = line.price_unit * line.product_qty
        # Resolve milestone from the line if not yet set
        if not self.milestone_id and line.name:
            milestone = self.env['realestate.construction.milestone'].search([
                ('project_id', '=', self.project_id.id or self.purchase_order_id.re_project_id.id),
                ('name', '=', line.name),
            ], limit=1)
            if milestone:
                self.milestone_id = milestone

    @api.constrains('current_certified_pct', 'previous_certified_pct')
    def _check_pct(self):
        for rec in self:
            if rec.current_certified_pct < 0:
                raise ValidationError(_("This period's certification cannot be negative."))
            if rec.cumulative_certified_pct > 100.0:
                raise ValidationError(_(
                    "Cumulative certification cannot exceed 100%% (would be %.2f%%)."
                ) % rec.cumulative_certified_pct)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.construction.payment.certificate') or 'CERT-NEW'
        return super().create(vals_list)

    def action_certify(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft certificates can be certified."))
            if rec.current_certified_pct <= 0:
                raise UserError(_("Set a non-zero 'This Period (%)' before certifying."))
            rec.state = 'certified'
            # If linked to a PO line, push cumulative-certified-fraction into qty_received
            if rec.purchase_order_line_id:
                line = rec.purchase_order_line_id
                fraction = (rec.cumulative_certified_pct or 0.0) / 100.0
                target_qty = fraction * (line.product_qty or 1.0)
                if target_qty > line.qty_received:
                    line.qty_received = target_qty

    def action_create_vendor_bill(self):
        for rec in self:
            if rec.state != 'certified':
                raise UserError(_("Only certified certificates can be billed."))
            if rec.vendor_bill_id:
                raise UserError(_("A vendor bill already exists for this certificate."))
            if rec.net_payable <= 0:
                raise UserError(_("Net payable must be positive to create a vendor bill."))
            line_vals = {
                'name': _('%s — %.2f%% of %s') % (
                    rec.name,
                    rec.current_certified_pct,
                    rec.milestone_id.name or rec.project_id.name,
                ),
                'quantity': 1,
                'price_unit': rec.gross_amount,
            }
            # Link to PO line when present so Odoo's PO/bill matching engages
            if rec.purchase_order_line_id:
                line_vals.update({
                    'purchase_line_id': rec.purchase_order_line_id.id,
                    'product_id': rec.purchase_order_line_id.product_id.id,
                    'quantity': (rec.current_certified_pct or 0.0) / 100.0 * (rec.purchase_order_line_id.product_qty or 1.0),
                    'price_unit': rec.purchase_order_line_id.price_unit,
                })
            invoice_lines = [(0, 0, line_vals)]
            if rec.retention_amount > 0:
                invoice_lines.append((0, 0, {
                    'name': _('Retention withheld %.2f%% — %s') % (rec.retention_pct, rec.name),
                    'quantity': 1,
                    'price_unit': -rec.retention_amount,
                }))
            bill_vals = {
                'move_type': 'in_invoice',
                'partner_id': rec.contractor_id.partner_id.id,
                'invoice_date': rec.period_end or fields.Date.today(),
                'ref': rec.name,
                'invoice_line_ids': invoice_lines,
            }
            bill = self.env['account.move'].create(bill_vals)
            rec.vendor_bill_id = bill.id
            rec.state = 'invoiced'
            # Post so the payable lands on the ledger.
            self.env['realestate.account.tools'].post_moves(bill)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.vendor_bill_id.id,
        }

    def action_mark_paid(self):
        for rec in self:
            if rec.state != 'invoiced':
                raise UserError(_("Only invoiced certificates can be marked paid."))
            if not rec.vendor_bill_id:
                raise UserError(_("No vendor bill is linked to this certificate."))
            self.env['realestate.account.tools'].post_moves(rec.vendor_bill_id)
            self.env['realestate.account.tools'].register_payment(rec.vendor_bill_id)
            if rec.vendor_bill_id.payment_state in ('paid', 'in_payment', 'reversed'):
                rec.state = 'paid'

    def action_cancel(self):
        for rec in self:
            if rec.vendor_bill_id and rec.vendor_bill_id.state == 'posted':
                raise UserError(_(
                    "Cannot cancel a certificate whose vendor bill is already posted. "
                    "Reverse the bill in Accounting first."
                ))
            rec.state = 'cancelled'
