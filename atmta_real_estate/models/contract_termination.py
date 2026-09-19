"""Lease termination and notice (Phase 19).

The pre-upgrade ``action_terminate`` set a state, cancelled a draft invoice and
stopped. Everything that actually makes a termination hard was left to somebody
to remember: notice period, early-exit penalty, what happens to obligations
already invoiced, the deposit, utility settlement, the move-out appointment,
and when the unit becomes lettable again.

This model captures the whole settlement. Two doors out of a lease:

* **Normal expiry** -- the term simply ran out. ``ACTIVE → ENDED``.
* **Early termination** -- somebody gave notice. ``ACTIVE → NOTICE → TERMINATED``.

Financial rules it will not break:

* posted invoices are **never** unlinked or deleted -- reversal is a credit
  note, issued through Odoo
* obligations that have not been invoiced are cancelled or prorated
* the period containing the termination date is reproraterated, not deleted
* the property does not become available just because the lease ended; that is
  the unit-turn's decision
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from . import proration as prorate_lib

_logger = logging.getLogger(__name__)

TERMINATION_STATES = [
    ('draft', 'Draft'),
    ('notice_given', 'Notice Given'),
    ('approved', 'Approved'),
    ('settled', 'Settled'),
    ('completed', 'Completed'),
    ('cancelled', 'Cancelled'),
]

TERMINATION_REASONS = [
    ('expiry', 'Normal Expiry'),
    ('tenant_notice', 'Tenant Gave Notice'),
    ('landlord_notice', 'Landlord Gave Notice'),
    ('breach', 'Breach of Contract'),
    ('non_payment', 'Non-Payment'),
    ('mutual', 'Mutual Agreement'),
    ('relocation', 'Tenant Relocation'),
    ('property_sold', 'Property Sold'),
    ('other', 'Other'),
]

DEPOSIT_TREATMENTS = [
    ('refund_full', 'Refund in Full'),
    ('refund_partial', 'Refund Partially'),
    ('forfeit', 'Forfeit'),
    ('apply_arrears', 'Apply to Arrears'),
    ('pending', 'Decide at Move-Out'),
]


class ContractTermination(models.Model):
    _name = 'realestate.contract.termination'
    _description = 'Lease Termination'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'effective_date desc, id desc'

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'),
    )
    contract_id = fields.Many2one(
        'realestate.contract', string='Lease', required=True,
        ondelete='cascade', index=True, tracking=True,
    )
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='contract_id.currency_id', store=True, readonly=True)
    partner_id = fields.Many2one(
        related='contract_id.partner_id', store=True, readonly=True)

    state = fields.Selection(
        TERMINATION_STATES, default='draft', required=True, tracking=True,
        index=True, copy=False,
    )
    is_early = fields.Boolean(
        string='Early Termination', compute='_compute_is_early', store=True,
        help="True when the lease ends before its contracted end date.",
    )

    # ---------------- Notice ----------------
    request_date = fields.Date(
        string='Requested On', default=fields.Date.context_today,
        required=True, tracking=True,
    )
    requested_by = fields.Selection(
        [('tenant', 'Tenant'), ('landlord', 'Landlord'), ('mutual', 'Mutual')],
        string='Requested By', default='tenant', required=True, tracking=True,
    )
    requested_end_date = fields.Date(
        string='Requested End Date', required=True, tracking=True,
        help="The date the tenant or landlord asked for.",
    )
    effective_date = fields.Date(
        string='Effective Termination Date', required=True, tracking=True,
        index=True,
        help="The date the lease actually ends and billing stops.",
    )
    notice_period_days = fields.Integer(
        string='Notice Period (days)', tracking=True,
        compute='_compute_notice', store=True, readonly=False,
    )
    notice_given_days = fields.Integer(
        string='Notice Actually Given', compute='_compute_notice', store=True,
    )
    notice_shortfall_days = fields.Integer(
        string='Notice Shortfall', compute='_compute_notice', store=True,
        help="Days of notice missing. Drives the default penalty.",
    )
    reason = fields.Selection(
        TERMINATION_REASONS, string='Reason', required=True,
        default='tenant_notice', tracking=True,
    )
    reason_note = fields.Text()

    # ---------------- Money ----------------
    penalty_amount = fields.Monetary(string='Penalty', tracking=True)
    penalty_waived = fields.Monetary(string='Penalty Waived', tracking=True)
    penalty_net = fields.Monetary(
        string='Net Penalty', compute='_compute_settlement', store=True)
    waiver_reason = fields.Text()
    waiver_approver_id = fields.Many2one(
        'res.users', string='Waiver Approved By', readonly=True, copy=False)

    outstanding_balance = fields.Monetary(
        string='Outstanding Rent', compute='_compute_settlement', store=True,
        help="Unpaid residual of every obligation up to the effective date, read "
             "from the invoices. Later periods are cancelled or credited at "
             "settlement, so they are not counted.",
    )
    utility_settlement = fields.Monetary(
        string='Utility Settlement', tracking=True,
        help="Final metered utilities owed by the tenant.",
    )
    final_charges = fields.Monetary(
        string='Other Final Charges', tracking=True)
    deposit_held = fields.Monetary(
        string='Deposit Held', compute='_compute_settlement', store=True)
    net_settlement = fields.Monetary(
        string='Net Settlement', compute='_compute_settlement', store=True,
        help="Positive = the tenant owes us. Negative = we owe the tenant.",
    )

    deposit_treatment = fields.Selection(
        DEPOSIT_TREATMENTS, string='Deposit Treatment', default='pending',
        required=True, tracking=True,
    )

    final_invoice_id = fields.Many2one(
        'account.move', string='Final Invoice', readonly=True, copy=False)
    credit_note_ids = fields.Many2many(
        'account.move', 'realestate_termination_credit_rel',
        'termination_id', 'move_id', string='Credit Notes',
        readonly=True, copy=False,
        help="Reversals of invoices covering periods after the termination "
             "date. Posted invoices are credited, never deleted.",
    )

    # ---------------- Operations ----------------
    move_out_id = fields.Many2one(
        'realestate.move.out', string='Move-Out', readonly=True, copy=False)
    property_available_date = fields.Date(
        string='Property Available From', tracking=True,
        help="When the unit can be re-let. Set by the unit turn, not by the "
             "termination -- a unit needing repairs is not available.",
    )

    approver_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False, tracking=True)
    approval_date = fields.Datetime(readonly=True, copy=False)
    notes = fields.Html()

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('effective_date', 'contract_id.end_date')
    def _compute_is_early(self):
        for rec in self:
            rec.is_early = bool(
                rec.effective_date and rec.contract_id.end_date
                and rec.effective_date < rec.contract_id.end_date)

    @api.depends('request_date', 'effective_date', 'company_id',
                 'notice_period_days')
    def _compute_notice(self):
        for rec in self:
            if not rec.notice_period_days:
                rec.notice_period_days = rec.company_id.re_notice_period_days or 0
            given = ((rec.effective_date - rec.request_date).days
                     if rec.effective_date and rec.request_date else 0)
            rec.notice_given_days = max(given, 0)
            rec.notice_shortfall_days = max(
                (rec.notice_period_days or 0) - rec.notice_given_days, 0)

    @api.depends('penalty_amount', 'penalty_waived', 'utility_settlement',
                 'final_charges', 'effective_date',
                 'contract_id.contract_payment_ids.amount_residual',
                 'contract_id.contract_payment_ids.period_start',
                 'contract_id.deposit_held_total')
    def _compute_settlement(self):
        for rec in self:
            rec.penalty_net = max(
                (rec.penalty_amount or 0.0) - (rec.penalty_waived or 0.0), 0.0)
            # .exists() guards against obligations unlinked earlier in the
            # same transaction by the settlement step.
            # Periods starting after the effective date are not owed: the
            # settlement cancels them (or credits them if already invoiced).
            # Counting them showed the whole remaining term as outstanding
            # while the manager was deciding whether to approve the exit.
            rec.outstanding_balance = sum(
                rec.contract_id.contract_payment_ids.exists().filtered(
                    lambda o, cutoff=rec.effective_date: not o.is_settled
                    and o.state != 'cancelled'
                    and not (cutoff and o.period_start and o.period_start > cutoff)
                ).mapped('amount_residual'))
            rec.deposit_held = rec.contract_id.deposit_held_total
            rec.net_settlement = (
                rec.outstanding_balance + rec.penalty_net
                + (rec.utility_settlement or 0.0) + (rec.final_charges or 0.0)
                - rec.deposit_held)

    # ==================================================================
    # Validation
    # ==================================================================
    @api.constrains('contract_id', 'state')
    def _check_one_open_termination(self):
        """A lease is exited once. Two open terminations would each settle,
        cancel and credit the same future periods and each open a unit turn."""
        open_states = ('draft', 'notice_given', 'approved', 'settled')
        for rec in self.filtered(lambda t: t.state in open_states):
            other = self.search([
                ('contract_id', '=', rec.contract_id.id),
                ('state', 'in', open_states),
                ('id', '!=', rec.id),
            ], limit=1)
            if other:
                raise ValidationError(_(
                    "Lease %(lease)s already has termination %(other)s in "
                    "progress. Continue that one, or cancel it first.",
                    lease=rec.contract_id.display_name, other=other.name))

    @api.constrains('effective_date', 'contract_id')
    def _check_effective_date(self):
        for rec in self:
            contract = rec.contract_id
            if contract.start_date and rec.effective_date < contract.start_date:
                raise ValidationError(_(
                    "Termination %(name)s takes effect before the lease started "
                    "(%(start)s).", name=rec.name, start=contract.start_date))

    # ==================================================================
    # Workflow
    # ==================================================================
    def action_give_notice(self):
        """Record the notice and move the lease into NOTICE."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Notice has already been given on %s.", rec.name))
            rec._suggest_penalty()
            rec.state = 'notice_given'
            if rec.contract_id.lifecycle_state == 'active':
                rec.contract_id._do_transition('notice', message=_(
                    "Notice given on %(date)s, effective %(eff)s (%(reason)s).",
                    date=rec.request_date, eff=rec.effective_date,
                    reason=dict(TERMINATION_REASONS).get(rec.reason)))
            rec.message_post(body=_(
                "Notice recorded: %(given)s days given, %(required)s required.",
                given=rec.notice_given_days, required=rec.notice_period_days))
        return True

    def action_approve(self):
        """Approve the terms of the exit, including any penalty waiver."""
        self.mapped('contract_id')._require_group(
            'atmta_real_estate.group_rental_manager')
        for rec in self:
            if rec.state != 'notice_given':
                raise UserError(_(
                    "Termination %s must have notice given before approval.",
                    rec.name))
            if rec.penalty_waived and not rec.waiver_reason:
                raise UserError(_(
                    "Waiving a penalty requires a written reason."))
            vals = {
                'state': 'approved',
                'approver_id': self.env.user.id,
                'approval_date': fields.Datetime.now(),
            }
            if rec.penalty_waived:
                vals['waiver_approver_id'] = self.env.user.id
            rec.write(vals)
            rec.message_post(body=_("Termination approved."))
        return True

    def action_settle(self):
        """Do the money: reprorate, cancel, credit, and raise the final invoice."""
        self.mapped('contract_id')._require_group(
            'atmta_real_estate.group_rental_manager')
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_(
                    "Termination %s must be approved before settlement.", rec.name))
            rec._settle_future_obligations()
            rec._reprorate_terminal_period()
            rec._issue_final_invoice()
            rec.state = 'settled'
            rec.message_post(body=_(
                "Settlement complete. Outstanding %(out)s, penalty %(pen)s, "
                "deposit held %(dep)s, net %(net)s.",
                out=rec.outstanding_balance, pen=rec.penalty_net,
                dep=rec.deposit_held, net=rec.net_settlement))
        return True

    def action_complete(self):
        """Close the lease out."""
        self.mapped('contract_id')._require_group(
            'atmta_real_estate.group_property_manager')
        for rec in self:
            if rec.state != 'settled':
                raise UserError(_(
                    "Settle termination %s before completing it.", rec.name))
            contract = rec.contract_id
            target = 'terminated' if rec.is_early else 'ended'
            contract.write({'end_date': rec.effective_date})
            contract.property_line_ids.filtered(
                lambda line: not line.end_date or line.end_date > rec.effective_date
            ).write({'end_date': rec.effective_date})
            contract._do_transition(target, message=_(
                "Lease closed by termination %(name)s effective %(date)s.",
                name=rec.name, date=rec.effective_date))
            rec._open_unit_turn()
            rec.state = 'completed'
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state in ('settled', 'completed'):
                raise UserError(_(
                    "Termination %s has already been settled.", rec.name))
            rec.state = 'cancelled'
            if rec.contract_id.lifecycle_state == 'notice':
                rec.contract_id._do_transition('active', message=_(
                    "Notice withdrawn (termination %s cancelled).") % rec.name)
        return True

    # ==================================================================
    # Settlement mechanics
    # ==================================================================
    def _suggest_penalty(self):
        """Default the early-exit penalty to the un-served notice.

        A suggestion, not a rule -- the manager can override or waive it, and
        every override is recorded.
        """
        self.ensure_one()
        if self.penalty_amount or not self.notice_shortfall_days:
            return
        daily_rent = self.contract_id._rent_on(self.effective_date) / 30.0
        self.penalty_amount = round(daily_rent * self.notice_shortfall_days, 2)

    def _settle_future_obligations(self):
        """Deal with everything scheduled after the termination date.

        * not invoiced  -> cancelled outright
        * invoiced      -> reversed with a credit note through Odoo
        """
        self.ensure_one()
        cutoff = self.effective_date
        obligations = self.contract_id.contract_payment_ids.filtered(
            lambda o: o.period_start and o.period_start > cutoff
            and o.state != 'cancelled')

        # Split the set BEFORE deleting anything. ``obligations`` is a snapshot;
        # filtering it after an unlink reads deleted rows and raises
        # MissingError, so both partitions are computed up front and the
        # deletion happens last.
        uninvoiced = obligations.filtered(lambda o: not o.move_id)
        to_reverse = obligations.filtered(
            lambda o: o.move_id and o.move_id.state == 'posted')

        # Reverse first: it needs to read move_id off records that are about to
        # go away.
        for obligation in to_reverse:
            self._credit_note_for(obligation.move_id, _(
                "Lease terminated %s — period no longer billable") % cutoff)

        if uninvoiced:
            uninvoiced.unlink()
            # Drop the deleted rows from the cache before anything recomputes
            # from contract_payment_ids -- otherwise the settlement computes
            # read unlinked records and raise MissingError.
            self.contract_id.invalidate_recordset(['contract_payment_ids'])
            self.invalidate_recordset(
                ['outstanding_balance', 'net_settlement', 'deposit_held'])

    def _reprorate_terminal_period(self):
        """Re-price the period the termination falls inside.

        The tenant occupied part of it, so the answer is a prorated charge --
        never a deleted obligation and never a full month.
        """
        self.ensure_one()
        cutoff = self.effective_date
        method = self.company_id.re_proration_method or 'actual'
        straddling = self.contract_id.contract_payment_ids.exists().filtered(
            lambda o: o.period_start and o.period_end
            and o.period_start <= cutoff <= o.period_end
            and o.state != 'cancelled')

        for obligation in straddling:
            new_rent = prorate_lib.prorate(
                obligation.gross_rent or obligation.amount, method,
                obligation.period_start, obligation.period_end,
                obligation.period_start, cutoff,
                precision=self.currency_id.decimal_places or 2)
            note = prorate_lib.describe(
                method, obligation.period_start, obligation.period_end,
                obligation.period_start, cutoff)

            if not obligation.move_id:
                obligation.write({
                    'gross_rent': new_rent,
                    'amount': max(new_rent - obligation.incentive_relief, 0.0),
                    'proration_note': note,
                })
                continue

            if obligation.move_id.state != 'posted':
                continue
            # Already billed: credit the difference rather than editing a
            # posted move.
            #
            # The difference is taken on the rent's own basis -- the rent this
            # obligation billed against the rent it should have -- and never
            # from `amount_total`. That total includes tax, so subtracting
            # untaxed rent and charges from it read the tax as overbilled
            # rent: a lease ending on the last day of its period still got a
            # credit for the whole tax amount. Charges are untouched either
            # way; only the rent is reprorated.
            billed_rent = obligation.gross_rent or obligation.amount or 0.0
            relief = obligation.incentive_relief or 0.0
            billed_net = max(billed_rent - relief, 0.0)
            due_net = max(new_rent - relief, 0.0)
            reduction = self.currency_id.round(billed_net - due_net)
            if self.currency_id.compare_amounts(reduction, 0.0) <= 0:
                continue
            rent_line = self._billed_rent_line(obligation, billed_rent)
            self._credit_note_for(
                obligation.move_id,
                _("Termination proration — %s") % note,
                amount=reduction, source_line=rent_line)

    def _billed_rent_line(self, obligation, billed_rent):
        """The rent line on a posted obligation invoice.

        Found by what it is rather than by position. The billing engine emits
        one rent line at exactly the obligation's gross rent, next to a relief
        line and any charge lines, and its product, account and taxes are what
        a partial credit has to mirror -- otherwise the tax on the reduction
        is never reversed.

        Refuses rather than guesses when the line is gone: an invoice that no
        longer carries the rent it was raised for has been changed by hand,
        and crediting it on an assumed tax treatment would be worse than
        telling Accounting to do it.
        """
        self.ensure_one()
        move = obligation.move_id
        currency = move.currency_id
        candidates = move.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product'
            and abs((l.quantity or 0.0) - 1.0) < 1e-6
            and currency.compare_amounts(l.price_unit, billed_rent) == 0)
        rent_product = obligation._rent_product()
        if rent_product and len(candidates) > 1:
            candidates = candidates.filtered(
                lambda l: l.product_id == rent_product) or candidates
        if not candidates:
            raise UserError(_(
                "Invoice %(move)s no longer carries the rent line of "
                "%(amount)s that obligation %(obligation)s billed, so the "
                "termination credit cannot mirror its account and taxes. "
                "Credit that invoice through Accounting.",
                move=move.display_name, amount=billed_rent,
                obligation=obligation.display_name))
        return candidates[:1]

    def _credit_note_for(self, move, reason, amount=None, source_line=None):
        """Reverse an invoice through Odoo's own credit-note machinery.

        Never touches the posted move. A full reversal uses
        ``_reverse_moves``; a partial one issues a credit note for the
        difference.

        ``amount`` is on the same basis as the ``source_line`` it reduces, and
        the credit line takes that line's product, account and taxes. A
        partial credit without them posts the reduction to whatever the journal
        defaults to and reverses no tax at all.
        """
        self.ensure_one()
        if amount is None:
            reversal = move._reverse_moves([{
                'ref': reason,
                'date': self.effective_date,
                'invoice_date': self.effective_date,
            }], cancel=False)
        else:
            reversal = self.env['account.move'].with_company(
                self.company_id).create({
                    'move_type': 'out_refund',
                    'partner_id': move.partner_id.id,
                    'company_id': self.company_id.id,
                    'currency_id': move.currency_id.id,
                    'invoice_date': self.effective_date,
                    'reversed_entry_id': move.id,
                    'ref': reason,
                    'contract_id': self.contract_id.id,
                    'invoice_line_ids': [(0, 0, self._credit_line_vals(
                        reason, amount, source_line))],
                })
        self.env['realestate.account.tools'].post_moves(reversal)
        self.credit_note_ids = [(4, rid) for rid in reversal.ids]
        return reversal

    @api.model
    def _credit_line_vals(self, reason, amount, source_line=None):
        vals = {'name': reason, 'quantity': 1.0, 'price_unit': amount}
        if source_line:
            vals.update({
                'product_id': source_line.product_id.id or False,
                'account_id': source_line.account_id.id,
                'tax_ids': [(6, 0, source_line.tax_ids.ids)],
            })
        return vals

    def _issue_final_invoice(self):
        """Bill the penalty, utility settlement and any final charges.

        The deposit is deliberately NOT netted off here -- it is settled
        through the deposit record so it stays on the liability account until
        it is genuinely refunded, forfeited or applied.
        """
        self.ensure_one()
        lines = []
        if self.penalty_net:
            lines.append((0, 0, {
                'name': _("Early termination penalty (%s days short notice)")
                        % self.notice_shortfall_days,
                'quantity': 1.0,
                'price_unit': self.penalty_net,
            }))
        if self.utility_settlement:
            lines.append((0, 0, {
                'name': _("Final utility settlement"),
                'quantity': 1.0,
                'price_unit': self.utility_settlement,
            }))
        if self.final_charges:
            lines.append((0, 0, {
                'name': _("Final charges"),
                'quantity': 1.0,
                'price_unit': self.final_charges,
            }))
        if not lines:
            return self.env['account.move']

        product = self.company_id.re_rent_product_id
        if product:
            for line in lines:
                line[2]['product_id'] = product.id

        move = self.env['account.move'].with_company(self.company_id).create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'invoice_date': self.effective_date,
            'contract_id': self.contract_id.id,
            'ref': _("Final settlement — %s") % self.contract_id.display_name,
            'invoice_line_ids': lines,
        })
        self.env['realestate.account.tools'].post_moves(move)
        self.final_invoice_id = move.id
        return move

    def _open_unit_turn(self):
        """Hand the unit to the turn process.

        Deliberately does NOT mark the property available -- a unit that needs
        cleaning or repair is not lettable, and pretending otherwise is how
        double-bookings happen.
        """
        self.ensure_one()
        Turn = self.env['realestate.unit.turn']
        for allocation in self.contract_id.property_line_ids:
            prop = allocation.property_id
            # The move-out of this exit usually opened the turn already. A
            # second one would block the unit again, even after the first was
            # marked ready and the unit re-let.
            existing = Turn.search([
                ('property_id', '=', prop.id),
                ('state', '!=', 'cancelled'),
                '|', ('termination_id', '=', self.id),
                     ('contract_id', '=', self.contract_id.id),
            ], limit=1) or Turn.search([
                ('property_id', '=', prop.id),
                ('state', 'not in', ('ready', 'cancelled')),
            ], limit=1)
            if existing:
                if not existing.termination_id:
                    existing.termination_id = self.id
                continue
            Turn.create({
                'property_id': prop.id,
                'contract_id': self.contract_id.id,
                'termination_id': self.id,
                'start_date': self.effective_date,
            })

    # ==================================================================
    # ORM
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.contract.termination') or _('New')
        return super().create(vals_list)


