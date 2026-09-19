"""Rent escalation (Phase 11).

The pre-upgrade module had ``realestate.contract.increment.rule``: a global
catalogue of "+X after month N" rules attached to contracts by many2many. It
worked, but it had three problems for enterprise leasing:

1. rules were **shared records** -- editing one silently re-priced every lease
   that referenced it, rewriting commercial history
2. there was no way to express "Year 1 = 500,000, Year 2 = 550,000" as agreed
   amounts rather than as deltas
3. escalation was *applied* imperatively during schedule generation, so
   regenerating a schedule could double-apply

This model is **declarative and per-lease**. Each row says "from this date, the
rent is this". :meth:`ContractEscalationMixin._rent_on` answers "what is the
rent on date D" by walking the rows in order. Nothing is mutated, so schedule
generation is idempotent by construction and duplicate application is not
possible -- there is no application step to run twice.

The legacy increment rules are untouched and still honoured by the legacy
schedule generator.
"""

from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

ESCALATION_TYPES = [
    ('percentage', 'Percentage Increase'),
    ('fixed', 'Fixed Increase'),
    ('scheduled_amount', 'Scheduled Rent Amount'),
    ('index', 'Index-Linked (manual)'),
]


class RentEscalationRule(models.Model):
    _name = 'realestate.rent.escalation.rule'
    _description = 'Rent Escalation Rule'
    _inherit = ['mail.thread']
    _order = 'contract_id, effective_date, sequence, id'

    sequence = fields.Integer(default=10)
    contract_id = fields.Many2one(
        'realestate.contract', string='Lease', required=True,
        ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related='contract_id.currency_id', store=True, readonly=True,
    )

    effective_date = fields.Date(
        string='Effective From', required=True, index=True, tracking=True,
        help="The first day the new rent applies.",
    )
    escalation_type = fields.Selection(
        ESCALATION_TYPES, string='Type', required=True, default='percentage',
        tracking=True,
    )
    percentage = fields.Float(string='Percentage (%)', tracking=True)
    fixed_amount = fields.Monetary(string='Fixed Increase', tracking=True)
    scheduled_amount = fields.Monetary(
        string='Scheduled Rent', tracking=True,
        help="The agreed rent from this date -- an absolute figure, not a delta.",
    )
    index_basis = fields.Char(
        string='Index Basis', tracking=True,
        help="Which index the increase references (e.g. 'CPI Egypt, Jan "
             "2027'). Stored for the audit trail; this module does not fetch "
             "external indexes.",
    )

    base_amount = fields.Monetary(
        string='Base Rent', compute='_compute_amounts', store=True,
        help="Rent in force immediately before this rule takes effect.",
    )
    resulting_amount = fields.Monetary(
        string='Resulting Rent', compute='_compute_amounts', store=True,
        tracking=True,
    )
    increase_amount = fields.Monetary(
        string='Increase', compute='_compute_amounts', store=True,
    )
    increase_pct = fields.Float(
        string='Increase (%)', compute='_compute_amounts', store=True,
    )

    notes = fields.Text()

    _sql_constraints = [
        ('one_rule_per_date',
         'unique(contract_id, effective_date)',
         'A lease can only have one escalation taking effect on a given date.'),
    ]

    # ------------------------------------------------------------------
    # Chained amount computation
    # ------------------------------------------------------------------
    @api.depends('contract_id.price', 'contract_id.escalation_rule_ids.effective_date',
                 'contract_id.escalation_rule_ids.escalation_type',
                 'contract_id.escalation_rule_ids.percentage',
                 'contract_id.escalation_rule_ids.fixed_amount',
                 'contract_id.escalation_rule_ids.scheduled_amount',
                 'effective_date', 'escalation_type', 'percentage',
                 'fixed_amount', 'scheduled_amount')
    def _compute_amounts(self):
        """Walk each lease's rules in date order, chaining the rent forward.

        Grouped per contract so a lease with 10 escalations costs one pass, not
        10 recursive lookups.
        """
        for contract in self.mapped('contract_id'):
            running = contract.price or 0.0
            # `_origin.id`: lines being added on the lease form are unsaved
            # records whose NewId cannot be compared. A line still missing
            # its date sorts last rather than at an arbitrary "today".
            rules = contract.escalation_rule_ids.sorted(
                lambda r: (r.effective_date or date.max, r.sequence, r._origin.id or 0))
            for rule in rules:
                base = running
                result = rule._apply_to(base)
                # Only write the records we were actually asked to compute;
                # the rest are updated by their own compute pass.
                if rule in self:
                    rule.base_amount = base
                    rule.resulting_amount = result
                    rule.increase_amount = result - base
                    rule.increase_pct = ((result - base) / base * 100.0) if base else 0.0
                running = result
        # Anything not attached to a contract (shouldn't happen, but compute
        # must assign every record it is given).
        for rule in self.filtered(lambda r: not r.contract_id):
            rule.base_amount = 0.0
            rule.resulting_amount = 0.0
            rule.increase_amount = 0.0
            rule.increase_pct = 0.0

    def _apply_to(self, base):
        """Resulting rent when this rule is applied to ``base``."""
        self.ensure_one()
        if self.escalation_type == 'percentage':
            return base * (1.0 + (self.percentage or 0.0) / 100.0)
        if self.escalation_type == 'fixed':
            return base + (self.fixed_amount or 0.0)
        if self.escalation_type in ('scheduled_amount', 'index'):
            # An index-linked rule stores the *negotiated result*; the basis is
            # recorded for audit but never computed from an external feed.
            return self.scheduled_amount or base
        return base

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('effective_date', 'contract_id')
    def _check_within_term(self):
        for rec in self:
            contract = rec.contract_id
            if not contract.start_date:
                continue
            if rec.effective_date <= contract.start_date:
                raise ValidationError(_(
                    "Escalation on lease '%(lease)s' takes effect %(date)s, "
                    "which is on or before the lease start (%(start)s). The "
                    "starting rent is the lease's Base Rent -- escalations must "
                    "come after it.",
                    lease=contract.display_name, date=rec.effective_date,
                    start=contract.start_date))
            if contract.end_date and rec.effective_date > contract.end_date:
                raise ValidationError(_(
                    "Escalation on lease '%(lease)s' takes effect after the "
                    "lease ends (%(end)s).",
                    lease=contract.display_name, end=contract.end_date))

    @api.constrains('escalation_type', 'percentage', 'fixed_amount',
                    'scheduled_amount')
    def _check_inputs(self):
        for rec in self:
            if rec.escalation_type == 'percentage' and not rec.percentage:
                raise ValidationError(_(
                    "Percentage escalation on %s has no percentage set.",
                    rec.effective_date))
            if rec.escalation_type == 'fixed' and not rec.fixed_amount:
                raise ValidationError(_(
                    "Fixed escalation on %s has no amount set.", rec.effective_date))
            if rec.escalation_type in ('scheduled_amount', 'index') and not rec.scheduled_amount:
                raise ValidationError(_(
                    "Scheduled-rent escalation on %s has no rent amount set.",
                    rec.effective_date))

    @api.depends('effective_date', 'resulting_amount')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = _("From %(date)s: %(amount)s") % {
                'date': rec.effective_date or '?',
                'amount': rec.resulting_amount,
            }


