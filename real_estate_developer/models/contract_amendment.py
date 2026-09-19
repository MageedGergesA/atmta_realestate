# -*- coding: utf-8 -*-
"""Phases 27 & 33 — amendments and installment adjustments.

A signed contract is a commercial record. Changing it is an *event* with a
reason, an approver and a date — not an edit. Two models carry that:

* `realestate.sale.contract.amendment` is the spine: one record per change to a
  signed deal, of a typed kind, approved and then applied exactly once;
* `realestate.sale.installment.adjustment` records what happened to a single
  obligation, with its before and after on the record.

Nothing here rewrites history. A paid instalment is never edited; a posted
invoice is never deleted. Amounts move by *adding* an adjustment, which is why
`installment.original_amount` and `installment.adjustment_amount` are separate
columns.
"""

from odoo import _, _lt, api, fields, models
from odoo.exceptions import UserError, ValidationError

AMENDMENT_TYPE = [
    ('unit_swap', 'Unit Swap'),
    ('price_change', 'Price Change'),
    ('buyer_change', 'Buyer Change / Transfer'),
    ('payment_plan_change', 'Payment Plan Change'),
    ('schedule_change', 'Schedule Restructuring'),
    ('early_settlement', 'Early Settlement'),
    ('property_addition', 'Property Added'),
    ('property_removal', 'Property Removed'),
    ('fee_change', 'Fee Change'),
    ('term_change', 'Term Change'),
    ('cancellation', 'Cancellation / Termination'),
    ('other', 'Other'),
]

AMENDMENT_STATE = [
    ('draft', 'Draft'),
    ('pending_approval', 'Pending Approval'),
    ('approved', 'Approved'),
    ('signed', 'Signed'),
    ('applied', 'Applied'),
    ('cancelled', 'Cancelled'),
]

ADJUSTMENT_TYPE = [
    ('reschedule', 'Reschedule'),
    ('discount', 'Discount'),
    ('penalty', 'Penalty'),
    ('waiver', 'Waiver'),
    ('correction', 'Correction'),
    ('settlement', 'Early Settlement'),
    ('transfer', 'Transfer'),
    ('cancellation', 'Cancellation'),
    ('other', 'Other'),
]

#: The approval action each amendment type is measured against, so one
#: authority matrix governs every post-signature change (Phase 34).
AMENDMENT_APPROVAL_ACTION = {
    'unit_swap': 'unit_swap',
    'price_change': 'price_change',
    'buyer_change': 'contract_transfer',
    'payment_plan_change': 'payment_restructure',
    'schedule_change': 'payment_restructure',
    'early_settlement': 'early_settlement',
    'cancellation': 'contract_cancellation',
}

#: Types only a contract wizard can carry out, with the button that opens it.
#: The wizard collects what the change needs (a discount, a penalty, whether the
#: unit returns to the market) and previews it; an amendment holds none of that.
WIZARD_ONLY_TYPES = {
    'early_settlement': _lt('Early Settlement'),
    'cancellation': _lt('Cancel Contract'),
}

#: Types whose effect on what is owed is expressed only through the instalment
#: adjustments attached to the amendment.
ADJUSTMENT_ONLY_TYPES = ('fee_change', 'property_addition', 'property_removal')


