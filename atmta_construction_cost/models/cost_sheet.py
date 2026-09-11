# -*- coding: utf-8 -*-
"""The integrated cost sheet — one row per cost code, every column sourced.

This service adds no financial truth. Every figure on it comes from the
milestone that owns it:

    budget, commitment, actual   M2 (`realestate.construction.controls`)
    ETC / EAC / variance         M3 (approved forecast only)
    potential and approved change M4
    certified, retention, advance M7
    claims and exposure           M8

A reporting layer that recomputes any of those becomes a sixth opinion, and a
sixth opinion is how two screens end up disagreeing about the same project in
front of the same client. So the rule here is strict: aggregate, label,
explain — never derive.

Two conventions are load-bearing:

* **Unknown is not zero.** A cost code nobody forecast has `etc = None` and
  `has_etc = False`, not 0.0. Every consumer must read the flag; the M3 rule
  is what makes a coverage percentage mean anything.
* **Unassigned is not hidden.** Commitments and actual cost that carry no cost
  code are reported under a row of their own. Dropping them would make the
  sheet tidy and wrong.
"""
from odoo import _, api, fields, models

#: The key used for everything that reached the ledger without a cost code.
UNASSIGNED = 0

#: Change events that are still live possibilities. `no_change`, `rejected`,
#: `duplicate`, `cancelled` and `closed` are decided; they are not exposure.
OPEN_EVENT_STATES = ('draft', 'identified', 'under_review', 'pricing',
                     'assessed', 'change_required')