class ContractEscalationMixin(models.Model):
    _inherit = 'realestate.contract'

    escalation_rule_ids = fields.One2many(
        'realestate.rent.escalation.rule', 'contract_id',
        string='Rent Escalations', copy=True,
    )
    escalation_count = fields.Integer(compute='_compute_escalation_summary')
    next_escalation_date = fields.Date(
        string='Next Escalation', compute='_compute_escalation_summary',
        store=True, index=True,
    )
    next_escalation_pct = fields.Float(
        string='Next Escalation (%)', compute='_compute_escalation_summary', store=True,
    )
    next_escalation_amount = fields.Monetary(
        string='Next Escalation Rent', compute='_compute_escalation_summary', store=True,
    )
    current_rent = fields.Monetary(
        string='Current Rent', compute='_compute_current_rent', store=True,
        help="Base rent in force today, after any escalations that have taken "
             "effect.",
    )

    @api.depends('escalation_rule_ids.effective_date',
                 'escalation_rule_ids.resulting_amount',
                 'escalation_rule_ids.increase_pct')
    def _compute_escalation_summary(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.escalation_count = len(rec.escalation_rule_ids)
            upcoming = rec.escalation_rule_ids.filtered(
                lambda r: r.effective_date and r.effective_date > today
            ).sorted('effective_date')
            nxt = upcoming[:1]
            rec.next_escalation_date = nxt.effective_date if nxt else False
            rec.next_escalation_pct = nxt.increase_pct if nxt else 0.0
            rec.next_escalation_amount = nxt.resulting_amount if nxt else 0.0

    @api.depends('price', 'escalation_rule_ids.effective_date',
                 'escalation_rule_ids.resulting_amount')
    def _compute_current_rent(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.current_rent = rec._rent_on(today)

    def _rent_on(self, on_date):
        """Base rent in force on ``on_date``.

        The single source of truth for "what is the rent". Deterministic,
        side-effect free, and safe to call any number of times -- which is what
        makes schedule regeneration idempotent.
        """
        self.ensure_one()
        rent = self.price or 0.0
        # Same key as the rent chain in `_compute_amounts`: on the lease form the
        # rules are unsaved, so ties are broken by the saved id (0 when new),
        # never by comparing unsaved ids; a rule not yet dated goes last.
        for rule in self.escalation_rule_ids.sorted(
                lambda r: (r.effective_date or date.max, r.sequence, r._origin.id or 0)):
            if rule.effective_date and rule.effective_date <= on_date:
                rent = rule.resulting_amount
            else:
                break
        return rent

    # ------------------------------------------------------------------
    # Generator
    # ------------------------------------------------------------------
    def action_generate_escalations(self, percentage=None, every_months=12,
                                    occurrences=None, replace=False):
        """Expand "+X% every N months" into explicit dated rules.

        Kept as a generator rather than a recurring rule so that what the
        system will charge is visible on the lease as a list of dated amounts,
        and so a single year can be renegotiated without unpicking a formula.
        """
        self.ensure_one()
        self._require_group('atmta_real_estate.group_rental_manager')
        if not self.start_date:
            raise UserError(_("Set the lease start date first."))
        percentage = percentage if percentage is not None else 0.0
        if not percentage:
            raise UserError(_("Set an escalation percentage."))
        every_months = max(int(every_months or 12), 1)

        if replace:
            self.escalation_rule_ids.unlink()

        if occurrences is None:
            if not self.end_date:
                raise UserError(_(
                    "Set a lease end date, or pass an explicit number of "
                    "occurrences."))
            span_months = ((self.end_date.year - self.start_date.year) * 12
                           + (self.end_date.month - self.start_date.month))
            occurrences = max(span_months // every_months, 0)

        Rule = self.env['realestate.rent.escalation.rule']
        created = Rule
        for step in range(1, int(occurrences) + 1):
            effective = self.start_date + relativedelta(months=every_months * step)
            if self.end_date and effective > self.end_date:
                break
            if self.escalation_rule_ids.filtered(
                    lambda r, d=effective: r.effective_date == d):
                continue  # never duplicate an existing dated rule
            created |= Rule.create({
                'contract_id': self.id,
                'effective_date': effective,
                'escalation_type': 'percentage',
                'percentage': percentage,
            })
        if created:
            self.message_post(body=_(
                "Generated %(count)s rent escalation(s) of %(pct)s%% every "
                "%(months)s months.",
                count=len(created), pct=percentage, months=every_months))
        return created

    def action_view_escalations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Rent Escalations'),
            'res_model': 'realestate.rent.escalation.rule',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }
