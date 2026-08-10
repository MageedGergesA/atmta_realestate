# -*- coding: utf-8 -*-
"""Interim payment certificates — what was claimed, what was certified.

M7 rebuilt this model around three facts Phase 0 found missing:

1. **The claim and the certificate are different documents.** A contractor
   applies for an amount; an engineer certifies an amount. Storing one number
   and overwriting it destroys the only evidence of a disallowance, which is
   exactly the number argued about at final account.
2. **Retention is a liability, not a discount.** The old bill posted a negative
   expense line, so a 100,000 certificate with 5% retention recorded 95,000 of
   cost. The work still cost 100,000. See `realestate.construction.retention`.
3. **Cumulative certification is a database question.** It used to be filled in
   by an onchange, which anything not going through the form skipped — two
   certificates could each claim the same progress.

The percentage fields are kept because live data uses them, and they still
drive an amount when no amount is given. What they no longer do is decide what
gets posted.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

CERTIFICATE_STATES = [
    ('draft', 'Draft'),
    ('submitted', 'Applied For'),
    ('certified', 'Certified'),
    ('invoiced', 'Invoiced'),
    ('paid', 'Paid'),
    ('rejected', 'Rejected'),
    ('cancelled', 'Cancelled'),
]

#: States in which a certificate counts as certified work.
COUNTED_STATES = ('certified', 'invoiced', 'paid')


class PaymentCertificate(models.Model):
    _name = 'realestate.construction.payment.certificate'
    _description = 'Contractor Payment Certificate'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, period_end desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    project_id = fields.Many2one(
        'realestate.project', string='Project', required=True, tracking=True,
        ondelete='cascade', index=True, check_company=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', string='Contract Package',
        ondelete='set null', check_company=True, tracking=True, index=True,
        domain="[('project_id', '=', project_id)]",
        help="The contract this certificate draws down. When set, the "
             "authorised value and the cumulative ceiling come from it.")
    milestone_id = fields.Many2one(
        'realestate.construction.milestone', string='Milestone',
        domain="[('project_id', '=', project_id)]",
        ondelete='set null', tracking=True,
        help='Leave blank for project-level certification.')
    contractor_id = fields.Many2one(
        'realestate.contractor', string='Contractor', required=True,
        tracking=True, index=True)
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', string='Cost Code',
        ondelete='set null',
        help="Where this certificate's cost lands in the cost report. "
             "Without it the largest document on the job reaches the project "
             "and no cost code.")

    period_start = fields.Date(string='Period Start', tracking=True)
    period_end = fields.Date(
        string='Period End', default=fields.Date.context_today,
        tracking=True, required=True, index=True)

    # ------------------------------------------------------------------
    # The contract base
    # ------------------------------------------------------------------
    contract_value = fields.Monetary(
        string='Contract Value', tracking=True,
        help='Base amount for percentage certification when no package is '
             'linked.')
    authorised_amount = fields.Monetary(
        compute='_compute_authorised', store=True,
        help="What may be certified in total: the package's current contract "
             "value (original plus approved variations), or the contract "
             "value typed here. Zero means no authorised base is known — not "
             "that nothing is authorised.")
    has_authorised_base = fields.Boolean(compute='_compute_authorised',
                                         store=True)

    # ------------------------------------------------------------------
    # Claim vs certificate — two numbers, never one
    # ------------------------------------------------------------------
    applied_amount = fields.Monetary(
        string='Applied For', tracking=True,
        compute='_compute_applied', store=True, readonly=False,
        help="What the contractor claimed for this period, tax-exclusive.")
    certified_amount = fields.Monetary(
        string='Certified', tracking=True,
        compute='_compute_certified_default', store=True, readonly=False,
        help="What is certified for payment. Frozen once certified.")
    disallowed_amount = fields.Monetary(
        compute='_compute_disallowed', store=True,
        help="Claimed and not certified. The number the final account argues "
             "about — kept, not overwritten.")
    disallowance_reason = fields.Text(
        help="Why the claim was not certified in full.")

    previous_certified_amount = fields.Monetary(
        compute='_compute_cumulative',
        help="Certified before this certificate, read from the register of "
             "certificates rather than typed.")
    cumulative_certified_amount = fields.Monetary(compute='_compute_cumulative')

    # Legacy percentage view of the same thing.
    previous_certified_pct = fields.Float(
        string='Previously Certified (%)', tracking=True, default=0.0)
    current_certified_pct = fields.Float(
        string='This Period (%)', tracking=True, default=0.0)
    cumulative_certified_pct = fields.Float(
        string='Cumulative (%)', compute='_compute_amounts', store=True)

    gross_amount = fields.Monetary(
        string='Gross This Period', compute='_compute_amounts', store=True,
        help="The certified amount. Kept under its old name because reports "
             "and live data refer to it.")
    retention_pct = fields.Float(
        string='Retention (%)', tracking=True,
        help='% withheld from this payment. Defaults from the contractor.')
    retention_amount = fields.Monetary(
        string='Retention Withheld', compute='_compute_amounts', store=True)
    recovery_line_ids = fields.One2many(
        'realestate.construction.advance.recovery', 'certificate_id',
        string='Advance Recovery')
    recovery_total = fields.Monetary(
        compute='_compute_amounts', store=True)
    net_payable = fields.Monetary(
        string='Net Payable', compute='_compute_amounts', store=True)

    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)

    state = fields.Selection(
        CERTIFICATE_STATES, default='draft', required=True, tracking=True,
        copy=False, index=True)
    certified_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    certified_on = fields.Datetime(readonly=True, copy=False)

    vendor_bill_id = fields.Many2one(
        'account.move', string='Vendor Bill', readonly=True, copy=False)
    retention_movement_id = fields.Many2one(
        'realestate.construction.retention', readonly=True, copy=False)
    retention_posted_correctly = fields.Boolean(
        readonly=True, copy=False,
        help="Set when the bill posted retention to the liability account. "
             "Certificates posted before M7 stay false, and the disclosure "
             "keeps reporting them.")

    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Analytic Account',
        compute='_compute_analytic', store=True, readonly=False,
        help='Legacy single-plan cost centre. The cost code drives the '
             'distribution when it is set.')

    purchase_order_id = fields.Many2one(
        'purchase.order', string='Subcontract PO', tracking=True,
        domain="[('partner_id', '=', contractor_partner_id)]",
        help="Source purchase order — usually the contractor's master "
             "subcontract PO.")
    purchase_order_line_id = fields.Many2one(
        'purchase.order.line', string='PO Line', tracking=True,
        domain="[('order_id', '=', purchase_order_id)]",
        help='Specific milestone line being certified. Drives qty_received.')
    contractor_partner_id = fields.Many2one(
        related='contractor_id.partner_id', store=False, readonly=True)
    is_po_linked = fields.Boolean(compute='_compute_is_po_linked', store=False)
    notes = fields.Html()

    line_ids = fields.One2many(
        'realestate.construction.payment.certificate.line',
        'certificate_id', string='BOQ Line Detail', copy=True,
        help='Quantity-based BOQ certification lines. When populated they '
             'drive the applied amount.')
    lines_gross_amount = fields.Monetary(
        string='Lines Gross', compute='_compute_amounts', store=True)

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('package_id', 'package_id.current_contract_value',
                 'contract_value')
    def _compute_authorised(self):
        for rec in self:
            if rec.package_id:
                rec.authorised_amount = rec.package_id.current_contract_value
                rec.has_authorised_base = True
            elif rec.contract_value:
                rec.authorised_amount = rec.contract_value
                rec.has_authorised_base = True
            else:
                # Not zero-as-a-number. Zero here means "unknown", and
                # `has_authorised_base` is what callers must read.
                rec.authorised_amount = 0.0
                rec.has_authorised_base = False

    @api.depends('line_ids.amount', 'contract_value', 'current_certified_pct')
    def _compute_applied(self):
        for rec in self:
            if rec.line_ids:
                rec.applied_amount = sum(rec.line_ids.mapped('amount'))
            elif rec.contract_value and rec.current_certified_pct:
                rec.applied_amount = (
                    rec.contract_value * rec.current_certified_pct / 100.0)
            else:
                rec.applied_amount = rec.applied_amount or 0.0

    @api.depends('applied_amount')
    def _compute_certified_default(self):
        """Certified starts at what was applied for; an engineer lowers it.

        The `or applied` branch also carries the upgrade: certificates that
        pre-date M7 have no certified amount stored, and their certified value
        is the amount they billed. Leaving them at zero would erase the
        history this milestone exists to protect.
        """
        for rec in self:
            if not rec.certified_amount:
                rec.certified_amount = rec.applied_amount

    @api.depends('applied_amount', 'certified_amount')
    def _compute_disallowed(self):
        for rec in self:
            rec.disallowed_amount = max(
                0.0, (rec.applied_amount or 0.0) - (rec.certified_amount or 0.0))

    @api.depends('certified_amount', 'retention_pct', 'previous_certified_pct',
                 'current_certified_pct', 'contract_value',
                 'line_ids.amount', 'recovery_line_ids.amount')
    def _compute_amounts(self):
        for rec in self:
            rec.lines_gross_amount = sum(rec.line_ids.mapped('amount'))
            rec.gross_amount = rec.certified_amount or 0.0
            rec.cumulative_certified_pct = (
                (rec.previous_certified_pct or 0.0)
                + (rec.current_certified_pct or 0.0))
            rec.retention_amount = (
                rec.gross_amount * (rec.retention_pct or 0.0) / 100.0)
            rec.recovery_total = sum(rec.recovery_line_ids.mapped('amount'))
            rec.net_payable = (
                rec.gross_amount - rec.retention_amount - rec.recovery_total)

    def _certificate_domain(self):
        """Other certificates drawing on the same contract."""
        self.ensure_one()
        domain = [('id', '!=', self.id or 0),
                  ('state', 'in', list(COUNTED_STATES)),
                  ('contractor_id', '=', self.contractor_id.id)]
        if self.package_id:
            domain.append(('package_id', '=', self.package_id.id))
        else:
            domain += [('project_id', '=', self.project_id.id),
                       ('package_id', '=', False)]
        return domain

    @api.depends('project_id', 'package_id', 'contractor_id',
                 'certified_amount', 'state')
    def _compute_cumulative(self):
        for rec in self:
            if not rec.contractor_id or not rec.project_id:
                rec.previous_certified_amount = 0.0
                rec.cumulative_certified_amount = rec.certified_amount or 0.0
                continue
            groups = self._read_group(rec._certificate_domain(),
                                      aggregates=['certified_amount:sum'])
            previous = (groups[0][0] if groups else 0.0) or 0.0
            rec.previous_certified_amount = previous
            rec.cumulative_certified_amount = previous + (
                rec.certified_amount or 0.0)

    @api.depends('project_id')
    def _compute_analytic(self):
        for rec in self:
            if rec.project_id and 'analytic_account_id' in \
                    rec.project_id._fields and \
                    rec.project_id.analytic_account_id and \
                    not rec.analytic_account_id:
                rec.analytic_account_id = rec.project_id.analytic_account_id

    @api.depends('purchase_order_line_id')
    def _compute_is_po_linked(self):
        for rec in self:
            rec.is_po_linked = bool(rec.purchase_order_line_id)

    # ------------------------------------------------------------------
    # Onchanges — convenience only. Nothing is enforced here.
    # ------------------------------------------------------------------
    @api.onchange('contractor_id')
    def _onchange_contractor_id(self):
        if self.contractor_id and not self.retention_pct:
            self.retention_pct = self.contractor_id.retention_pct

    @api.onchange('package_id')
    def _onchange_package_id(self):
        if self.package_id:
            if not self.contractor_id:
                self.contractor_id = self.package_id.contractor_id
            if not self.retention_pct:
                self.retention_pct = self.package_id.retention_pct

    @api.onchange('milestone_id')
    def _onchange_milestone_id(self):
        if self.milestone_id and not self.contract_value:
            self.contract_value = self.milestone_id.budget_amount

    @api.onchange('purchase_order_id')
    def _onchange_purchase_order_id(self):
        if not self.purchase_order_id:
            self.purchase_order_line_id = False
            return
        if self.purchase_order_id.partner_id and self.contractor_id and \
                self.purchase_order_id.partner_id != \
                self.contractor_id.partner_id:
            self.purchase_order_id = False
            self.purchase_order_line_id = False
            return {'warning': {
                'title': _('Mismatch'),
                'message': _("The selected PO belongs to a different vendor "
                             "than this contractor."),
            }}

    @api.onchange('purchase_order_line_id')
    def _onchange_purchase_order_line_id(self):
        line = self.purchase_order_line_id
        if line and not self.contract_value:
            self.contract_value = line.price_unit * line.product_qty

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('applied_amount', 'certified_amount')
    def _check_amounts(self):
        for rec in self:
            if rec.applied_amount < 0 or rec.certified_amount < 0:
                raise ValidationError(_(
                    "A certificate for a negative amount is a credit note, "
                    "and belongs in Accounting."))
            if rec.currency_id.compare_amounts(
                    rec.certified_amount, rec.applied_amount) > 0:
                raise ValidationError(_(
                    "%(name)s certifies %(certified)s against a claim of "
                    "%(applied)s. More cannot be certified than was applied "
                    "for — the extra belongs on the next application.",
                    name=rec.name, certified=rec.certified_amount,
                    applied=rec.applied_amount))

    @api.constrains('current_certified_pct', 'previous_certified_pct')
    def _check_pct(self):
        for rec in self:
            if rec.current_certified_pct < 0:
                raise ValidationError(_(
                    "This period's certification cannot be negative."))
            if rec.cumulative_certified_pct > 100.0:
                raise ValidationError(_(
                    "Cumulative certification cannot exceed 100%% "
                    "(would be %.2f%%).") % rec.cumulative_certified_pct)

    @api.constrains('project_id', 'company_id', 'contractor_id')
    def _check_company_consistency(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Certificate %(name)s is in %(company)s but its project "
                    "is in %(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))
            partner = rec.contractor_id.partner_id
            if partner and partner.company_id and \
                    partner.company_id != rec.company_id:
                raise ValidationError(_(
                    "%(contractor)s belongs to %(other)s. A certificate "
                    "cannot bill another company's vendor.",
                    contractor=rec.contractor_id.display_name,
                    other=partner.company_id.display_name))

    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.payment.certificate') \
                    or 'CERT-NEW'
        return super().create(vals_list)

    def write(self, vals):
        """A certified figure is evidence. It is superseded, not edited."""
        frozen = {'certified_amount', 'applied_amount', 'retention_pct',
                  'contract_value', 'period_end', 'contractor_id',
                  'package_id', 'cost_code_id'}
        if frozen & set(vals) and not self.env.context.get('re_certifying'):
            locked = self.filtered(lambda c: c.state in COUNTED_STATES)
            if locked:
                raise UserError(_(
                    "%(refs)s have been certified. Issue the next certificate "
                    "with an adjustment rather than rewriting one somebody "
                    "signed and a bill was posted from.",
                    refs=', '.join(locked.mapped('name'))))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit(self):
        """The contractor's application. Certifying is a separate act."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("%s is not a draft application.") % rec.name)
            if rec.applied_amount <= 0:
                raise UserError(_(
                    "An application for nothing cannot be assessed."))
            rec.state = 'submitted'
        return True

    def action_certify(self):
        for rec in self:
            if rec.state not in ('draft', 'submitted'):
                raise UserError(_(
                    "Only a draft or applied-for certificate is certified."))
            if rec.certified_amount <= 0:
                raise UserError(_(
                    "Certify an amount, or reject the application."))
            rec._check_recovery_within_advances()
            # Serialise per contract: two people certifying at once is how the
            # same progress gets paid twice.
            self.env.cr.execute(
                'SELECT pg_advisory_xact_lock(%s, %s)',
                (rec.package_id.id or rec.project_id.id,
                 rec.contractor_id.id))
            rec._check_within_authorised()
            rec._check_boq_quantities()

            rec.with_context(re_certifying=True).write({
                'state': 'certified',
                'certified_by_id': self.env.user.id,
                'certified_on': fields.Datetime.now(),
            })
            rec._push_po_progress()
        return True

    def _check_within_authorised(self):
        """Cumulative certification, answered from the database."""
        self.ensure_one()
        if not self.has_authorised_base:
            # No contract base is recorded. There is nothing to check against,
            # and inventing a ceiling of zero would refuse every certificate.
            return True
        groups = self._read_group(self._certificate_domain(),
                                  aggregates=['certified_amount:sum'])
        previous = (groups[0][0] if groups else 0.0) or 0.0
        cumulative = previous + self.certified_amount
        if self.currency_id.compare_amounts(
                cumulative, self.authorised_amount) > 0:
            raise UserError(_(
                "%(name)s would take cumulative certification to "
                "%(cumulative).2f against an authorised %(authorised).2f. "
                "Certifying beyond the contract needs a variation, not a "
                "bigger certificate.",
                name=self.name, cumulative=cumulative,
                authorised=self.authorised_amount))
        return True

    def _check_boq_quantities(self):
        """A BOQ line cannot be certified beyond its authorised quantity.

        Phase 0 proved the old guard could never fire — it added the line's own
        quantity back into the ceiling — and that nothing re-checked at
        certification, so two drafts could each certify the whole line.
        """
        self.ensure_one()
        for line in self.line_ids:
            boq_line = line.boq_line_id
            if not boq_line:
                continue
            authorised = boq_line.authorised_quantity
            others = self.env[
                'realestate.construction.payment.certificate.line'].search([
                    ('boq_line_id', '=', boq_line.id),
                    ('certificate_id', '!=', self.id),
                    ('certificate_id.state', 'in', list(COUNTED_STATES)),
                ])
            already = sum(others.mapped('qty'))
            if float_round_gt(already + line.qty, authorised):
                raise UserError(_(
                    "%(item)s: certifying %(qty).2f would take the total to "
                    "%(total).2f against an authorised quantity of "
                    "%(authorised).2f. Extra quantity is a variation.",
                    item=boq_line.display_name, qty=line.qty,
                    total=already + line.qty, authorised=authorised))
        return True

    def _check_recovery_within_advances(self):
        self.ensure_one()
        for recovery in self.recovery_line_ids:
            advance = recovery.advance_id
            others = self.env[
                'realestate.construction.advance.recovery'].search([
                    ('advance_id', '=', advance.id),
                    ('certificate_id', '!=', self.id),
                    ('certificate_id.state', 'in', list(COUNTED_STATES)),
                ])
            already = sum(others.mapped('amount'))
            if advance.currency_id.compare_amounts(
                    already + recovery.amount, advance.amount) > 0:
                raise UserError(_(
                    "%(advance)s advanced %(amount).2f and %(already).2f has "
                    "already been recovered. Recovering %(now).2f more would "
                    "take back money that was never paid out.",
                    advance=advance.name, amount=advance.amount,
                    already=already, now=recovery.amount))
        return True

    def _push_po_progress(self):
        """Drive Odoo's own three-way matching, forward only.

        Procurement owns the purchase order; this moves the received quantity
        it already uses for matching, and never backwards.
        """
        self.ensure_one()
        line = self.purchase_order_line_id
        if not line or not self.has_authorised_base:
            return False
        fraction = min(1.0, (self.cumulative_certified_amount or 0.0)
                       / (self.authorised_amount or 1.0))
        target = fraction * (line.product_qty or 1.0)
        if target > line.qty_received:
            line.qty_received = target
        return True

    def action_reject(self):
        for rec in self:
            if rec.state not in ('draft', 'submitted'):
                raise UserError(_(
                    "Only an open application can be rejected."))
            rec.state = 'rejected'
        return True

    # ------------------------------------------------------------------
    # Accounting
    # ------------------------------------------------------------------
    def _work_line_vals(self):
        """The cost lines — the only lines that carry the cost dimension."""
        self.ensure_one()
        Analytic = self.env['realestate.construction.analytic']
        distribution = False
        if self.cost_code_id:
            distribution = Analytic.distribution_for(
                self.project_id, self.cost_code_id)
        elif self.analytic_account_id:
            distribution = {str(self.analytic_account_id.id): 100.0}

        lines = []
        if self.line_ids and self.currency_id.compare_amounts(
                self.lines_gross_amount, self.certified_amount) == 0:
            # Detail mode, and the detail still agrees with the certificate.
            for detail in self.line_ids:
                vals = {
                    'name': detail.description
                            or detail.work_item_id.display_name,
                    'quantity': detail.qty,
                    'price_unit': detail.unit_rate,
                    'tax_ids': [(5, 0, 0)],
                }
                if detail.boq_line_id.product_id:
                    vals['product_id'] = detail.boq_line_id.product_id.id
                line_distribution = distribution
                if detail.boq_line_id.cost_code_id:
                    line_distribution = Analytic.distribution_for(
                        self.project_id, detail.boq_line_id.cost_code_id)
                if line_distribution:
                    vals['analytic_distribution'] = line_distribution
                lines.append((0, 0, vals))
            return lines

        vals = {
            'name': _("%(ref)s — work certified to %(date)s",
                      ref=self.name, date=self.period_end),
            'quantity': 1.0,
            'price_unit': self.certified_amount,
            'tax_ids': [(5, 0, 0)],
        }
        if self.purchase_order_line_id:
            vals.update({
                'purchase_line_id': self.purchase_order_line_id.id,
                'product_id': self.purchase_order_line_id.product_id.id,
            })
        if distribution:
            vals['analytic_distribution'] = distribution
        return [(0, 0, vals)]

    def action_create_vendor_bill(self):
        """Post the certificate.

            Dr Work in progress / expense   certified
            Cr Retention payable            retention
            Cr Advance recovered            recovery
            Cr Accounts payable             net

        Retention and recovery lines deliberately carry **no analytic
        distribution**: they are balance-sheet movements, and letting them
        reach the cost report is precisely the defect being fixed.
        """
        Accounts = self.env['realestate.construction.accounts']
        Retention = self.env['realestate.construction.retention']
        for rec in self:
            if rec.state != 'certified':
                raise UserError(_(
                    "Only certified certificates can be billed."))
            if rec.vendor_bill_id:
                raise UserError(_(
                    "A vendor bill already exists for this certificate."))
            if rec.currency_id.compare_amounts(rec.net_payable, 0.0) <= 0:
                raise UserError(_(
                    "%(name)s nets to %(net).2f. Nothing is payable, so there "
                    "is nothing to bill — reduce the recovery or wait for the "
                    "next certificate.", name=rec.name, net=rec.net_payable))

            # Resolve the control accounts *before* creating anything, so a
            # missing account never leaves half a document behind.
            retention_account = Accounts.retention_account(rec.company_id) \
                if rec.retention_amount > 0 else False
            advance_account = Accounts.advance_account(rec.company_id) \
                if rec.recovery_total > 0 else False

            invoice_lines = rec._work_line_vals()
            if retention_account:
                invoice_lines.append((0, 0, {
                    'name': _("Retention %(pct).2f%% withheld — %(ref)s",
                              pct=rec.retention_pct, ref=rec.name),
                    'quantity': 1.0,
                    'price_unit': -rec.retention_amount,
                    'account_id': retention_account.id,
                    'tax_ids': [(5, 0, 0)],
                }))
            for recovery in rec.recovery_line_ids:
                invoice_lines.append((0, 0, {
                    'name': _("Advance recovery — %(advance)s",
                              advance=recovery.advance_id.name),
                    'quantity': 1.0,
                    'price_unit': -recovery.amount,
                    'account_id': advance_account.id,
                    'tax_ids': [(5, 0, 0)],
                }))

            bill = self.env['account.move'].create({
                'move_type': 'in_invoice',
                'partner_id': rec.contractor_id.partner_id.id,
                'company_id': rec.company_id.id,
                'currency_id': rec.currency_id.id,
                'invoice_date': rec.period_end or fields.Date.context_today(rec),
                'date': rec.period_end or fields.Date.context_today(rec),
                'ref': rec.name,
                'invoice_line_ids': invoice_lines,
            })
            self.env['realestate.account.tools'].post_moves(bill)

            movement = False
            if rec.retention_amount > 0:
                movement = Retention.create({
                    'project_id': rec.project_id.id,
                    'company_id': rec.company_id.id,
                    'contractor_id': rec.contractor_id.id,
                    'package_id': rec.package_id.id or False,
                    'currency_id': rec.currency_id.id,
                    'movement_type': 'hold',
                    'date': rec.period_end,
                    'amount': rec.retention_amount,
                    'certificate_id': rec.id,
                    'move_id': bill.id,
                })
            rec.with_context(re_certifying=True).write({
                'vendor_bill_id': bill.id,
                'state': 'invoiced',
                'retention_movement_id': movement and movement.id or False,
                'retention_posted_correctly': True,
            })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.vendor_bill_id.id,
        }

    def action_mark_paid(self):
        """Read the ledger. Paying suppliers is treasury's job, not this form."""
        for rec in self:
            if rec.state != 'invoiced':
                raise UserError(_(
                    "Only invoiced certificates can be marked paid."))
            if not rec.vendor_bill_id:
                raise UserError(_(
                    "No vendor bill is linked to this certificate."))
            if rec.vendor_bill_id.payment_state not in (
                    'paid', 'in_payment', 'reversed'):
                raise UserError(_(
                    "%(bill)s is not paid yet. Register the payment in "
                    "Accounting; this certificate follows the ledger rather "
                    "than deciding it.", bill=rec.vendor_bill_id.name))
            rec.with_context(re_certifying=True).state = 'paid'
        return True

    def action_cancel(self):
        for rec in self:
            if rec.vendor_bill_id and rec.vendor_bill_id.state == 'posted':
                raise UserError(_(
                    "Cannot cancel a certificate whose vendor bill is already "
                    "posted. Reverse the bill in Accounting first."))
            rec.with_context(re_certifying=True).state = 'cancelled'
        return True


def float_round_gt(value, ceiling, precision=0.000001):
    """`value > ceiling` without tripping over binary float noise."""
    return (value - ceiling) > precision
