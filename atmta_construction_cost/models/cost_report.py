# -*- coding: utf-8 -*-
"""M2 — the Cost Report.

One row per cost code, three independent readings side by side:

```
    ORIGINAL BUDGET  + CHANGES  =  CURRENT BUDGET
    ORIGINAL COMMIT. + CHANGES  =  CURRENT COMMITMENT
    ACTUAL                      =  posted ledger cost
```

### What this report deliberately does not have

**A "total cost" column.** Commitment and actual are two views of the same
money — a purchase order of 10M with 4M billed against it has not cost 14M.
Adding them is the single easiest way to make a project look ruined, and the
column simply does not exist, so nobody can add it by accident.

**ETC, EAC and forecast variance.** They belong to M3 and are shown as blank
rather than zero. A zero would read as "nothing left to spend", which is the
opposite of "we have not forecast yet".

### Drilldown

Every figure resolves to its records (§29): budget lines, the purchase orders
and packages behind a commitment, and the analytic postings behind an actual.
The actual drilldown runs **as the user**, not `sudo()` — a cost controller who
may not read the general ledger should be told so by Accounting, not shown it
by us (§34).
"""

from odoo import _, api, fields, models


class ConstructionCostReport(models.TransientModel):
    """A rendered cost report for one project."""
    _name = 'realestate.construction.cost.report'
    _description = 'Construction Cost Report'

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade')
    company_id = fields.Many2one(
        related='project_id.company_id', readonly=True)
    currency_id = fields.Many2one(
        related='project_id.currency_id', readonly=True)
    line_ids = fields.One2many(
        'realestate.construction.cost.report.line', 'report_id',
        string='Cost Codes')

    original_budget = fields.Monetary(compute='_compute_totals')
    approved_budget_changes = fields.Monetary(compute='_compute_totals')
    current_budget = fields.Monetary(compute='_compute_totals')
    current_commitment = fields.Monetary(compute='_compute_totals')
    actual_cost = fields.Monetary(compute='_compute_totals')
    available_before_commitment = fields.Monetary(compute='_compute_totals')
    budget_remaining_vs_actual = fields.Monetary(compute='_compute_totals')

    over_committed = fields.Boolean(compute='_compute_totals')
    retention_warning = fields.Char(compute='_compute_totals')

    forecast_id = fields.Many2one(
        'realestate.construction.forecast', compute='_compute_totals',
        string='Forecast Used')
    forecast_date = fields.Date(compute='_compute_totals')
    forecast_coverage = fields.Float(
        string='Forecast Coverage (%)', compute='_compute_totals')
    forecast_is_stale = fields.Boolean(compute='_compute_totals')
    forecast_state = fields.Selection([
        ('none', 'No Forecast'), ('incomplete', 'Incomplete'),
        ('complete', 'Complete'), ('stale', 'Stale'),
    ], compute='_compute_totals')
    total_etc = fields.Monetary(compute='_compute_totals')
    total_eac = fields.Monetary(compute='_compute_totals')
    total_forecast_variance = fields.Monetary(compute='_compute_totals')

    @api.depends('line_ids')
    def _compute_totals(self):
        Controls = self.env['realestate.construction.controls']
        Retention = self.env['realestate.construction.retention.disclosure']
        for rec in self:
            totals = Controls.project_totals(rec.project_id)
            rec.original_budget = totals['original_budget']
            rec.approved_budget_changes = totals['approved_budget_changes']
            rec.current_budget = totals['current_budget']
            rec.current_commitment = totals['current_commitment']
            rec.actual_cost = totals['actual_cost']
            rec.available_before_commitment = \
                totals['available_before_commitment']
            rec.budget_remaining_vs_actual = \
                totals['budget_remaining_vs_actual']
            rec.over_committed = totals['over_committed']
            rec.retention_warning = Retention.warning_for(rec.project_id)

            project = rec.project_id
            rec.forecast_id = project.ctrl_forecast_id.id or False
            rec.forecast_date = project.ctrl_forecast_date
            rec.forecast_coverage = project.ctrl_forecast_coverage
            rec.forecast_is_stale = project.ctrl_forecast_is_stale
            rec.forecast_state = project.ctrl_forecast_state
            rec.total_eac = project.ctrl_eac
            rec.total_forecast_variance = project.ctrl_forecast_variance
            rec.total_etc = max(project.ctrl_eac - totals['actual_cost'], 0.0) \
                if project.ctrl_forecast_id else 0.0

    @api.model
    def build_for(self, project):
        """Render the report for a project and return it."""
        report = self.create({'project_id': project.id})
        rows = self.env['realestate.construction.controls'].cost_report_rows(
            project)
        self.env['realestate.construction.cost.report.line'].create([
            dict(row, report_id=report.id) for row in
            (self._line_vals(r) for r in rows)
        ])
        return report

    @api.model
    def _line_vals(self, row):
        """Only the fields the line model holds — M3's stay out until M3."""
        return {
            'cost_code_id': row['cost_code_id'],
            'cost_code_label': row['cost_code'],
            'category': row['category'] or False,
            'is_contingency': row['is_contingency'],
            'original_budget': row['original_budget'],
            'approved_budget_changes': row['approved_budget_changes'],
            'current_budget': row['current_budget'],
            'original_commitment': row['original_commitment'],
            'approved_commitment_changes': row['approved_commitment_changes'],
            'current_commitment': row['current_commitment'],
            'actual_cost': row['actual_cost'],
            'available_before_commitment': row['available_before_commitment'],
            'budget_remaining_vs_actual': row['budget_remaining_vs_actual'],
            # M3 columns. `has_forecast` is what keeps a missing forecast from
            # rendering as a confident zero.
            'has_forecast': row['etc'] is not None,
            'etc_amount': row['etc'] or 0.0,
            'eac_amount': row['eac'] or 0.0,
            'forecast_variance': row['forecast_variance'] or 0.0,
            'previous_eac': row['previous_eac'] or 0.0,
            'eac_movement': row['eac_movement'] or 0.0,
            'forecast_method': row['forecast_method'] or False,
            'committed_remaining': row['committed_remaining'] or 0.0,
            'committed_remaining_known': row['committed_remaining'] is not None,
        }

    def action_open(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cost Report — %s') % self.project_id.display_name,
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }


class ConstructionCostReportLine(models.TransientModel):
    _name = 'realestate.construction.cost.report.line'
    _description = 'Construction Cost Report Line'
    _order = 'cost_code_label, id'

    report_id = fields.Many2one(
        'realestate.construction.cost.report', required=True,
        ondelete='cascade')
    project_id = fields.Many2one(
        related='report_id.project_id', readonly=True)
    currency_id = fields.Many2one(
        related='report_id.currency_id', readonly=True)

    cost_code_id = fields.Many2one('realestate.construction.cost.code')
    cost_code_label = fields.Char(string='Cost Code')
    category = fields.Char()
    is_contingency = fields.Boolean()

    original_budget = fields.Monetary()
    approved_budget_changes = fields.Monetary()
    current_budget = fields.Monetary()

    original_commitment = fields.Monetary()
    approved_commitment_changes = fields.Monetary()
    current_commitment = fields.Monetary()

    actual_cost = fields.Monetary()
    available_before_commitment = fields.Monetary(
        string='Available Before Commitment',
        help="Current Budget − Current Commitment. What is left to commit.")
    budget_remaining_vs_actual = fields.Monetary(
        string='Remaining vs Actual',
        help="Current Budget − Actual. What is left before the money is "
             "actually spent. A different question from the column beside it.")

    # M3 columns. Read `has_forecast` before reading any of them: a line with
    # no forecast carries zeros that mean "no answer", not "no cost".
    has_forecast = fields.Boolean(
        help="False when the latest approved forecast does not cover this "
             "cost code. The amounts below are then meaningless.")
    etc_amount = fields.Monetary(string='ETC')
    eac_amount = fields.Monetary(string='EAC')
    forecast_variance = fields.Monetary(
        help="Current Budget − EAC. Positive is favourable.")
    previous_eac = fields.Monetary()
    eac_movement = fields.Monetary(
        help="Growth in expected cost since the previous approved forecast.")
    forecast_method = fields.Char()
    committed_remaining = fields.Monetary()
    committed_remaining_known = fields.Boolean(
        help="False means unknown, which the report shows as N/A rather than "
             "as zero.")

    # ------------------------------------------------------------------
    # Drilldown — §29: no opaque totals
    # ------------------------------------------------------------------
    def action_drill_budget(self):
        self.ensure_one()
        budget = self.env['realestate.construction.budget'].current_for(
            self.project_id)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Budget Lines'),
            'res_model': 'realestate.construction.budget.line',
            'view_mode': 'list,form',
            'domain': [('budget_id', '=', budget.id),
                       ('cost_code_id', '=', self.cost_code_id.id)],
        }

    def action_drill_commitment(self):
        """The orders and packages that make up this figure."""
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

    def action_drill_actual(self):
        """The analytic postings behind the actual.

        Deliberately **not** `sudo()`: if the user may not read these records,
        Accounting says so. Showing them anyway would be this module deciding
        who may see the general ledger, which is not its decision to make.
        """
        self.ensure_one()
        Analytic = self.env['realestate.construction.analytic']
        domain = Analytic.actual_domain(
            self.project_id, cost_codes=self.cost_code_id)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Posted Cost'),
            'res_model': 'account.analytic.line',
            'view_mode': 'list,form',
            'domain': domain,
        }


