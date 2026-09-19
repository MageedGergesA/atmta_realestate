# -*- coding: utf-8 -*-
"""M6 / M7 — presentation to the bank, and the accounting moment.

### The division of labour

`realestate.check.deposit` is the **physical** batch: this stack of paper, taken
to this bank, on this day, prepared by this person and confirmed by that one. It
keeps its 0.1 identity because production slips have ids (Rule 5).

Odoo owns the **accounting** batch. Confirming a deposit raises one inbound
`account.payment` per cheque and, when Enterprise's `account.batch.payment` is
present, groups them so a single bank line reconciles the lot. When it is not
present the payments still exist and still reconcile individually — this module
is LGPL-3 and will not take a hard dependency on an `OEEL-1` module in order to
work.

### Why the payment is created here and not at clearance

0.1 created the payment when a treasurer declared the cheque cleared. That meant
a portfolio of cheques sitting at the bank was invisible to Accounting, and
"cleared" was an assertion rather than an observation.

Confirming a deposit is a real, dated, externally verifiable event: the paper
left the building. That is the moment the receivable becomes *money on its way*,
which in Odoo is exactly what an inbound payment against an Outstanding Receipts
account expresses. Clearance then stops being something we assert and becomes
something we read — see `check._sync_clearance_from_accounting`.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .check_states import CHECK_DEPOSITABLE, DEPOSIT_STATE

_logger = logging.getLogger(__name__)

#: Kept for anything that imported it from 0.1.
DEPOSIT_STATES = DEPOSIT_STATE


class CheckDeposit(models.Model):
    """One physical trip to the bank with a batch of cheques."""
    _name = 'realestate.check.deposit'
    _description = 'Bank Deposit Slip'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'deposit_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index=True,
    )
    deposit_date = fields.Date(
        string='Deposit Date', default=fields.Date.context_today,
        required=True, tracking=True, index=True,
    )
    journal_id = fields.Many2one(
        'account.journal', string='Bank Journal', required=True, tracking=True,
        check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
    )
    payment_method_line_id = fields.Many2one(
        'account.payment.method.line', string='Payment Method',
        compute='_compute_payment_method_line', store=True, readonly=False,
        domain="[('id', 'in', available_payment_method_line_ids)]",
        help="Which inbound method the resulting payments use. Defaults to the "
             "company's configured cheque method, falling back to the "
             "journal's. This is what decides whether the money lands in "
             "Outstanding Receipts — so that 'presented' and 'cleared' can "
             "differ at all — or straight in the bank balance.")
    available_payment_method_line_ids = fields.Many2many(
        'account.payment.method.line',
        compute='_compute_payment_method_line')
    bank_slip_ref = fields.Char(string='Bank Slip #', tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
        help="Every cheque on a slip must be in this currency. Mixed-currency "
             "batches are refused rather than silently summed.")

    prepared_by_id = fields.Many2one(
        'res.users', string='Prepared By', readonly=True, copy=False,
        default=lambda self: self.env.user, tracking=True)
    confirmed_by_id = fields.Many2one(
        'res.users', string='Confirmed By', readonly=True, copy=False,
        tracking=True)
    confirmed_on = fields.Datetime(readonly=True, copy=False)

    check_ids = fields.One2many(
        'realestate.check', 'deposit_id', string='Checks',
    )
    presentation_ids = fields.One2many(
        'realestate.check.presentation', 'deposit_id', string='Presentations')
    payment_ids = fields.One2many(
        'account.payment', 'realestate_deposit_id', string='Payments',
        readonly=True)
    payment_count = fields.Integer(compute='_compute_totals', store=True)

    has_live_payments = fields.Boolean(
        compute='_compute_has_live_payments',
        help="A posted payment still stands on this slip, so it cannot be "
             "cancelled. Drives the Cancel button; `action_cancel` enforces "
             "the same rule.")

    batch_payment_id = fields.Many2one(
        'account.batch.payment', string='Odoo Batch Payment', readonly=True,
        copy=False, ondelete='set null',
        help="Set only when Odoo's Batch Payment module is installed. ATMTA "
             "owns the physical batch; that record owns the accounting one.")

    check_count = fields.Integer(compute='_compute_totals', store=True)
    total_amount = fields.Monetary(
        string='Total Amount', compute='_compute_totals', store=True,
    )
    total_cleared = fields.Monetary(
        string='Cleared', compute='_compute_totals', store=True,
    )
    total_bounced = fields.Monetary(
        string='Bounced', compute='_compute_totals', store=True,
    )
    total_pending = fields.Monetary(
        string='Awaiting Clearance', compute='_compute_totals', store=True,
    )

    state = fields.Selection(
        DEPOSIT_STATE, default='draft', tracking=True, required=True,
        copy=False, index=True,
    )
    notes = fields.Html()

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('journal_id', 'company_id')
    def _compute_payment_method_line(self):
        Line = self.env['account.payment.method.line']
        for rec in self:
            lines = (rec.journal_id._get_available_payment_method_lines('inbound')
                     if rec.journal_id else Line)
            rec.available_payment_method_line_ids = lines
            configured = rec.company_id.check_payment_method_line_id
            if configured and configured in lines:
                rec.payment_method_line_id = configured
            elif rec.payment_method_line_id and rec.payment_method_line_id in lines:
                pass  # an explicit choice is respected
            else:
                rec.payment_method_line_id = lines[:1]

    @api.depends('check_ids', 'check_ids.amount', 'check_ids.state',
                 'check_ids.is_bank_matched', 'payment_ids')
    def _compute_totals(self):
        for rec in self:
            checks = rec.check_ids
            rec.check_count = len(checks)
            rec.payment_count = len(rec.payment_ids)
            rec.total_amount = sum(checks.mapped('amount'))
            rec.total_cleared = sum(
                c.amount for c in checks if c.state == 'cleared')
            rec.total_bounced = sum(
                c.amount for c in checks if c.state == 'bounced')
            rec.total_pending = sum(
                c.amount for c in checks
                if c.state in ('deposited', 'in_clearing'))

    # ==================================================================
    # Constraints
    # ==================================================================
    @api.constrains('journal_id', 'company_id')
    def _check_journal_company(self):
        for rec in self:
            journal_company = rec.journal_id.company_id
            if journal_company and journal_company != rec.company_id:
                raise ValidationError(_(
                    "Deposit %(name)s is in %(own)s but its journal belongs to "
                    "%(other)s.", name=rec.name,
                    own=rec.company_id.display_name,
                    other=journal_company.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                company_id = vals.get('company_id') or self.env.company.id
                vals['name'] = self.env['ir.sequence'].with_company(
                    company_id).next_by_code(
                        'realestate.check.deposit') or 'DEP/NEW'
        return super().create(vals_list)

    # ==================================================================
    # Validation of the batch (M6)
    # ==================================================================
    def _validate_batch(self):
        """Everything that must be true before paper leaves the building.

        0.1 checked one thing: that each cheque was `draft` or `registered`. It
        let a Company A cheque go into Company B's journal, mixed currencies
        into a single meaningless total, and presented cheques years before
        their due date.

        Every failure is collected and reported together — a treasurer fixing a
        50-cheque batch should not have to discover the problems one press at a
        time.
        """
        self.ensure_one()
        if not self.check_ids:
            raise UserError(_("Add at least one check before confirming."))

        company = self.company_id
        today = fields.Date.context_today(self)
        problems = []

        for check in self.check_ids:
            label = '%s (%s)' % (check.name, check.check_number)

            if check.company_id != company:
                problems.append(_(
                    "%(check)s belongs to %(other)s, not %(own)s.",
                    check=label, other=check.company_id.display_name,
                    own=company.display_name))
                continue

            if check.currency_id != self.currency_id:
                problems.append(_(
                    "%(check)s is in %(their)s; this slip is in %(ours)s. "
                    "Mixed-currency batches are not grouped — make one slip "
                    "per currency.",
                    check=label, their=check.currency_id.name,
                    ours=self.currency_id.name))
                continue

            if check.state not in CHECK_DEPOSITABLE:
                problems.append(_(
                    "%(check)s is '%(state)s'; only a registered cheque can be "
                    "presented.", check=label, state=check.state))
                continue

            if check.presentation_ids.filtered(
                    lambda p: p.state in ('presented', 'clearing')):
                problems.append(_(
                    "%(check)s already has an open presentation at a bank.",
                    check=label))
                continue

            # ---- maturity policy (M5) ----
            if check.due_date and check.due_date > today:
                if not company.check_allow_early_deposit:
                    problems.append(_(
                        "%(check)s matures on %(due)s. This company does not "
                        "allow presenting a post-dated cheque early.",
                        check=label, due=check.due_date))
                    continue
                window = company.check_early_deposit_days or 0
                days_early = (check.due_date - today).days
                if days_early > window:
                    problems.append(_(
                        "%(check)s matures on %(due)s — %(early)s days early, "
                        "and this company allows at most %(window)s.",
                        check=label, due=check.due_date, early=days_early,
                        window=window))
                    continue

            if check.is_stale:
                problems.append(_(
                    "%(check)s matured on %(due)s and is past this company's "
                    "cheque validity period. Presenting a stale instrument is "
                    "a decision for Treasury, not an automatic one.",
                    check=label, due=check.due_date))
                continue

            # ---- allocation policy (M3) ----
            if (company.check_require_full_allocation
                    and self.currency_id.compare_amounts(
                        check.unapplied_amount, 0) > 0):
                problems.append(_(
                    "%(check)s has %(amount)s unallocated, and this company "
                    "requires full allocation before deposit.",
                    check=label,
                    amount=self.currency_id.format(check.unapplied_amount)))
                continue

        if problems:
            raise UserError(_(
                "This deposit cannot be confirmed:\n\n%s"
            ) % '\n'.join('- %s' % p for p in problems))
        return True

    def _check_maker_checker(self):
        """M28 — whoever prepared a large batch should not also approve it.

        Enforced on the server, not by hiding a button. Configurable because
        segregation of duties is a house policy, and default-off because
        turning it on for a one-person treasury would simply lock them out.
        """
        self.ensure_one()
        if not self._requires_other_confirmer():
            return True
        raise UserError(_(
            "You prepared deposit %(name)s totalling %(amount)s. Under this "
            "company's maker/checker policy it must be confirmed by someone "
            "else.", name=self.name,
            amount=self.currency_id.format(self.total_amount)))

    def _requires_other_confirmer(self):
        """True when the current user may not confirm this slip themselves.

        The single statement of the maker/checker rule, so the workbench can
        ask the same question the confirmation enforces instead of finding out
        by being refused.
        """
        self.ensure_one()
        company = self.company_id
        if not company.check_maker_checker:
            return False
        if self.env.user != self.prepared_by_id:
            return False
        limit = company.check_self_approve_limit or 0.0
        if limit and self.currency_id.compare_amounts(
                self.total_amount, limit) <= 0:
            return False
        return True

    def _assert_treasurer(self, what):
        self.env['realestate.check']._assert_group(
            'real_estate_checks.group_checks_treasurer', what)

    # ==================================================================
    # Actions
    # ==================================================================
    def action_confirm(self):
        """Present the batch. This is the accounting moment (M7)."""
        for rec in self:
            rec._assert_treasurer(_('confirm a deposit'))
            if rec.state != 'draft':
                raise UserError(_("Only draft deposits can be confirmed."))
            rec._validate_batch()
            rec._check_maker_checker()

            Presentation = self.env['realestate.check.presentation']
            Custody = self.env['realestate.check.custody']
            bank_location = rec._bank_location()

            for check in rec.check_ids:
                attempt = Presentation.open_attempt(
                    check, deposit=rec, journal=rec.journal_id,
                    date=rec.deposit_date, amount=check.amount)
                check.write({
                    'state': 'deposited',
                    'deposit_date': rec.deposit_date,
                    'presented_date': rec.deposit_date,
                    'journal_id': rec.journal_id.id,
                })
                payment = rec._create_payment_for(check, attempt)
                attempt.payment_id = payment.id
                check.payment_id = payment.id

            Custody.transfer(
                rec.check_ids, to_custodian=False, to_location=bank_location,
                reason='deposit',
                note=_('Presented on deposit slip %s.') % rec.name)

            rec.write({
                'state': 'confirmed',
                'confirmed_by_id': self.env.user.id,
                'confirmed_on': fields.Datetime.now(),
            })
            rec._link_batch_payment()
            rec.message_post(body=_(
                "Presented to %(journal)s: %(count)s cheque(s), %(total)s. "
                "%(payments)s inbound payment(s) registered — this is money on "
                "its way, not money received.",
                journal=rec.journal_id.display_name,
                count=len(rec.check_ids),
                total=rec.currency_id.format(rec.total_amount),
                payments=len(rec.payment_ids)))
        return True

    def _bank_location(self):
        """The custody location representing 'at the bank'.

        Created with elevated rights when it is missing. It is a system-managed
        configuration record — "the cheque is no longer in our building" — not
        user data, and only a Manager may create locations. Without this, a
        Treasury Officer confirming the first deposit in a fresh company was
        refused for the sake of a row the module was about to create on their
        behalf. (Found by driving the deposit flow in a browser as an officer.)
        """
        self.ensure_one()
        Location = self.env['realestate.check.location'].sudo()
        location = Location.search([
            ('company_id', '=', self.company_id.id),
            ('kind', '=', 'bank'),
        ], limit=1)
        if not location:
            location = Location.create({
                'name': _('At Bank'),
                'code': 'BANK',
                'kind': 'bank',
                'company_id': self.company_id.id,
            })
        return location

    def _create_payment_for(self, check, presentation):
        """One physical instrument, one accounting payment (M17).

        A cheque covering three instalments produces **one** payment reconciled
        across three invoices — not three invented payments. Two cheques for one
        instalment produce two payments, because there are two pieces of paper.
        The correspondence between what the bank will process and what the
        ledger records stays legible.
        """
        self.ensure_one()
        vals = {
            'partner_id': check.partner_id.id,
            'partner_type': 'customer',
            'payment_type': 'inbound',
            'amount': check.amount,
            'currency_id': check.currency_id.id,
            'journal_id': self.journal_id.id,
            'company_id': self.company_id.id,
            'date': self.deposit_date,
            'memo': _('Cheque %(ref)s no. %(number)s — %(bank)s',
                      ref=check.name, number=check.check_number,
                      bank=check.bank_id.name or ''),
            'realestate_check_id': check.id,
            'realestate_deposit_id': self.id,
        }
        if self.payment_method_line_id:
            vals['payment_method_line_id'] = self.payment_method_line_id.id
        payment = self.env['account.payment'].create(vals)
        payment.action_post()
        self._reconcile_payment(payment, check)
        return payment

    def _reconcile_payment(self, payment, check):
        """Match the payment against the invoices behind the cheque.

        This is where 0.1 crashed: it read `payment.line_ids`, which Odoo 18
        removed when it dropped `account.payment`'s `_inherits` delegation to
        `account.move`. The lines live on `payment.move_id.line_ids`.

        What is matched is the payment's **receivable** counterpart line against
        each invoice's receivable line, for the same partner and company. When
        a cheque covers several invoices Odoo's own partial reconciliation
        distributes it; nothing here does arithmetic on residuals, and nothing
        writes `payment_state` or zeroes a residual (Rule 3).
        """
        move = payment.move_id
        if not move:
            # A configuration with no outstanding account produces no journal
            # entry at all and the payment is already 'paid'. There is nothing
            # to reconcile; the cheque reads as cleared on the next sync.
            return False
        settlements = check._allocations_to_settle()
        if not settlements:
            return False

        company_currency = payment.company_id.currency_id
        matched = False
        for invoice, allocated in settlements:
            payment_lines = move.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable'
                and not l.reconciled)
            if not payment_lines:
                break
            invoice_lines = invoice.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable'
                and not l.reconciled
                and l.partner_id == payment.partner_id
                and l.company_id == payment.company_id)
            if not invoice_lines:
                continue
            try:
                # How much of the instrument this obligation is entitled to.
                # `reconcile()` takes as much as it can, so on its own it
                # hands the first invoice the cheque's whole residual and
                # leaves the next one open -- the accounting then disagrees
                # with the allocation records that Treasury works from.
                available = -sum(payment_lines.mapped('amount_residual'))
                owing = sum(invoice_lines.mapped('amount_residual'))
                share = min(allocated, available, owing)
                full_cover = (
                    company_currency.compare_amounts(share, owing) >= 0
                    or company_currency.is_zero(owing - share))
                simple_currency = all(
                    (line.currency_id or company_currency) == company_currency
                    for line in payment_lines | invoice_lines)
                if full_cover or not simple_currency:
                    # The obligation is covered in full, or the entry is in a
                    # foreign currency where splitting an amount by hand is
                    # not something to improvise. Odoo's own reconciliation
                    # settles it, which is the previous behaviour and what the
                    # accounting tests exercise.
                    (payment_lines | invoice_lines).reconcile()
                else:
                    self._reconcile_partial_share(
                        payment_lines, invoice_lines, share)
                matched = True
            except UserError as err:
                # Say so. 0.1 swallowed this into a chatter line that was never
                # reached; a reconciliation that cannot happen is something
                # Treasury needs told, not something to hide.
                check.message_post(body=_(
                    "Payment %(payment)s could not be reconciled with invoice "
                    "%(invoice)s: %(error)s\n\nThe payment is posted and the "
                    "invoice is unchanged — reconcile it in Accounting.",
                    payment=payment.display_name, invoice=invoice.name,
                    error=err))
                _logger.warning(
                    "real_estate_checks: reconciliation of %s with %s failed: %s",
                    payment.display_name, invoice.name, err)
        return matched

    def _reconcile_partial_share(self, payment_lines, invoice_lines, share):
        """Match exactly `share` of the payment against the invoice.

        `reconcile()` has no amount argument, so an explicit partial is the
        only way to honour an allocation smaller than the invoice. This is the
        same record Odoo creates internally when a reconciliation does not
        clear both sides.

        One partial per line pair, taking the smaller of the two residuals
        each time, until `share` is used up. Only the company currency reaches
        here; the caller sends anything else down the ordinary path.
        """
        Partial = self.env['account.partial.reconcile']
        currency = self.env.company.currency_id
        remaining = share
        for invoice_line in invoice_lines:
            if currency.is_zero(remaining):
                break
            owing = invoice_line.amount_residual
            if currency.compare_amounts(owing, 0.0) <= 0:
                continue
            for payment_line in payment_lines:
                if currency.is_zero(remaining):
                    break
                available = -payment_line.amount_residual
                if currency.compare_amounts(available, 0.0) <= 0:
                    continue
                amount = min(remaining, owing, available)
                if currency.is_zero(amount):
                    continue
                Partial.create({
                    'debit_move_id': invoice_line.id,
                    'credit_move_id': payment_line.id,
                    'amount': amount,
                    'debit_amount_currency': amount,
                    'credit_amount_currency': amount,
                })
                remaining -= amount
                owing -= amount
                if currency.compare_amounts(owing, 0.0) <= 0:
                    break

    def _link_batch_payment(self):
        """M6 — hand the accounting batch to Odoo when Odoo can take it.

        `account.batch.payment` is `OEEL-1` (Enterprise) and this module is
        LGPL-3, so it can never be a hard dependency. When the model is in the
        registry the payments are grouped into one so a single bank line
        reconciles them all; when it is not, each payment reconciles on its own
        and nothing about the treasury workflow changes.
        """
        self.ensure_one()
        if 'account.batch.payment' not in self.env:
            return False
        if self.batch_payment_id or not self.payment_ids:
            return False
        if self.journal_id.type != 'bank':
            return False
        methods = self.payment_ids.mapped(
            'payment_method_line_id.payment_method_id')
        if len(methods) != 1:
            # A batch has one payment method by definition. Rather than pick
            # one, leave the payments ungrouped and say why.
            self.message_post(body=_(
                "Payments were not grouped into an Odoo batch: they use %s "
                "different payment methods.") % len(methods))
            return False
        try:
            batch = self.env['account.batch.payment'].create({
                'journal_id': self.journal_id.id,
                'batch_type': 'inbound',
                'date': self.deposit_date,
                'payment_ids': [(6, 0, self.payment_ids.ids)],
                'payment_method_id': methods.id,
            })
        except (UserError, ValidationError) as err:
            self.message_post(body=_(
                "Payments were not grouped into an Odoo batch payment: %s"
            ) % err)
            return False
        self.batch_payment_id = batch.id
        return batch

    def action_clear_all(self):
        """0.1's button, kept — but it now reads clearance instead of asserting it.

        0.1 marked every deposited cheque on the slip cleared and created their
        payments, which made "we pressed a button" indistinguishable from "the
        bank paid us". This refreshes each cheque from Odoo's reconciliation and
        reports what is still outstanding.
        """
        self.ensure_one()
        self._assert_treasurer(_('clear cheques'))
        if self.state not in ('confirmed', 'partial'):
            raise UserError(_(
                "Deposit must be Confirmed or Partially Bounced to clear the "
                "rest."))
        candidates = self.check_ids.filtered(
            lambda c: c.state in ('deposited', 'in_clearing'))
        cleared = candidates._sync_clearance_from_accounting()
        self._recompute_final_state()
        outstanding = candidates - cleared
        self.message_post(body=_(
            "Refreshed from Accounting: %(cleared)s cheque(s) cleared, "
            "%(waiting)s still awaiting a bank match.",
            cleared=len(cleared), waiting=len(outstanding)))
        return True

    def action_sync_accounting(self):
        """M8's manual override — a refresh, never a pretence.

        Treasury sometimes needs the state now rather than after the nightly
        cron. What it must never do is let someone declare a cheque cleared that
        Odoo says is not, so this takes exactly the same path as the cron.
        """
        self.ensure_one()
        self.check_ids._sync_clearance_from_accounting()
        self._recompute_final_state()
        return True

    def _recompute_final_state(self):
        for rec in self:
            if rec.state in ('draft', 'cancelled') or not rec.check_ids:
                continue
            states = set(rec.check_ids.mapped('state'))
            if states <= {'cleared'}:
                # Bank-matched throughout is a stronger statement than "all
                # cleared", and 0.1 had no way to express it.
                rec.state = ('reconciled'
                             if all(rec.check_ids.mapped('is_bank_matched'))
                             else 'cleared')
            elif states & {'bounced'}:
                rec.state = 'partial'
        return True

    def action_cancel(self):
        """A slip can only be cancelled while nothing has happened at the bank."""
        self.ensure_one()
        self._assert_treasurer(_('cancel a deposit'))
        settled = self.check_ids.filtered(
            lambda c: c.state in ('cleared', 'bounced'))
        if settled:
            raise UserError(_(
                "Deposit %(name)s has %(count)s cheque(s) the bank has already "
                "acted on (%(refs)s). Cancelling the slip would erase a "
                "presentation that really happened — reverse the payments in "
                "Accounting instead.",
                name=self.name, count=len(settled),
                refs=', '.join(settled.mapped('name')[:5])))
        posted = self.payment_ids.filtered(
            lambda p: p.state not in ('draft', 'canceled'))
        if posted:
            raise UserError(_(
                "Deposit %s has posted payments. Cancel or reverse them in "
                "Accounting first — this module does not delete journal "
                "entries.") % self.name)

        returning = self.check_ids.filtered(
            lambda c: c.state in ('deposited', 'in_clearing'))
        # Every cheque the bank never acted on for THIS slip leaves it. A draft
        # slip's cheques are still `registered`, and releasing only the
        # presented ones left them pointing at a cancelled slip: the workbench
        # (which offers `deposit_id = False` only) never showed them again and
        # Back to Draft refused them. A cheque whose attempt on this slip
        # cleared or bounced keeps the link, because that presentation happened.
        stranded = self.check_ids.filtered(
            lambda c: c not in returning and not c.presentation_ids.filtered(
                lambda p: p.deposit_id == self
                and p.state in ('cleared', 'bounced')))
        self.presentation_ids.filtered(
            lambda p: p.state in ('presented', 'clearing')).write(
                {'state': 'cancelled'})
        returning.write({'state': 'registered', 'deposit_id': False})
        stranded.write({'deposit_id': False})
        self.env['realestate.check.custody'].transfer(
            returning, to_custodian=self.env.user,
            to_location=self.company_id.check_default_location_id or False,
            reason='transfer', note=_('Deposit %s cancelled.') % self.name)
        self.state = 'cancelled'
        return True

    @api.depends('payment_ids.state')
    def _compute_has_live_payments(self):
        for rec in self:
            rec.has_live_payments = bool(rec.payment_ids.filtered(
                lambda p: p.state not in ('draft', 'canceled')))

    def action_view_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Payments — %s') % self.name,
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.payment_ids.ids)],
        }


class AccountPaymentCheckLink(models.Model):
    """The back-references that make a deposit's payments queryable.

    Without these the only way to reach a cheque's payment is
    `check.payment_id`, which cannot be searched from the payment side — and
    reconciliation work happens from the payment side.
    """
    _inherit = 'account.payment'

    realestate_check_id = fields.Many2one(
        'realestate.check', string='Cheque', readonly=True, index=True,
        ondelete='set null', copy=False)
    realestate_deposit_id = fields.Many2one(
        'realestate.check.deposit', string='Cheque Deposit', readonly=True,
        index=True, ondelete='set null', copy=False)
