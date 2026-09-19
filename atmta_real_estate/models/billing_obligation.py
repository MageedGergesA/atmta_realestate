"""Billing obligations and arrears data (Phases 9 and 16).

``realestate.contract.payment`` is preserved -- ``real_estate_checks``,
``real_estate_api`` and the partner statement report all reference it by name --
and evolved into a proper **billing obligation**: a statement of what the tenant
owes for a period, and why.

The division of responsibility is strict:

* **ATMTA owns the obligation** -- period, rent, charges, incentives, proration.
* **Odoo Accounting owns the money** -- ``amount_invoiced``, ``amount_paid``
  and ``amount_residual`` are all read from ``account.move``. Nothing about
  reconciliation is re-implemented or cached as an independent truth.

That is why an obligation is never marked settled on its own say-so: it is paid
when Odoo says the invoice is paid *and* the residual is zero. A payment that
is registered but not yet reconciled with the bank leaves the obligation
outstanding, which is the accounting-correct answer.

Arrears fields (``days_overdue``, ``overdue_bucket``) are exposed here for a
future shared Collections module. Odoo's own follow-up engine keeps working on
the generated invoices -- this module deliberately does not duplicate it.
"""

from odoo import _, api, fields, models

from datetime import timedelta

from odoo.exceptions import UserError
from odoo.osv import expression

from .lease_states import OVERDUE_BUCKETS, overdue_bucket

#: Anything below this is rounding noise, not a debt.
SETTLEMENT_EPSILON = 0.01

#: Days-overdue range of each ageing bucket, inclusive; ``None`` is open.
#: Mirrors ``lease_states.overdue_bucket``.
OVERDUE_BUCKET_RANGES = {
    'current': (None, 0),
    '1_30': (1, 30),
    '31_60': (31, 60),
    '61_90': (61, 90),
    '91_120': (91, 120),
    '120_plus': (121, None),
}


def _negate(domain):
    return ['!'] + expression.normalize_domain(domain)

COLLECTION_STATES = [
    ('none', 'No Action'),
    ('reminded', 'Reminder Sent'),
    ('escalated', 'Escalated'),
    ('legal', 'With Legal'),
    ('written_off', 'Written Off'),
]


