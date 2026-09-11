# -*- coding: utf-8 -*-
"""M2 — the cost control equation, in one place.

```
    ORIGINAL BUDGET     + APPROVED BUDGET CHANGES     = CURRENT BUDGET
    ORIGINAL COMMITMENT + APPROVED COMMITMENT CHANGES = CURRENT COMMITMENT
    ACTUAL                                            = posted ledger cost
```

Three independent readings of the same project, deliberately never added
together. §25 is explicit and worth repeating in code: commitment and actual
are two views of the same money, so `current_commitment + actual_cost` is not a
total of anything. This service does not expose such a field, and a test
asserts that it never grows one.

ETC, EAC and forecast variance are **absent**, not zero. M3 owns them, and a
zero would be read as "nothing left to spend" by anybody who saw it.
"""

from odoo import api, fields, models


class ConstructionControls(models.AbstractModel):
    """Assembles the equation for a project, and for each cost code."""
    _name = 'realestate.construction.controls'
    _description = 'Construction Project Controls'

    # ------------------------------------------------------------------
    @api.model
    def budget_by_cost_code(self, project):
        """`{cost_code_id: {original, changes, current}}` from the baseline."""
        budget = self.env['realestate.construction.budget'].current_for(project)
        if not budget:
            return {}
        groups = self.env['realestate.construction.budget.line'].sudo(
            )._read_group(
                [('budget_id', '=', budget.id)],
                groupby=['cost_code_id'],
                aggregates=['original_amount:sum',
                            'approved_change_amount:sum'],
            )
        return {
            cost_code.id: {
                'original': original or 0.0,
                'changes': changes or 0.0,
                'current': (original or 0.0) + (changes or 0.0),
            }
            for cost_code, original, changes in groups if cost_code
        }

    @api.model
    def project_totals(self, project):
        """Every control number for one project, and nothing derived from two.

        Deliberately a plain dict rather than stored fields: these are read
        far less often than they would be recomputed, and storing them would
        mean a purchase order confirmation had to write to the project.
        """
        Budget = self.env['realestate.construction.budget']
        Commitment = self.env['realestate.construction.commitment']
        Analytic = self.env['realestate.construction.analytic']

        budget = Budget.current_for(project)
        original_budget = budget.original_amount if budget else 0.0
        budget_changes = budget.approved_change_amount if budget else 0.0
        current_budget = original_budget + budget_changes

        commitment_by_code = Commitment.current_commitment_by_cost_code(project)
        current_commitment = sum(commitment_by_code.values())
        original_commitment = sum(
            Commitment.original_commitment_by_cost_code(project).values())
        commitment_changes = sum(
            Commitment.approved_change_by_cost_code(project).values())

        actual_by_code = Analytic.actual_by_cost_code(project)
        actual_cost = sum(actual_by_code.values())

        return {
            'project_id': project.id,
            'currency_id': (budget.currency_id.id if budget
                            else project.currency_id.id),
            'budget_id': budget.id if budget else False,
            'budget_state': budget.state if budget else 'none',

            'original_budget': original_budget,
            'approved_budget_changes': budget_changes,
            'current_budget': current_budget,

            # Original is what was committed before any approved variation;
            # changes are M4's implemented commitment changes. A variation
            # folded into its purchase order lives in the original figure,
            # because that is where the order now carries it.
            'original_commitment': original_commitment,
            'approved_commitment_changes': commitment_changes,
            'current_commitment': current_commitment,

            'actual_cost': actual_cost,

            # Two balances answering two different questions, named so that
            # nobody has to guess which is which.
            'available_before_commitment': current_budget - current_commitment,
            'budget_remaining_vs_actual': current_budget - actual_cost,

            'over_committed': current_commitment > current_budget,
        }

    @api.model
    def forecast_by_cost_code(self, project):
        """The latest **approved** forecast, keyed by cost code.

        Draft forecasts are deliberately ignored: an unapproved number on a
        cost report is somebody's working paper, and readers of a cost report
        cannot tell the difference once it is in a column.
        """
        Forecast = self.env['realestate.construction.forecast']
        forecast = Forecast.latest_approved(project)
        if not forecast:
            return {}, forecast
        return {
            line.cost_code_id.id: line
            for line in forecast.line_ids if line.has_etc
        }, forecast

    @api.model
    def cost_report_rows(self, project):
        """One row per cost code: budget, commitment and actual side by side.

        The three sources are read independently and joined on the cost code,
        so a code that appears in only one of them still produces a row. A
        commitment against a code with no budget is exactly the kind of thing
        a cost report exists to show.
        """
        Commitment = self.env['realestate.construction.commitment']
        Analytic = self.env['realestate.construction.analytic']
        CostCode = self.env['realestate.construction.cost.code']

        budget = self.budget_by_cost_code(project)
        commitment = Commitment.current_commitment_by_cost_code(project)
        original_commitment = Commitment.original_commitment_by_cost_code(
            project)
        commitment_changes = Commitment.approved_change_by_cost_code(project)
        actual = Analytic.actual_by_cost_code(project)
        forecast_lines, forecast = self.forecast_by_cost_code(project)

        code_ids = {cid for cid in
                    set(budget) | set(commitment) | set(actual)
                    | set(commitment_changes) if cid}
        codes = {c.id: c for c in CostCode.browse(sorted(code_ids)).exists()}

        rows = []
        for code_id in sorted(code_ids, key=lambda i: codes[i].code
                              if i in codes else ''):
            code = codes.get(code_id)
            if not code:
                continue
            figures = budget.get(code_id, {})
            original = figures.get('original', 0.0)
            changes = figures.get('changes', 0.0)
            current = figures.get('current', 0.0)
            committed = commitment.get(code_id, 0.0)
            spent = actual.get(code_id, 0.0)
            forecast_line = forecast_lines.get(code_id)
            rows.append({
                'cost_code_id': code_id,
                'cost_code': code.display_name,
                'category': code.category,
                'is_contingency': code.is_contingency,
                'original_budget': original,
                'approved_budget_changes': changes,
                'current_budget': current,
                'original_commitment': original_commitment.get(code_id, 0.0),
                'approved_commitment_changes': commitment_changes.get(
                    code_id, 0.0),
                'current_commitment': committed,
                'actual_cost': spent,
                'available_before_commitment': current - committed,
                'budget_remaining_vs_actual': current - spent,
                'committed_remaining': (
                    forecast_line.committed_remaining if forecast_line
                    and forecast_line.committed_remaining_known else None),
                # M3: from the latest approved forecast, and **absent**
                # rather than zero when that forecast does not cover this code.
                'etc': forecast_line.etc_amount if forecast_line else None,
                'eac': forecast_line.eac_amount if forecast_line else None,
                'forecast_variance': (
                    forecast_line.forecast_variance if forecast_line else None),
                'previous_eac': (
                    forecast_line.previous_eac if forecast_line else None),
                'eac_movement': (
                    forecast_line.eac_movement if forecast_line else None),
                'forecast_method': (
                    forecast_line.method if forecast_line else False),
            })

        unassigned = commitment.get(False, 0.0)
        if unassigned:
            rows.append({
                'cost_code_id': False,
                'cost_code': 'Unassigned',
                'category': False,
                'is_contingency': False,
                'original_budget': 0.0,
                'approved_budget_changes': 0.0,
                'current_budget': 0.0,
                'original_commitment': unassigned,
                'approved_commitment_changes': 0.0,
                'current_commitment': unassigned,
                'actual_cost': actual.get(False, 0.0),
                'available_before_commitment': -unassigned,
                'budget_remaining_vs_actual': -actual.get(False, 0.0),
                # The unassigned row must carry every key a coded row does.
                # It did not, and `cost_report.build_for()` raised
                # `KeyError: 'previous_eac'` on any project with uncoded
                # commitment — found by the M10 reconciliation gate.
                'etc': None, 'eac': None, 'forecast_variance': None,
                'previous_eac': None, 'eac_movement': None,
                'forecast_method': False, 'committed_remaining': None,
            })
        return rows


