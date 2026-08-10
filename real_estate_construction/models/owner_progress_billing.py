from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


OWNER_BILLING_STATES = [
    ('draft', 'Draft'),
    ('certified', 'Certified'),
    ('invoiced', 'Invoiced'),
    ('paid', 'Paid'),
    ('cancelled', 'Cancelled'),
]


class OwnerProgressBilling(models.Model):
    """Customer-side progress billing.

    Mirrors :class:`realestate.construction.payment.certificate` but bills the
    OWNER (project developer's client) rather than paying a contractor. Used
    when the developer sells construction progress to an end-buyer under a
    build-to-order contract.

    Amount = contract value × this-period %, minus deductions
    (e.g. mobilization advance recovery).
    """
    _name = 'realestate.owner.progress.billing'
    _description = 'Owner-Side Progress Billing'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, period_end desc, id desc'

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'),
    )
    project_id = fields.Many2one(
        'realestate.project', string='Project', required=True, tracking=True,
        ondelete='cascade', index=True,
    )
    sale_contract_id = fields.Many2one(
        'realestate.sale.contract', string='Sale Contract',
        tracking=True, ondelete='set null', index=True,
        help='Customer-side contract this billing certifies against.',
    )
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, tracking=True,
        compute='_compute_partner_from_contract', store=True, readonly=False,
    )
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    period_start = fields.Date(tracking=True)
    period_end = fields.Date(
        default=fields.Date.context_today, required=True, tracking=True,
    )

    contract_value = fields.Monetary(
        string='Contract Value', tracking=True, required=True,
        help='Base amount for percentage certification.',
    )
    previous_certified_pct = fields.Float(
        string='Previously Certified (%)', tracking=True, default=0.0,
    )
    current_certified_pct = fields.Float(
        string='This Period (%)', tracking=True, required=True, default=0.0,
    )
    cumulative_certified_pct = fields.Float(
        string='Cumulative (%)', compute='_compute_amounts', store=True,
    )

    gross_amount = fields.Monetary(
        string='Gross This Period', compute='_compute_amounts', store=True,
    )
    deduction_ids = fields.One2many(
        'realestate.owner.progress.billing.deduction',
        'billing_id', string='Deductions', copy=True,
    )
    deduction_total = fields.Monetary(
        string='Deductions', compute='_compute_amounts', store=True,
    )
    retention_pct = fields.Float(
        string='Retention (%)', tracking=True,
        help="Withheld by the owner from this billing. It is still ours to "
             "collect, so it is posted to a receivable — not written off as "
             "revenue we never earned.",
    )
    retention_amount = fields.Monetary(
        compute='_compute_amounts', store=True,
    )
    retention_movement_id = fields.Many2one(
        'realestate.construction.retention', readonly=True, copy=False,
    )
    net_invoiced = fields.Monetary(
        string='Net To Invoice', compute='_compute_amounts', store=True,
    )
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    state = fields.Selection(
        OWNER_BILLING_STATES, default='draft', tracking=True, required=True, copy=False,
    )
    customer_invoice_id = fields.Many2one(
        'account.move', string='Customer Invoice', readonly=True, copy=False,
    )
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Analytic Account',
        compute='_compute_analytic', store=True, readonly=False,
    )
    notes = fields.Html()

    # ---------- Computes ----------
    @api.depends('sale_contract_id')
    def _compute_partner_from_contract(self):
        for rec in self:
            if rec.sale_contract_id and rec.sale_contract_id.partner_id and not rec.partner_id:
                rec.partner_id = rec.sale_contract_id.partner_id

    @api.depends('project_id')
    def _compute_analytic(self):
        for rec in self:
            if rec.project_id and 'analytic_account_id' in rec.project_id._fields \
                    and rec.project_id.analytic_account_id and not rec.analytic_account_id:
                rec.analytic_account_id = rec.project_id.analytic_account_id

    @api.depends(
        'current_certified_pct', 'previous_certified_pct', 'contract_value',
        'deduction_ids.amount', 'retention_pct',
    )
    def _compute_amounts(self):
        for rec in self:
            rec.cumulative_certified_pct = rec.previous_certified_pct + rec.current_certified_pct
            rec.gross_amount = (rec.contract_value or 0.0) * (rec.current_certified_pct or 0.0) / 100.0
            rec.deduction_total = sum(rec.deduction_ids.mapped('amount'))
            rec.retention_amount = (
                rec.gross_amount * (rec.retention_pct or 0.0) / 100.0)
            rec.net_invoiced = (
                rec.gross_amount - rec.deduction_total - rec.retention_amount)

    # ---------- Constraints ----------
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
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.owner.progress.billing') or 'OPB-NEW'
        return super().create(vals_list)

    # ---------- Actions ----------
    def action_certify(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft billings can be certified."))
            if rec.current_certified_pct <= 0:
                raise UserError(_("Set a non-zero 'This Period (%)' before certifying."))
            # Auto-fill previous cumulative from prior billings on same contract
            prior = self.search([
                ('sale_contract_id', '=', rec.sale_contract_id.id or 0),
                ('state', 'in', ('certified', 'invoiced', 'paid')),
                ('id', '!=', rec.id),
            ])
            expected_prev = sum(prior.mapped('current_certified_pct'))
            if abs(expected_prev - rec.previous_certified_pct) > 0.01:
                rec.previous_certified_pct = expected_prev
            rec.state = 'certified'

    def action_create_customer_invoice(self):
        for rec in self:
            if rec.state != 'certified':
                raise UserError(_("Only certified billings can be invoiced."))
            if rec.customer_invoice_id:
                raise UserError(_("A customer invoice already exists."))
            if rec.net_invoiced <= 0:
                raise UserError(_("Net-to-invoice must be positive."))
            analytic_dist = False
            if rec.analytic_account_id and 'analytic_distribution' in \
                    self.env['account.move.line']._fields:
                analytic_dist = {str(rec.analytic_account_id.id): 100.0}
            main_line = {
                'name': _('%s — %.2f%% construction progress on %s') % (
                    rec.name, rec.current_certified_pct,
                    rec.sale_contract_id.name or rec.project_id.name,
                ),
                'quantity': 1,
                'price_unit': rec.gross_amount,
            }
            if analytic_dist:
                main_line['analytic_distribution'] = analytic_dist
            invoice_lines = [(0, 0, main_line)]
            retention_account = False
            if rec.retention_amount > 0:
                # Resolved before anything is created: a missing account must
                # refuse the posting, not leave half an invoice behind.
                retention_account = self.env[
                    'realestate.construction.accounts'].owner_retention_account(
                        rec.company_id)
                invoice_lines.append((0, 0, {
                    'name': _("Retention %(pct).2f%% held by the owner — "
                              "%(ref)s", pct=rec.retention_pct, ref=rec.name),
                    'quantity': 1.0,
                    'price_unit': -rec.retention_amount,
                    'account_id': retention_account.id,
                    'tax_ids': [(5, 0, 0)],
                }))
            for ded in rec.deduction_ids:
                ded_line = {
                    'name': ded.description or _('Deduction'),
                    'quantity': 1,
                    'price_unit': -ded.amount,
                }
                if analytic_dist:
                    ded_line['analytic_distribution'] = analytic_dist
                invoice_lines.append((0, 0, ded_line))
            invoice = self.env['account.move'].create({
                'move_type': 'out_invoice',
                'company_id': rec.company_id.id,
                'partner_id': rec.partner_id.id,
                'invoice_date': rec.period_end or fields.Date.context_today(rec),
                'ref': rec.name,
                'currency_id': rec.currency_id.id,
                'invoice_line_ids': invoice_lines,
            })
            rec.customer_invoice_id = invoice
            rec.state = 'invoiced'
            if rec.retention_amount > 0:
                rec.retention_movement_id = self.env[
                    'realestate.construction.retention'].create({
                        'project_id': rec.project_id.id,
                        'company_id': rec.company_id.id,
                        'side': 'owner',
                        'partner_id': rec.partner_id.id,
                        'currency_id': rec.currency_id.id,
                        'movement_type': 'hold',
                        'date': rec.period_end,
                        'amount': rec.retention_amount,
                        'move_id': invoice.id,
                    })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.customer_invoice_id.id,
        }

    def action_mark_paid(self):
        for rec in self:
            if rec.state != 'invoiced':
                raise UserError(_("Only invoiced billings can be marked paid."))
            if rec.customer_invoice_id.payment_state in ('paid', 'in_payment', 'reversed'):
                rec.state = 'paid'
            else:
                raise UserError(_(
                    "Invoice %s is not yet fully paid.", rec.customer_invoice_id.name,
                ))

    def action_cancel(self):
        for rec in self:
            if rec.customer_invoice_id and rec.customer_invoice_id.state == 'posted':
                raise UserError(_(
                    "Cannot cancel — reverse the customer invoice first."
                ))
            rec.state = 'cancelled'


class OwnerBillingDeduction(models.Model):
    """A single deduction line on an owner-progress-billing (e.g. mobilization
    advance recovery, materials advance, retention)."""
    _name = 'realestate.owner.progress.billing.deduction'
    _description = 'Owner Billing Deduction'
    _order = 'billing_id, sequence, id'

    DEDUCTION_KINDS = [
        ('mobilization', 'Mobilization Advance Recovery'),
        ('materials', 'Materials Advance Recovery'),
        ('retention', 'Retention'),
        ('penalty', 'Penalty / LD'),
        ('other', 'Other'),
    ]

    billing_id = fields.Many2one(
        'realestate.owner.progress.billing', required=True, ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    kind = fields.Selection(DEDUCTION_KINDS, required=True, default='other')
    description = fields.Char(required=True)
    amount = fields.Monetary(required=True)
    currency_id = fields.Many2one(
        related='billing_id.currency_id', readonly=True,
    )
