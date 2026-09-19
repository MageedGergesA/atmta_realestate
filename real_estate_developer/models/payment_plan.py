# -*- coding: utf-8 -*-
"""Phases 11–14 — the developer payment plan.

**Why this exists at all (Rule 4).** Odoo's `account.payment.term` describes how
*one invoice* is split into due dates. A developer plan is not that: it is a
5–10 year commercial agreement that produces many independent obligations and
many invoices. The Phase 0 audit found two concrete consequences of pretending
otherwise:

* months were approximated as **30 days**, so a 96-month plan drifted to 2,880
  days — 7.9 years, with no instalment ever landing on a fixed day of the month;
* `action_re_generate_lines()` **destroyed and rebuilt** the term's lines in
  place, silently restating the schedule of every contract still referencing it.

Both are structural, not bugs to patch. So developer schedules move here, and
`account.payment.term` goes back to meaning what Odoo means by it.

Dates are computed with `relativedelta`, so "3 months after booking" lands on
the same day of the month, every time, for ten years.
"""

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

PLAN_STATE = [
    ('draft', 'Draft'),
    ('pending_approval', 'Pending Approval'),
    ('active', 'Active'),
    ('expired', 'Expired'),
    ('archived', 'Archived'),
]

#: States in which a plan's schedule may no longer be edited.
PLAN_FROZEN = ('active', 'expired', 'archived')

#: What an instalment *is*. Kept explicit rather than free text because
#: downstream (Checks, collections, revenue reporting) groups on it.
LINE_KIND = [
    ('booking', 'Booking Amount'),
    ('down_payment', 'Down Payment'),
    ('installment', 'Instalment'),
    ('balloon', 'Balloon Payment'),
    ('handover', 'Handover Payment'),
    ('maintenance', 'Maintenance'),
    ('service', 'Service Charge'),
    ('custom', 'Other'),
]

CALC_TYPE = [
    ('percent', '% of Price'),
    ('fixed_amount', 'Fixed Amount'),
    ('residual', 'Residual (balance to 100%)'),
]

DATE_RULE = [
    ('on_booking', 'On Booking'),
    ('days_after_booking', 'Days After Booking'),
    ('months_after_booking', 'Months After Booking'),
    ('months_after_contract', 'Months After Contract'),
    ('on_handover', 'On Handover'),
    ('fixed_date', 'Fixed Date'),
]

INTERVAL_MONTHS = {
    'monthly': 1,
    'quarterly': 3,
    'semiannual': 6,
    'annual': 12,
}


