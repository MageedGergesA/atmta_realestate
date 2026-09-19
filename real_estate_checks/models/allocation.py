# -*- coding: utf-8 -*-
"""M3 / M18 — what a cheque is meant to pay.

0.1 had two single `Many2one` fields on the cheque and nothing else. That
supports exactly one shape — one cheque, one instalment — and quietly
mis-supports every other: two cheques naming the same instalment were never
summed or validated, and one cheque covering three instalments was impossible
to express at all.

This model is the one authoritative answer to "what does this cheque pay?".
The legacy fields survive as a *mirror* of the simple case (see
`check._sync_simple_allocation` and `_mirror_to_check` below) so that nothing
downstream has to change, but there is only one engine.

### What an allocation is NOT

It is not a payment and it is not a reconciliation. Allocating 600,000 of a
cheque to an instalment says "when this cheque clears, that is the obligation
it settles". It moves no money and touches no ledger. The obligation stays
outstanding until Odoo's reconciliation says otherwise — which is Rule 2 and
Rule 3, expressed as a data model.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .check_states import ALLOCATION_STATE


class CheckAllocation(models.Model):
    _name = 'realestate.check.allocation'
    _description = 'Cheque Allocation to an Obligation'
    _order = 'check_id, id'
    _check_company_auto = True

    check_id = fields.Many2one(
        'realestate.check', string='Cheque', required=True, index=True,
        ondelete='cascade')
    company_id = fields.Many2one(
        related='check_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='check_id.currency_id', store=True, readonly=True)
    partner_id = fields.Many2one(
        related='check_id.partner_id', store=True, readonly=True,
        string='Drawer')

    # ------------------------------------------------------------------
    # The obligation. Exactly one of these two is set.
    # ------------------------------------------------------------------
    sale_installment_id = fields.Many2one(
        'realestate.sale.installment', string='Sale Instalment', index=True,
        ondelete='cascade')
    rental_payment_id = fields.Many2one(
        'realestate.contract.payment', string='Rental Payment', index=True,
        ondelete='cascade',
        help="M16 — Rental obligations are first-class here. The allocation "
             "engine is not a Developer-only feature.")

    obligation_kind = fields.Selection(
        [('sale', 'Sale Instalment'), ('rental', 'Rental Payment')],
        compute='_compute_obligation', store=True)
    obligation_ref = fields.Char(
        compute='_compute_obligation', store=True, string='Obligation')
    obligation_due_date = fields.Date(
        compute='_compute_obligation', store=True, string='Obligation Due')
    obligation_amount = fields.Monetary(
        compute='_compute_obligation', store=True, string='Obligation Amount')

    sale_contract_id = fields.Many2one(
        'realestate.sale.contract', compute='_compute_obligation', store=True,
        index=True, string='Sale Contract')
    rental_contract_id = fields.Many2one(
        'realestate.contract', compute='_compute_obligation', store=True,
        index=True, string='Rental Contract')

    # The invoice, when the obligation has reached the ledger. Read from the
    # obligation, never authored here — Accounting owns this link.
    move_id = fields.Many2one(
        'account.move', string='Invoice', compute='_compute_obligation',
        store=True, index=True)

    allocated_amount = fields.Monetary(
        string='Allocated', required=True,
        help="How much of the cheque's face value is committed to this "
             "obligation. Not a payment: no money moves because of this "
             "number.")

    state = fields.Selection(
        ALLOCATION_STATE, default='active', required=True, index=True,
        tracking=False,
        help="Cancelled allocations are kept, never deleted — knowing that a "
             "cheque was once pointed at an instalment is part of the audit "
             "trail of a restructuring.")
    cancel_reason = fields.Char(readonly=True)
    note = fields.Char()

    _sql_constraints = [
        ('allocation_amount_positive', 'CHECK (allocated_amount > 0)',
         'An allocation must be for a positive amount.'),
    ]

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('sale_installment_id', 'rental_payment_id',
                 'sale_installment_id.move_id', 'rental_payment_id.move_id',
                 'sale_installment_id.current_amount',
                 'rental_payment_id.amount_total')
    def _compute_obligation(self):
        # `sudo` on the read. This compute fires whenever an allocation is
        # touched — including when Treasury presents a deposit — and a Treasury
        # Officer holds no Developer or Rental role. Everything it reads is a
        # description of the obligation the cheque already points at.
        for rec in self.sudo():
            inst, rent = rec.sale_installment_id, rec.rental_payment_id
            if inst:
                rec.obligation_kind = 'sale'
                rec.obligation_ref = inst.display_name
                rec.obligation_due_date = inst.date_due
                rec.obligation_amount = inst.current_amount
                rec.sale_contract_id = inst.sale_contract_id
                rec.rental_contract_id = False
                rec.move_id = inst.move_id
            elif rent:
                rec.obligation_kind = 'rental'
                rec.obligation_ref = rent.display_name
                rec.obligation_due_date = rent.date_due
                rec.obligation_amount = rent.amount_total
                rec.sale_contract_id = False
                rec.rental_contract_id = rent.contract_id
                rec.move_id = rent.move_id
            else:
                rec.obligation_kind = False
                rec.obligation_ref = False
                rec.obligation_due_date = False
                rec.obligation_amount = 0.0
                rec.sale_contract_id = False
                rec.rental_contract_id = False
                rec.move_id = False

    # ==================================================================
    # Constraints
    # ==================================================================
    # `check_id` is in the trigger list on purpose. Odoo only validates the
    # constrained fields that appear in `vals`, so a create() that simply omits
    # BOTH obligation fields -- the exact mistake this guards against -- would
    # never have fired it. `check_id` is required, so it is always present.
    @api.constrains('sale_installment_id', 'rental_payment_id', 'check_id')
    def _check_exactly_one_obligation(self):
        for rec in self:
            if bool(rec.sale_installment_id) == bool(rec.rental_payment_id):
                raise ValidationError(_(
                    "An allocation must name exactly one obligation — either a "
                    "sale instalment or a rental payment, not both and not "
                    "neither."))

    @api.constrains('check_id', 'allocated_amount', 'state')
    def _check_cheque_not_over_allocated(self):
        """The cheque-side invariant: SUM(active) <= face value.

        Enforced here as well as on the cheque, because an allocation can be
        created without the cheque itself being written, and Odoo only fires a
        model's constraints when that model is touched.
        """
        for check in self.mapped('check_id'):
            currency = check.currency_id or check.company_id.currency_id
            live = check.allocation_ids.filtered(lambda a: a.state == 'active')
            allocated = sum(live.mapped('allocated_amount'))
            if currency.compare_amounts(allocated, check.amount) > 0:
                raise ValidationError(_(
                    "Cheque %(check)s is for %(amount)s but %(allocated)s "
                    "would be allocated. A cheque cannot pay more than it is "
                    "worth.",
                    check=check.name,
                    amount=currency.format(check.amount),
                    allocated=currency.format(allocated)))

    @api.constrains('sale_installment_id', 'rental_payment_id',
                    'allocated_amount', 'state')
    def _check_obligation_not_over_covered(self):
        """The obligation-side invariant.

        Two cheques for one instalment is Case B and must work. Two cheques
        totalling *more* than the instalment is a data-entry error, and saying
        so at the point of entry is far kinder than discovering it at
        reconciliation.
        """
        for rec in self:
            if rec.state != 'active':
                continue
            obligation_amount = rec.obligation_amount
            if not obligation_amount:
                continue
            currency = rec.currency_id or rec.company_id.currency_id
            covered = sum(rec._sibling_allocations().mapped('allocated_amount'))
            if currency.compare_amounts(covered, obligation_amount) > 0:
                raise ValidationError(_(
                    "Obligation %(obligation)s is for %(amount)s but cheques "
                    "totalling %(covered)s have been allocated to it.\n\n"
                    "Two cheques covering one instalment is fine; covering it "
                    "twice over is not.",
                    obligation=rec.obligation_ref,
                    amount=currency.format(obligation_amount),
                    covered=currency.format(covered)))

    @api.constrains('check_id', 'sale_installment_id', 'rental_payment_id')
    def _check_company_and_partner(self):
        """M29 — an allocation must not straddle companies."""
        for rec in self:
            obligation = rec.sale_installment_id or rec.rental_payment_id
            if not obligation:
                continue
            company = getattr(obligation, 'company_id', False)
            if company and company != rec.check_id.company_id:
                raise ValidationError(_(
                    "Cheque %(check)s belongs to %(own)s but the obligation it "
                    "would pay belongs to %(other)s.",
                    check=rec.check_id.name,
                    own=rec.check_id.company_id.display_name,
                    other=company.display_name))

    def _sibling_allocations(self):
        """Every active allocation against the same obligation, including self."""
        self.ensure_one()
        Allocation = self.env['realestate.check.allocation']
        if self.sale_installment_id:
            domain = [('sale_installment_id', '=', self.sale_installment_id.id)]
        else:
            domain = [('rental_payment_id', '=', self.rental_payment_id.id)]
        return Allocation.search(domain + [('state', '=', 'active')])

    # ==================================================================
    # Create / write — keep the legacy mirror honest
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        allocations = super().create(vals_list)
        allocations._mirror_to_check()
        return allocations

    def write(self, vals):
        res = super().write(vals)
        if {'sale_installment_id', 'rental_payment_id', 'state'} & set(vals):
            self._mirror_to_check()
        return res

    def unlink(self):
        checks = self.mapped('check_id')
        res = super().unlink()
        checks.exists()._compute_allocation_totals()
        checks.exists().browse()  # no-op, keeps the intent explicit
        return res

    def _mirror_to_check(self):
        """Keep `check.sale_installment_id` / `rental_payment_id` truthful.

        The rule: those fields describe the cheque when — and only when — the
        cheque has exactly one active allocation. A cheque split across three
        instalments has no single instalment, and pretending it points at the
        first one would be a lie that Developer's guards would then act on. So
        the field goes blank and the allocations are the answer.

        Guarded against recursion by the context flag the cheque sets when it
        is the one seeding the allocation.
        """
        if self.env.context.get('skip_allocation_mirror'):
            return
        for check in self.mapped('check_id'):
            live = check.allocation_ids.filtered(lambda a: a.state == 'active')
            if len(live) == 1:
                target = live
                vals = {
                    'sale_installment_id': target.sale_installment_id.id or False,
                    'rental_payment_id': target.rental_payment_id.id or False,
                }
            else:
                vals = {'sale_installment_id': False, 'rental_payment_id': False}
            current = {
                'sale_installment_id': check.sale_installment_id.id or False,
                'rental_payment_id': check.rental_payment_id.id or False,
            }
            if vals != current:
                check.with_context(skip_allocation_mirror=True).write(vals)

    # ==================================================================
    # Actions
    # ==================================================================
    def action_cancel(self, reason=None):
        """Never delete. A cancelled allocation is evidence."""
        for rec in self:
            if rec.state == 'cancelled':
                continue
            rec.write({'state': 'cancelled',
                       'cancel_reason': reason or rec.cancel_reason})
        self._mirror_to_check()
        return True

    def action_activate(self):
        for rec in self:
            if rec.check_id.state in ('cancelled', 'returned', 'replaced'):
                raise UserError(_(
                    "Cheque %(name)s is %(state)s and cannot be allocated to "
                    "anything.", name=rec.check_id.name,
                    state=rec.check_id.state))
            rec.state = 'active'
        self._mirror_to_check()
        return True


class SaleInstallmentCoverage(models.Model):
    """M18 / M19 — how much of an obligation is covered by paper.

    Deliberately named `secured_by_checks_amount`, never anything containing
    the word "paid" or "collected". A PDC in the safe secures an obligation; it
    does not settle it. `paid_amount` on this model continues to mean what
    Developer says it means: money that Odoo's reconciliation confirms.
    """
    _inherit = 'realestate.sale.installment'

    # The two links are restricted to Checks users. Instalments have no form
    # view of their own, so the generated one names every field; a Developer
    # salesperson opening a contract (or one of its instalment rows) was
    # refused on 'Cheque Allocation to an Obligation'. The amounts below stay
    # visible to them — they are computed with `sudo` for exactly that reason.
    check_allocation_ids = fields.One2many(
        'realestate.check.allocation', 'sale_installment_id',
        string='Cheque Allocations',
        groups='real_estate_checks.group_checks_user')
    check_ids = fields.One2many(
        'realestate.check', 'sale_installment_id', string='Cheques',
        groups='real_estate_checks.group_checks_user',
        help="0.1's direct link, kept. For the full picture including split "
             "cheques use the allocations.")
    secured_by_checks_amount = fields.Monetary(
        compute='_compute_check_coverage', store=True,
        string='Secured by Cheques',
        help="Face value of live cheques allocated to this obligation. This is "
             "PAPER RECEIVED, not cash collected — the obligation stays "
             "outstanding until Odoo's reconciliation says otherwise.")
    unsecured_amount = fields.Monetary(
        compute='_compute_check_coverage', store=True,
        help="What is still owed and has no cheque behind it.")
    check_count = fields.Integer(compute='_compute_check_coverage', store=True)

    @api.depends('check_allocation_ids.allocated_amount',
                 'check_allocation_ids.state',
                 'check_allocation_ids.check_id.state',
                 'current_amount', 'paid_amount')
    def _compute_check_coverage(self):
        # Cheques that have been cancelled, returned or superseded secure
        # nothing. A cleared one has already turned into cash, so counting it
        # as "secured" as well would double-count the same money.
        dead = ('cancelled', 'returned', 'replaced', 'cleared')
        # `sudo` on the READ only. This compute fires for Developer users who
        # may hold no Checks group at all — a salesperson opening a contract
        # must not get an AccessError because a treasury model exists. Nothing
        # is written with elevated rights, and the values exposed (an amount)
        # carry no bank detail. Company isolation is unaffected: the
        # obligation is already company-scoped and its allocations cannot
        # belong to another company (see `_check_company_and_partner`).
        for rec in self.sudo():
            live = rec.check_allocation_ids.filtered(
                lambda a: a.state == 'active' and a.check_id.state not in dead)
            secured = sum(live.mapped('allocated_amount'))
            # A new instalment has no contract yet, so no currency or company
            # of its own; the form is still being filled in the user's company.
            currency = (rec.currency_id or rec.company_id.currency_id
                        or self.env.company.currency_id)
            rec.check_count = len(live)
            rec.secured_by_checks_amount = secured
            outstanding = (rec.current_amount or 0.0) - (rec.paid_amount or 0.0)
            rec.unsecured_amount = currency.round(
                max(outstanding - secured, 0.0))


class RentalPaymentCoverage(models.Model):
    """M16 — the same treatment for Rental. Parity, not an afterthought."""
    _inherit = 'realestate.contract.payment'

    # Restricted to Checks users, as on the sale instalment: a Rental user
    # without a Checks group reading every field of a rental payment (the
    # generated form) was refused on 'Cheque Allocation to an Obligation'.
    # The amounts below stay visible — they are computed with `sudo`.
    check_allocation_ids = fields.One2many(
        'realestate.check.allocation', 'rental_payment_id',
        string='Cheque Allocations',
        groups='real_estate_checks.group_checks_user')
    check_ids = fields.One2many(
        'realestate.check', 'rental_payment_id', string='Cheques',
        groups='real_estate_checks.group_checks_user')
    secured_by_checks_amount = fields.Float(
        compute='_compute_check_coverage', store=True,
        string='Secured by Cheques',
        help="Face value of live cheques allocated to this rental obligation. "
             "Paper received, not cash collected.")
    check_count = fields.Integer(compute='_compute_check_coverage', store=True)

    @api.depends('check_allocation_ids.allocated_amount',
                 'check_allocation_ids.state',
                 'check_allocation_ids.check_id.state')
    def _compute_check_coverage(self):
        dead = ('cancelled', 'returned', 'replaced', 'cleared')
        # `sudo` on the read only — see the note on the sale instalment above.
        for rec in self.sudo():
            live = rec.check_allocation_ids.filtered(
                lambda a: a.state == 'active' and a.check_id.state not in dead)
            rec.check_count = len(live)
            rec.secured_by_checks_amount = sum(live.mapped('allocated_amount'))