class ConstructionCostSheet(models.AbstractModel):
    _name = 'realestate.construction.cost.sheet'
    _description = 'Integrated Construction Cost Sheet'

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    @api.model
    def _as_project(self, project):
        """Accept a recordset or an id.

        The client calls `drilldown` and `rows_for` over RPC, where a record
        arrives as an integer. Coercing here — once — is what stops a caller
        discovering it the way the M9 browser gate did: with an
        `'int' object has no attribute 'id'` from inside a drilldown.
        """
        if isinstance(project, int):
            return self.env['realestate.project'].browse(project)
        return project

    @api.model
    def rows_for(self, project, wbs=None, package=None, contractor=None):
        """One row per cost code, plus one for whatever carries no code.

        The filters narrow *commitment and certification* sources, which are
        contractor- and package-scoped by nature. They deliberately do not
        narrow the budget: budget lines are not contractor-scoped, and
        silently shrinking an authorised budget because somebody filtered by
        contractor would be the most dangerous thing this report could do.
        """
        project = self._as_project(project)
        Controls = self.env['realestate.construction.controls']
        Commitment = self.env['realestate.construction.commitment']
        Analytic = self.env['realestate.construction.analytic']
        Forecast = self.env['realestate.construction.forecast']
        CostCode = self.env['realestate.construction.cost.code']

        budget_by_code = Controls.budget_by_cost_code(project)
        original_commitment = Commitment.original_commitment_by_cost_code(
            project)
        commitment_changes = Commitment.approved_change_by_cost_code(project)
        current_commitment = Commitment.current_commitment_by_cost_code(
            project)
        actual_by_code = Analytic.actual_by_cost_code(project)
        forecast_by_code, forecast = Controls.forecast_by_cost_code(project)
        potential_by_code = self._potential_change_by_cost_code(project)
        pending_budget_by_code = self._pending_change_by_cost_code(
            project, 'budget')
        certified_by_code = self._certified_by_cost_code(
            project, package=package, contractor=contractor)

        code_ids = set(budget_by_code) | set(current_commitment) \
            | set(actual_by_code) | set(forecast_by_code) \
            | set(potential_by_code) | set(certified_by_code) \
            | set(pending_budget_by_code)
        codes = CostCode.browse(
            [code_id for code_id in code_ids if code_id]).exists()
        code_by_id = {code.id: code for code in codes}

        if wbs:
            allowed = self._codes_under_wbs(project, wbs)
            code_ids = {code_id for code_id in code_ids if code_id in allowed}

        rows = []
        for code_id in sorted(code_ids, key=lambda cid: (
                cid == UNASSIGNED or cid is False,
                code_by_id[cid].code if cid in code_by_id else '')):
            code = code_by_id.get(code_id)
            budget = budget_by_code.get(code_id) or {}
            forecast_line = forecast_by_code.get(code_id)

            original_budget = budget.get('original', 0.0)
            budget_changes = budget.get('changes', 0.0)
            current_budget = budget.get('current',
                                        original_budget + budget_changes)
            actual = actual_by_code.get(code_id, 0.0)

            has_etc = bool(forecast_line and forecast_line.has_etc)
            etc = forecast_line.etc_amount if has_etc else None
            eac = (actual + etc) if has_etc else None

            rows.append({
                'cost_code_id': code_id or UNASSIGNED,
                'code': code.code if code else _('Unassigned'),
                'name': code.name if code else _(
                    'No cost code — see the data-quality worklist'),
                'is_unassigned': not code,
                'category': code.category if code else False,

                # Budget — M2
                'original_budget': original_budget,
                'approved_budget_changes': budget_changes,
                'current_budget': current_budget,
                'pending_budget_change': pending_budget_by_code.get(
                    code_id, 0.0),

                # Commitment — M2
                'original_commitment': original_commitment.get(code_id, 0.0),
                'approved_commitment_changes': commitment_changes.get(
                    code_id, 0.0),
                'current_commitment': current_commitment.get(code_id, 0.0),
                'committed_remaining': (
                    forecast_line.committed_remaining
                    if forecast_line and forecast_line.committed_remaining_known
                    else None),
                'committed_remaining_known': bool(
                    forecast_line and forecast_line.committed_remaining_known),

                # Actual and certification — ledger and M7
                'certified_amount': certified_by_code.get(code_id, 0.0),
                'actual_cost': actual,

                # Forecast — M3, approved only
                'has_etc': has_etc,
                'etc': etc,
                'eac': eac,
                'forecast_variance': (
                    current_budget - eac) if has_etc else None,
                'previous_eac': (
                    forecast_line.previous_eac if forecast_line else 0.0),
                'eac_movement': (
                    forecast_line.eac_movement if forecast_line else 0.0),
                'forecast_method': (
                    forecast_line.method if forecast_line else False),
                'forecast_status': (
                    forecast_line.forecast_status if forecast_line
                    else 'missing'),
                'forecast_reason': (
                    forecast_line.status_reason if forecast_line else _(
                        'No approved forecast line for this cost code.')),

                # Change — M4, potential kept apart from authorised
                'potential_cost_exposure': potential_by_code.get(code_id, 0.0),
            })
        return rows

    @api.model
    def _codes_under_wbs(self, project, wbs):
        """Cost codes reachable from a WBS node, including its children."""
        nodes = self.env['realestate.construction.wbs'].search([
            ('project_id', '=', project.id),
            ('id', 'child_of', wbs.id)])
        budget = self.env['realestate.construction.budget'].current_for(project)
        codes = set()
        if budget:
            codes |= set(budget.line_ids.filtered(
                lambda l: l.wbs_id in nodes).mapped('cost_code_id').ids)
        return codes

    # ------------------------------------------------------------------
    # Totals
    # ------------------------------------------------------------------
    @api.model
    def totals_for(self, project, wbs=None, package=None, contractor=None):
        """Project totals, aggregated from the rows a reader can open.

        The control figures come from `project_totals()` so that this report
        and every other surface answer with literally the same calculation —
        the M9AS rule. The row aggregation is asserted against it by
        `test_e_one_number_one_calculation`, so a divergence fails a test
        rather than reaching a client.
        """
        project = self._as_project(project)
        Controls = self.env['realestate.construction.controls']
        controls = Controls.project_totals(project)
        rows = self.rows_for(project, wbs=wbs, package=package,
                             contractor=contractor)

        forecast_rows = [row for row in rows if row['has_etc']]
        etc = sum(row['etc'] for row in forecast_rows)
        actual = controls['actual_cost']
        has_any_forecast = bool(forecast_rows)

        totals = dict(controls)
        totals.update({
            'row_count': len(rows),
            'certified_amount': sum(row['certified_amount'] for row in rows),
            'potential_cost_exposure': sum(
                row['potential_cost_exposure'] for row in rows),
            'pending_budget_change': sum(
                row['pending_budget_change'] for row in rows),

            'etc': etc if has_any_forecast else None,
            'eac': (actual + etc) if has_any_forecast else None,
            'forecast_variance': (
                controls['current_budget'] - (actual + etc)
                if has_any_forecast else None),
            'has_forecast': has_any_forecast,

            'unassigned_commitment': sum(
                row['current_commitment'] for row in rows
                if row['is_unassigned']),
            'unassigned_actual': sum(
                row['actual_cost'] for row in rows if row['is_unassigned']),
        })
        return totals

    # ------------------------------------------------------------------
    # Sources the other milestones do not already expose by cost code
    # ------------------------------------------------------------------
    @api.model
    def _potential_change_by_cost_code(self, project):
        """Open change events and unapproved orders — never authorised money."""
        totals = {}
        events = self.env['realestate.construction.change.event'].search([
            ('project_id', '=', project.id),
            ('state', 'in', OPEN_EVENT_STATES),
        ])
        for event in events:
            totals[event.cost_code_id.id or UNASSIGNED] = totals.get(
                event.cost_code_id.id or UNASSIGNED,
                0.0) + (event.estimated_cost_impact or 0.0)
        return totals

    @api.model
    def _pending_change_by_cost_code(self, project, impact_side):
        """Change order lines awaiting approval, by side."""
        groups = self.env['realestate.construction.change.order.line']._read_group(
            [('order_id.project_id', '=', project.id),
             ('order_id.state', 'in', ('draft', 'submitted',
                                       'pending_approval')),
             ('impact_side', '=', impact_side)],
            groupby=['cost_code_id'], aggregates=['negotiated_amount:sum'])
        return {code.id if code else UNASSIGNED: total or 0.0
                for code, total in groups}

    @api.model
    def _certified_by_cost_code(self, project, package=None, contractor=None):
        """Certified work from M7 certificates, by the code they were coded to."""
        domain = [('project_id', '=', project.id),
                  ('state', 'in', ('certified', 'invoiced', 'paid'))]
        if package:
            domain.append(('package_id', '=', package.id))
        if contractor:
            domain.append(('contractor_id', '=', contractor.id))
        groups = self.env[
            'realestate.construction.payment.certificate']._read_group(
                domain, groupby=['cost_code_id'],
                aggregates=['certified_amount:sum'])
        return {code.id if code else UNASSIGNED: total or 0.0
                for code, total in groups}

    # ------------------------------------------------------------------
    # Drilldowns — the backend owns the domain, the client just runs it
    # ------------------------------------------------------------------
    @api.model
    def drilldown(self, project, column, cost_code_id=None):
        """`(action_xmlid_or_model, domain, context)` for one cell.

        Every column on the sheet must open the records behind it. Domains are
        built here rather than in JavaScript so that the number and the list
        cannot drift apart, and so that record rules apply to the drilldown
        exactly as they applied to the aggregate.
        """
        project = self._as_project(project)
        code_domain = []
        if cost_code_id and cost_code_id != UNASSIGNED:
            code_domain = [('cost_code_id', '=', cost_code_id)]
        elif cost_code_id == UNASSIGNED:
            code_domain = [('cost_code_id', '=', False)]

        budget = self.env['realestate.construction.budget'].current_for(project)
        specs = {
            'original_budget': (
                'realestate.construction.budget.line',
                [('budget_id', '=', budget.id if budget else 0)] + code_domain,
                _('Baseline budget lines')),
            'approved_budget_changes': (
                'realestate.construction.change.order.line',
                [('order_id.project_id', '=', project.id),
                 ('order_id.state', '=', 'implemented'),
                 ('impact_side', '=', 'budget')] + code_domain,
                _('Implemented budget changes')),
            'current_commitment': (
                'purchase.order.line',
                [('order_id.re_project_id', '=', project.id),
                 ('order_id.state', 'in', ('purchase', 'done'))]
                + ([('re_cost_code_id', '=', cost_code_id)]
                   if cost_code_id and cost_code_id != UNASSIGNED else []),
                _('Purchase order lines')),
            'approved_commitment_changes': (
                'realestate.construction.change.order.line',
                [('order_id.project_id', '=', project.id),
                 ('order_id.state', '=', 'implemented'),
                 ('impact_side', '=', 'commitment')] + code_domain,
                _('Implemented commitment changes')),
            'actual_cost': (
                'account.analytic.line',
                self._actual_drilldown_domain(project, cost_code_id),
                _('Analytic postings')),
            'certified_amount': (
                'realestate.construction.payment.certificate',
                [('project_id', '=', project.id),
                 ('state', 'in', ('certified', 'invoiced', 'paid'))]
                + code_domain,
                _('Payment certificates')),
            'etc': (
                'realestate.construction.forecast.line',
                [('forecast_id.project_id', '=', project.id),
                 ('forecast_id.state', '=', 'approved')] + code_domain,
                _('Approved forecast lines')),
            'potential_cost_exposure': (
                'realestate.construction.change.event',
                [('project_id', '=', project.id),
                 ('state', 'in', list(OPEN_EVENT_STATES))] + code_domain,
                _('Open change events')),
            'retention': (
                'realestate.construction.retention',
                [('project_id', '=', project.id)],
                _('Retention register')),
        }
        if column not in specs:
            return False
        model, domain, name = specs[column]
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': model,
            'domain': domain,
            'view_mode': 'list,form',
            # `views` is not optional for an action handed straight to the
            # web client: it maps over them, and an inline act_window without
            # them fails in `_preprocessAction` rather than opening anything.
            'views': [[False, 'list'], [False, 'form']],
            'target': 'current',
            'context': {'create': False},
        }

    @api.model
    def _actual_drilldown_domain(self, project, cost_code_id=None):
        """Analytic postings behind an actual-cost figure.

        Uses the same UNTAXED filter and plan columns as
        `actual_by_cost_code()`, because a drilldown that applies a different
        filter from the total it opened is worse than no drilldown.
        """
        project = self._as_project(project)
        Analytic = self.env['realestate.construction.analytic']
        account = project.analytic_account_id
        if not account:
            return [('id', '=', 0)]
        domain = list(Analytic.UNTAXED) + [
            (Analytic._project_plan()._column_name(), '=', account.id)]
        if cost_code_id and cost_code_id != UNASSIGNED:
            code = self.env['realestate.construction.cost.code'].browse(
                cost_code_id)
            code_account = code.analytic_account_id
            domain.append(
                (Analytic._cost_code_plan()._column_name(), '=',
                 code_account.id if code_account else 0))
        elif cost_code_id == UNASSIGNED:
            domain.append(
                (Analytic._cost_code_plan()._column_name(), '=', False))
        return domain