class BillingObligation(models.Model):
    _inherit = 'realestate.contract.payment'

    # ------------------------------------------------------------------
    # Scoping
    # ------------------------------------------------------------------
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related='contract_id.currency_id', store=True, readonly=True,
    )
    property_line_id = fields.Many2one(
        'realestate.contract.property.line', string='Property Allocation',
        ondelete='set null', index=True,
        help="The allocation this obligation bills. Empty on whole-lease "
             "obligations.",
    )

    # ------------------------------------------------------------------
    # Billing period
    # ------------------------------------------------------------------
    period_start = fields.Date(
        string='Period From', index=True,
        help="First day of the service period this obligation covers.",
    )
    period_end = fields.Date(string='Period To', index=True)
    period_label = fields.Char(
        string='Period', compute='_compute_period_label', store=True,
    )

    # ------------------------------------------------------------------
    # What is owed
    # ------------------------------------------------------------------
    #: ``amount`` (pre-existing) remains the NET base rent for the period, so
    #: every legacy reader keeps its meaning. It is re-declared as Monetary
    #: rather than Float: the column type is unchanged (both are numeric), but
    #: it now formats against the lease currency instead of the user's default,
    #: which matters the moment a portfolio holds leases in two currencies.
    amount = fields.Monetary(
        string='Net Base Rent', required=True, currency_field='currency_id',
        help="Base (rent) amount for this due date, net of incentives and "
             "before additional charges.",
    )
    base_rent = fields.Monetary(
        related='amount', string='Net Base Rent (alias)', readonly=False,
        help="Alias of the pre-existing 'amount' field, which has always held "
             "the net base rent for the period.",
    )
    gross_rent = fields.Monetary(
        string='Gross Rent',
        help="Contracted rent for the period before incentives. Recorded so a "
             "rent-free month is visible as a concession rather than as a "
             "missing line.",
    )
    incentive_relief = fields.Monetary(
        string='Incentive Relief',
        help="Rent relieved by rent-free / fit-out / discount incentives.",
    )
    charge_total = fields.Monetary(
        string='Recurring + One-Off Charges',
        compute='_compute_charge_totals', store=True,
    )
    recurring_charge_total = fields.Monetary(
        compute='_compute_charge_totals', store=True)
    one_time_charge_total = fields.Monetary(
        compute='_compute_charge_totals', store=True)

    tax_amount = fields.Monetary(
        string='Tax', compute='_compute_money', store=True,
        help="Read from the invoice once issued. Odoo computes tax; this "
             "module never does.",
    )
    amount_due = fields.Monetary(
        string='Amount Due', compute='_compute_money', store=True, index=True,
        help="Total the tenant owes for this obligation, including tax.",
    )

    proration_note = fields.Char(
        string='Proration', readonly=True,
        help="Plain-language explanation when the period was prorated, so an "
             "invoice is always answerable to a tenant.",
    )

    # ------------------------------------------------------------------
    # What has actually happened (Odoo Accounting is the source of truth)
    # ------------------------------------------------------------------
    amount_invoiced = fields.Monetary(
        string='Invoiced', compute='_compute_money', store=True,
    )
    amount_paid = fields.Monetary(
        string='Paid', compute='_compute_money', store=True,
    )
    amount_residual = fields.Monetary(
        string='Outstanding', compute='_compute_money', store=True, index=True,
    )
    is_settled = fields.Boolean(
        string='Settled', compute='_compute_money', store=True, index=True,
        help="True only when Odoo reports the invoice fully paid AND the "
             "residual is zero. A registered-but-unreconciled payment does not "
             "settle an obligation.",
    )

    # ------------------------------------------------------------------
    # Arrears (Phase 16)
    # ------------------------------------------------------------------
    # Arrears change every day without the obligation changing, so they are
    # not stored: they are computed for today when read and searched through
    # the due date (``_overdue_since_domain``).
    days_overdue = fields.Integer(
        string='Days Overdue', compute='_compute_arrears',
        search='_search_days_overdue',
    )
    overdue_bucket = fields.Selection(
        OVERDUE_BUCKETS, string='Ageing', compute='_compute_arrears',
        search='_search_overdue_bucket',
    )
    collection_state = fields.Selection(
        COLLECTION_STATES, string='Collection', default='none',
        help="Optional hook for a future Collections module. This module does "
             "not act on it.",
    )

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('period_start', 'period_end', 'date_due')
    def _compute_period_label(self):
        for rec in self:
            if rec.period_start and rec.period_end:
                rec.period_label = '%s → %s' % (rec.period_start, rec.period_end)
            elif rec.date_due:
                rec.period_label = str(rec.date_due)
            else:
                rec.period_label = ''

    @api.depends('charge_line_ids.amount', 'charge_line_ids.charge_rule_id')
    def _compute_charge_totals(self):
        for rec in self:
            recurring = 0.0
            one_off = 0.0
            for line in rec.charge_line_ids:
                if line.charge_rule_id and line.charge_rule_id.frequency != 'one_time':
                    recurring += line.amount
                else:
                    one_off += line.amount
            rec.recurring_charge_total = recurring
            rec.one_time_charge_total = one_off
            rec.charge_total = recurring + one_off

    @api.depends('move_id', 'move_id.state', 'move_id.payment_state',
                 'move_id.amount_total', 'move_id.amount_residual',
                 'move_id.amount_tax', 'amount_total')
    def _compute_money(self):
        """Read the money from Odoo, never from a local cache.

        Before an invoice exists the obligation is a *forecast*: due = the
        scheduled total, invoiced/paid = 0. Once an invoice exists, the invoice
        is authoritative for every figure including tax.
        """
        for rec in self:
            move = rec.move_id
            if move and move.state == 'posted':
                rec.tax_amount = move.amount_tax
                rec.amount_invoiced = move.amount_total
                rec.amount_residual = move.amount_residual
                rec.amount_paid = move.amount_total - move.amount_residual
                rec.amount_due = move.amount_total
                rec.is_settled = (
                    move.payment_state in ('paid', 'reversed')
                    and abs(move.amount_residual) < SETTLEMENT_EPSILON
                )
            elif move and move.state == 'cancel':
                rec.tax_amount = 0.0
                rec.amount_invoiced = 0.0
                rec.amount_residual = 0.0
                rec.amount_paid = 0.0
                rec.amount_due = 0.0
                rec.is_settled = False
            else:
                rec.tax_amount = 0.0
                rec.amount_invoiced = 0.0
                rec.amount_paid = 0.0
                rec.amount_due = rec.amount_total or 0.0
                rec.amount_residual = rec.amount_total or 0.0
                rec.is_settled = False

    @api.depends('date_due', 'amount_residual', 'is_settled', 'state')
    def _compute_arrears(self):
        today = fields.Date.context_today(self)
        for rec in self:
            outstanding = (
                not rec.is_settled
                and rec.state not in ('cancelled',)
                and rec.amount_residual > SETTLEMENT_EPSILON
            )
            if outstanding and rec.date_due and rec.date_due < today:
                rec.days_overdue = (today - rec.date_due).days
            else:
                rec.days_overdue = 0
            rec.overdue_bucket = overdue_bucket(rec.days_overdue)

    @api.model
    def _outstanding_domain(self):
        """Obligations with money still owed -- the precondition of arrears."""
        return [('is_settled', '=', False),
                ('state', '!=', 'cancelled'),
                ('amount_residual', '>', SETTLEMENT_EPSILON)]

    @api.model
    def _overdue_since_domain(self, days):
        """Obligations at least ``days`` days overdue today.

        The same rule as ``_compute_arrears``: outstanding, and due ``days`` or
        more days before today.
        """
        if days <= 0:
            return list(expression.TRUE_DOMAIN)
        today = fields.Date.context_today(self)
        return self._outstanding_domain() + [
            ('date_due', '<=', today - timedelta(days=days))]

    @api.model
    def _overdue_bucket_domain(self, bucket):
        """Obligations in one ageing bucket today."""
        low, high = OVERDUE_BUCKET_RANGES[bucket]
        domain = self._overdue_since_domain(low) if low else list(expression.TRUE_DOMAIN)
        if high is not None:
            domain = expression.AND([domain, _negate(self._overdue_since_domain(high + 1))])
        return domain

    def _search_days_overdue(self, operator, value):
        if operator not in ('=', '!=', '<', '<=', '>', '>='):
            raise UserError(_("Days Overdue cannot be searched with '%s'.", operator))
        value = int(value or 0)
        at_least = self._overdue_since_domain
        if operator == '>=':
            return at_least(value)
        if operator == '>':
            return at_least(value + 1)
        if operator == '<':
            return _negate(at_least(value))
        if operator == '<=':
            return _negate(at_least(value + 1))
        exact = expression.AND([at_least(value), _negate(at_least(value + 1))])
        return exact if operator == '=' else _negate(exact)

    def _search_overdue_bucket(self, operator, value):
        if operator in ('=', '!='):
            buckets = [value]
        elif operator in ('in', 'not in'):
            buckets = list(value)
        else:
            raise UserError(_("Ageing cannot be searched with '%s'.", operator))
        known = [bucket for bucket in buckets if bucket in OVERDUE_BUCKET_RANGES]
        domain = (expression.OR([self._overdue_bucket_domain(b) for b in known])
                  if known else list(expression.FALSE_DOMAIN))
        return _negate(domain) if operator in ('!=', 'not in') else domain

    # ==================================================================
    # State -- tightened settlement rule
    # ==================================================================
    @api.depends('move_id', 'move_id.state', 'move_id.payment_state',
                 'move_id.amount_residual')
    def _compute_state(self):
        """Override the base implementation.

        The pre-upgrade version treated ``in_payment`` as paid. A payment that
        has been registered but not yet reconciled against the bank statement
        is *not* collected cash, so an obligation in that situation stays
        'Invoiced'. ``payment_state`` remains available for callers that want
        the granular Odoo value.
        """
        for rec in self:
            move = rec.move_id
            if not move:
                rec.state = 'draft'
            elif move.state == 'cancel':
                rec.state = 'cancelled'
            elif (move.payment_state in ('paid', 'reversed')
                    and abs(move.amount_residual) < SETTLEMENT_EPSILON):
                rec.state = 'paid'
            elif move.state == 'posted':
                rec.state = 'invoiced'
            else:
                rec.state = 'draft'

    # ==================================================================
    # Actions
    # ==================================================================
    def action_open_invoice(self):
        self.ensure_one()
        if not self.move_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
        }

    def action_view_payments(self):
        """Open the Odoo payments reconciled against this obligation."""
        self.ensure_one()
        payments = self.move_id._get_reconciled_payments() if self.move_id else False
        return {
            'type': 'ir.actions.act_window',
            'name': _('Payments'),
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': [('id', 'in', payments.ids if payments else [])],
        }


class BillingObligationChargeLine(models.Model):
    """Trace each charge line back to the rule that generated it."""
    _inherit = 'realestate.contract.payment.line'

    charge_rule_id = fields.Many2one(
        'realestate.contract.charge.rule', string='Recurring Charge',
        ondelete='set null', index=True,
        help="The recurring-charge rule that produced this line. Empty for "
             "manually added or maintenance-recharge lines.",
    )
    property_line_id = fields.Many2one(
        'realestate.contract.property.line', string='Property Allocation',
        ondelete='set null',
    )
