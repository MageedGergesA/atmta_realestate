# -*- coding: utf-8 -*-
"""Advances — money paid before the work, recovered as the work arrives.

An advance is not cost. Paying a contractor 200,000 to mobilise buys nothing
yet; it buys a claim on future work. Until it is recovered it is an asset, and
the only question that matters at any moment is how much is still outstanding.

Phase 0 had no advance document at all — only a free-typed deduction line on
owner billing called "Mobilization Advance Recovery", with nothing anywhere
saying whether an advance had ever been paid, or how much was left of it.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

SIDES = [
    ('contractor', 'Paid to a Contractor'),
    ('owner', 'Received from the Owner'),
]

ADVANCE_TYPES = [
    ('mobilization', 'Mobilisation Advance'),
    ('materials', 'Materials Advance'),
    ('other', 'Other Advance'),
]


class ConstructionAdvance(models.Model):
    _name = 'realestate.construction.advance'
    _description = 'Contractor Advance'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, advance_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    side = fields.Selection(
        SIDES, required=True, default='contractor', tracking=True, index=True,
        help="An advance we pay is an asset. An advance we receive is a "
             "liability. Opposite money, identical recovery arithmetic.")
    contractor_id = fields.Many2one(
        'realestate.contractor', tracking=True, index=True)
    partner_id = fields.Many2one(
        'res.partner', string='Owner', tracking=True, index=True)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)

    advance_type = fields.Selection(
        ADVANCE_TYPES, required=True, default='mobilization', tracking=True)
    advance_date = fields.Date(required=True, tracking=True,
                               default=fields.Date.context_today)
    amount = fields.Monetary(required=True, tracking=True,
                             help="Tax-exclusive amount advanced.")

    recovery_pct = fields.Float(
        string='Recovery Rate (%)', default=20.0, tracking=True,
        help="Share of each certificate's certified amount recovered against "
             "this advance. A rate, not a rule — the certificate decides how "
             "much it actually recovers.")

    recovered_amount = fields.Monetary(
        compute='_compute_recovery', store=True,
        help="Recovered by certified certificates. Draft claims do not count.")
    outstanding_amount = fields.Monetary(
        compute='_compute_recovery', store=True)
    is_fully_recovered = fields.Boolean(compute='_compute_recovery', store=True)

    guarantee_reference = fields.Char(
        string='Advance Payment Guarantee',
        help="Bank guarantee covering the advance.")
    guarantee_expiry = fields.Date()

    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('paid', 'Paid'),
        ('recovered', 'Fully Recovered'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)

    vendor_bill_id = fields.Many2one(
        'account.move', string='Vendor Bill', readonly=True, copy=False)
    recovery_ids = fields.One2many(
        'realestate.construction.advance.recovery', 'advance_id',
        readonly=True)
    notes = fields.Html()

    @api.depends('amount', 'recovery_ids.amount',
                 'recovery_ids.certificate_id.state')
    def _compute_recovery(self):
        for rec in self:
            counted = rec.recovery_ids.filtered(
                lambda r: r.certificate_id.state in (
                    'certified', 'invoiced', 'paid'))
            rec.recovered_amount = sum(counted.mapped('amount'))
            rec.outstanding_amount = (rec.amount or 0.0) - rec.recovered_amount
            rec.is_fully_recovered = rec.currency_id.compare_amounts(
                rec.outstanding_amount, 0.0) <= 0 if rec.currency_id else False

    @api.constrains('side', 'contractor_id', 'partner_id')
    def _check_counterparty(self):
        for rec in self:
            if rec.side == 'contractor' and not rec.contractor_id:
                raise ValidationError(_("Name the contractor advanced to."))
            if rec.side == 'owner' and not rec.partner_id:
                raise ValidationError(_("Name the owner who advanced it."))

    @api.constrains('amount', 'recovery_pct')
    def _check_amounts(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_("An advance of nothing is not an advance."))
            if not 0.0 <= rec.recovery_pct <= 100.0:
                raise ValidationError(_(
                    "Recovery rate is a percentage of each certificate."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.advance') or 'ADV/NEW'
        return super().create(vals_list)

    def write(self, vals):
        """Once money has gone out, the amount is history."""
        protected = {'amount', 'contractor_id', 'partner_id', 'project_id',
                     'currency_id', 'side'}
        if protected & set(vals):
            posted = self.filtered(lambda a: a.vendor_bill_id)
            if posted:
                raise UserError(_(
                    "%(refs)s have already been billed. Raise a further "
                    "advance or a credit note — the amount advanced is not "
                    "edited after the fact.",
                    refs=', '.join(posted.mapped('name'))))
        return super().write(vals)

    def action_confirm(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("%s is not a draft advance.") % rec.name)
            rec.state = 'confirmed'
        return True

    def action_create_vendor_bill(self):
        """Contractor side: Dr Advances to Contractors / Cr Accounts Payable.
        Owner side: Dr Receivable / Cr Advances Received.

        Deliberately not an expense line: the project has not consumed
        anything, and a cost report that showed this as spend would be
        describing work that has not happened.
        """
        Accounts = self.env['realestate.construction.accounts']
        for rec in self:
            if rec.state not in ('confirmed',):
                raise UserError(_(
                    "Confirm %s before billing the advance.") % rec.name)
            if rec.vendor_bill_id:
                raise UserError(_(
                    "%s already has a bill.") % rec.name)
            if rec.side == 'contractor':
                account = Accounts.advance_account(rec.company_id)
                move_type = 'in_invoice'
                partner = rec.contractor_id.partner_id
            else:
                account = Accounts.owner_advance_account(rec.company_id)
                move_type = 'out_invoice'
                partner = rec.partner_id
            bill = self.env['account.move'].create({
                'move_type': move_type,
                'partner_id': partner.id,
                'company_id': rec.company_id.id,
                'currency_id': rec.currency_id.id,
                'invoice_date': rec.advance_date,
                'date': rec.advance_date,
                'ref': rec.name,
                'invoice_line_ids': [(0, 0, {
                    'name': _("%(type)s — %(ref)s",
                              type=dict(ADVANCE_TYPES).get(rec.advance_type),
                              ref=rec.name),
                    'quantity': 1.0,
                    'price_unit': rec.amount,
                    'account_id': account.id,
                    'tax_ids': [(5, 0, 0)],
                })],
            })
            self.env['realestate.account.tools'].post_moves(bill)
            rec.write({'vendor_bill_id': bill.id, 'state': 'paid'})
        return True

    def action_cancel(self):
        for rec in self:
            if rec.vendor_bill_id:
                raise UserError(_(
                    "%s has been billed. Reverse the bill in Accounting.")
                    % rec.name)
            rec.state = 'cancelled'
        return True

    @api.model
    def outstanding_for(self, project, contractor=None):
        """What is still owed back to the project by advances it has paid."""
        domain = [('project_id', '=', project.id),
                  ('state', 'in', ('confirmed', 'paid'))]
        if contractor:
            domain.append(('contractor_id', '=', contractor.id))
        groups = self._read_group(domain,
                                  aggregates=['outstanding_amount:sum'])
        return (groups[0][0] if groups else 0.0) or 0.0

    @api.constrains('project_id', 'company_id')
    def _check_company_matches_project(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Advance %(name)s is in %(company)s but its project is in "
                    "%(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))


class ConstructionAdvanceRecovery(models.Model):
    """One certificate recovering part of one advance."""
    _name = 'realestate.construction.advance.recovery'
    _description = 'Advance Recovery'
    _order = 'certificate_id, id'

    certificate_id = fields.Many2one(
        'realestate.construction.payment.certificate', required=True,
        ondelete='cascade', index=True)
    advance_id = fields.Many2one(
        'realestate.construction.advance', required=True,
        ondelete='restrict', index=True)
    amount = fields.Monetary(required=True)
    currency_id = fields.Many2one(
        related='certificate_id.currency_id', readonly=True)
    company_id = fields.Many2one(
        related='certificate_id.company_id', store=True, readonly=True)

    @api.constrains('amount')
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_(
                    "A recovery of nothing is not a recovery."))

    @api.constrains('advance_id', 'certificate_id')
    def _check_same_contractor(self):
        for rec in self:
            certificate, advance = rec.certificate_id, rec.advance_id
            if advance.contractor_id != certificate.contractor_id:
                raise ValidationError(_(
                    "%(advance)s was paid to %(paid)s. It cannot be recovered "
                    "from %(other)s's certificate.",
                    advance=advance.name,
                    paid=advance.contractor_id.display_name,
                    other=certificate.contractor_id.display_name))
            if advance.project_id != certificate.project_id:
                raise ValidationError(_(
                    "An advance is recovered on the project that paid it."))