class SaleContractAmendment(models.Model):
    _name = 'realestate.sale.contract.amendment'
    _description = 'Sale Contract Amendment'
    _inherit = ['mail.thread', 'mail.activity.mixin',
                'realestate.commercial.approval.mixin']
    _order = 'effective_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        required=True, copy=False, readonly=True,
        default=lambda self: _('New'), index='trigram')
    contract_id = fields.Many2one(
        'realestate.sale.contract', required=True, ondelete='cascade',
        index=True, tracking=True, check_company=True)
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True, readonly=True)
    project_id = fields.Many2one(
        related='contract_id.project_id', store=True, readonly=True, index=True)
    currency_id = fields.Many2one(
        related='contract_id.currency_id', store=True, readonly=True)
    partner_id = fields.Many2one(
        related='contract_id.partner_id', store=True, readonly=True)

    amendment_type = fields.Selection(
        AMENDMENT_TYPE, required=True, tracking=True, index=True)
    state = fields.Selection(
        AMENDMENT_STATE, default='draft', required=True, tracking=True,
        index=True, copy=False)

    effective_date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True)
    reason = fields.Text(required=True, tracking=True)
    notes = fields.Html()

    # ---- What the change costs, as previewed and as agreed ----
    financial_effect = fields.Monetary(
        string='Financial Effect', tracking=True,
        help="Net change to what the buyer owes. Positive means they owe more.")
    fee_amount = fields.Monetary(string='Amendment Fee', tracking=True)

    # ---- Type-specific payload ----
    old_property_id = fields.Many2one(
        'realestate.property', string='From Unit', readonly=True)
    new_property_id = fields.Many2one(
        'realestate.property', string='To Unit', check_company=True)
    old_price = fields.Monetary(readonly=True)
    new_price = fields.Monetary()
    old_partner_id = fields.Many2one(
        'res.partner', string='From Buyer', readonly=True)
    new_partner_id = fields.Many2one('res.partner', string='To Buyer')
    old_payment_plan_id = fields.Many2one(
        'realestate.payment.plan', string='From Plan', readonly=True)
    new_payment_plan_id = fields.Many2one(
        'realestate.payment.plan', string='To Plan', check_company=True)

    adjustment_ids = fields.One2many(
        'realestate.sale.installment.adjustment', 'amendment_id',
        string='Instalment Adjustments')
    adjustment_count = fields.Integer(compute='_compute_adjustment_count')

    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    signed_on = fields.Date(tracking=True, copy=False)
    applied_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    applied_on = fields.Datetime(readonly=True, copy=False)

    def _compute_adjustment_count(self):
        for rec in self:
            rec.adjustment_count = len(rec.adjustment_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.sale.contract.amendment') or _('New')
        return super().create(vals_list)

    @api.constrains('contract_id', 'state')
    def _check_contract_is_amendable(self):
        """An amendment may only be *raised* against a live contract.

        Two exemptions, both necessary rather than convenient:

        * a **cancellation** amendment exists precisely to move the contract to
          `cancelled`, so judging it by the state it produces is circular;
        * an **applied** amendment describes something that has already
          happened. Re-validating history against the present is how a
          correctly-executed cancellation ends up unable to record that it
          executed.
        """
        for rec in self:
            if rec.state in ('draft', 'applied', 'cancelled'):
                continue
            if rec.amendment_type == 'cancellation':
                continue
            if rec.contract_id.state in ('draft', 'cancelled', 'terminated'):
                raise ValidationError(_(
                    "Contract %s is %s. There is nothing live to amend."
                ) % (rec.contract_id.name, rec.contract_id.state))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft amendments can be submitted."))
            rec.state = 'pending_approval'

    def action_approve(self):
        for rec in self:
            if rec.state != 'pending_approval':
                raise UserError(_(
                    "Amendment %s is not awaiting approval.") % rec.name)
            action = AMENDMENT_APPROVAL_ACTION.get(rec.amendment_type)
            if action:
                # Measured on the money it moves, so one authority matrix
                # governs discounts, swaps, settlements and cancellations alike.
                rec._require_approval(
                    action, abs(rec.financial_effect),
                    amount=abs(rec.financial_effect), reason=rec.reason)
            rec.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })

    def _approval_needed(self):
        """The authority `action_approve` checks, measured the same way."""
        self.ensure_one()
        action = AMENDMENT_APPROVAL_ACTION.get(self.amendment_type)
        if self.state != 'pending_approval' or not action:
            return None
        return (action, abs(self.financial_effect),
                abs(self.financial_effect), self.reason)

    def action_sign(self):
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_(
                    "Amendment %s must be approved before it is signed."
                ) % rec.name)
            rec.write({
                'state': 'signed',
                'signed_on': rec.signed_on or fields.Date.context_today(rec),
            })

    def action_apply(self):
        """Apply the change to the contract. Idempotent by construction.

        Phase 33 requires this: applying twice must not double the effect. The
        state guard is the mechanism — an applied amendment cannot be applied
        again, and every mutation happens inside this one call.
        """
        for rec in self:
            if rec.state == 'applied':
                raise UserError(_(
                    "Amendment %s has already been applied (%s)."
                ) % (rec.name, rec.applied_on))
            if rec.state not in ('approved', 'signed'):
                raise UserError(_(
                    "Amendment %s must be approved before it can be applied."
                ) % rec.name)

            rec._apply_payload()
            rec.write({
                'state': 'applied',
                'applied_by_id': self.env.user.id,
                'applied_on': fields.Datetime.now(),
            })
            rec.contract_id.message_post(body=_(
                "Amendment %s applied: %s."
            ) % (rec.name, dict(AMENDMENT_TYPE)[rec.amendment_type]))
        return True

    def _apply_payload(self):
        """Carry out the change on the contract, or refuse.

        Applying used to succeed for every type and act on only four, and on
        those only when their payload was filled in. A cancellation, a
        schedule change, an early settlement, or a swap with no target unit
        was recorded as applied (irreversibly) while the contract and its
        schedule stayed as they were.
        """
        self.ensure_one()
        contract = self.contract_id
        kind = self.amendment_type
        label = dict(AMENDMENT_TYPE)[kind]

        if kind in WIZARD_ONLY_TYPES:
            raise UserError(_(
                "Amendment %(name)s is a %(type)s. Carry it out from contract "
                "%(contract)s with %(button)s, which asks for what the change "
                "needs and previews its effect. Applying it here would record "
                "it as applied without changing the contract."
            ) % {'name': self.name, 'type': label, 'contract': contract.name,
                 'button': WIZARD_ONLY_TYPES[kind]})

        if kind == 'unit_swap':
            contract._apply_unit_swap(self)
        elif kind == 'buyer_change':
            contract._apply_buyer_change(self)
        elif kind == 'price_change':
            contract._apply_price_change(self)
        elif kind in ('payment_plan_change', 'schedule_change'):
            # Setting the plan alone left the schedule cut under the old one.
            if not self.new_payment_plan_id:
                raise UserError(_(
                    "Amendment %s changes the payment plan but names no new "
                    "plan.") % self.name)
            if not self.old_payment_plan_id:
                self.old_payment_plan_id = contract.payment_plan_id
            contract._apply_restructure(
                self, self.new_payment_plan_id, self.effective_date)
        elif kind in ADJUSTMENT_ONLY_TYPES and not self.adjustment_ids:
            raise UserError(_(
                "Amendment %(name)s is a %(type)s, which changes what the buyer "
                "owes through instalment adjustments, and it has none. Applying "
                "it would change nothing."
            ) % {'name': self.name, 'type': label})

        # Adjustments attached to the amendment are applied whatever the type,
        # because a swap, a restructuring and a settlement all express their
        # effect on the schedule the same way.
        self.adjustment_ids.action_apply()
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == 'applied':
                raise UserError(_(
                    "Amendment %s has been applied and cannot be cancelled. "
                    "Raise a further amendment to reverse it."
                ) % rec.name)
            rec.state = 'cancelled'

    def unlink(self):
        applied = self.filtered(lambda a: a.state == 'applied')
        if applied:
            raise UserError(_(
                "Applied amendments are part of the contract's history and "
                "cannot be deleted."))
        return super().unlink()