class ConstructionRetentionDisclosure(models.AbstractModel):
    """§23 — the retention defect, disclosed rather than quietly patched.

    Phase 0 proved that retention is posted as a **negative line on the vendor
    bill with no account of its own**, so it nets against the expense: a
    100,000 certificate with 5% retention posts 95,000 of cost, and the 5,000
    the developer is still holding appears nowhere as a liability.

    That understates ledger construction cost — which is now the definition of
    actual cost — so the Cost Report says so on its face until it is fixed.

    **M2 does not fix it.** The correct treatment is:

    ```
        gross certified   →  project cost / expense
        retention withheld→  retention payable (liability)
        net               →  vendor payable
    ```

    and that is a change to how certificates post, which is M7's subject and
    needs the retention register, the release workflow and the advance
    recovery beside it. Doing it here would mean designing M7 in the middle of
    M2, and doing it *partially* would leave two certificate accounting paths.

    What M2 does instead: identify the affected postings, quantify the
    understatement exactly, and refuse to let the number be read as clean.
    Posted moves are never rewritten — Rule 3 — so correction of existing bills
    is a Finance reclassification, described in the report.
    """
    _name = 'realestate.construction.retention.disclosure'
    _description = 'Retention Treatment Disclosure'

    @api.model
    def affected_certificates(self, project):
        """Certificates whose posted bill nets retention against expense."""
        return self.env[
            'realestate.construction.payment.certificate'].sudo().search([
                ('project_id', '=', project.id),
                ('state', 'in', ('invoiced', 'paid')),
                ('retention_amount', '>', 0),
                # M7 posts retention to the liability account and stamps the
                # certificate. What remains here is genuinely pre-M7 history.
                ('retention_posted_correctly', '=', False),
            ])

    @api.model
    def understatement_for(self, project):
        """How much construction cost is missing from the ledger, exactly."""
        certificates = self.affected_certificates(project)
        return sum(certificates.mapped('retention_amount'))

    @api.model
    def warning_for(self, project):
        """One sentence for the face of the Cost Report, or nothing."""
        amount = self.understatement_for(project)
        if not amount:
            return ''
        certificates = self.affected_certificates(project)
        return _(
            "Actual cost is understated by %(amount)s: retention on "
            "%(count)s posted certificate(s) was booked as a negative expense "
            "line rather than a retention liability. The posted entries are "
            "not rewritten; Finance reclassification is required. See "
            "IMPLEMENTATION_REPORT.md §Retention.",
            amount=project.currency_id.format(amount),
            count=len(certificates))