class ProjectControls(models.Model):
    """The control figures, on the project, for the form and the dashboard."""
    _inherit = 'realestate.project'

    construction_budget_ids = fields.One2many(
        'realestate.construction.budget', 'project_id', string='Budgets')
    construction_package_ids = fields.One2many(
        'realestate.construction.contract.package', 'project_id',
        string='Contract Packages')

    ctrl_original_budget = fields.Monetary(
        string='Original Budget', compute='_compute_construction_controls')
    ctrl_current_budget = fields.Monetary(
        string='Current Budget', compute='_compute_construction_controls')
    ctrl_current_commitment = fields.Monetary(
        string='Current Commitment', compute='_compute_construction_controls')
    ctrl_actual_cost = fields.Monetary(
        string='Actual Cost (Ledger)', compute='_compute_construction_controls')
    ctrl_available_before_commitment = fields.Monetary(
        string='Budget Available Before Commitment',
        compute='_compute_construction_controls')
    ctrl_budget_remaining_vs_actual = fields.Monetary(
        string='Budget Remaining vs Actual',
        compute='_compute_construction_controls')
    ctrl_over_committed = fields.Boolean(
        string='Over-committed', compute='_compute_construction_controls')

    # ----- M3: forecast status on the project (§19, §22) -----
    ctrl_forecast_id = fields.Many2one(
        'realestate.construction.forecast', string='Latest Approved Forecast',
        compute='_compute_forecast_status')
    ctrl_forecast_date = fields.Date(
        string='Forecast As Of', compute='_compute_forecast_status')
    ctrl_eac = fields.Monetary(
        string='EAC', compute='_compute_forecast_status',
        help="From the latest approved forecast. Blank when none exists — a "
             "zero would read as 'this project will cost nothing more'.")
    ctrl_previous_eac = fields.Monetary(
        string='Previous EAC', compute='_compute_forecast_status')
    ctrl_eac_movement = fields.Monetary(
        string='EAC Movement', compute='_compute_forecast_status')
    ctrl_forecast_variance = fields.Monetary(
        string='Forecast Variance', compute='_compute_forecast_status',
        help="Current Budget − EAC. Positive is favourable.")
    ctrl_forecast_coverage = fields.Float(
        string='Forecast Coverage (%)', compute='_compute_forecast_status')
    ctrl_forecast_age_days = fields.Integer(
        string='Forecast Age (days)', compute='_compute_forecast_status')
    ctrl_forecast_is_stale = fields.Boolean(
        string='Forecast Stale', compute='_compute_forecast_status',
        help="Older than the configured forecast interval "
             "(`real_estate_construction.forecast_interval_days`, default 35).")
    ctrl_forecast_state = fields.Selection([
        ('none', 'No Forecast'),
        ('incomplete', 'Incomplete'),
        ('complete', 'Complete'),
        ('stale', 'Stale'),
    ], string='Forecast Status', compute='_compute_forecast_status')

    def _compute_forecast_status(self):
        Forecast = self.env['realestate.construction.forecast']
        interval = int(self.env['ir.config_parameter'].sudo().get_param(
            'real_estate_construction.forecast_interval_days', '35'))
        today = fields.Date.context_today(self)
        for rec in self:
            forecast = Forecast.latest_approved(rec)
            rec.ctrl_forecast_id = forecast.id if forecast else False
            if not forecast:
                rec.ctrl_forecast_date = False
                rec.ctrl_eac = 0.0
                rec.ctrl_previous_eac = 0.0
                rec.ctrl_eac_movement = 0.0
                rec.ctrl_forecast_variance = 0.0
                rec.ctrl_forecast_coverage = 0.0
                rec.ctrl_forecast_age_days = 0
                rec.ctrl_forecast_is_stale = False
                rec.ctrl_forecast_state = 'none'
                continue
            age = (today - forecast.as_of_date).days
            stale = age > interval
            rec.ctrl_forecast_date = forecast.as_of_date
            rec.ctrl_eac = forecast.total_eac
            rec.ctrl_previous_eac = forecast.previous_eac
            rec.ctrl_eac_movement = forecast.eac_movement
            rec.ctrl_forecast_variance = forecast.total_forecast_variance
            rec.ctrl_forecast_coverage = forecast.forecast_coverage
            rec.ctrl_forecast_age_days = age
            rec.ctrl_forecast_is_stale = stale
            rec.ctrl_forecast_state = (
                'stale' if stale
                else 'complete' if forecast.eac_is_complete
                else 'incomplete')

    def _compute_construction_controls(self):
        Controls = self.env['realestate.construction.controls']
        for rec in self:
            totals = Controls.project_totals(rec)
            rec.ctrl_original_budget = totals['original_budget']
            rec.ctrl_current_budget = totals['current_budget']
            rec.ctrl_current_commitment = totals['current_commitment']
            rec.ctrl_actual_cost = totals['actual_cost']
            rec.ctrl_available_before_commitment = \
                totals['available_before_commitment']
            rec.ctrl_budget_remaining_vs_actual = \
                totals['budget_remaining_vs_actual']
            rec.ctrl_over_committed = totals['over_committed']