class SaleInstallmentAdjustment(models.Model):
    """Phase 27 — what happened to one obligation, with before and after.

    Never edits `original_amount`. The effect lands in
    `installment.adjustment_amount`, so the schedule as signed stays readable
    next to the schedule as it now stands.
    """
    _name = 'realestate.sale.installment.adjustment'
    _description = 'Sale Instalment Adjustment'
    _order = 'create_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(compute='_compute_name', store=True)
    installment_id = fields.Many2one(
        'realestate.sale.installment', required=True, ondelete='cascade',
        index=True)
    amendment_id = fields.Many2one(
        'realestate.sale.contract.amendment', ondelete='cascade', index=True)
    contract_id = fields.Many2one(
        related='installment_id.sale_contract_id', store=True, index=True,
        readonly=True)
    company_id = fields.Many2one(
        related='installment_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='installment_id.currency_id', store=True, readonly=True)

    adjustment_type = fields.Selection(
        ADJUSTMENT_TYPE, required=True, default='correction')

    amount_before = fields.Monetary(readonly=True)
    amount_after = fields.Monetary(required=True)
    amount_delta = fields.Monetary(compute='_compute_delta', store=True)
    date_before = fields.Date(readonly=True)
    date_after = fields.Date()

    reason = fields.Text(required=True)
    requested_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True)
    applied_on = fields.Datetime(readonly=True, copy=False)
    is_applied = fields.Boolean(readonly=True, copy=False)

    @api.depends('installment_id', 'adjustment_type')
    def _compute_name(self):
        labels = dict(ADJUSTMENT_TYPE)
        for rec in self:
            rec.name = '%s — %s' % (
                rec.installment_id.description or _('Instalment'),
                labels.get(rec.adjustment_type, ''))

    @api.depends('amount_before', 'amount_after')
    def _compute_delta(self):
        for rec in self:
            rec.amount_delta = (rec.amount_after or 0.0) - (
                rec.amount_before or 0.0)

    @api.model_create_multi
    def create(self, vals_list):
        """Capture the 'before' from the record itself, not from the caller."""
        for vals in vals_list:
            installment = self.env['realestate.sale.installment'].browse(
                vals.get('installment_id'))
            vals.setdefault('amount_before', installment.current_amount)
            vals.setdefault('date_before', installment.date_due)
        return super().create(vals_list)

    def action_apply(self):
        """Move the obligation. Refuses to touch money already collected."""
        for rec in self:
            if rec.is_applied:
                continue
            installment = rec.installment_id
            if installment.paid_amount > 0.0001 and rec.amount_after < installment.paid_amount:
                raise UserError(_(
                    "Instalment %s already has %s paid against it, so it "
                    "cannot be adjusted down to %s. Money that has been "
                    "received has to stay accounted for — use a credit note."
                ) % (installment.description, installment.paid_amount,
                     rec.amount_after))
            if installment.move_id and installment.move_id.state == 'posted':
                raise UserError(_(
                    "Instalment %s is already invoiced (%s). Adjust the "
                    "invoice through Odoo — reversing or crediting it — rather "
                    "than changing the obligation underneath it."
                ) % (installment.description, installment.move_id.name))

            installment.adjustment_amount = (
                rec.amount_after - installment.original_amount)
            if rec.date_after:
                installment.date_due = rec.date_after
            rec.write({
                'is_applied': True,
                'applied_on': fields.Datetime.now(),
            })
        return True

    def unlink(self):
        applied = self.filtered('is_applied')
        if applied:
            raise UserError(_(
                "An applied adjustment is part of the obligation's history and "
                "cannot be deleted."))
        return super().unlink()


