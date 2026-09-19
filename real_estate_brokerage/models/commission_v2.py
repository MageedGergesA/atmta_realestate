# -*- coding: utf-8 -*-
"""M13 / M14 — one gross, then splits. Not five independent percentages.

### What 0.1 actually did

Each commission line computed itself:

```python
rec.amount = rec.transaction_id.sale_price * rec.percentage / 100.0
```

Nothing anywhere knew what the company had earned. Three agents at 2% each
billed out 6% of the sale price and no code objected, because there was no
figure for them to be inconsistent with. On a 5,000,000 sale that is 300,000
leaving the business against an agency fee that may have been 100,000.

So the model here is the one the brief asks for, in that order:

```
   gross ──▶ splits ──▶ approval ──▶ payable ──▶ accounting ──▶ paid
     │          │           │            │            │           │
  mandate    % of the    manager      vendor       posted     reconciled
  or listing  gross,     signs off     bill        by Odoo    by Odoo
              sum ≤ 100%
```

The **gross** is the company's revenue on the deal, computed once, from the
mandate that the owner signed. A **split** is a share of that gross. It is
arithmetically impossible to pay out more than was earned, because the splits
are constrained to the gross rather than to the sale price.

### Three more things 0.1 let through

**Anyone could bill themselves.** `action_create_vendor_bill` had no approval
gate at all, and an agent with ordinary write access is exactly who a
commission is paid to.

**A commission could be paid on a deal that had not closed.** Nothing looked at
the transaction's state.

**There was no clawback.** A deal that collapses after the agent has been paid
is an ordinary event in this business, and 0.1 had no answer to it.

### What this deliberately does not do

It does not write `payment_state`, force a bill to `paid`, or reconcile
anything itself. Module 3 settled that argument for the whole suite: Odoo owns
payment truth. `paid` here is read from the bill, and the one correction made to
0.1 is that a **reversed** bill no longer counts as paid — a reversal is the
opposite of a payment.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

COMMISSION_STATE = [
    ('draft', 'Draft'),
    ('approved', 'Approved'),
    ('billed', 'Billed'),
    ('paid', 'Paid'),
    ('clawed_back', 'Clawed Back'),
    ('cancelled', 'Cancelled'),
]

CLAWBACK_REASON = [
    ('deal_collapsed', 'Deal Fell Through'),
    ('overpaid', 'Overpaid in Error'),
    ('wrong_recipient', 'Paid to the Wrong Recipient'),
    ('rescinded', 'Sale Rescinded'),
    ('other', 'Other'),
]


class TransactionCommissionGross(models.Model):
    """The single authoritative gross, on the transaction that earned it."""
    _inherit = 'realestate.transaction'

    commission_gross_amount = fields.Monetary(
        string='Gross Commission', compute='_compute_commission_gross',
        store=True, readonly=False, tracking=True,
        help="What the company earns on this deal, before anybody is paid out "
             "of it. Derived from the mandate the owner signed, and "
             "overridable — but overriding it wants a reason, because it is "
             "the number every split is measured against.")
    commission_gross_source = fields.Selection([
        ('mandate', 'Owner Mandate'),
        ('listing', 'Listing Terms'),
        ('manual', 'Set Manually'),
    ], string='Gross Basis', compute='_compute_commission_gross', store=True,
        readonly=False)
    commission_gross_note = fields.Char(string='Gross Override Reason')

    commission_allocated = fields.Monetary(
        string='Allocated', compute='_compute_commission_split', store=True)
    commission_unallocated = fields.Monetary(
        string='Unallocated', compute='_compute_commission_split', store=True,
        help="Gross the company keeps: what is left after the agents, "
             "referrers and co-brokers have been paid out of it.")
    commission_over_allocated = fields.Boolean(
        compute='_compute_commission_split', store=True)

    @api.depends('sale_price', 'listing_id.active_mandate_id.commission_basis',
                 'listing_id.active_mandate_id.commission_percentage',
                 'listing_id.active_mandate_id.commission_fixed',
                 'listing_id.commission_basis',
                 'listing_id.commission_percentage',
                 'listing_id.commission_fixed')
    def _compute_commission_gross(self):
        """One place, one calculation.

        The mandate wins where there is one: it is the signed instruction from
        the owner, and the listing's own terms are this company's note to
        itself.
        """
        for rec in self:
            if rec.commission_gross_source == 'manual':
                # A human has taken responsibility for this number; do not
                # quietly walk over it because a related field moved.
                rec.commission_gross_amount = rec.commission_gross_amount
                continue
            amount, source = rec._re_gross_from_terms()
            rec.commission_gross_amount = amount
            rec.commission_gross_source = source

    def _re_gross_from_terms(self):
        self.ensure_one()
        price = self.sale_price or 0.0
        mandate = self.listing_id.active_mandate_id.sudo()
        if mandate:
            if mandate.commission_basis == 'fixed':
                return mandate.commission_fixed, 'mandate'
            if mandate.commission_basis == 'percentage':
                return price * (mandate.commission_percentage or 0.0) / 100.0, \
                    'mandate'
        listing = self.listing_id
        if listing.commission_basis == 'fixed':
            return listing.commission_fixed, 'listing'
        if listing.commission_basis == 'percentage':
            return price * (listing.commission_percentage or 0.0) / 100.0, \
                'listing'
        return 0.0, 'manual'

    @api.depends('commission_ids.amount', 'commission_ids.state',
                 'commission_gross_amount')
    def _compute_commission_split(self):
        for rec in self:
            live = rec.commission_ids.filtered(
                lambda c: c.state not in ('cancelled', 'clawed_back'))
            allocated = sum(live.mapped('amount'))
            rec.commission_allocated = allocated
            rec.commission_unallocated = rec.commission_gross_amount - allocated
            if not rec.commission_gross_amount:
                # "Over-allocated relative to nothing" is not a statement about
                # anything. A legacy row with no gross is caught by the
                # entitlement gate, which says something useful; flagging it
                # here as well would light a permanent red banner on every
                # pre-0.2 transaction and block an explicit fixed entitlement
                # that is perfectly well defined.
                rec.commission_over_allocated = False
                continue
            rec.commission_over_allocated = (
                rec.currency_id.compare_amounts(
                    allocated, rec.commission_gross_amount) > 0
                if rec.currency_id else allocated > rec.commission_gross_amount)

    def action_set_gross_manually(self, amount, reason):
        """Override the computed gross, on the record."""
        self.ensure_one()
        if not reason:
            raise UserError(_(
                "Overriding the gross commission needs a reason. It is the "
                "number every payout is measured against."))
        self.write({
            'commission_gross_amount': amount,
            'commission_gross_source': 'manual',
            'commission_gross_note': reason,
        })
        self.message_post(body=_(
            "Gross commission set manually to %(amount)s. Reason: %(reason)s",
            amount=amount, reason=reason))
        return True


class CommissionV2(models.Model):
    _inherit = 'realestate.commission'
    _check_company_auto = True

    company_id = fields.Many2one(
        related='transaction_id.company_id', store=True, index=True,
        readonly=True)

    state = fields.Selection(
        COMMISSION_STATE, default='draft', required=True, index=True,
        tracking=True,
        help="draft → approved → billed → paid. `paid` is read from the "
             "vendor bill; this module never writes it into accounting.")

    # ------------------------------------------------------------------
    # A split is a share of the gross, not of the sale price
    # ------------------------------------------------------------------
    calculation_method = fields.Selection(
        selection_add=[('share', 'Share of Gross Commission')],
        ondelete={'share': 'set default'},
        default='share')
    share_percentage = fields.Float(
        string='Share of Gross (%)',
        help="This recipient's share of what the company earned. Constrained "
             "so the splits cannot exceed the gross — 0.1 measured every line "
             "against the sale price instead, which is how three agents at 2% "
             "each could bill out 6% of a sale.")
    gross_amount = fields.Monetary(
        related='transaction_id.commission_gross_amount', readonly=True,
        string='Gross')

    # ------------------------------------------------------------------
    # The entitlement snapshot
    #
    # An entitlement is a promise made on a particular day, on a particular
    # basis. Recomputing it later from whatever the configuration happens to
    # say now is how an agent's approved pay changes without anybody deciding
    # it should. Everything needed to reconstruct the figure is frozen onto the
    # line when it is created, and the figure itself stops moving the moment it
    # is approved.
    # ------------------------------------------------------------------
    snapshot_gross_amount = fields.Monetary(
        string='Gross at Allocation', readonly=True, copy=False,
        help="The gross brokerage commission this share was calculated "
             "against, as it stood when the allocation was made.")
    snapshot_share_percentage = fields.Float(
        string='Share at Allocation (%)', readonly=True, copy=False)
    snapshot_amount = fields.Monetary(
        string='Entitlement at Allocation', readonly=True, copy=False)
    snapshot_method = fields.Selection(
        [('share', 'Share of Gross Commission'),
         ('percentage', 'Percentage of Sale Price'),
         ('fixed', 'Fixed Amount')],
        string='Method at Allocation', readonly=True, copy=False)
    snapshot_taken_on = fields.Datetime(
        string='Allocated On', readonly=True, copy=False)
    effective_date = fields.Date(
        string='Effective Date', copy=False,
        default=fields.Date.context_today,
        help="The date this entitlement is deemed to arise on. Kept separate "
             "from the creation timestamp so a commission can be backdated to "
             "the deal it belongs to.")
    share_source = fields.Selection([
        ('manual', 'Entered Manually'),
        ('user_default', "Agent's Default"),
        ('agreement', 'Broker Agreement'),
        ('migrated', 'Migrated from 0.1'),
    ], string='Share Source', default='manual', readonly=True, copy=False,
        help="Where the percentage came from. A share taken from an agent "
             "default whose 0.1 meaning was never resolved cannot be approved "
             "until somebody resolves it.")
    source_rule = fields.Char(
        string='Source Rule', readonly=True, copy=False,
        help="Human-readable statement of the rule that produced this share.")
    source_agreement_id = fields.Many2one(
        'realestate.broker.agreement', string='Source Agreement',
        readonly=True, copy=False, ondelete='set null')

    entitlement_locked = fields.Boolean(
        string='Entitlement Locked', compute='_compute_entitlement_locked',
        store=True,
        help="Once approved, the figure stops following the configuration. "
             "What was approved is what is owed.")

    needs_config_review = fields.Boolean(
        string='Needs Configuration Review', compute='_compute_needs_review',
        store=True,
        help="This share came from an agent default whose 0.1 meaning was "
             "never resolved. Blocks approval and payment; blocks nothing "
             "else.")

    # ------------------------------------------------------------------
    # Approval (M14)
    # ------------------------------------------------------------------
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Clawback (M14)
    # ------------------------------------------------------------------
    clawback_of_id = fields.Many2one(
        'realestate.commission', string='Clawback Of', readonly=True,
        copy=False, ondelete='set null')
    clawback_id = fields.Many2one(
        'realestate.commission', string='Clawed Back By', readonly=True,
        copy=False, ondelete='set null')
    clawback_reason = fields.Selection(
        CLAWBACK_REASON, string='Clawback Reason', tracking=True)
    clawback_note = fields.Char()
    reversal_move_id = fields.Many2one(
        'account.move', string='Reversal', readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Locking and review
    # ------------------------------------------------------------------
    @api.depends('state')
    def _compute_entitlement_locked(self):
        for rec in self:
            rec.entitlement_locked = rec.state in (
                'approved', 'billed', 'paid', 'clawed_back')

    @api.depends('share_source', 'partner_id',
                 'partner_id.user_ids.commission_share_needs_review')
    def _compute_needs_review(self):
        for rec in self:
            if rec.share_source != 'user_default':
                rec.needs_config_review = False
                continue
            agents = rec.partner_id.sudo().user_ids
            rec.needs_config_review = any(
                agents.mapped('commission_share_needs_review'))

    # ------------------------------------------------------------------
    # Amount — the share method, and the frozen figure
    # ------------------------------------------------------------------
    @api.depends('calculation_method', 'percentage', 'fixed_amount',
                 'share_percentage', 'transaction_id.sale_price',
                 'transaction_id.commission_gross_amount', 'state',
                 'snapshot_amount')
    def _compute_amount(self):
        """An approved entitlement does not move.

        Editing a closed deal's sale price would otherwise silently restate
        what an agent was already told they had earned — and, once billed,
        disagree with the vendor bill.
        """
        locked = self.filtered(
            lambda c: c.state in ('approved', 'billed', 'paid', 'clawed_back'))
        for rec in locked:
            rec.amount = rec.snapshot_amount

        live = self - locked
        share_lines = live.filtered(
            lambda c: c.calculation_method == 'share')
        for rec in share_lines:
            rec.amount = (rec.transaction_id.commission_gross_amount or 0.0) \
                * (rec.share_percentage or 0.0) / 100.0
        return super(CommissionV2, live - share_lines)._compute_amount()

    @api.depends('bill_id.payment_state')
    def _compute_paid(self):
        """A reversed bill is not a paid one.

        0.1 counted `reversed` as paid, which is precisely backwards: a
        reversal is the accounting record of a payment being undone.
        """
        for rec in self:
            rec.paid = rec.bill_id.payment_state in ('paid', 'in_payment')

    # ------------------------------------------------------------------
    # Constraints — the arithmetic that 0.1 had nowhere to do
    # ------------------------------------------------------------------
    @api.constrains('amount', 'state', 'transaction_id')
    def _check_within_gross(self):
        for txn in self.mapped('transaction_id'):
            gross = txn.commission_gross_amount or 0.0
            if not gross:
                # No gross established — usually a pre-0.2 transaction whose
                # listing carried no commission terms. Measuring a split
                # against a gross that was never recorded would fail every
                # legacy row on upgrade and teach nobody anything. The
                # enforcement that matters is in `_check_billable`: no money
                # leaves without a gross to measure it against.
                continue
            live = txn.commission_ids.filtered(
                lambda c: c.state not in ('cancelled', 'clawed_back'))
            allocated = sum(live.mapped('amount'))
            currency = txn.currency_id
            over = (currency.compare_amounts(allocated, gross) > 0
                    if currency else allocated > gross)
            if over:
                raise ValidationError(_(
                    "The commission splits on %(txn)s come to %(allocated)s, "
                    "but the company only earned %(gross)s on the deal.\n\n"
                    "A split is a share of what was earned. Paying out more "
                    "than that is not a commission, it is a loss.",
                    txn=txn.name, allocated=allocated, gross=gross))

    @api.constrains('share_percentage')
    def _check_share_range(self):
        for rec in self:
            if rec.calculation_method != 'share':
                continue
            if not 0.0 <= rec.share_percentage <= 100.0:
                raise ValidationError(_(
                    "A share of %s%% of the gross is not a share."
                ) % rec.share_percentage)

    # ------------------------------------------------------------------
    # Create — freeze the basis
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            line._take_snapshot()
        return lines

    def _re_calculated_entitlement(self):
        """The figure this line's basis produces, computed from the basis.

        Deliberately not read off `amount`: a locked line's `amount` *is* the
        snapshot, so snapshotting from it would freeze a record at whatever it
        already said — which for a line created directly in a locked state
        (a clawback mirror) is nothing at all.
        """
        self.ensure_one()
        if self.calculation_method == 'fixed':
            return self.fixed_amount or 0.0
        if self.calculation_method == 'share':
            return (self.transaction_id.commission_gross_amount or 0.0) \
                * (self.share_percentage or 0.0) / 100.0
        return (self.transaction_id.sale_price or 0.0) \
            * (self.percentage or 0.0) / 100.0

    def _take_snapshot(self):
        """Freeze everything needed to reconstruct this figure later."""
        self.ensure_one()
        self.sudo().write({
            'snapshot_gross_amount': (
                self.transaction_id.commission_gross_amount or 0.0),
            'snapshot_share_percentage': self.share_percentage or 0.0,
            'snapshot_amount': self._re_calculated_entitlement(),
            'snapshot_method': self.calculation_method,
            'snapshot_taken_on': fields.Datetime.now(),
        })
        return True

    # ------------------------------------------------------------------
    # Approval
    # ------------------------------------------------------------------
    def action_approve(self):
        """Manager-only, and never your own."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_(
                    "%s is not a draft commission.") % rec.display_name)
            rec._check_may_approve()
            rec._check_entitlement_calculable()
            # Re-freeze at the moment of approval: what a manager signed off is
            # what is owed, not whatever the draft happened to say earlier.
            rec._take_snapshot()
            rec.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })
        return True

    def _check_entitlement_calculable(self):
        """No money may move on an entitlement nobody can compute.

        Legacy rows with a zero gross are allowed to *exist* — that
        compatibility decision stands — but existing is not the same as being
        payable. A fixed amount is a complete entitlement on its own and passes;
        a share or a percentage of nothing is not a number.
        """
        self.ensure_one()

        if self.needs_config_review:
            agents = self.partner_id.sudo().user_ids.filtered(
                'commission_share_needs_review')
            raise UserError(_(
                "%(who)s's default commission share has not been resolved "
                "since the 0.1 → 0.3 semantic change, so this entitlement "
                "would be based on a number whose meaning is unknown.\n\n"
                "A Brokerage Manager must set the share explicitly (the "
                "original value is preserved on the agent record) before this "
                "commission can be approved.",
                who=', '.join(agents.mapped('display_name'))
                or self.partner_id.display_name))

        if self.calculation_method == 'fixed':
            if self.fixed_amount <= 0:
                raise UserError(_(
                    "%s is a fixed commission of nothing.") % self.display_name)
            return True

        gross = self.transaction_id.commission_gross_amount or 0.0
        if not gross:
            raise UserError(_(
                "Transaction %(txn)s has no gross brokerage commission, so a "
                "%(method)s entitlement cannot be calculated.\n\n"
                "Set the gross — from the mandate, from the listing terms, or "
                "manually with a reason — or record an explicit fixed amount. "
                "The row may stay as it is; it just cannot be approved or "
                "paid while the entitlement is undefined.",
                txn=self.transaction_id.name,
                method=dict(self._fields['calculation_method'].selection).get(
                    self.calculation_method, self.calculation_method)))
        return True

    def _check_may_approve(self):
        self.ensure_one()
        if not self.env.user.has_group(
                'real_estate_brokerage.group_realestate_sales_manager'):
            raise UserError(_(
                "Approving a commission is a manager's decision."))
        if self.partner_id == self.env.user.partner_id:
            raise UserError(_(
                "You cannot approve your own commission. Ask another manager — "
                "this is the one control that stops the payout process being a "
                "single person's signature."))
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state in ('billed', 'paid'):
                raise UserError(_(
                    "%s has already been billed. Claw it back instead — "
                    "cancelling would leave the vendor bill behind with "
                    "nothing pointing at it.") % rec.display_name)
            rec.state = 'cancelled'
        return True

    # ------------------------------------------------------------------
    # Billing — the gates 0.1 did not have
    # ------------------------------------------------------------------
    def action_create_vendor_bill(self):
        for rec in self:
            rec._check_billable()
        result = super().action_create_vendor_bill()
        self.filtered(lambda c: c.bill_id).write({'state': 'billed'})
        return result

    def _check_billable(self):
        self.ensure_one()
        if self.state == 'draft':
            raise UserError(_(
                "Commission %s has not been approved. 0.1 let any agent with "
                "write access raise a vendor bill to themselves; that is what "
                "the approval step is for.") % self.display_name)
        if self.state in ('clawed_back', 'cancelled'):
            raise UserError(_(
                "%s is %s and cannot be billed.") % (
                    self.display_name, self.state))
        # A broker suspended, unlicensed or unverified since approval may not
        # be paid — the same eligibility rule that stops them registering a
        # lead. Agents' own partners are not brokers and are not asked.
        if self.partner_id.is_realestate_broker:
            self.partner_id._check_may_transact()
        self._check_entitlement_calculable()
        txn = self.transaction_id
        if txn.commission_over_allocated:
            raise UserError(_(
                "The splits on %(txn)s come to %(allocated)s against a gross "
                "of %(gross)s. Fix the allocation before any of it is paid.",
                txn=txn.name, allocated=txn.commission_allocated,
                gross=txn.commission_gross_amount))
        if self.transaction_id.state != 'closed':
            raise UserError(_(
                "Transaction %(txn)s is %(state)s. A commission is earned when "
                "the deal completes, not when it is expected to.",
                txn=self.transaction_id.name,
                state=self.transaction_id.state))
        return True

    def action_mark_paid(self):
        for rec in self:
            rec._check_billable()
        result = super().action_mark_paid()
        # Read the outcome back rather than declaring it: Odoo owns payment
        # truth, and `paid` is computed from the bill's own state.
        for rec in self:
            if rec.paid:
                rec.state = 'paid'
        return result

    # ------------------------------------------------------------------
    # Clawback (M14)
    # ------------------------------------------------------------------
    def action_clawback(self, reason, note=None):
        """Undo a commission that should not have been paid.

        Creates a **reversal** of the vendor bill and a mirrored negative
        commission line, so the transaction's own arithmetic stops counting
        the original. It does not reconcile the reversal against the payment
        or declare anything settled — that is bank and accounting work, and
        Module 3 established for the whole suite that this code does not
        pretend to do it.
        """
        self.ensure_one()
        if not reason:
            raise UserError(_(
                "A clawback needs a reason. Money going back out of somebody's "
                "pay is not something to record silently."))
        # Asked before the state check: a clawed-back line is no longer
        # `billed`, and telling the manager it "has not been billed" sends
        # them looking for a bill that exists.
        if self.clawback_id:
            raise UserError(_(
                "%s has already been clawed back by %s."
            ) % (self.display_name, self.clawback_id.display_name))
        if self.state not in ('billed', 'paid'):
            raise UserError(_(
                "%s has not been billed, so there is nothing to claw back. "
                "Cancel it instead.") % self.display_name)
        if not self.env.user.has_group(
                'real_estate_brokerage.group_realestate_sales_manager'):
            raise UserError(_("Clawing back a commission is a manager's "
                              "decision."))

        reversal = self._reverse_bill(reason)

        mirror = self.copy({
            'calculation_method': 'fixed',
            'fixed_amount': -self.amount,
            'share_percentage': 0.0,
            'state': 'clawed_back',
            'clawback_of_id': self.id,
            'clawback_reason': reason,
            'clawback_note': note,
            'bill_id': False,
            'payment_date': False,
            'approved_by_id': False,
            'approved_on': False,
            'reversal_move_id': reversal.id if reversal else False,
        })
        self.write({
            'state': 'clawed_back',
            'clawback_id': mirror.id,
            'clawback_reason': reason,
            'clawback_note': note,
            'reversal_move_id': reversal.id if reversal else False,
        })
        self.transaction_id.message_post(body=_(
            "Commission to %(who)s clawed back (%(reason)s). The reversal is "
            "posted; settling it against the original payment is bank work "
            "and is not done here.",
            who=self.partner_id.display_name,
            reason=dict(CLAWBACK_REASON).get(reason, reason)))
        return mirror

    def _reverse_bill(self, reason):
        self.ensure_one()
        bill = self.bill_id
        if not bill or bill.state != 'posted':
            return self.env['account.move']
        reversal = bill._reverse_moves([{
            'ref': _('Clawback — %(ref)s (%(reason)s)',
                     ref=bill.ref or bill.name,
                     reason=dict(CLAWBACK_REASON).get(reason, reason)),
            'invoice_date': fields.Date.context_today(self),
        }])
        # `_reverse_moves` leaves a draft credit note. A clawback that produces
        # an unposted document has not put the exposure on the ledger at all —
        # it sits in somebody's drafts until they notice. Posting it is this
        # module's job; *reconciling* it against the original payment is not,
        # and is deliberately still left to accounting.
        if reversal:
            self.env['realestate.account.tools'].post_moves(reversal)
        return reversal

    # ------------------------------------------------------------------
    # Seeding splits from the agents' defaults
    # ------------------------------------------------------------------
    @api.model
    def _default_split_lines(self, transaction):
        """Shares of the gross, not percentages of the sale price.

        Refuses loudly for an agent whose 0.1 default was never resolved.
        Silently skipping them would be the same failure in a quieter costume:
        somebody would notice the missing line weeks later, or not at all.
        """
        unresolved = []
        lines = []
        pairs = [(transaction.lister_agent_id, 'lister'),
                 (transaction.selling_agent_id, 'selling')]
        for agent, role in pairs:
            if not agent:
                continue
            if agent.sudo().commission_share_needs_review:
                unresolved.append(agent)
                continue
            share = agent.commission_share_default or 0.0
            if share <= 0:
                continue
            lines.append({
                'partner_id': agent.partner_id.id,
                'role': role,
                'calculation_method': 'share',
                'share_percentage': share,
                'share_source': 'user_default',
                'source_rule': _(
                    "%(agent)s's default share of gross (%(share)s%%)",
                    agent=agent.display_name, share=share),
            })

        if unresolved:
            raise UserError(_(
                "%(who)s still carry a 0.1 commission default whose meaning "
                "has not been resolved — the stored number used to mean a "
                "percentage of the sale price and now means a share of the "
                "agency's fee.\n\n"
                "Seeding a split from it would produce a materially wrong "
                "figure. The original values are preserved on the agent "
                "records; a Brokerage Manager needs to set the new share "
                "before these agents can be allocated automatically. Adding a "
                "line by hand is unaffected.",
                who=', '.join(u.display_name for u in unresolved)))
        return lines


class TransactionCommissionSeeding(models.Model):
    _inherit = 'realestate.transaction'

    def action_add_default_commissions(self):
        """Seed splits of the gross.

        0.1 seeded `percentage` lines that each took a cut of the *sale price*,
        so two agents on 2.5% defaults quietly allocated 5% of the whole sale
        against an agency fee that was often 2%.
        """
        Commission = self.env['realestate.commission']
        for rec in self:
            if rec.commission_ids:
                continue
            for vals in Commission._default_split_lines(rec):
                Commission.create(dict(vals, transaction_id=rec.id))
        return True
