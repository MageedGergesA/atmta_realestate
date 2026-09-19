# -*- coding: utf-8 -*-
"""M1 / M2 / M5 — the cheque as a financial instrument.

### What this model is

A `realestate.check` is a piece of paper. It is **not** an obligation and it is
**not** a payment:

* the obligation is `realestate.sale.installment` (Developer) or
  `realestate.contract.payment` (Rental) — they say what is owed;
* the accounting document is `account.move` — it says what was billed;
* the money is `account.payment` + reconciliation — Odoo says what arrived;
* this record says *what physical instrument the customer handed over*.

Receiving one is not receiving money (Rule 2), and nothing here ever writes an
accounting field to assert otherwise (Rule 3).

### What changed in 0.2, and what did not

Every field and every `state` value that shipped in 0.1 is still here with its
original meaning — `real_estate_developer` matches on these strings and
production rows carry them (Rule 5). Two states were **added**: `in_clearing`
(at the bank, outcome not yet known) and `returned` (physically given back).

The behavioural change is where clearance comes from. In 0.1 `cleared` meant
"a user pressed a button": `action_mark_cleared` created a payment and set the
state unconditionally, with no bank involvement at all. In 0.2 the deposit
creates the payment (M7) and clearance is **read back from Odoo's
reconciliation** (M8). `action_mark_cleared` survives as a compatibility entry
point, but it now refuses to assert cash that Odoo has not confirmed.
"""

import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .check_states import (
    CHECK_ACCOUNTING_STATE,
    CHECK_AT_BANK,
    CHECK_ON_HAND,
    CHECK_STATE,
    CHECK_TERMINAL,
    MATURITY_BUCKET,
    maturity_bucket_for,
)

_logger = logging.getLogger(__name__)

#: Kept as a module-level name because 0.1 exported it and external code may
#: import it. Now sourced from the single vocabulary.
CHECK_STATES = CHECK_STATE

#: Context key and value marking a clearance written by
#: `_sync_clearance_from_accounting`, which is the only place allowed to write
#: it. The value is an object compared by identity: a context arriving over
#: RPC is JSON and cannot carry it, so no caller can claim to be that method.
CLEARANCE_CTX = 're_check_clearance'
CLEARANCE_TOKEN = object()


