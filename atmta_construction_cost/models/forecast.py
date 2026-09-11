# -*- coding: utf-8 -*-
"""M3 — forecasting: what do we now expect this project to cost when complete?

```
    ETC               = expected future cost from the forecast date forward
    EAC               = ACTUAL + ETC
    FORECAST VARIANCE = CURRENT BUDGET − EAC      positive = favourable
```

### There is no universal ETC formula, and pretending otherwise is the bug

A fixed-price subcontract forecasts well from its remaining commitment. Direct
labour does not — it needs productivity. Unawarded scope has no commitment to
forecast from at all, and its ETC is a management estimate. A single formula
would be wrong for most of a project and would be wrong *quietly*.

So ETC is produced by a **named method** per line, each with its own
prerequisites, and every line records which method produced its number and from
what. A cost controller asked "why is ETC 3.7M?" can answer without reading
Python.

### Missing is not zero

The single most important rule in this milestone. A cost code nobody has
forecast has **no** ETC; it does not have an ETC of zero. Zero says the
remaining work is free, and a project total built on such zeros reads as
comfortable when it is unknown. `has_etc` distinguishes them, `forecast_status`
names the reason, and `eac_is_complete` refuses to call a total authoritative
while any line is missing.

### Snapshot, not a live view

A draft forecast refreshes from budget, commitment and ledger. An **approved**
forecast is frozen: April's vendor bill must not change March's approved EAC,
because the point of last month's forecast is what we believed last month.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

FORECAST_STATES = [
    ('draft', 'Draft'),
    ('review', 'Under Review'),
    ('approved', 'Approved'),
    ('superseded', 'Superseded'),
    ('cancelled', 'Cancelled'),
]

#: How a line's ETC was produced.
FORECAST_METHODS = [
    ('manual', 'Manual Estimate'),
    ('remaining_commitment', 'Remaining Commitment'),
    ('finish_at_budget', 'Finish at Budget'),
    ('percent_complete', 'Performance (% Complete)'),
    ('date_range', 'Time-Based (Remaining Periods)'),
    ('remaining_boq', 'Remaining BOQ Quantity'),
    ('not_required', 'No Forecast Required'),
]

#: Methods that cannot be used until the milestone that makes their input
#: trustworthy has landed. §34: forecast truth is not built on data Phase 0
#: classified as unreliable.
#: Emptied by M7. Remaining-BOQ forecasting was withheld because Phase 0
#: proved a certificate could exceed its BOQ quantity and that two
#: certificates could consume the same remaining quantity. M7 made the
#: authorised quantity explicit, checked certification against it under a
#: lock, and refused the second claim — so the input is now worth forecasting
#: from. The mechanism stays, because the next unreliable input will need it.
DISABLED_METHODS = {}

FORECAST_STATUS = [
    ('forecasted', 'Forecasted'),
    ('missing', 'Missing Forecast'),
    ('insufficient_data', 'Insufficient Source Data'),
    ('not_required', 'No Forecast Required'),
]

ADJUSTMENT_TYPES = [
    ('productivity', 'Productivity'),
    ('inflation', 'Inflation'),
    ('anticipated_variation', 'Anticipated Variation'),
    ('market_rate', 'Market Rate'),
    ('delay', 'Delay'),
    ('unawarded_scope', 'Unawarded Scope'),
    ('risk', 'Risk Allowance'),
    ('fx', 'FX Assumption'),
    ('management', 'Management Adjustment'),
    ('other', 'Other'),
]


class ConstructionForecast(models.Model):
    """One dated statement of expected outturn cost."""
    _name = 'realestate.construction.forecast'
    _description = 'Construction Cost Forecast'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, as_of_date desc, version desc, id desc'
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
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
        help="The control currency. Foreign-currency sources are converted "
             "for comparison; nothing here re-accounts FX.")

    as_of_date = fields.Date(
        required=True, index=True, tracking=True,
        default=fields.Date.context_today,
        help="The cutoff. Actual counts postings on or before this date, so a "
             "bill posted afterwards cannot rewrite this forecast.")
    period_label = fields.Char(
        compute='_compute_period_label', store=True,
        help="March 2027. A label, not a rule — month-end is not assumed.")
    version = fields.Integer(default=1, readonly=True, copy=False)
    state = fields.Selection(
        FORECAST_STATES, default='draft', required=True, tracking=True,
        copy=False, index=True)

    prepared_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, readonly=True)
    reviewed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_by_id = fields.Many2one(
        'res.users', readonly=True, copy=False, tracking=True)
    approved_on = fields.Datetime(readonly=True, copy=False)

    previous_forecast_id = fields.Many2one(
        'realestate.construction.forecast', readonly=True, copy=False,
        help="The approved forecast this one follows, for the movement bridge.")
    superseded_by_id = fields.Many2one(
        'realestate.construction.forecast', readonly=True, copy=False)

    line_ids = fields.One2many(
        'realestate.construction.forecast.line', 'forecast_id',
        string='Forecast Lines')

    total_current_budget = fields.Monetary(compute='_compute_totals', store=True)
    total_actual = fields.Monetary(compute='_compute_totals', store=True)
    total_commitment = fields.Monetary(compute='_compute_totals', store=True)
    total_etc = fields.Monetary(compute='_compute_totals', store=True)
    total_eac = fields.Monetary(compute='_compute_totals', store=True)
    total_forecast_variance = fields.Monetary(
        compute='_compute_totals', store=True,
        help="Current Budget − EAC. Positive is favourable.")

    line_count = fields.Integer(compute='_compute_totals', store=True)
    line_count_forecasted = fields.Integer(compute='_compute_totals', store=True)
    line_count_missing = fields.Integer(compute='_compute_totals', store=True)
    line_count_insufficient = fields.Integer(
        compute='_compute_totals', store=True)
    forecast_coverage = fields.Float(
        string='Coverage (%)', compute='_compute_totals', store=True,
        help="Share of forecastable lines that actually carry an estimate, "
             "weighted by current budget where there is one.")
    eac_is_complete = fields.Boolean(
        compute='_compute_totals', store=True,
        help="False while any line is missing a forecast. A partial total is "
             "shown, and is never labelled authoritative.")

    previous_eac = fields.Monetary(compute='_compute_movement', store=True)
    eac_movement = fields.Monetary(
        compute='_compute_movement', store=True,
        help="This EAC minus the previous approved EAC. Positive means the "
             "expected cost has grown, which is adverse.")

    retention_warning = fields.Char(compute='_compute_quality')
    notes = fields.Html()

    # ------------------------------------------------------------------
    def init(self):
        """One approved forecast per project per date, in the database.

        A Python check cannot prevent two people approving two competing
        forecasts for the same period at the same moment. A partial unique
        index can.
        """
        super().init()
        self._cr.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_class
                               WHERE relname = 'construction_forecast_one_approved') THEN
                    CREATE UNIQUE INDEX construction_forecast_one_approved
                        ON realestate_construction_forecast (project_id, as_of_date)
                     WHERE state = 'approved';
                END IF;
            END $$;
        """)

    @api.depends('as_of_date')
    def _compute_period_label(self):
        for rec in self:
            rec.period_label = (
                rec.as_of_date.strftime('%B %Y') if rec.as_of_date else '')

    @api.depends('line_ids.current_budget', 'line_ids.actual_amount',
                 'line_ids.etc_amount', 'line_ids.eac_amount',
                 'line_ids.commitment_amount', 'line_ids.forecast_status',
                 'line_ids.has_etc')
    def _compute_totals(self):
        for rec in self:
            lines = rec.line_ids
            rec.line_count = len(lines)
            forecastable = lines.filtered(
                lambda l: l.forecast_status != 'not_required')
            forecasted = lines.filtered(lambda l: l.has_etc)
            missing = forecastable.filtered(
                lambda l: l.forecast_status == 'missing')
            insufficient = forecastable.filtered(
                lambda l: l.forecast_status == 'insufficient_data')

            rec.line_count_forecasted = len(forecasted)
            rec.line_count_missing = len(missing)
            rec.line_count_insufficient = len(insufficient)

            rec.total_current_budget = sum(lines.mapped('current_budget'))
            rec.total_actual = sum(lines.mapped('actual_amount'))
            rec.total_commitment = sum(lines.mapped('commitment_amount'))
            # Only lines that *have* an ETC contribute one. A missing line
            # contributes nothing rather than a zero, and the coverage figure
            # beside the total says how much of the project that leaves out.
            rec.total_etc = sum(forecasted.mapped('etc_amount'))
            rec.total_eac = rec.total_actual + rec.total_etc
            rec.total_forecast_variance = (
                rec.total_current_budget - rec.total_eac)

            weight_total = sum(abs(l.current_budget) for l in forecastable)
            if not forecastable:
                rec.forecast_coverage = 100.0
            elif weight_total:
                covered = sum(abs(l.current_budget) for l in forecasted)
                rec.forecast_coverage = covered / weight_total * 100.0
            else:
                rec.forecast_coverage = (
                    len(forecasted) / len(forecastable) * 100.0)
            rec.eac_is_complete = not (missing or insufficient)

    @api.depends('previous_forecast_id.total_eac', 'total_eac')
    def _compute_movement(self):
        for rec in self:
            previous = rec.previous_forecast_id
            rec.previous_eac = previous.total_eac if previous else 0.0
            rec.eac_movement = (
                rec.total_eac - rec.previous_eac) if previous else 0.0

    def _compute_quality(self):
        Disclosure = self.env['realestate.construction.retention.disclosure']
        for rec in self:
            rec.retention_warning = Disclosure.warning_for(rec.project_id)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.forecast') or 'FC/NEW'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Snapshot / refresh
    # ------------------------------------------------------------------
    @api.model
    def generate_for(self, project, as_of_date=None, copy_forward=True):
        """Build a draft forecast: one line per budgeted or committed cost code.

        Everything is pre-aggregated — budget, commitment, actual and the
        previous forecast are each read once for the whole project (§47), not
        once per cost code. A project with two thousand cost codes costs four
        queries, not eight thousand.
        """
        as_of_date = as_of_date or fields.Date.context_today(self)
        Controls = self.env['realestate.construction.controls']
        Commitment = self.env['realestate.construction.commitment']
        Analytic = self.env['realestate.construction.analytic']

        budgets = Controls.budget_by_cost_code(project)
        commitments = Commitment.current_commitment_by_cost_code(project)
        actuals = Analytic.actual_by_cost_code(project, date_to=as_of_date)

        previous = self.search([
            ('project_id', '=', project.id),
            ('state', '=', 'approved'),
            ('as_of_date', '<', as_of_date),
        ], order='as_of_date desc', limit=1)
        carried = {}
        if previous and copy_forward:
            for line in previous.line_ids:
                carried[line.cost_code_id.id] = line

        forecast = self.create({
            'project_id': project.id,
            'company_id': (project.company_id or self.env.company).id,
            'currency_id': project.currency_id.id,
            'as_of_date': as_of_date,
            'previous_forecast_id': previous.id if previous else False,
        })

        code_ids = {cid for cid in
                    set(budgets) | set(commitments) | set(actuals) if cid}
        Line = self.env['realestate.construction.forecast.line']
        vals_list = []
        for code_id in sorted(code_ids):
            source = carried.get(code_id)
            vals_list.append({
                'forecast_id': forecast.id,
                'cost_code_id': code_id,
                'current_budget': budgets.get(code_id, {}).get('current', 0.0),
                'commitment_amount': commitments.get(code_id, 0.0),
                'actual_amount': actuals.get(code_id, 0.0),
                # Carried forward: how it was forecast and why. Never the
                # previous actual — that is refreshed from the ledger.
                'method': source.method if source else False,
                'manual_etc': source.manual_etc if source else 0.0,
                'manual_reason': source.manual_reason if source else False,
                'comment': source.comment if source else False,
                'previous_eac': source.eac_amount if source else 0.0,
            })
        Line.create(vals_list)
        return forecast

    def action_refresh(self):
        """Re-read budget, commitment and actual; keep the manual inputs.

        Refusing on an approved forecast is the whole point of approving one.
        """
        for rec in self:
            if rec.state not in ('draft', 'review'):
                raise UserError(_(
                    "An approved forecast is a record of what was believed on "
                    "%(date)s. Create a new forecast rather than refreshing "
                    "this one.", date=rec.as_of_date))
            Controls = self.env['realestate.construction.controls']
            Commitment = self.env['realestate.construction.commitment']
            Analytic = self.env['realestate.construction.analytic']
            budgets = Controls.budget_by_cost_code(rec.project_id)
            commitments = Commitment.current_commitment_by_cost_code(
                rec.project_id)
            actuals = Analytic.actual_by_cost_code(
                rec.project_id, date_to=rec.as_of_date)
            for line in rec.line_ids:
                code_id = line.cost_code_id.id
                line.write({
                    'current_budget': budgets.get(code_id, {}).get(
                        'current', 0.0),
                    'commitment_amount': commitments.get(code_id, 0.0),
                    'actual_amount': actuals.get(code_id, 0.0),
                })
            rec.message_post(body=_("Actuals and commitments refreshed."))
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft forecast can be submitted."))
            if not rec.line_ids:
                raise UserError(_("A forecast with no lines forecasts nothing."))
            rec.write({'state': 'review', 'reviewed_by_id': self.env.user.id})
        return True

    def action_approve(self):
        """Validate, lock the period, and freeze the numbers."""
        for rec in self:
            if rec.state != 'review':
                raise UserError(_(
                    "Only a forecast under review can be approved."))
            rec._check_approver()
            rec._validate_for_approval()
            rec._lock_period()

            clash = self.search([
                ('project_id', '=', rec.project_id.id),
                ('as_of_date', '=', rec.as_of_date),
                ('state', '=', 'approved'),
                ('id', '!=', rec.id),
            ], limit=1)
            if clash:
                raise UserError(_(
                    "%(project)s already has an approved forecast for "
                    "%(date)s (%(name)s). Supersede it rather than approving a "
                    "second one for the same period.",
                    project=rec.project_id.display_name,
                    date=rec.as_of_date, name=clash.name))

            rec.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })
            rec.message_post(body=_(
                "Approved. EAC %(eac)s against a current budget of "
                "%(budget)s (variance %(variance)s). Coverage %(coverage).1f%%.",
                eac=rec.currency_id.format(rec.total_eac),
                budget=rec.currency_id.format(rec.total_current_budget),
                variance=rec.currency_id.format(rec.total_forecast_variance),
                coverage=rec.forecast_coverage))
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == 'approved':
                raise UserError(_(
                    "An approved forecast is history. Supersede it with a "
                    "later one instead of cancelling it."))
            rec.state = 'cancelled'
        return True

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state == 'approved':
                raise UserError(_("An approved forecast cannot be reopened."))
            rec.state = 'draft'
        return True

    # ------------------------------------------------------------------
    def _lock_period(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (hash('re.construction.forecast') % 2147483647,
             self.project_id.id))

    def _check_approver(self):
        self.ensure_one()
        allow_self = self.env['ir.config_parameter'].sudo().get_param(
            'real_estate_construction.allow_self_approval', 'False')
        if allow_self in ('True', 'true', '1'):
            return True
        if self.prepared_by_id == self.env.user and not self.env.user.has_group(
                'atmta_roles.group_construction_manager'):
            raise UserError(_(
                "A forecast is approved by somebody other than the person who "
                "prepared it."))
        return True

    def _validate_for_approval(self):
        """§31 — everything that must be true before a forecast becomes fact."""
        self.ensure_one()
        problems = []
        for line in self.line_ids:
            problems.extend(line._approval_problems())
        if problems:
            raise UserError(_(
                "This forecast cannot be approved yet:\n\n%s",
                '\n'.join('• %s' % problem for problem in problems[:20])))
        return True

    # ------------------------------------------------------------------
    @api.model
    def latest_approved(self, project):
        return self.search([
            ('project_id', '=', project.id),
            ('state', '=', 'approved'),
        ], order='as_of_date desc, id desc', limit=1)

    @api.model
    def movement_bridge(self, forecast):
        """§20 — how the EAC moved since the previous approved forecast.

        Categorised by the adjustment types people actually recorded, plus a
        residual for movement nobody explained. The residual is shown rather
        than hidden: an unexplained 3M swing is exactly what a bridge is for.
        """
        previous = forecast.previous_forecast_id
        if not previous:
            return {'previous_eac': 0.0, 'movements': [],
                    'current_eac': forecast.total_eac, 'unexplained': 0.0}
        movements = {}
        for adjustment in forecast.line_ids.mapped('adjustment_ids'):
            movements[adjustment.adjustment_type] = movements.get(
                adjustment.adjustment_type, 0.0) + adjustment.amount
        explained = sum(movements.values())
        total_movement = forecast.total_eac - previous.total_eac
        return {
            'previous_eac': previous.total_eac,
            'current_eac': forecast.total_eac,
            'movements': sorted(movements.items()),
            'explained': explained,
            'unexplained': total_movement - explained,
            'total_movement': total_movement,
        }