class PaymentPlan(models.Model):
    _name = 'realestate.payment.plan'
    _description = 'Real Estate Payment Plan'
    _inherit = ['mail.thread', 'mail.activity.mixin',
                'realestate.commercial.approval.mixin']
    _order = 'project_id, sequence, version desc, id desc'
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True, index='trigram')
    code = fields.Char(copy=False)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    project_id = fields.Many2one(
        'realestate.project', string='Project', ondelete='cascade',
        index=True, tracking=True, check_company=True,
        help="Leave empty for a plan available across the whole company.")
    phase_id = fields.Many2one(
        'realestate.phase', string='Phase', ondelete='cascade',
        domain="[('project_id', '=', project_id)]", check_company=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)

    state = fields.Selection(
        PLAN_STATE, default='draft', required=True, tracking=True,
        index=True, copy=False)
    active = fields.Boolean(default=True)

    # ---- Versioning (Phase 13) ----
    version = fields.Integer(default=1, required=True, readonly=True, copy=False)
    previous_version_id = fields.Many2one(
        'realestate.payment.plan', string='Supersedes', readonly=True,
        copy=False, ondelete='restrict')

    valid_from = fields.Date(tracking=True)
    valid_until = fields.Date(tracking=True)

    # ---- Custom plans (Phase 14) ----
    is_custom = fields.Boolean(
        string='Negotiated Plan', readonly=True, copy=False,
        help="A one-off plan agreed for a single deal. It never becomes a "
             "template and never appears in the standard plan list.")
    source_plan_id = fields.Many2one(
        'realestate.payment.plan', string='Based On', readonly=True, copy=False,
        help="The template this negotiated plan started from.")

    line_ids = fields.One2many(
        'realestate.payment.plan.line', 'plan_id', string='Schedule', copy=True)

    # ---- Booking-fee treatment (Phase 18 depends on this) ----
    booking_handling = fields.Selection([
        ('part_of_price', 'Counts Towards the Price'),
        ('separate', 'Charged on Top of the Price'),
    ], default='part_of_price', required=True, tracking=True,
        help="Whether the booking amount is part of the 100% or an extra "
             "charge. Getting this wrong is how the same money gets recorded "
             "twice.")

    total_percent = fields.Float(
        compute='_compute_totals', store=True, string='Total %')
    duration_months = fields.Integer(
        compute='_compute_totals', store=True, string='Duration (months)')
    installment_count = fields.Integer(
        compute='_compute_totals', store=True, string='Payments')

    notes = fields.Html()
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)

    _sql_constraints = [
        ('payment_plan_version_uniq',
         'UNIQUE(company_id, project_id, phase_id, name, version)',
         'A payment plan with this name and version already exists here.'),
    ]

    # ------------------------------------------------------------------
    # Totals
    # ------------------------------------------------------------------
    @api.depends('line_ids.value', 'line_ids.calculation_type',
                 'line_ids.occurrences', 'line_ids.date_rule',
                 'line_ids.offset_value', 'line_ids.interval')
    def _compute_totals(self):
        for plan in self:
            percent = 0.0
            count = 0
            for line in plan.line_ids:
                count += max(line.occurrences or 1, 1)
                if line.calculation_type == 'percent':
                    percent += (line.value or 0.0) * max(line.occurrences or 1, 1)
            plan.total_percent = percent
            plan.installment_count = count
            plan.duration_months = plan._duration_months()

    def _duration_months(self):
        """How far into the future the last payment falls, in months."""
        self.ensure_one()
        longest = 0
        for line in self.line_ids:
            longest = max(longest, line._last_offset_months())
        return longest

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @api.constrains('valid_from', 'valid_until')
    def _check_validity(self):
        for plan in self:
            if (plan.valid_from and plan.valid_until
                    and plan.valid_until < plan.valid_from):
                raise ValidationError(_(
                    "Plan '%s': validity ends before it starts.") % plan.name)

    def _check_schedule_completeness(self):
        """The schedule must account for exactly 100% of the price.

        Enforced at activation rather than on every keystroke, so a plan can be
        built up line by line without fighting the validator.
        """
        self.ensure_one()
        residual_lines = self.line_ids.filtered(
            lambda l: l.calculation_type == 'residual')
        fixed_lines = self.line_ids.filtered(
            lambda l: l.calculation_type == 'fixed_amount')

        if not self.line_ids:
            raise UserError(_("Plan '%s' has no schedule.") % self.name)

        if fixed_lines and not residual_lines:
            # A plan mixing fixed amounts with percentages cannot be validated
            # against 100% without knowing the price, so it needs a residual
            # line to absorb whatever is left.
            raise UserError(_(
                "Plan '%s' contains fixed amounts but no residual line. "
                "Add a 'Residual' line so the schedule always reaches the full "
                "price, whatever the unit costs."
            ) % self.name)

        if residual_lines:
            if len(residual_lines) > 1:
                raise UserError(_(
                    "Plan '%s' has %s residual lines. Only one line can absorb "
                    "the balance, or the split is ambiguous."
                ) % (self.name, len(residual_lines)))
            if self.total_percent > 100.0001:
                raise UserError(_(
                    "Plan '%s' already allocates %.2f%%, so the residual line "
                    "would be negative."
                ) % (self.name, self.total_percent))
            return

        if abs(self.total_percent - 100.0) > 0.0001:
            raise UserError(_(
                "Plan '%s' allocates %.2f%% of the price, not 100%%.\n\n"
                "Either adjust the percentages or add a 'Residual' line to "
                "absorb the remaining %.2f%%."
            ) % (self.name, self.total_percent, 100.0 - self.total_percent))

    # ------------------------------------------------------------------
    # Immutability + versioning
    # ------------------------------------------------------------------
    SCHEDULE_FIELDS = {'line_ids', 'booking_handling', 'currency_id',
                       'project_id', 'phase_id', 'version'}

    def write(self, vals):
        if self.SCHEDULE_FIELDS & set(vals):
            frozen = self.filtered(lambda p: p.state in PLAN_FROZEN)
            if frozen:
                raise UserError(_(
                    "Plan '%s' is %s and its schedule can no longer be "
                    "changed.\n\nUse 'New Version'. Editing an active plan "
                    "would restate the schedule of every deal signed under it."
                ) % (frozen[0].display_name,
                     dict(PLAN_STATE)[frozen[0].state]))
        return super().write(vals)

    def unlink(self):
        used = self.filtered(lambda p: p.state != 'draft')
        if used:
            raise UserError(_(
                "Only draft plans can be deleted. Archive '%s' instead."
            ) % used[0].display_name)
        return super().unlink()

    def action_submit(self):
        for plan in self:
            if plan.state != 'draft':
                raise UserError(_("Only draft plans can be submitted."))
            plan._check_schedule_completeness()
            plan.state = 'pending_approval'

    def action_activate(self):
        for plan in self:
            if plan.state not in ('draft', 'pending_approval'):
                raise UserError(_(
                    "Plan '%s' cannot be activated from %s."
                ) % (plan.name, dict(PLAN_STATE)[plan.state]))
            plan._check_schedule_completeness()
            plan.write({
                'state': 'active',
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })

    def action_expire(self):
        self.write({'state': 'expired'})

    def action_archive_plan(self):
        self.write({'state': 'archived'})

    def action_new_version(self):
        """Copy as the next version, in draft. The only supported reprice.

        Changing "10% down over 8 years" into "15% down over 7" must not touch
        a single existing deal, so the old plan is left exactly as it is.
        """
        self.ensure_one()
        latest = self.search([
            ('company_id', '=', self.company_id.id),
            ('project_id', '=', self.project_id.id),
            ('phase_id', '=', self.phase_id.id),
            ('name', '=', self.name),
        ], order='version desc', limit=1)
        new_plan = self.copy({
            'version': (latest.version or self.version) + 1,
            'previous_version_id': self.id,
            'state': 'draft',
            'approved_by_id': False,
            'approved_on': False,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('New Plan Version'),
            'res_model': self._name,
            'views': [(False, 'form')],
            'view_mode': 'form',
            'res_id': new_plan.id,
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Phase 14 — negotiated plans
    # ------------------------------------------------------------------
    def action_create_custom_copy(self):
        """Fork this template into a one-off plan for a single deal.

        The template is never edited to satisfy one buyer. The fork is flagged
        `is_custom`, so it stays out of the standard plan list and its
        divergence from the template can be reported on.
        """
        self.ensure_one()
        custom = self.copy({
            'name': _('%s (negotiated)') % self.name,
            'state': 'draft',
            'is_custom': True,
            'source_plan_id': self.id,
            'version': 1,
            'previous_version_id': False,
            'approved_by_id': False,
            'approved_on': False,
        })
        return custom

    def _custom_divergence(self):
        """How far a negotiated plan strays from its template.

        Returned as a percentage-point difference in the down payment and a
        month difference in duration — the two things a commercial director
        actually asks about.
        """
        self.ensure_one()
        if not self.source_plan_id:
            return {}
        def down_pct(plan):
            return sum(
                (l.value or 0.0) * max(l.occurrences or 1, 1)
                for l in plan.line_ids
                if l.kind == 'down_payment' and l.calculation_type == 'percent')
        return {
            'down_payment_delta': down_pct(self) - down_pct(self.source_plan_id),
            'duration_delta_months': (
                self.duration_months - self.source_plan_id.duration_months),
        }

    # ------------------------------------------------------------------
    # Phase 12 — schedule generation and preview
    # ------------------------------------------------------------------
    def _generate_schedule(self, total_price, booking_date=None,
                           contract_date=None, handover_date=None,
                           booking_amount=0.0):
        """Expand this plan into concrete obligations for one deal.

        Returns a list of dicts, in date order, each with ``sequence``,
        ``kind``, ``name``, ``percent``, ``amount``, ``date_due`` and running
        cumulative figures.

        The **last** line absorbs rounding so the schedule sums to
        ``total_price`` exactly at the currency's precision. Silent residual
        drift on a ten-year plan is how a customer ends up owing 3 EGP forever.
        """
        self.ensure_one()
        currency = self.currency_id
        booking_date = booking_date or fields.Date.context_today(self)
        contract_date = contract_date or booking_date

        rows = []
        for line in self.line_ids.sorted(lambda l: (l.sequence, l._origin.id or 0)):
            rows.extend(line._expand(
                total_price=total_price,
                booking_date=booking_date,
                contract_date=contract_date,
                handover_date=handover_date,
                booking_amount=booking_amount,
            ))

        # Resolve the residual line now that every other amount is known.
        allocated = sum(r['amount'] for r in rows if not r.get('is_residual'))
        for row in rows:
            if row.get('is_residual'):
                row['amount'] = currency.round(total_price - allocated)
                row['percent'] = (
                    row['amount'] / total_price * 100.0 if total_price else 0.0)

        rows.sort(key=lambda r: (r['date_due'], r['sequence']))
        for index, row in enumerate(rows, start=1):
            row['sequence'] = index * 10
            row['amount'] = currency.round(row['amount'])

        # Absorb ROUNDING on the final row — and only rounding.
        #
        # The largest error that per-row rounding can accumulate is one
        # rounding unit per row, so anything bigger than that is not rounding:
        # it is a plan that does not add up to 100%, and silently topping up the
        # last payment would hide a misconfiguration by billing the customer the
        # difference. Beyond the tolerance the drift is left in place for
        # `_validate_schedule_total` to reject.
        total = sum(r['amount'] for r in rows)
        drift = currency.round(total_price - total)
        tolerance = (currency.rounding or 0.01) * max(len(rows), 1)
        if rows and drift and abs(drift) <= tolerance:
            rows[-1]['amount'] = currency.round(rows[-1]['amount'] + drift)

        running = 0.0
        for row in rows:
            running += row['amount']
            row['cumulative_amount'] = running
            row['cumulative_percent'] = (
                running / total_price * 100.0 if total_price else 0.0)
            row['remaining_amount'] = currency.round(total_price - running)
            row.pop('is_residual', None)
        return rows

    def _validate_schedule_total(self, rows, total_price):
        """Prove the generated schedule adds up. Never let drift pass silently."""
        self.ensure_one()
        currency = self.currency_id
        total = currency.round(sum(r['amount'] for r in rows))
        if currency.compare_amounts(total, currency.round(total_price)) != 0:
            raise UserError(_(
                "Plan '%s' produced a schedule totalling %s, but the price is "
                "%s. The plan is misconfigured — check for a missing residual "
                "line."
            ) % (self.name, total, total_price))
        return True

    def action_preview(self):
        """Open the preview wizard for a sample price."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Schedule Preview — %s') % self.display_name,
            'res_model': 'realestate.payment.plan.preview',
            'views': [(False, 'form')],
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_plan_id': self.id},
        }

    @api.model
    def _available_for(self, property_record, date=None):
        """Plans a buyer may choose for this unit today."""
        date = date or fields.Date.context_today(self)
        domain = [
            ('state', '=', 'active'),
            ('is_custom', '=', False),
            ('company_id', '=', property_record.company_id.id),
            '|', ('valid_from', '=', False), ('valid_from', '<=', date),
            '|', ('valid_until', '=', False), ('valid_until', '>=', date),
            '|', ('project_id', '=', False),
            ('project_id', '=', property_record.project_id.id),
        ]
        plans = self.search(domain)
        return plans.filtered(
            lambda p: not p.phase_id or p.phase_id == property_record.phase_id)


class PaymentPlanLine(models.Model):
    """One element of a plan — possibly repeating.

    A single line can stand for "48 quarterly instalments of 1.5%", which is how
    a developer actually describes a plan, instead of forcing 48 rows.
    """
    _name = 'realestate.payment.plan.line'
    _description = 'Payment Plan Line'
    _order = 'plan_id, sequence, id'
    _check_company_auto = True

    plan_id = fields.Many2one(
        'realestate.payment.plan', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='plan_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='plan_id.currency_id', store=True, readonly=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Label')

    kind = fields.Selection(LINE_KIND, required=True, default='installment')
    calculation_type = fields.Selection(
        CALC_TYPE, required=True, default='percent', string='Amount')
    value = fields.Float(
        string='Value',
        help="A percentage of the price, or a fixed amount. Ignored for a "
             "residual line, which takes whatever is left.")

    occurrences = fields.Integer(
        string='Count', default=1, required=True,
        help="How many payments this line stands for. 48 quarterly "
             "instalments is one line with a count of 48.")
    interval = fields.Selection([
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('semiannual', 'Semi-annual'),
        ('annual', 'Annual'),
    ], default='monthly',
        help="Spacing between repeats. Ignored when the count is 1.")

    date_rule = fields.Selection(
        DATE_RULE, required=True, default='months_after_booking',
        string='Due')
    offset_value = fields.Integer(
        string='Offset',
        help="Days or months after the anchor, depending on the rule.")
    fixed_date = fields.Date(string='On Date')

    @api.constrains('occurrences')
    def _check_occurrences(self):
        for line in self:
            if line.occurrences < 1:
                raise ValidationError(_(
                    "A plan line must stand for at least one payment."))

    @api.constrains('calculation_type', 'value')
    def _check_value(self):
        for line in self:
            if line.calculation_type == 'residual':
                continue
            if line.value <= 0:
                raise ValidationError(_(
                    "Line '%s': the amount must be positive."
                ) % (line.name or line.kind))
            if line.calculation_type == 'percent' and line.value > 100:
                raise ValidationError(_(
                    "Line '%s': %.2f%% of the price in a single payment is "
                    "almost certainly a typo."
                ) % (line.name or line.kind, line.value))

    @api.constrains('date_rule', 'fixed_date')
    def _check_fixed_date(self):
        for line in self:
            if line.date_rule == 'fixed_date' and not line.fixed_date:
                raise ValidationError(_(
                    "Line '%s' is due on a fixed date, but no date is set."
                ) % (line.name or line.kind))

    def _last_offset_months(self):
        """Months from the anchor to this line's final payment."""
        self.ensure_one()
        step = INTERVAL_MONTHS.get(self.interval, 1)
        base = 0
        if self.date_rule in ('months_after_booking', 'months_after_contract'):
            base = self.offset_value or 0
        elif self.date_rule == 'days_after_booking':
            base = int(round((self.offset_value or 0) / 30.0))
        return base + step * (max(self.occurrences or 1, 1) - 1)

    def _due_date(self, index, booking_date, contract_date, handover_date):
        """The due date of repeat ``index`` (0-based).

        Uses ``relativedelta`` so "3 months after booking" means the same day
        of the month three months later — not 90 days later. Over a 96-month
        plan the difference is roughly two months of drift.
        """
        self.ensure_one()
        step = INTERVAL_MONTHS.get(self.interval, 1)
        offset = self.offset_value or 0

        if self.date_rule == 'fixed_date':
            # A fixed date cannot repeat meaningfully; repeats step by interval.
            return self.fixed_date + relativedelta(months=step * index)
        if self.date_rule == 'on_booking':
            return booking_date + relativedelta(months=step * index)
        if self.date_rule == 'days_after_booking':
            return (booking_date + relativedelta(days=offset)
                    + relativedelta(months=step * index))
        if self.date_rule == 'months_after_booking':
            return booking_date + relativedelta(months=offset + step * index)
        if self.date_rule == 'months_after_contract':
            return contract_date + relativedelta(months=offset + step * index)
        if self.date_rule == 'on_handover':
            if not handover_date:
                # No handover date known yet. Falling back to the booking date
                # would invent a due date years too early, so the caller is told
                # instead of being given a wrong number.
                raise UserError(_(
                    "Line '%s' is due on handover, but this deal has no "
                    "expected handover date yet. Set one on the project or the "
                    "contract before generating the schedule."
                ) % (self.name or self.kind))
            return handover_date + relativedelta(months=step * index)
        return booking_date

    def _expand(self, total_price, booking_date, contract_date,
                handover_date, booking_amount=0.0):
        """Turn this line into one dict per payment it represents."""
        self.ensure_one()
        label = self.name or dict(LINE_KIND).get(self.kind, self.kind)
        count = max(self.occurrences or 1, 1)
        rows = []
        for index in range(count):
            if self.calculation_type == 'percent':
                amount = total_price * (self.value or 0.0) / 100.0
                percent = self.value or 0.0
            elif self.calculation_type == 'fixed_amount':
                amount = self.value or 0.0
                percent = (amount / total_price * 100.0) if total_price else 0.0
            else:
                amount = 0.0          # resolved by the plan once the rest is known
                percent = 0.0
            rows.append({
                'sequence': self.sequence * 100 + index,
                'plan_line_id': self.id,
                'kind': self.kind,
                'name': ('%s %s/%s' % (label, index + 1, count)
                         if count > 1 else label),
                'percent': percent,
                'amount': amount,
                'date_due': self._due_date(
                    index, booking_date, contract_date, handover_date),
                'is_residual': self.calculation_type == 'residual',
            })
        return rows