class RealEstateCheck(models.Model):
    _name = 'realestate.check'
    _description = 'Real Estate Post-Dated Check'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'due_date, id'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index=True,
    )
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # The physical instrument
    # ------------------------------------------------------------------
    # Char, and it stays Char. `000012345` is a different cheque number from
    # `12345`, and the leading zeros are printed on the paper.
    check_number = fields.Char(string='Check #', required=True, tracking=True,
                               index=True)
    bank_id = fields.Many2one(
        'res.bank', string='Drawer Bank', required=True, tracking=True,
        index=True,
        help="Odoo's own bank master. Relational since 0.1 — there is no "
             "free-text bank name to migrate.")
    branch = fields.Char(string='Branch')
    account_number = fields.Char(
        string='Drawer Account',
        help="The drawer's account at that bank. Part of what makes a cheque "
             "number unique: the same customer can hold two accounts at the "
             "same bank, each with a chequebook starting at 000001.")
    partner_bank_id = fields.Many2one(
        'res.partner.bank', string='Drawer Bank Account', ondelete='set null',
        help="Optional link to a recorded bank account of the drawer. When "
             "set it is the authority; `branch` and `account_number` remain "
             "for cheques whose account was never registered in Odoo.")

    front_image = fields.Image(
        string='Front Scan', max_width=2000, max_height=2000, attachment=True,
        help="Stored through Odoo's attachment layer, not inline in the row — "
             "a 100,000-cheque table must not carry 100,000 images in its "
             "heap.")
    back_image = fields.Image(
        string='Back Scan', max_width=2000, max_height=2000, attachment=True)

    # ------------------------------------------------------------------
    # Parties
    # ------------------------------------------------------------------
    partner_id = fields.Many2one(
        'res.partner', string='Drawer (Buyer)', required=True, tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency', required=True, tracking=True,
        default=lambda self: self.env.company.currency_id,
    )

    # ------------------------------------------------------------------
    # Financial face value
    # ------------------------------------------------------------------
    amount = fields.Monetary(string='Amount', required=True, tracking=True)
    issue_date = fields.Date(
        string='Issue Date', default=fields.Date.context_today, tracking=True,
    )
    due_date = fields.Date(string='Due Date', required=True, tracking=True,
                           index=True)
    received_date = fields.Date(
        string='Received On', default=fields.Date.context_today, tracking=True,
        help="When Treasury actually took possession. Distinct from the issue "
             "date written on the cheque, which is the drawer's claim.")
    presented_date = fields.Date(
        string='Presented On', readonly=True, copy=False,
        help="When the cheque last went to the bank. Held on the cheque so "
             "that cancelling a deposit slip cannot erase the record of a "
             "presentation that really happened.")
    cleared_date = fields.Date(string='Cleared On', readonly=True, copy=False)
    bounce_date = fields.Date(string='Last Bounce On', readonly=True, copy=False)
    cancelled_date = fields.Date(readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Commercial source
    # ------------------------------------------------------------------
    sale_contract_id = fields.Many2one(
        'realestate.sale.contract', string='Sale Contract', tracking=True,
        ondelete='set null', index=True, check_company=True,
    )
    # `sale_installment_id` and `rental_payment_id` are 0.1's simple-case
    # fields. They are kept — `atmta_real_estate.tests.test_compatibility`
    # asserts the second one exists, and Developer searches on the first — but
    # they are now MIRRORS of the allocation engine rather than a second source
    # of truth. See `allocation.py`; the sync runs both ways, so the easy case
    # (create a cheque naming one instalment) stays a one-liner.
    sale_installment_id = fields.Many2one(
        'realestate.sale.installment', string='Sale Installment', tracking=True,
        ondelete='set null', index=True,
        help='Primary instalment this cheque pays. Mirrors the single active '
             'allocation when there is exactly one; blank when the cheque is '
             'split across several obligations.',
    )
    rental_contract_id = fields.Many2one(
        'realestate.contract', string='Rental Contract', tracking=True,
        ondelete='set null', index=True,
    )
    rental_payment_id = fields.Many2one(
        'realestate.contract.payment', string='Rental Payment', tracking=True,
        ondelete='set null', index=True,
    )
    project_id = fields.Many2one(
        'realestate.project', string='Project', tracking=True,
        compute='_compute_project_from_source', store=True, readonly=False,
        index=True,
    )
    property_id = fields.Many2one(
        'realestate.property', string='Property', tracking=True,
        compute='_compute_project_from_source', store=True, readonly=False,
        index=True,
    )

    # ------------------------------------------------------------------
    # Allocation (M3)
    # ------------------------------------------------------------------
    allocation_ids = fields.One2many(
        'realestate.check.allocation', 'check_id', string='Allocations')
    allocated_amount = fields.Monetary(
        compute='_compute_allocation_totals', store=True,
        help="Face value committed to obligations.")
    unapplied_amount = fields.Monetary(
        string='Unapplied', compute='_compute_allocation_totals', store=True,
        help="Face value not yet pointed at any obligation. Shown explicitly "
             "rather than hidden: an unapplied balance is a real operational "
             "state, not an error to be papered over.")
    allocation_count = fields.Integer(
        compute='_compute_allocation_totals', store=True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    state = fields.Selection(
        CHECK_STATE, default='draft', tracking=True, required=True, copy=False,
        index=True,
    )
    deposit_id = fields.Many2one(
        'realestate.check.deposit', string='Deposit Slip', readonly=True,
        copy=False, ondelete='set null', index=True, check_company=True,
    )
    # 0.1 made this `related='deposit_id.deposit_date'`, so cancelling a slip
    # erased the record of when the cheque had been presented. It is now a
    # stored value written at presentation time and never taken back.
    deposit_date = fields.Date(
        string='Deposited On', readonly=True, copy=False,
        help="When this cheque was last sent to the bank.")

    presentation_ids = fields.One2many(
        'realestate.check.presentation', 'check_id', string='Presentations')
    presentation_count = fields.Integer(
        compute='_compute_presentation_stats', store=True)
    current_presentation_id = fields.Many2one(
        'realestate.check.presentation', string='Current Attempt',
        compute='_compute_presentation_stats', store=True)

    payment_id = fields.Many2one(
        'account.payment', string='Payment', readonly=True, copy=False,
        check_company=True, index=True,
        help='Created when the deposit is confirmed — not when someone '
             'declares the cheque cleared.',
    )
    bounce_id = fields.Many2one(
        'realestate.check.bounce', string='Latest Bounce', readonly=True,
        copy=False, ondelete='set null',
        help="0.1 field, kept. Points at the most recent bounce; the full "
             "history is on `bounce_ids`.")
    bounce_ids = fields.One2many(
        'realestate.check.bounce', 'check_id', string='Bounces')
    bounce_count = fields.Integer(compute='_compute_bounce_stats', store=True)

    # ------------------------------------------------------------------
    # Replacement lineage (M13)
    # ------------------------------------------------------------------
    replacement_check_id = fields.Many2one(
        'realestate.check', string='Replacement Check', readonly=True,
        copy=False, ondelete='set null', index=True,
        help='The cheque that replaced this one.',
    )
    replaces_check_id = fields.Many2one(
        'realestate.check', string='Replaces', readonly=True, copy=False,
        ondelete='set null', index=True,
        help='The cheque this one replaced.',
    )
    root_check_id = fields.Many2one(
        'realestate.check', string='Original Cheque',
        compute='_compute_lineage', store=True, recursive=True, index=True,
        help="First instrument in the replacement chain. Lets the whole chain "
             "be pulled with one indexed query instead of walking parents.")
    replacement_generation = fields.Integer(
        compute='_compute_lineage', store=True, recursive=True,
        help="0 for an original cheque, 1 for its replacement, and so on.")

    # ------------------------------------------------------------------
    # Custody (M4) — mirrored from the movement history, never typed in
    # ------------------------------------------------------------------
    custody_ids = fields.One2many(
        'realestate.check.custody', 'check_id', string='Custody History')
    custodian_id = fields.Many2one(
        'res.users', string='Current Custodian', readonly=True, copy=False,
        help="Derived from the last custody movement. Not editable: custody "
             "that can be typed over is not custody.")
    location_id = fields.Many2one(
        'realestate.check.location', string='Current Location', readonly=True,
        copy=False, check_company=True, index=True)

    # ------------------------------------------------------------------
    # Maturity (M5) — treasury concepts, not accounting ones
    # ------------------------------------------------------------------
    days_to_due = fields.Integer(
        compute='_compute_maturity',
        help="Negative once the cheque has matured.")
    is_due = fields.Boolean(compute='_compute_maturity', search='_search_is_due')
    is_overdue_for_deposit = fields.Boolean(
        compute='_compute_maturity',
        help="Matured, still on hand, and past the company's tolerance.")
    is_stale = fields.Boolean(
        compute='_compute_maturity',
        help="Past the company's configured validity period, if one is set.")
    maturity_bucket = fields.Selection(
        MATURITY_BUCKET, compute='_compute_maturity_bucket', store=True, index=True,
        help="Stored so the forecast can group in the database instead of in "
             "Python; refreshed nightly by the maturity cron.")

    # ------------------------------------------------------------------
    # The accounting dimension (M2) — a view of Odoo, never a copy
    # ------------------------------------------------------------------
    accounting_state = fields.Selection(
        CHECK_ACCOUNTING_STATE, string='Accounting Status',
        compute='_compute_accounting_state', store=True, index=True,
        help="Read from the linked payment and its reconciliation. This module "
             "never writes an accounting field to make one of these true.")
    payment_state = fields.Selection(
        related='payment_id.state', string='Payment State', store=True)
    is_bank_matched = fields.Boolean(
        string='Bank Matched', compute='_compute_accounting_state', store=True,
        help="Odoo's own answer to 'has this payment met a bank transaction'.")

    journal_id = fields.Many2one(
        'account.journal', string='Bank Journal', check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
        help='Journal the cheque is banked into.',
    )

    notes = fields.Text()

    # 0.1 shipped `unique(company_id, bank_id, check_number, partner_id)` as an
    # unconditional table constraint. Two problems: it ignored the drawer's
    # account (the same customer's two chequebooks both start at 000001), and
    # being unconditional it made a cheque number unusable for ever once a
    # cheque was cancelled or returned — real developers reissue.
    #
    # It is replaced by a PARTIAL unique index over live cheques only, built in
    # `init()` so legacy collisions can be reported instead of aborting the
    # upgrade. See `_create_identity_index`.
    # No SQL CHECK on `amount`: the Python constraint below carries a
    # translatable message, and a table constraint would fire first and
    # surface a raw psycopg2 error instead.
    _sql_constraints = []

    #: The identity of a cheque: this drawer's account at this bank, in this
    #: company, bearing this number.
    _IDENTITY_LIVE_STATES = ('draft', 'registered', 'deposited', 'in_clearing',
                             'cleared', 'bounced')

    # ==================================================================
    # Schema
    # ==================================================================
    def init(self):
        super_init = getattr(super(), 'init', None)
        if super_init:
            super_init()
        self._create_identity_index()

    def _create_identity_index(self):
        """Create the partial identity index — but only if the data allows it.

        M1 is explicit: *do not apply a new SQL constraint until legacy data has
        been analyzed; migration must report duplicates rather than crashing
        blindly.* So this probes first. If live rows already collide, the index
        is skipped and the collisions are logged by cheque reference; the
        upgrade completes and Treasury can clean up. It is retried on every
        subsequent upgrade, so the index appears by itself once the data is
        sound.
        """
        cr = self.env.cr
        cr.execute("""
            SELECT indexname FROM pg_indexes
             WHERE tablename = 'realestate_check'
               AND indexname = 'realestate_check_identity_uniq'
        """)
        if cr.fetchone():
            return

        cr.execute("""
            SELECT array_agg(name) AS refs
              FROM realestate_check
             WHERE active AND state = ANY(%s)
             GROUP BY company_id, partner_id, bank_id,
                      COALESCE(account_number, ''), COALESCE(check_number, '')
            HAVING count(*) > 1
             LIMIT 50
        """, (list(self._IDENTITY_LIVE_STATES),))
        clashes = cr.fetchall()
        if clashes:
            _logger.error(
                "real_estate_checks: the cheque identity index "
                "(company, drawer, bank, account, number) was NOT created — "
                "%s group(s) of live cheques already share an identity. "
                "Nothing has been changed or deleted. Resolve these and "
                "upgrade again; the index will be created automatically.\n%s",
                len(clashes),
                '\n'.join('  - %s' % ', '.join(row[0]) for row in clashes))
            return

        cr.execute("""
            CREATE UNIQUE INDEX realestate_check_identity_uniq
                ON realestate_check (
                    company_id, partner_id, bank_id,
                    COALESCE(account_number, ''), COALESCE(check_number, ''))
             WHERE active AND state IN %s
        """, (self._IDENTITY_LIVE_STATES,))
        _logger.info("real_estate_checks: cheque identity index created.")

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('sale_contract_id', 'rental_contract_id', 'sale_installment_id')
    def _compute_project_from_source(self):
        """0.1 assigned nothing when a cheque had no contract at all.

        Both fields are stored computes, so a standalone cheque — perfectly
        legitimate, and what the Deposit Workbench creates — left them
        unassigned. The `else` branch is the whole fix; the
        `'project_id' in ..._fields` guards 0.1 carried were noise, since those
        fields have existed on the contract since Developer M1.

        Read with `sudo`: a Treasury Officer is not necessarily a Developer
        user, and this compute fires whenever a cheque is touched. Without it,
        presenting a cheque that belongs to a sale contract raised
        `AccessError` on `realestate.sale.contract` — found in a browser
        driving the real deposit flow. Only the project and unit the cheque
        already names are read, and both are stored on the cheque anyway.
        """
        for rec in self.sudo():
            project = property_ = False
            if rec.sale_contract_id:
                project = rec.sale_contract_id.project_id
                property_ = rec.sale_contract_id.property_id
            elif rec.rental_contract_id:
                property_ = rec.rental_contract_id.property_id
            rec.project_id = project
            rec.property_id = property_

    @api.depends('amount', 'currency_id', 'allocation_ids.allocated_amount',
                 'allocation_ids.state')
    def _compute_allocation_totals(self):
        for rec in self:
            live = rec.allocation_ids.filtered(lambda a: a.state == 'active')
            allocated = sum(live.mapped('allocated_amount'))
            currency = rec.currency_id or self.env.company.currency_id
            rec.allocation_count = len(live)
            rec.allocated_amount = allocated
            rec.unapplied_amount = currency.round((rec.amount or 0.0) - allocated)

    @api.depends('presentation_ids', 'presentation_ids.state',
                 'presentation_ids.attempt')
    def _compute_presentation_stats(self):
        for rec in self:
            attempts = rec.presentation_ids.sorted('attempt')
            rec.presentation_count = len(attempts)
            open_ = attempts.filtered(
                lambda p: p.state in ('presented', 'clearing'))
            rec.current_presentation_id = (open_[-1:] or attempts[-1:]) or False

    @api.depends('bounce_ids')
    def _compute_bounce_stats(self):
        for rec in self:
            rec.bounce_count = len(rec.bounce_ids)

    @api.depends('replaces_check_id', 'replaces_check_id.root_check_id',
                 'replaces_check_id.replacement_generation')
    def _compute_lineage(self):
        for rec in self:
            parent = rec.replaces_check_id
            if not parent:
                rec.root_check_id = rec._origin or rec
                rec.replacement_generation = 0
            else:
                rec.root_check_id = parent.root_check_id or parent
                rec.replacement_generation = parent.replacement_generation + 1

    @api.depends('due_date')
    def _compute_maturity_bucket(self):
        """The stored bucket, kept apart from the live figures below.

        It shared `_compute_maturity` with four fields that are not stored, so
        merely reading the cheque list (which shows them) recomputed the bucket
        too and wrote it inside a read-only request: Odoo logged "cannot execute
        UPDATE in a read-only transaction" and ran the request again with write
        access. The bucket is refreshed nightly by the maturity cron, as its
        help says.
        """
        today = fields.Date.context_today(self)
        for rec in self:
            rec.maturity_bucket = (
                maturity_bucket_for((rec.due_date - today).days)
                if rec.due_date else False)

    @api.depends('due_date', 'state')
    def _compute_maturity(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.due_date:
                rec.days_to_due = 0
                rec.is_due = False
                rec.is_overdue_for_deposit = False
                rec.is_stale = False
                continue
            delta = (rec.due_date - today).days
            company = rec.company_id or self.env.company
            rec.days_to_due = delta
            rec.is_due = delta <= 0
            rec.is_overdue_for_deposit = bool(
                rec.state in CHECK_ON_HAND
                and delta < -(company.check_overdue_presentation_days or 0))
            stale_days = company.check_stale_days or 0
            # Zero means "no legal duration configured", and this module
            # refuses to invent one — cheque validity is localisation's call.
            rec.is_stale = bool(stale_days and -delta > stale_days
                                and rec.state not in CHECK_TERMINAL)

    def _search_is_due(self, operator, value):
        if operator not in ('=', '!='):
            raise UserError(_("Unsupported operator on 'Is Due'."))
        today = fields.Date.context_today(self)
        want_due = bool(value) if operator == '=' else not bool(value)
        return [('due_date', '<=', today)] if want_due else [('due_date', '>', today)]

    @api.depends('payment_id', 'payment_id.state', 'payment_id.is_matched',
                 'payment_id.move_id.state',
                 'payment_id.reconciled_invoice_ids')
    def _compute_accounting_state(self):
        """Derived entirely from Odoo. This is the heart of Rule 3.

        Odoo 18 gives two honest signals and both are used:

        * `payment.state` — `in_process` once posted, `paid` once the liquidity
          line has no residual left, i.e. once a bank transaction has matched
          it (`account_payment.py::_compute_state`);
        * `payment.is_matched` — Odoo's own "matched with a bank statement".

        Note what is deliberately *not* used: `invoice.payment_state`. On
        Community, `_get_invoice_in_payment_state()` returns `'paid'`, so an
        invoice reads as paid the moment a payment is reconciled against it,
        bank or no bank. Deriving cheque clearance from the invoice would
        therefore report cash that has not arrived — on exactly the
        configuration most customers run.
        """
        for rec in self:
            payment = rec.payment_id
            if not payment:
                rec.accounting_state = 'no_payment'
                rec.is_bank_matched = False
                continue
            move = payment.move_id
            if payment.state in ('canceled', 'rejected') or (
                    move and move.state == 'cancel'):
                rec.accounting_state = 'reversed'
                rec.is_bank_matched = False
                continue
            matched = bool(payment.is_matched)
            rec.is_bank_matched = matched
            if matched:
                # `is_matched`, not `state == 'paid'` — see `_is_cash_confirmed`
                # for why the two are different and why only one of them is a
                # statement about the bank.
                rec.accounting_state = 'reconciled'
            elif payment.reconciled_invoice_ids:
                rec.accounting_state = 'in_payment'
            else:
                rec.accounting_state = 'payment_registered'

    # ==================================================================
    # Constraints
    # ==================================================================
    @api.constrains('amount')
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_("Check amount must be positive."))

    @api.constrains('due_date', 'issue_date')
    def _check_dates(self):
        for rec in self:
            if rec.due_date and rec.issue_date and rec.due_date < rec.issue_date:
                raise ValidationError(_(
                    "Due date (%(due)s) must be on or after the issue date (%(issue)s).",
                    due=rec.due_date, issue=rec.issue_date,
                ))

    @api.constrains('company_id', 'sale_contract_id', 'journal_id', 'deposit_id')
    def _check_company_consistency(self):
        """M29. 0.1 had no company plumbing at all: a Company A cheque could be
        deposited into Company B's bank journal, with nothing to stop it.

        Read with `sudo`. A constraint is a data-integrity check, not a
        permission check, and it fires on every write — including the write
        that presents a deposit. Reading the contract's company as the user
        made presenting a cheque require Developer rights, which a Treasury
        Officer does not have. Nothing is exposed: the comparison yields a
        yes/no, and the message names companies the user can already see.
        """
        for rec in self.sudo():
            for field_name, label in (('sale_contract_id', _('sale contract')),
                                      ('journal_id', _('bank journal')),
                                      ('deposit_id', _('deposit slip'))):
                target = rec[field_name]
                if target and target.company_id and target.company_id != rec.company_id:
                    raise ValidationError(_(
                        "Cheque %(check)s belongs to %(own)s but its %(what)s "
                        "belongs to %(other)s. A cheque cannot cross companies.",
                        check=rec.name, own=rec.company_id.display_name,
                        what=label, other=target.company_id.display_name))

    @api.constrains('replaces_check_id', 'replacement_check_id')
    def _check_no_replacement_cycle(self):
        """M13 — no self-replacement, no cycles."""
        for rec in self:
            if rec.replaces_check_id == rec or rec.replacement_check_id == rec:
                raise ValidationError(_("A cheque cannot replace itself."))
            seen = set()
            node = rec.replaces_check_id
            while node:
                if node.id in seen or node == rec:
                    raise ValidationError(_(
                        "Replacement chain for %s forms a cycle.") % rec.name)
                seen.add(node.id)
                node = node.replaces_check_id

    @api.constrains('allocation_ids', 'amount')
    def _check_not_over_allocated(self):
        """SUM(active allocations) <= face value. Always. (M3)"""
        for rec in self:
            currency = rec.currency_id or rec.company_id.currency_id
            live = rec.allocation_ids.filtered(lambda a: a.state == 'active')
            allocated = sum(live.mapped('allocated_amount'))
            if currency.compare_amounts(allocated, rec.amount) > 0:
                raise ValidationError(_(
                    "Cheque %(check)s is for %(amount)s but %(allocated)s has "
                    "been allocated to obligations. A cheque cannot pay more "
                    "than it is worth.",
                    check=rec.name,
                    amount=currency.format(rec.amount),
                    allocated=currency.format(allocated)))

    # ==================================================================
    # Create / write
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                company_id = vals.get('company_id') or self.env.company.id
                vals['name'] = self.env['ir.sequence'].with_company(
                    company_id).next_by_code('realestate.check') or 'CHK/NEW'
        for vals in vals_list:
            self._assert_clearance_is_earned(vals)
        checks = super().create(vals_list)
        checks._sync_simple_allocation()
        checks._open_custody()
        return checks

    def write(self, vals):
        self._assert_clearance_is_earned(vals)
        res = super().write(vals)
        if {'sale_installment_id', 'rental_payment_id'} & set(vals):
            self._sync_simple_allocation()
        return res

    @api.model
    def _assert_clearance_is_earned(self, vals):
        """A cheque clears because the bank honoured it, not because somebody
        typed it.

        `_sync_clearance_from_accounting` says it is the only place that writes
        `cleared`, and `action_mark_cleared` refuses to assert cash Odoo has
        not confirmed. Neither statement held at the ORM boundary: the Checks
        User role has write access, so `write({'state': 'cleared'})` skipped
        the treasurer check, the at-bank check, the payment check and
        `is_matched` in one call -- and a cleared cheque is counted as
        collected money by the deposit and contract roll-ups.

        `env.su` stays exempt for the cron, migrations and data loads.
        """
        if vals.get('state') != 'cleared':
            return
        if self.env.su or \
                self.env.context.get(CLEARANCE_CTX) is CLEARANCE_TOKEN:
            return
        raise AccessError(_(
            "A cheque is not marked cleared by writing to it. Confirm its "
            "deposit slip and reconcile the bank statement; the cheque then "
            "clears from Accounting, which is the only thing that can say "
            "the money arrived."))

    def _sync_simple_allocation(self):
        """Keep the easy case easy (M3's compatibility clause).

        Naming `sale_installment_id` on a cheque must go on meaning "this
        cheque pays that instalment" — that is how the bulk wizard, the API and
        every existing script work. So the legacy field is mirrored into a real
        allocation. The allocation is the engine; the field is a shortcut into
        it, and `allocation.py` mirrors the value back the other way when a
        cheque ends up with exactly one active allocation.
        """
        Allocation = self.env['realestate.check.allocation']
        for rec in self:
            if not (rec.sale_installment_id or rec.rental_payment_id):
                continue
            if rec.allocation_ids.filtered(lambda a: a.state == 'active'):
                continue
            Allocation.with_context(skip_allocation_mirror=True).create({
                'check_id': rec.id,
                'sale_installment_id': rec.sale_installment_id.id or False,
                'rental_payment_id': rec.rental_payment_id.id or False,
                'allocated_amount': rec.amount,
                'state': 'active',
            })

    def _open_custody(self):
        """Every cheque starts somewhere. Record it rather than leave a blank.

        Written with elevated rights on purpose. Registering a cheque is a
        Checks User's job, but custody movements are Treasury's to create —
        and a clerk recording a cheque they are holding must not be refused
        because of it. The row records the clerk as custodian, so the audit
        trail is unaffected.
        """
        Custody = self.env['realestate.check.custody'].sudo()
        for rec in self:
            if rec.custody_ids:
                continue
            Custody.create({
                'check_id': rec.id,
                'to_custodian_id': self.env.user.id,
                'to_location_id': rec.company_id.check_default_location_id.id or False,
                'reason': 'receipt',
                'date': rec.received_date or fields.Date.context_today(rec),
                'note': _('Cheque registered.'),
            })

    # ==================================================================
    # Permission helper (M31 — buttons disappearing is not security)
    # ==================================================================
    def _assert_group(self, group_xmlid, what):
        if not self.env.user.has_group(group_xmlid):
            group = self.env.ref(group_xmlid, raise_if_not_found=False)
            raise AccessError(_(
                "You are not allowed to %(what)s. That operation needs the "
                "'%(group)s' access level.",
                what=what, group=group.display_name if group else group_xmlid))

    # ==================================================================
    # Lifecycle actions
    # ==================================================================
    def action_register(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft checks can be registered."))
            if (rec.company_id.check_require_full_allocation
                    and not rec.allocation_ids.filtered(
                        lambda a: a.state == 'active')):
                raise UserError(_(
                    "Cheque %s has nothing allocated to it, and this company "
                    "requires full allocation.") % rec.name)
            rec.state = 'registered'
        return True

    def action_back_to_draft(self):
        for rec in self:
            if rec.state not in ('registered', 'cancelled'):
                raise UserError(_(
                    "Can only return to draft from Registered or Cancelled."))
            if rec.deposit_id:
                raise UserError(_("Remove the check from its deposit slip first."))
            reinstating = rec.state == 'cancelled'
            rec.state = 'draft'
            if reinstating:
                rec._reinstate_allocations()
        return True

    def _reinstate_allocations(self):
        """Give a reinstated cheque back the obligations its cancellation took.

        Cancelling a cheque cancels its active allocations. Bringing the cheque
        back without them left it silently pointing at nothing -- and, under a
        full-allocation policy, unregistrable for no visible reason.

        Only the allocations released by that cancellation are candidates: they
        were cancelled together, in one transaction, so they share the latest
        `write_date` among the cheque's cancelled allocations. One cancelled
        deliberately at some earlier point is left alone. Each is reactivated
        only if it is still valid -- the obligation still exists and is not
        now covered by other paper -- and whatever cannot be is named on the
        cheque rather than dropped.
        """
        self.ensure_one()
        cancelled = self.allocation_ids.filtered(lambda a: a.state == 'cancelled')
        if not cancelled:
            return True
        latest = max(cancelled.mapped('write_date'))
        candidates = cancelled.filtered(lambda a: a.write_date == latest)
        refused = []
        for allocation in candidates:
            obligation = (allocation.sudo().sale_installment_id
                          or allocation.sudo().rental_payment_id)
            if (allocation.sale_installment_id and obligation.is_cancelled) or (
                    allocation.rental_payment_id
                    and obligation.state == 'cancelled'):
                refused.append(_("%(obligation)s: the obligation is cancelled.",
                                 obligation=allocation.obligation_ref))
                continue
            try:
                with self.env.cr.savepoint():
                    allocation.action_activate()
                    self.env.flush_all()
            except (UserError, ValidationError) as err:
                refused.append('%s: %s' % (allocation.obligation_ref, err))
        if refused:
            items = Markup('').join(Markup('<li>%s</li>') % line for line in refused)
            self.message_post(body=Markup('<p>%s</p><ul>%s</ul>') % (_(
                "Cheque reinstated, but these allocations released when it "
                "was cancelled could not be restored and remain cancelled. "
                "Allocate the cheque again if it should still cover them:"
            ), items))
        return True

    def action_cancel(self):
        """M14 — cancellation is auditable and state-sensitive.

        A cheque at the bank cannot be cancelled by deciding it is cancelled;
        the bank and the ledger have to be resolved first. A cleared cheque
        cannot be cancelled at all — the money arrived, and that is a fact.
        Correcting it is a refund or a credit note, which is Accounting's job.
        """
        self._assert_group('real_estate_checks.group_checks_treasurer',
                           _('cancel a cheque'))
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.state == 'cleared':
                raise UserError(_(
                    "Cheque %s has cleared: the money arrived. An instrument "
                    "that cleared is not cancellable — issue a refund or a "
                    "credit note through Accounting instead.") % rec.name)
            if rec.state in CHECK_AT_BANK:
                raise UserError(_(
                    "Cheque %s is at the bank. Resolve the presentation first "
                    "(record the bounce, or wait for clearance), then cancel."
                ) % rec.name)
            if rec.state in ('replaced', 'cancelled', 'returned'):
                raise UserError(_(
                    "Cheque %(name)s is already %(state)s.",
                    name=rec.name, state=rec.state))
            was_bounced = rec.state == 'bounced'
            rec.write({'state': 'cancelled', 'cancelled_date': today})
            rec.allocation_ids.filtered(
                lambda a: a.state == 'active').action_cancel()
            if was_bounced:
                rec._resolve_pending_bounces('cancelled')
            rec.message_post(body=_("Cheque cancelled."))
        return True

    def action_return_to_customer(self):
        """M15 — settlement and contract cancellation both need this.

        Physically handing the paper back is a different act from cancelling
        it, and the two must be distinguishable years later.
        """
        self._assert_group('real_estate_checks.group_checks_treasurer',
                           _('return a cheque to the customer'))
        Custody = self.env['realestate.check.custody']
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.state not in CHECK_ON_HAND + ('bounced',):
                raise UserError(_(
                    "Cheque %(name)s is %(state)s — only a cheque on hand, or "
                    "a bounced one, can be handed back.",
                    name=rec.name, state=rec.state))
            was_bounced = rec.state == 'bounced'
            rec.write({'state': 'returned', 'cancelled_date': today})
            rec.allocation_ids.filtered(
                lambda a: a.state == 'active').action_cancel()
            if was_bounced:
                # The instrument is withdrawn; the bounce is no longer waiting
                # on a re-presentation or a replacement.
                rec._resolve_pending_bounces('cancelled')
            # Through `transfer`, which stamps the movement with the current
            # time. A plain date is midnight, so any earlier movement on the
            # same day sorted after it and the cheque kept showing its old
            # custodian and location.
            Custody.transfer(
                rec, to_custodian=False, to_location=False,
                reason='return_to_customer',
                note=_('Handed back to the drawer.'))
            rec.message_post(body=_("Cheque returned to the customer."))
        return True

    def _resolve_pending_bounces(self, resolution, replacement=None):
        """Close the bounce still waiting on this cheque.

        A bounce stays `pending` until something decides the instrument's fate.
        Cancelling, handing back or replacing a bounced cheque is that decision,
        whichever screen it was taken from; leaving the bounce pending kept it
        in the follow-up queue and the reminder cron for ever.
        """
        today = fields.Date.context_today(self)
        for rec in self:
            pending = rec.bounce_ids.filtered(
                lambda b: b.resolution == 'pending')
            if not pending:
                continue
            vals = {'resolution': resolution, 'resolved_date': today}
            if replacement:
                vals['replacement_check_id'] = replacement.id
            pending.write(vals)
        return True

    def action_open_bounce_wizard(self):
        self.ensure_one()
        if self.state not in CHECK_AT_BANK:
            raise UserError(_(
                "Only a cheque that is at the bank can bounce. "
                "%(name)s is %(state)s.", name=self.name, state=self.state))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Register Bounce'),
            'res_model': 'realestate.check.bounce.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_check_id': self.id},
        }

    # ------------------------------------------------------------------
    # Clearance (M8)
    # ------------------------------------------------------------------
    def action_mark_cleared(self):
        """0.1's entry point, kept — but it no longer invents clearance.

        In 0.1 this created a payment and wrote `state = 'cleared'`
        unconditionally. It now refuses to assert cash that Odoo has not
        confirmed, and delegates to the same synchronisation the cron uses. A
        treasurer who presses it on an unreconciled cheque is told what is
        missing rather than shown a green badge.
        """
        self._assert_group('real_estate_checks.group_checks_treasurer',
                           _('clear a cheque'))
        for rec in self:
            if rec.state not in CHECK_AT_BANK:
                raise UserError(_(
                    "Check %s is not at the bank — clear only presented checks.",
                    rec.name))
            if not rec.payment_id:
                raise UserError(_(
                    "Cheque %s has no payment. Confirm its deposit slip first: "
                    "that is what registers the money with Accounting."
                ) % rec.name)
            if not rec._is_cash_confirmed():
                raise UserError(_(
                    "Cheque %(check)s cannot be marked cleared. Its payment "
                    "%(payment)s is '%(state)s' and has not been matched "
                    "against a bank transaction.\n\n"
                    "Reconcile the bank statement in Accounting and the cheque "
                    "will clear by itself. Marking it cleared here would "
                    "record cash that has not arrived.",
                    check=rec.name, payment=rec.payment_id.display_name,
                    state=rec.payment_id.state))
        self._sync_clearance_from_accounting()
        return True

    def _candidate_obligations(self):
        """Obligations this cheque could plausibly pay, oldest first.

        Returns `(record, outstanding_and_unsecured)` pairs. "Outstanding" nets
        off what Odoo says has been paid; "unsecured" nets off what other live
        cheques already cover, so the allocation wizard never proposes covering
        the same instalment twice.
        """
        self.ensure_one()
        pairs = []
        if self.sale_contract_id:
            # Same reasoning as `_compute_project_from_source`: reading which
            # obligations a cheque could pay is Checks' own bookkeeping.
            for inst in self.sudo().sale_contract_id.installment_ids.filtered(
                    lambda i: not i.is_cancelled).sorted(
                        key=lambda i: (i.date_due or fields.Date.today(), i.id)):
                outstanding = (inst.current_amount or 0.0) - (inst.paid_amount or 0.0)
                free = outstanding - (inst.secured_by_checks_amount or 0.0)
                if self.currency_id.compare_amounts(free, 0) > 0:
                    pairs.append((inst, self.currency_id.round(free)))
        if self.rental_contract_id:
            payments = self.env['realestate.contract.payment'].search([
                ('contract_id', '=', self.rental_contract_id.id),
                ('state', '!=', 'cancelled'),
            ], order='date_due')
            for payment in payments:
                if payment.state == 'paid':
                    continue
                free = (payment.amount_total or 0.0) - (
                    payment.secured_by_checks_amount or 0.0)
                if self.currency_id.compare_amounts(free, 0) > 0:
                    pairs.append((payment, self.currency_id.round(free)))
        return pairs

    def _invoices_to_settle(self):
        """The posted invoices behind this cheque, in due order.

        Sourced from the allocations, so a cheque split across three
        instalments returns three invoices and one covering none returns
        nothing. 0.1 looked at a single instalment's `move_id` and had no
        answer for either case.

        Reversed invoices are excluded: a credit-noted invoice is not an
        obligation, and reconciling a payment against one would settle a
        document that has already been cancelled.
        """
        self.ensure_one()
        Move = self.env['account.move']
        # `sudo` on the READ of the obligation link only. Presenting a cheque
        # must not require Developer rights; the invoices themselves are then
        # reconciled with the user's own accounting rights, which is where the
        # real permission lives.
        allocations = self.sudo().allocation_ids.filtered(
            lambda a: a.state == 'active').sorted('obligation_due_date')
        moves = Move.browse()
        for allocation in allocations:
            move = allocation.move_id
            if (move and move.state == 'posted'
                    and move.payment_state != 'reversed'
                    and move not in moves):
                moves |= move
        return moves

    def _allocations_to_settle(self):
        """`(move, amount)` in due order: what this cheque pays, and how much.

        `_invoices_to_settle` deliberately returns only the moves, which is
        all the clearance sync needs. Reconciliation needs the amounts too:
        a cheque split 400/600 across two obligations must settle them 400 and
        600, not hand the whole instrument to whichever invoice came first.

        Several allocations can point at one move -- two instalments invoiced
        together -- so the amounts are summed per move.
        """
        self.ensure_one()
        allocations = self.sudo().allocation_ids.filtered(
            lambda a: a.state == 'active').sorted('obligation_due_date')
        ordered = []
        totals = {}
        for allocation in allocations:
            move = allocation.move_id
            if not (move and move.state == 'posted'
                    and move.payment_state != 'reversed'):
                continue
            if move.id not in totals:
                ordered.append(move)
                totals[move.id] = 0.0
            totals[move.id] += allocation.allocated_amount
        return [(move, totals[move.id]) for move in ordered]

    def _is_cash_confirmed(self):
        """The single definition of "the bank has honoured this cheque".

        The test is `payment.is_matched`, and **not** `payment.state == 'paid'`.
        That distinction is the entire point of this module, and Odoo 18 makes
        it easy to get wrong: `_compute_state` promotes a payment to `paid` as
        soon as every invoice it is reconciled against reads `paid` —

            if payment.state == 'in_process' and payment.reconciled_invoice_ids
               and all(inv.payment_state == 'paid' for inv in ...):
                payment.state = 'paid'

        — and on Community `_get_invoice_in_payment_state()` returns `'paid'`,
        so an invoice reads as paid the moment a payment is reconciled against
        it, bank or no bank. Keying clearance off `state` would therefore mark
        a cheque cleared the instant it was *presented*, which is exactly the
        error 0.1 made by hand.

        `is_matched` is Odoo's own "matched with a bank statement", and it is
        honest in all three configurations
        (`account_payment.py::_compute_reconciliation_status`):

        * outstanding-receipts journal — true only once the liquidity line has
          no residual left, i.e. once a bank transaction really matched it;
        * payment posted straight into the bank account — true immediately,
          which is correct, because that configuration has no separate
          matching step to wait for;
        * no outstanding account at all — falls back to `state == 'paid'`,
          which in that configuration is the only signal there is.
        """
        self.ensure_one()
        payment = self.payment_id
        if not payment:
            return False
        if payment.state in ('draft', 'canceled', 'rejected'):
            return False
        move = payment.move_id
        if move and move.state == 'cancel':
            return False
        return bool(payment.is_matched)

    def _sync_clearance_from_accounting(self):
        """Pull clearance from Odoo. Idempotent, batch-safe, re-runnable.

        This is the *only* place `state = 'cleared'` is written. The manual
        action, the cron and the deposit all come through here, so there is no
        route by which a cheque clears on a weaker test.
        """
        today = fields.Date.context_today(self)
        cleared = self.browse()
        for rec in self:
            if rec.state not in CHECK_AT_BANK or not rec._is_cash_confirmed():
                continue
            payment = rec.payment_id
            date = (payment.move_id.date if payment.move_id and payment.move_id.date
                    else today)
            # Bound to a name rather than chained, so this stays the one
            # visible writer of `cleared`. `test_only_one_method_writes_cleared`
            # parses for `<name>.write({...})` and a chained receiver would
            # hide the write from the very invariant that guards it.
            authorised = rec.with_context(
                **{CLEARANCE_CTX: CLEARANCE_TOKEN})
            authorised.write({'state': 'cleared', 'cleared_date': date})
            rec.current_presentation_id.write({
                'state': 'cleared', 'cleared_date': date})
            rec.message_post(body=_(
                "Cleared. Payment %s is matched against the bank."
            ) % payment.display_name)
            cleared |= rec
        cleared.mapped('deposit_id')._recompute_final_state()
        return cleared

    # ------------------------------------------------------------------
    # Replacement (M13) and custody (M4) entry points
    # ------------------------------------------------------------------
    def action_open_replacement_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Replace Cheque'),
            'res_model': 'realestate.check.replace.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_original_check_id': self.id},
        }

    def action_view_lineage(self):
        """The whole replacement chain, in one indexed query."""
        self.ensure_one()
        root = self.root_check_id or self
        return {
            'type': 'ir.actions.act_window',
            'name': _('Replacement Chain — %s') % root.name,
            'res_model': 'realestate.check',
            'view_mode': 'list,form',
            'domain': ['|', ('id', '=', root.id),
                       ('root_check_id', '=', root.id)],
        }

    def action_open_allocate_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Allocate Cheque'),
            'res_model': 'realestate.check.allocate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_check_id': self.id},
        }

    def action_open_custody_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Transfer Custody'),
            'res_model': 'realestate.check.custody.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_check_ids': [(6, 0, self.ids)]},
        }

    # ==================================================================
    # Crons (M34) — all idempotent, batch-safe, company-safe
    # ==================================================================
    @api.model
    def _cron_sync_accounting(self):
        """M8's safety net.

        Reconciliation happens in Accounting, where nothing calls us. The
        payment's `state` compute does fire on reconciliation, but relying on
        another module's recompute cascade to drive a treasury state is
        fragile, so a nightly sweep confirms it. Re-running changes nothing
        that is already right.
        """
        checks = self.search([('state', 'in', list(CHECK_AT_BANK)),
                              ('payment_id', '!=', False)])
        cleared = checks._sync_clearance_from_accounting()
        if cleared:
            _logger.info("real_estate_checks: %s cheque(s) cleared by bank "
                         "reconciliation.", len(cleared))
        return True

    @api.model
    def _cron_refresh_maturity(self):
        """`maturity_bucket` is stored so the forecast can group in SQL, which
        makes it date-dependent stored data and so needs a daily nudge."""
        stale = self.search([('state', 'not in', list(CHECK_TERMINAL)),
                             ('due_date', '!=', False)])
        stale._compute_maturity_bucket()
        return True

    @api.model
    def _cron_due_soon(self):
        """Flag maturing cheques to their custodian. Thresholds are per company
        and a company silences it by setting the window to zero."""
        today = fields.Date.context_today(self)
        for company in self.env['res.company'].search([]):
            window = company.check_due_soon_days
            if not window:
                continue
            horizon = fields.Date.add(today, days=window)
            self.search([
                ('company_id', '=', company.id),
                ('state', 'in', list(CHECK_ON_HAND)),
                ('due_date', '>=', today),
                ('due_date', '<=', horizon),
            ])._ensure_activity(
                'due-soon', _('Cheque matures on %s — prepare for presentation.'))
        return True

    @api.model
    def _cron_overdue_presentation(self):
        on_hand = self.search([('state', 'in', list(CHECK_ON_HAND))])
        on_hand.filtered('is_overdue_for_deposit')._ensure_activity(
            'overdue', _('Cheque matured on %s and is still on hand.'))
        return True

    def _ensure_activity(self, key, template):
        """One open activity per cheque per alert. Re-running a cron does not
        pile up duplicates (M27: do not spam activities)."""
        if not self:
            return True
        Activity = self.env['mail.activity']
        todo = self.env.ref('mail.mail_activity_data_todo',
                            raise_if_not_found=False)
        if not todo:
            return True
        model_id = self.env['ir.model']._get_id('realestate.check')
        # Deduplicated per alert, not per cheque. Any open To-Do used to count,
        # so the due-soon reminder silenced the later, more urgent overdue one.
        # The key is the `[key]` prefix every alert's summary carries.
        existing = set(Activity.search([
            ('res_model_id', '=', model_id),
            ('res_id', 'in', self.ids),
            ('activity_type_id', '=', todo.id),
            ('summary', '=like', '[%s]%%' % key),
        ]).mapped('res_id'))
        for rec in self:
            if rec.id in existing:
                continue
            user = rec.custodian_id or rec.create_uid
            Activity.create({
                'res_model_id': model_id,
                'res_id': rec.id,
                'activity_type_id': todo.id,
                'summary': '[%s] %s' % (key, rec.name),
                'note': template % rec.due_date,
                'date_deadline': rec.due_date,
                'user_id': user.id,
            })
        return True