class ConstructionForecastLine(models.Model):
    """One cost code's expectation, and the reasoning behind it."""
    _name = 'realestate.construction.forecast.line'
    _description = 'Construction Forecast Line'
    _order = 'forecast_id, cost_code_id, id'
    _check_company_auto = True

    forecast_id = fields.Many2one(
        'realestate.construction.forecast', required=True, ondelete='cascade',
        index=True)
    project_id = fields.Many2one(
        related='forecast_id.project_id', store=True, index=True, readonly=True)
    company_id = fields.Many2one(
        related='forecast_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='forecast_id.currency_id', store=True, readonly=True)
    state = fields.Selection(
        related='forecast_id.state', store=True, readonly=True, index=True)
    as_of_date = fields.Date(related='forecast_id.as_of_date', store=True,
                             readonly=True)

    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', required=True, index=True,
        ondelete='restrict', check_company=True)
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='restrict',
        check_company=True)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', string='Package',
        ondelete='set null',
        help="Reference only. Lines stay cost-code based so a package "
             "spanning several codes cannot be counted twice.")
    description = fields.Char()

    # ----- snapshot inputs (frozen once approved) -----
    current_budget = fields.Monetary(readonly=True)
    commitment_amount = fields.Monetary(string='Current Commitment',
                                        readonly=True)
    actual_amount = fields.Monetary(string='Actual to Date', readonly=True)

    committed_remaining = fields.Monetary(
        compute='_compute_committed_remaining', store=True,
        help="Commitment left to spend, where actual cost can be attributed "
             "to that same commitment.")
    committed_remaining_known = fields.Boolean(
        compute='_compute_committed_remaining', store=True,
        help="False means unknown, which is not the same as zero. The report "
             "shows N/A rather than a number nobody can stand behind.")

    # ----- method and result -----
    method = fields.Selection(
        FORECAST_METHODS, string='Forecast Method', tracking=True)
    calculated_etc = fields.Monetary(
        string='Calculated ETC', compute='_compute_etc', store=True,
        help="What the method produced, kept even when overridden.")
    manual_etc = fields.Monetary(string='Manual ETC')
    manual_reason = fields.Char(string='Basis / Reason')
    override_etc = fields.Monetary(
        string='Override ETC',
        help="Management's number when it differs from the method's.")
    override_reason = fields.Char()
    overridden_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    override_date = fields.Datetime(readonly=True, copy=False)
    is_overridden = fields.Boolean(compute='_compute_etc', store=True)

    adjustment_ids = fields.One2many(
        'realestate.construction.forecast.adjustment', 'line_id',
        string='Forecast Adjustments')
    adjustment_total = fields.Monetary(
        compute='_compute_etc', store=True,
        help="Forecast-only. Adjustments never touch budget, commitment or "
             "the ledger.")

    etc_amount = fields.Monetary(
        string='ETC', compute='_compute_etc', store=True)
    has_etc = fields.Boolean(
        compute='_compute_etc', store=True,
        help="False when nothing has produced an estimate. Distinguishes "
             "'no forecast' from 'a forecast of zero'.")
    eac_amount = fields.Monetary(
        string='EAC', compute='_compute_etc', store=True)
    forecast_variance = fields.Monetary(
        compute='_compute_etc', store=True,
        help="Current Budget − EAC. Positive is favourable.")
    forecast_status = fields.Selection(
        FORECAST_STATUS, compute='_compute_etc', store=True, index=True)
    status_reason = fields.Char(compute='_compute_etc', store=True)

    previous_eac = fields.Monetary(readonly=True)
    eac_movement = fields.Monetary(compute='_compute_etc', store=True)

    # ----- method inputs -----
    progress_pct = fields.Float(
        string='Progress (%)',
        help="Physical completion, for the performance method.")
    #: Held for the remaining-BOQ method, which fills them in from the BOQ
    #: rather than taking them on trust — a typed quantity under a name that
    #: says "BOQ" is a manual estimate wearing somebody else's authority.
    forecast_quantity = fields.Float(readonly=True)
    forecast_unit_rate = fields.Monetary(readonly=True)
    periods_remaining = fields.Float(
        help="For time-based costs: how many periods of the rate remain.")
    period_rate = fields.Monetary(string='Rate per Period')

    # ----- flags -----
    actual_exceeds_commitment = fields.Boolean(
        compute='_compute_flags', store=True)
    eac_exceeds_budget = fields.Boolean(compute='_compute_flags', store=True)
    is_unawarded = fields.Boolean(
        compute='_compute_flags', store=True,
        help="Budgeted scope with no commitment yet.")
    uncommitted_etc = fields.Monetary(
        compute='_compute_flags', store=True,
        help="ETC beyond what is committed — scope still to be procured.")

    comment = fields.Char()
    responsible_id = fields.Many2one('res.users', string='Responsible')

    # ------------------------------------------------------------------
    @api.depends('commitment_amount', 'actual_amount', 'package_id')
    def _compute_committed_remaining(self):
        """Only where the actual can be tied to the same commitment.

        M2 deliberately refused to fake this. The honest rule: when a line's
        actual cost cannot be attributed to its commitment, the remaining
        commitment is **unknown**, and unknown is reported as unknown rather
        than as zero. Zero means known zero.
        """
        for line in self:
            if not line.commitment_amount:
                line.committed_remaining = 0.0
                line.committed_remaining_known = not line.actual_amount
                continue
            # Attribution is authoritative when the cost code's actual cost
            # comes from documents under the same commitment. Today that holds
            # when the cost code carries commitment and actual on the same
            # project; anything more precise waits for M7's certificate links.
            line.committed_remaining = max(
                line.commitment_amount - line.actual_amount, 0.0)
            line.committed_remaining_known = True

    @api.depends('method', 'manual_etc', 'manual_reason', 'override_etc',
                 'override_reason', 'current_budget',
                 'commitment_amount', 'actual_amount', 'progress_pct',
                 'forecast_quantity', 'forecast_unit_rate',
                 'periods_remaining', 'period_rate',
                 'adjustment_ids.amount', 'committed_remaining',
                 'committed_remaining_known', 'previous_eac')
    def _compute_etc(self):
        for line in self:
            adjustments = sum(line.adjustment_ids.mapped('amount'))
            line.adjustment_total = adjustments
            calculated, status, reason = line._calculate_etc()
            line.calculated_etc = calculated or 0.0
            line.is_overridden = bool(line.override_etc)

            if line.is_overridden:
                etc, has_etc = line.override_etc, True
                status, reason = 'forecasted', _("Management override.")
            elif calculated is None:
                etc, has_etc = 0.0, False
            else:
                etc, has_etc = calculated + adjustments, True

            line.etc_amount = etc
            line.has_etc = has_etc
            line.forecast_status = status
            line.status_reason = reason
            # EAC is Actual + ETC, and when there is no ETC there is no EAC
            # either — only an actual-to-date that must not masquerade as one.
            line.eac_amount = (line.actual_amount + etc) if has_etc else 0.0
            line.forecast_variance = (
                line.current_budget - line.eac_amount) if has_etc else 0.0
            line.eac_movement = (
                line.eac_amount - line.previous_eac
                if has_etc and line.previous_eac else 0.0)

    def _calculate_etc(self):
        """`(etc_or_None, status, reason)` — None means no estimate exists."""
        self.ensure_one()
        method = self.method
        if not method:
            return None, 'missing', _("No forecast method has been chosen.")
        if method == 'not_required':
            return None, 'not_required', _(
                "Marked as needing no forecast.")
        if method in DISABLED_METHODS:
            return None, 'insufficient_data', DISABLED_METHODS[method]

        if method == 'manual':
            if not self.manual_reason:
                return None, 'insufficient_data', _(
                    "A manual estimate needs a stated basis.")
            return self.manual_etc, 'forecasted', _("Manual estimate.")

        if method == 'remaining_commitment':
            if not self.committed_remaining_known:
                return None, 'insufficient_data', _(
                    "Actual cost on this code cannot be attributed to its "
                    "commitment, so remaining commitment is unknown. Use a "
                    "manual estimate rather than guessing.")
            if not self.commitment_amount:
                return None, 'insufficient_data', _(
                    "Nothing is committed against this code, so there is no "
                    "remaining commitment to forecast from.")
            return self.committed_remaining, 'forecasted', _(
                "Remaining commitment.")

        if method == 'finish_at_budget':
            # Named for what it is: an assumption, not a discovery.
            return max(self.current_budget - self.actual_amount, 0.0), \
                'forecasted', _("Assumed to finish at budget.")

        if method == 'percent_complete':
            progress = self.progress_pct
            if progress <= 0.0:
                return None, 'insufficient_data', _(
                    "Progress of zero cannot forecast an outturn — dividing "
                    "by it says the project costs infinity.")
            if progress > 100.0:
                return None, 'insufficient_data', _(
                    "Progress above 100% is not a measurement.")
            if not self.actual_amount:
                return None, 'insufficient_data', _(
                    "No cost has been posted yet, so performance cannot be "
                    "measured from it.")
            eac = self.actual_amount / (progress / 100.0)
            return max(eac - self.actual_amount, 0.0), 'forecasted', _(
                "Performance: %(actual)s at %(pct).1f%% complete.",
                actual=self.currency_id.format(self.actual_amount),
                pct=progress)

        if method == 'remaining_boq':
            remaining, priced, unpriced = self._remaining_boq_value()
            self.forecast_quantity = self._remaining_boq_quantity()
            self.forecast_unit_rate = (
                remaining / self.forecast_quantity
                if self.forecast_quantity else 0.0)
            if not priced and not unpriced:
                return None, 'insufficient_data', _(
                    "No BOQ line on this cost code has an authorised quantity "
                    "to forecast from.")
            if unpriced:
                # A partial answer presented as a whole one is worse than no
                # answer: it looks complete.
                return None, 'insufficient_data', _(
                    "%(count)s BOQ line(s) on this cost code have no rate. "
                    "Forecasting the rest would report part of the work as "
                    "all of it.", count=unpriced)
            return remaining, 'forecasted', _(
                "Remaining authorised BOQ quantity at contract rates.")

        if method == 'date_range':
            if self.periods_remaining < 0 or self.period_rate < 0:
                return None, 'insufficient_data', _(
                    "Negative periods or rates are not a forecast.")
            return self.periods_remaining * self.period_rate, 'forecasted', _(
                "%(periods)s period(s) at %(rate)s.",
                periods=self.periods_remaining,
                rate=self.currency_id.format(self.period_rate))

        return None, 'missing', _("Unknown forecast method.")

    def _remaining_boq_quantity(self):
        """Uncertified authorised quantity, for the record on the line."""
        self.ensure_one()
        if not self.cost_code_id:
            return 0.0
        lines = self.env['realestate.boq.line'].search([
            ('project_id', '=', self.forecast_id.project_id.id),
            ('cost_code_id', '=', self.cost_code_id.id),
            ('boq_id.state', 'in', ('approved', 'locked')),
        ])
        return sum(max(0.0, l.authorised_quantity - l.certified_qty)
                   for l in lines)

    def _remaining_boq_value(self):
        """`(value, priced_lines, unpriced_lines)` for this cost code.

        Remaining means **authorised minus certified**, so an approved
        variation is included and an over-certified line contributes nothing
        rather than a negative that would quietly fund something else.
        """
        self.ensure_one()
        if not self.cost_code_id:
            return 0.0, 0, 0
        lines = self.env['realestate.boq.line'].search([
            ('project_id', '=', self.forecast_id.project_id.id),
            ('cost_code_id', '=', self.cost_code_id.id),
            ('boq_id.state', 'in', ('approved', 'locked')),
        ])
        value, priced, unpriced = 0.0, 0, 0
        for line in lines:
            if not line.unit_rate:
                unpriced += 1
                continue
            priced += 1
            value += max(0.0, line.authorised_quantity - line.certified_qty) \
                * line.unit_rate
        return value, priced, unpriced

    @api.depends('actual_amount', 'commitment_amount', 'eac_amount',
                 'current_budget', 'etc_amount', 'has_etc',
                 'committed_remaining')
    def _compute_flags(self):
        for line in self:
            line.actual_exceeds_commitment = bool(
                line.commitment_amount
                and line.actual_amount > line.commitment_amount)
            line.eac_exceeds_budget = bool(
                line.has_etc and line.eac_amount > line.current_budget)
            line.is_unawarded = bool(
                line.current_budget and not line.commitment_amount)
            line.uncommitted_etc = max(
                line.etc_amount - line.committed_remaining, 0.0
            ) if line.has_etc else 0.0

    # ------------------------------------------------------------------
    def write(self, vals):
        """An approved forecast is frozen.

        Not "mostly frozen": tomorrow's bill must not change last month's
        approved EAC, and neither must anybody's second thoughts.
        """
        if not self.env.context.get('re_forecast_approving'):
            frozen = self.filtered(lambda l: l.state in ('approved',
                                                         'superseded'))
            if frozen and set(vals) - {'comment'}:
                raise UserError(_(
                    "This forecast was approved on %(date)s and cannot be "
                    "changed. Create a new forecast — the history is the "
                    "point.", date=frozen[0].as_of_date))
        if 'override_etc' in vals and vals.get('override_etc'):
            vals.setdefault('overridden_by_id', self.env.user.id)
            vals.setdefault('override_date', fields.Datetime.now())
        return super().write(vals)

    def _approval_problems(self):
        """Everything wrong with this line, in words a person can act on."""
        self.ensure_one()
        problems = []
        label = self.cost_code_id.display_name
        if self.forecast_status == 'missing':
            problems.append(_("%s has no forecast method.") % label)
        elif self.forecast_status == 'insufficient_data':
            problems.append('%s — %s' % (label, self.status_reason))
        if self.is_overridden and not self.override_reason:
            problems.append(
                _("%s is overridden without a reason.") % label)
        if self.method == 'manual' and self.manual_etc and \
                not self.manual_reason:
            problems.append(_("%s has a manual ETC with no basis.") % label)
        if self.currency_id != self.forecast_id.currency_id:
            problems.append(
                _("%s is in a different currency from the forecast.") % label)
        return problems

    # ------------------------------------------------------------------
    # Drilldowns — §41
    # ------------------------------------------------------------------
    def action_drill_actual(self):
        self.ensure_one()
        domain = self.env['realestate.construction.analytic'].actual_domain(
            self.project_id, cost_codes=self.cost_code_id,
            date_to=self.as_of_date)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Posted Cost to %s') % self.as_of_date,
            'res_model': 'account.analytic.line',
            'view_mode': 'list,form',
            'domain': domain,
        }

    def action_drill_commitment(self):
        self.ensure_one()
        Commitment = self.env['realestate.construction.commitment']
        lines = self.env['purchase.order.line'].search(
            Commitment._po_line_domain(self.project_id)
            + [('re_cost_code_id', '=', self.cost_code_id.id)])
        return {
            'type': 'ir.actions.act_window',
            'name': _('Commitment Detail'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', lines.order_id.ids)],
        }


class ConstructionForecastAdjustment(models.Model):
    """A named reason the forecast differs from its calculated basis.

    Forecast-only, always: an adjustment changes what we expect, never what was
    authorised (budget), agreed (commitment) or posted (ledger). M4 can turn a
    recorded concern into a Change Event; recording one here does not.
    """
    _name = 'realestate.construction.forecast.adjustment'
    _description = 'Forecast Adjustment'
    _order = 'line_id, id'

    line_id = fields.Many2one(
        'realestate.construction.forecast.line', required=True,
        ondelete='cascade', index=True)
    forecast_id = fields.Many2one(
        related='line_id.forecast_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='line_id.currency_id', readonly=True)
    adjustment_type = fields.Selection(
        ADJUSTMENT_TYPES, required=True, default='other')
    description = fields.Char(required=True)
    amount = fields.Monetary(
        required=True,
        help="Positive increases the forecast. Negative records an expected "
             "saving.")
    created_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, readonly=True)

    # ----- M4 link: anticipation → approved change -----
    change_event_id = fields.Many2one(
        'realestate.construction.change.event', string='Change Event',
        ondelete='set null', index=True,
        help="The potential change this allowance anticipates. When that "
             "change is approved and implemented, the allowance is marked "
             "converted so the next forecast does not count the same money "
             "twice.")
    converted_to_change_order = fields.Boolean(
        readonly=True, copy=False,
        help="True once the anticipated change became a real approved one. "
             "The allowance stays on the historical forecast that recorded "
             "it — that is what was believed at the time — and is not carried "
             "into the next one.")
    converted_by_order_id = fields.Many2one(
        'realestate.construction.change.order', readonly=True, copy=False)