class ContractTerminationMixin(models.Model):
    _inherit = 'realestate.contract'

    termination_ids = fields.One2many(
        'realestate.contract.termination', 'contract_id', string='Terminations',
    )
    termination_count = fields.Integer(compute='_compute_termination_count')
    active_termination_id = fields.Many2one(
        'realestate.contract.termination', string='Open Termination',
        compute='_compute_termination_count',
    )

    @api.depends('termination_ids.state')
    def _compute_termination_count(self):
        for rec in self:
            rec.termination_count = len(rec.termination_ids)
            open_terms = rec.termination_ids.filtered(
                lambda t: t.state in ('draft', 'notice_given', 'approved', 'settled'))
            rec.active_termination_id = open_terms[:1].id if open_terms else False

    def action_start_termination(self):
        self.ensure_one()
        if self.active_termination_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'realestate.contract.termination',
                'res_id': self.active_termination_id.id,
                'view_mode': 'form',
            }
        default_end = fields.Date.context_today(self) + relativedelta(
            days=self.company_id.re_notice_period_days or 0)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Terminate Lease'),
            'res_model': 'realestate.contract.termination',
            'view_mode': 'form',
            'context': {
                'default_contract_id': self.id,
                'default_requested_end_date': default_end,
                'default_effective_date': default_end,
            },
        }

    def action_view_terminations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Terminations'),
            'res_model': 'realestate.contract.termination',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }

    # ------------------------------------------------------------------
    # Automation -- Phase 30
    # ------------------------------------------------------------------
    @api.model
    def _cron_expire_leases(self, limit=500):
        """Move leases past their end date to ENDED.

        Only touches leases the company has opted in for, only when nothing is
        outstanding, and never when a termination is mid-flight. Idempotent and
        batch-safe.
        """
        today = fields.Date.context_today(self)
        closed = 0
        for company in self.env['res.company'].sudo().search(
                [('re_auto_expire_leases', '=', True)]):
            leases = self.sudo().search([
                ('company_id', '=', company.id),
                ('lifecycle_state', '=', 'active'),
                ('end_date', '!=', False),
                ('end_date', '<', today),
            ], limit=limit)
            for lease in leases:
                if lease.active_termination_id:
                    continue
                if lease.payment_status in ('not_paid', 'partial', 'overdue') \
                        and lease.billing_status != 'not_started':
                    # Leave it Active so it stays on the arrears reports.
                    continue
                lease._do_transition('ended', message=_(
                    "Lease automatically ended: term expired on %s with no "
                    "outstanding balance.") % lease.end_date)
                closed += 1
        _logger.info("Lease expiry cron ended %s lease(s)", closed)
        return closed
