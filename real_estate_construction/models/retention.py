# -*- coding: utf-8 -*-
"""Retention — held, and then released, as a register that reconciles.

Phase 0 found retention represented by a percentage on a certificate and a
boolean on the contractor. A percentage cannot say how much is held today, and
a boolean cannot say that half of it was released at practical completion and
the rest is due at the end of the defects liability period.

So retention gets what money always needs: a register of movements, each one
tied to the accounting entry that made it true.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

SIDES = [
    ('contractor', 'Withheld from a Contractor'),
    ('owner', 'Withheld by the Owner from us'),
]

RELEASE_STAGES = [
    ('practical_completion', 'Practical Completion'),
    ('defects_liability', 'End of Defects Liability Period'),
    ('partial', 'Partial Release'),
    ('other', 'Other'),
]


class ConstructionRetention(models.Model):
    """One movement of retention: withheld by a certificate, or released.

    Balance is the sum of the movements — never a recomputation from
    certificates, because a released amount would have no way of showing up.
    """
    _name = 'realestate.construction.retention'
    _description = 'Retention Movement'
    _order = 'date desc, id desc'
    _check_company_auto = True

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    side = fields.Selection(
        SIDES, required=True, default='contractor', index=True,
        help="Contractor retention is a liability we owe back. Owner "
             "retention is an asset somebody owes us. They are the same "
             "arithmetic and opposite money, so they are kept apart.")
    contractor_id = fields.Many2one(
        'realestate.contractor', index=True,
        help="Set on the contractor side.")
    partner_id = fields.Many2one(
        'res.partner', index=True,
        help="Set on the owner side — whoever is holding our money.")
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True, index=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)

    movement_type = fields.Selection([
        ('hold', 'Withheld'),
        ('release', 'Released'),
    ], required=True, index=True)
    date = fields.Date(required=True, index=True,
                       default=fields.Date.context_today)
    amount = fields.Monetary(required=True,
                             help="Always positive. The type carries the sign.")
    signed_amount = fields.Monetary(
        compute='_compute_signed', store=True,
        help="Withheld is positive, released is negative. The balance is "
             "their sum.")

    certificate_id = fields.Many2one(
        'realestate.construction.payment.certificate', ondelete='restrict',
        readonly=True, index=True)
    release_id = fields.Many2one(
        'realestate.construction.retention.release', ondelete='restrict',
        readonly=True, index=True)
    move_id = fields.Many2one(
        'account.move', string='Journal Entry', readonly=True,
        help="The entry that made this movement real. A movement without one "
             "is a note, not a balance.")
    note = fields.Char()

    @api.depends('movement_type', 'amount')
    def _compute_signed(self):
        for rec in self:
            rec.signed_amount = (rec.amount or 0.0) * (
                1.0 if rec.movement_type == 'hold' else -1.0)

    @api.constrains('side', 'contractor_id', 'partner_id')
    def _check_counterparty(self):
        for rec in self:
            if rec.side == 'contractor' and not rec.contractor_id:
                raise ValidationError(_(
                    "Contractor retention is held from somebody. Name them."))
            if rec.side == 'owner' and not rec.partner_id:
                raise ValidationError(_(
                    "Owner retention is held by somebody. Name them."))

    @api.constrains('amount')
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_(
                    "A retention movement of nothing is not a movement. Use "
                    "the type to say whether it is held or released."))

    def write(self, vals):
        """The register is history. It is added to, not edited."""
        protected = {'movement_type', 'amount', 'date', 'certificate_id',
                     'release_id', 'move_id', 'project_id', 'contractor_id',
                     'partner_id', 'side'}
        if protected & set(vals) and not self.env.context.get(
                're_retention_posting'):
            raise UserError(_(
                "Retention movements are a register. Post a release rather "
                "than editing what was already withheld."))
        return super().write(vals)

    # ------------------------------------------------------------------
    @api.model
    def balance(self, project=None, contractor=None, package=None,
                company=None, side='contractor', partner=None):
        """Retention held right now, for whatever slice is asked for."""
        domain = [('side', '=', side)]
        if project:
            domain.append(('project_id', '=', project.id))
        if contractor:
            domain.append(('contractor_id', '=', contractor.id))
        if partner:
            domain.append(('partner_id', '=', partner.id))
        if package:
            domain.append(('package_id', '=', package.id))
        if company:
            domain.append(('company_id', '=', company.id))
        groups = self._read_group(domain, aggregates=['signed_amount:sum'])
        return (groups[0][0] if groups else 0.0) or 0.0

    @api.model
    def held_by_contractor(self, project):
        """{contractor_id: held} — what the project still owes back."""
        groups = self._read_group(
            [('project_id', '=', project.id), ('side', '=', 'contractor')],
            groupby=['contractor_id'], aggregates=['signed_amount:sum'])
        return {contractor.id: total or 0.0
                for contractor, total in groups if contractor}


class ConstructionRetentionRelease(models.Model):
    """Releasing retention is a document, because it is a decision.

    Somebody decides the works reached practical completion, or that the
    defects liability period expired without a claim. That decision has a
    date, an author and an amount, and the payment follows from it.
    """
    _name = 'realestate.construction.retention.release'
    _description = 'Retention Release'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, release_date desc, id desc'
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
    side = fields.Selection(SIDES, required=True, default='contractor',
                            tracking=True, index=True)
    contractor_id = fields.Many2one(
        'realestate.contractor', tracking=True, index=True)
    partner_id = fields.Many2one('res.partner', tracking=True, index=True)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)

    stage = fields.Selection(RELEASE_STAGES, required=True, tracking=True,
                             default='practical_completion')
    release_date = fields.Date(required=True, tracking=True,
                               default=fields.Date.context_today)
    amount = fields.Monetary(required=True, tracking=True)
    held_amount = fields.Monetary(
        string='Currently Held', compute='_compute_held',
        help="What the register says is still held for this contractor.")
    reason = fields.Text(
        help="Why it is due now. A release with no reason is a payment "
             "nobody can explain later.")

    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)

    vendor_bill_id = fields.Many2one(
        'account.move', string='Vendor Bill', readonly=True, copy=False)
    movement_id = fields.Many2one(
        'realestate.construction.retention', readonly=True, copy=False)
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    @api.depends('project_id', 'contractor_id', 'partner_id', 'package_id',
                 'side')
    def _compute_held(self):
        Retention = self.env['realestate.construction.retention']
        for rec in self:
            if rec.project_id and (rec.contractor_id or rec.partner_id):
                rec.held_amount = Retention.balance(
                    project=rec.project_id, side=rec.side,
                    contractor=rec.contractor_id or None,
                    partner=rec.partner_id or None,
                    package=rec.package_id or None)
            else:
                rec.held_amount = 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.retention.release') or 'RET/NEW'
        return super().create(vals_list)

    def action_confirm(self):
        """Release what is held — never more, and never twice."""
        Retention = self.env['realestate.construction.retention']
        Accounts = self.env['realestate.construction.accounts']
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("%s is not a draft release.") % rec.name)
            if rec.amount <= 0:
                raise UserError(_("Release an amount."))
            # Serialise against other releases for the same contractor so two
            # people cannot each release the last of the retention.
            counterparty = rec.contractor_id or rec.partner_id
            if not counterparty:
                raise UserError(_(
                    "Say who is holding the retention before releasing it."))
            self.env.cr.execute(
                'SELECT pg_advisory_xact_lock(%s, %s)',
                (rec.project_id.id, counterparty.id))
            held = Retention.balance(
                project=rec.project_id, side=rec.side,
                contractor=rec.contractor_id or None,
                partner=rec.partner_id or None,
                package=rec.package_id or None)
            if rec.currency_id.compare_amounts(rec.amount, held) > 0:
                raise UserError(_(
                    "%(release)s would release %(amount)s but only "
                    "%(held)s is held. Retention that was never withheld "
                    "cannot be paid back.",
                    release=rec.name,
                    amount=rec.currency_id.format(rec.amount) if hasattr(
                        rec.currency_id, 'format') else rec.amount,
                    held=rec.currency_id.format(held) if hasattr(
                        rec.currency_id, 'format') else held))

            if rec.side == 'contractor':
                account = Accounts.retention_account(rec.company_id)
                move_type = 'in_invoice'
                partner = rec.contractor_id.partner_id
            else:
                account = Accounts.owner_retention_account(rec.company_id)
                move_type = 'out_invoice'
                partner = rec.partner_id
            bill = self.env['account.move'].create({
                'move_type': move_type,
                'partner_id': partner.id,
                'company_id': rec.company_id.id,
                'currency_id': rec.currency_id.id,
                'invoice_date': rec.release_date,
                'date': rec.release_date,
                'ref': rec.name,
                'invoice_line_ids': [(0, 0, {
                    'name': _("Retention release — %(stage)s (%(ref)s)",
                              stage=dict(RELEASE_STAGES).get(rec.stage),
                              ref=rec.name),
                    'quantity': 1.0,
                    'price_unit': rec.amount,
                    'account_id': account.id,
                    'tax_ids': [(5, 0, 0)],
                })],
            })
            self.env['realestate.account.tools'].post_moves(bill)

            movement = Retention.create({
                'project_id': rec.project_id.id,
                'company_id': rec.company_id.id,
                'contractor_id': rec.contractor_id.id or False,
                'package_id': rec.package_id.id or False,
                'currency_id': rec.currency_id.id,
                'movement_type': 'release',
                'side': rec.side,
                'partner_id': rec.partner_id.id or False,
                'date': rec.release_date,
                'amount': rec.amount,
                'release_id': rec.id,
                'move_id': bill.id,
                'note': rec.reason or dict(RELEASE_STAGES).get(rec.stage),
            })
            rec.write({
                'state': 'confirmed',
                'vendor_bill_id': bill.id,
                'movement_id': movement.id,
                'approved_by_id': self.env.user.id,
            })
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == 'confirmed':
                raise UserError(_(
                    "%s already released retention and posted a bill. Reverse "
                    "the bill in Accounting; the register keeps both entries.")
                    % rec.name)
            rec.state = 'cancelled'
        return True

    @api.constrains('project_id', 'company_id')
    def _check_company_matches_project(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Release %(name)s is in %(company)s but its project is "
                    "in %(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))
