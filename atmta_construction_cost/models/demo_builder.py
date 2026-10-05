# -*- coding: utf-8 -*-
"""Demo layer: the cost spine the Control Tower reports on.

The Control Tower is a cost-control screen, and without a budget, a
commitment and an actual it has nothing to control: every figure reads $0.00
or N/A and the screen looks broken rather than empty. This builds the spine
it reports on, in the order a real project acquires it:

    cost codes -> a budget, approved and baselined -> commitments on the
    awarded packages -> actual expenditure in the analytic ledger -> an
    approved forecast, which is the only thing that can produce an ETC

The forecast matters most. The tower deliberately shows ``N/A`` rather than
zero when no forecast is approved, because an EAC of zero and an EAC nobody
has produced are different statements -- so a demo with no forecast shows off
the honesty and none of the arithmetic.

Expenditure is written NEGATIVE into ``account.analytic.line``, which is the
ledger's own convention; the reader flips the sign. Writing it positive here
would make every actual come back negative.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


def _overrun_reason(code, overrun):
    """Why a line is forecast above its budget, in words somebody can argue with."""
    return ("Rate pressure and remeasured quantities; cost to complete "
            "carried at %.0f%% of the budgeted rate for %s."
            % (overrun * 100, code))

MODULE = 'atmta_construction_cost'
SENTINEL = '%s.demo_budget' % MODULE

#: (code, name, category, budget, committed share, spent share, forecast overrun)
#:
#: The shares are deliberately uneven. A demo where every line is 80% spent
#: shows nothing; a cost report earns its place by showing WHICH line is in
#: trouble, so substructure overruns and finishes has barely started.
COST_PLAN = [
    ('01-100', 'Enabling works and site setup', 'subcontract',
     18_000_000, 1.00, 0.98, 1.00),
    ('02-200', 'Substructure and piling', 'subcontract',
     64_000_000, 1.00, 1.06, 1.12),
    ('03-300', 'Superstructure — concrete frame', 'subcontract',
     142_000_000, 0.96, 0.71, 1.03),
    ('04-400', 'Envelope and glazing', 'material',
     88_000_000, 0.82, 0.34, 1.00),
    ('05-500', 'MEP installation', 'subcontract',
     96_000_000, 0.74, 0.21, 1.05),
    ('06-600', 'Internal finishes', 'material',
     71_000_000, 0.31, 0.04, 1.00),
    ('07-700', 'External works and landscaping', 'subcontract',
     24_000_000, 0.12, 0.00, 1.00),
    ('08-800', 'Professional fees', 'professional',
     31_000_000, 0.88, 0.62, 1.00),
    ('09-900', 'Contingency', 'contingency',
     46_000_000, 0.00, 0.00, 1.00),
]


class ConstructionCostDemoBuilder(models.AbstractModel):
    _name = 'realestate.demo.construction.cost'
    _description = 'Demo Builder — Construction Cost Spine'

    @api.model
    def _xmlid(self, suffix, record):
        self.env['ir.model.data']._update_xmlids([{
            'xml_id': '%s.%s' % (MODULE, suffix), 'record': record,
            'noupdate': True}])
        return record

    @api.model
    def _project(self):
        project = self.env.ref('real_estate_developer.demo_tmr_project',
                               raise_if_not_found=False)
        if project:
            return project
        return self.env['realestate.project'].search(
            [('company_id', 'in', self.env.companies.ids)], limit=1)

    # ------------------------------------------------------------------
    @api.model
    def _build(self):
        if self.env.ref(SENTINEL, raise_if_not_found=False):
            return True
        project = self._project()
        if not project:
            _logger.warning("Demo: no project, construction cost skipped")
            return True

        account = self._project_analytic(project)
        if not account:
            _logger.warning("Demo: no analytic account, construction cost skipped")
            return True

        codes = self._build_cost_codes()
        if not codes:
            return True
        budget = self._build_budget(project, codes)
        self._build_actuals(project, account, codes)
        self._build_forecast(project, codes)
        if budget:
            self._xmlid('demo_budget', budget)
        return True

    # ------------------------------------------------------------------
    @api.model
    def _approver(self):
        """A user who may approve what this builder prepared.

        Maker/checker is a real control: whoever prepares a budget is not the
        person who approves it. The demo honours that by finding a SECOND
        user and granting them the commercial authority, rather than flipping
        `allow_self_approval` — which would switch the control off and leave
        a demo that cannot show it working.
        """
        group = self.env.ref('atmta_roles.group_construction_manager',
                             raise_if_not_found=False)
        approver = self.env['res.users'].search(
            [('id', '!=', self.env.uid), ('share', '=', False),
             ('active', '=', True)], limit=1)
        if not approver:
            return False
        if group and not approver.has_group('atmta_roles.group_construction_manager'):
            # Written from the USER side. Writing `user_ids` on the group
            # re-runs the implied-group recompute across every member and
            # fails; granting one user one group does not.
            field = 'groups_id' if 'groups_id' in approver._fields else 'group_ids'
            try:
                with self.env.cr.savepoint():
                    approver.sudo().write({field: [(4, group.id)]})
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: could not grant approver rights: %s", error)
                return False
        return approver

    @api.model
    def _advance(self, record, steps, approver):
        """Walk a record through its own workflow, approving as the checker."""
        for step in steps:
            if not hasattr(record, step):
                continue
            actor = approver if step in ('action_approve', 'action_baseline') else None
            target = record.with_user(actor) if actor else record
            try:
                with self.env.cr.savepoint():
                    getattr(target, step)()
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: %s on %s failed: %s",
                                step, record._name, error)
                return False
        return True

    @api.model
    def _project_analytic(self, project):
        """The project's analytic account, on the Real Estate Projects plan.

        Every actual is found by the project plan's column, so without this
        the analytic ledger has nothing to attach the cost to.
        """
        if project.analytic_account_id:
            return project.analytic_account_id
        Analytic = self.env['realestate.construction.analytic']
        plan = Analytic._project_plan()
        account = self.env['account.analytic.account'].create({
            'name': project.display_name,
            'plan_id': plan.id,
            'company_id': project.company_id.id or self.env.company.id,
        })
        try:
            with self.env.cr.savepoint():
                project.sudo().analytic_account_id = account.id
        except Exception as error:              # noqa: BLE001 - demo only
            _logger.warning("Demo: could not attach analytic account: %s", error)
            return False
        return account

    @api.model
    def _build_cost_codes(self):
        CostCode = self.env['realestate.construction.cost.code']
        company = self.env.company
        codes = {}
        for index, (code, name, category, *_rest) in enumerate(COST_PLAN, start=1):
            existing = CostCode.search(
                [('code', '=', code), ('company_id', '=', company.id)], limit=1)
            if existing:
                codes[code] = existing
                continue
            try:
                with self.env.cr.savepoint():
                    record = CostCode.create({
                        'code': code, 'name': name, 'category': category,
                        'company_id': company.id,
                    })
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: cost code %s failed: %s", code, error)
                continue
            codes[code] = record
            self._xmlid('demo_cost_code_%d' % index, record)
        # Each code needs its own analytic account before any actual can be
        # posted against it.
        if codes:
            self.env['realestate.construction.cost.code'].browse(
                [c.id for c in codes.values()])._analytic_accounts()
        return codes

    # ------------------------------------------------------------------
    @api.model
    def _build_budget(self, project, codes):
        Budget = self.env['realestate.construction.budget']
        today = fields.Date.context_today(self)
        try:
            with self.env.cr.savepoint():
                budget = Budget.create({
                    'name': 'TMR — Phase 1 Control Budget',
                    'project_id': project.id,
                    'source': 'manual',
                    'company_id': project.company_id.id or self.env.company.id,
                })
        except Exception as error:              # noqa: BLE001 - demo only
            _logger.warning("Demo: budget failed: %s", error)
            return False

        Line = self.env['realestate.construction.budget.line']
        for code, _name, _cat, amount, *_rest in COST_PLAN:
            if code not in codes:
                continue
            try:
                with self.env.cr.savepoint():
                    Line.create({
                        'budget_id': budget.id,
                        'cost_code_id': codes[code].id,
                        'description': dict(
                            (c, n) for c, n, *_r in COST_PLAN)[code],
                        'amount_mode': 'lumpsum',
                        'original_amount': amount,
                    })
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: budget line %s failed: %s", code, error)

        # A budget only becomes the control baseline once it is approved AND
        # baselined. A draft budget is somebody's spreadsheet.
        self._advance(budget, ('action_submit', 'action_approve', 'action_baseline'),
                      self._approver())
        _logger.info("Demo: budget %s is %s", budget.name, budget.state)
        return budget

    # ------------------------------------------------------------------
    @api.model
    def _build_actuals(self, project, account, codes):
        """Expenditure in the analytic ledger, spread over the last year.

        Written negative: `account.analytic.line` carries expenditure as a
        negative amount and the cost report flips the sign. Positive lines
        here would make every actual come back as a credit.
        """
        Analytic = self.env['realestate.construction.analytic']
        plan_column = Analytic._project_plan()._column_name()
        code_column = Analytic._cost_code_plan()._column_name()
        Line = self.env['account.analytic.line'].sudo()
        today = fields.Date.context_today(self)
        company = project.company_id or self.env.company

        for code, name, _cat, amount, _committed, spent, _f in COST_PLAN:
            if code not in codes or not spent:
                continue
            code_account = codes[code]._get_or_create_analytic_account()
            # Posting the ledger twice doubles every actual, and a cost report
            # that silently doubles is worse than one that is empty. The
            # sentinel guards a whole run; this guards a re-run that got
            # part-way.
            if Line.search_count([(plan_column, '=', account.id),
                                  (code_column, '=', code_account.id)]):
                continue
            total = amount * spent
            # Six monthly postings rather than one lump, so the ledger looks
            # like a project being built instead of a single journal entry.
            for month in range(6):
                portion = total / 6.0
                try:
                    with self.env.cr.savepoint():
                        Line.create({
                            'name': '%s — month %d' % (name, month + 1),
                            'date': today - relativedelta(months=6 - month),
                            'amount': -portion,
                            'company_id': company.id,
                            plan_column: account.id,
                            code_column: code_account.id,
                        })
                except Exception as error:      # noqa: BLE001 - demo only
                    _logger.warning("Demo: actual for %s failed: %s", code, error)
                    break

    # ------------------------------------------------------------------
    @api.model
    def _build_forecast(self, project, codes):
        """An approved forecast, which is the only source of an ETC.

        Without one the tower shows N/A rather than zero, deliberately. With
        one it can finally answer the question the screen exists for: what
        will this cost by the time it is finished?
        """
        Forecast = self.env['realestate.construction.forecast']
        today = fields.Date.context_today(self)
        try:
            with self.env.cr.savepoint():
                forecast = Forecast.create({
                    'name': 'TMR — Forecast %s' % today.strftime('%b %Y'),
                    'project_id': project.id,
                    'as_of_date': today,
                    'company_id': project.company_id.id or self.env.company.id,
                })
        except Exception as error:              # noqa: BLE001 - demo only
            _logger.warning("Demo: forecast failed: %s", error)
            return False

        Line = self.env['realestate.construction.forecast.line']
        for code, _name, _cat, amount, _committed, spent, overrun in COST_PLAN:
            if code not in codes:
                continue
            # Cost to complete = what is left of the line, at the forecast
            # overrun. Substructure comes in 12% over; finishes still track.
            etc = max(amount * overrun - amount * spent, 0.0)
            # A manual ETC must carry its basis. The module refuses to
            # approve a forecast whose numbers nobody can account for, which
            # is the whole point of a forecast being a controlled document --
            # so the demo supplies a real reason rather than a placeholder.
            vals = {
                'forecast_id': forecast.id,
                'cost_code_id': codes[code].id,
                'method': 'manual',
                'manual_etc': etc,
                'manual_reason': (
                    _overrun_reason(code, overrun) if overrun > 1.0
                    else "Remaining scope priced at the contract rate."),
            }
            try:
                with self.env.cr.savepoint():
                    Line.create(vals)
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: forecast line %s failed: %s", code, error)

        self._advance(forecast, ('action_submit', 'action_approve'), self._approver())
        _logger.info("Demo: forecast %s is %s", forecast.name, forecast.state)
        return forecast