class SaleContractAmendmentLink(models.Model):
    """Amendment surface on the contract itself."""
    _inherit = 'realestate.sale.contract'

    amendment_ids = fields.One2many(
        'realestate.sale.contract.amendment', 'contract_id',
        string='Amendments')
    amendment_count = fields.Integer(compute='_compute_amendment_count')

    def _compute_amendment_count(self):
        for rec in self:
            rec.amendment_count = len(rec.amendment_ids)

    def action_view_amendments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Amendments — %s') % self.name,
            'res_model': 'realestate.sale.contract.amendment',
            'views': [(False, 'list'), (False, 'form')],
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Phase 36 — do not corrupt financial instruments
    # ------------------------------------------------------------------
    def _blocking_checks(self, installments=None):
        """Cheques that must be resolved before a schedule can be restructured.

        `real_estate_checks` is NOT a dependency of this module — the reference
        is late-bound, so Developer keeps working without it. Phase 36 is
        explicit: a deposited or cleared cheque is a financial instrument and
        must never be silently altered.
        """
        self.ensure_one()
        if 'realestate.check' not in self.env:
            return self.env['realestate.sale.installment'].browse()
        Check = self.env['realestate.check']
        target = installments if installments is not None else self.installment_ids
        domain = [('sale_installment_id', 'in', target.ids)]
        checks = Check.search(domain)
        # Anything past "registered" has left the developer's hands.
        settled_states = ('deposited', 'cleared', 'collected', 'endorsed')
        return checks.filtered(
            lambda c: (c.state in settled_states
                       if 'state' in c._fields else False))

    def _assert_no_blocking_checks(self, installments=None):
        self.ensure_one()
        blocking = self._blocking_checks(installments)
        if blocking:
            raise UserError(_(
                "Contract %s has %s cheque(s) that have been deposited or "
                "cleared against the instalments being changed:\n\n%s\n\n"
                "Resolve those cheques first. Restructuring underneath a "
                "presented cheque would corrupt a financial instrument."
            ) % (self.name, len(blocking),
                 '\n'.join('· %s' % c.display_name for c in blocking[:10])))
        return True
