# -*- coding: utf-8 -*-
"""Investment appraisal on the shared dashboard provider.

Every money figure on this screen is labelled **Modelled**, because that is
what it is. An NPV is the output of a discounted cash-flow model, not of the
ledger, and a dashboard that shows a modelled return in the same typeface as
collected rent is how a forecast ends up quoted as a fact. Treasury draws the
same line between paper and cash; this is the same discipline applied to
appraisal.

Only approved studies feed the headline numbers. A draft is somebody's working
file, and averaging it into a portfolio return would mean the board's number
moves whenever an analyst saves a spreadsheet.
"""

from datetime import timedelta

from odoo import _, models

APPROVED = [('state', '=', 'approved')]
FEASIBILITY = 'realestate.investment.feasibility'
SCENARIO = 'realestate.investment.scenario'
CASH_FLOW = 'realestate.investment.cash.flow'


class InvestmentProvider(models.AbstractModel):
    _name = 'realestate.investment.provider'
    _inherit = 'atmta.dashboard.provider'
    _description = 'Investment Dashboard'

    def _dashboard_title(self):
        return _("Investment")

    # ------------------------------------------------------------------
    def _approved(self):
        if not self._can_read(FEASIBILITY):
            return self.env[FEASIBILITY].browse()
        return self.env[FEASIBILITY].search(APPROVED)

    # ------------------------------------------------------------------
    def _dashboard_sections(self, scope):
        approved = self._approved()
        modelled = {'qualifier': 'modelled', 'format': 'monetary'}

        # Peak funding need is the deepest point of the cumulative curve: the
        # most cash the business is ever out of pocket. It is the number that
        # decides whether a scheme is financeable, and it is nowhere in a
        # total-cost figure.
        peak = sum(self._peak_funding(study) for study in approved)
        positive = approved.filtered('is_positive')

        return [
            {'id': 'returns', 'title': _("Modelled Returns"), 'icon': 'fa-line-chart',
             'subtitle': _("Approved studies only — a draft is somebody's working file"),
             'tiles': [
                 {'key': 'npv_total', 'label': _("Portfolio NPV"),
                  'value': sum(approved.mapped('npv')),
                  'icon': 'fa-money', 'tone': 'success',
                  'hint': _("Net present value of every approved study at its "
                            "own discount rate."), **modelled},
                 {'key': 'peak_funding', 'label': _("Peak Funding Need"),
                  'value': peak, 'icon': 'fa-arrow-down', 'tone': 'danger',
                  'higher_is_better': False,
                  'hint': _("The deepest point of the cumulative curve — the "
                            "most cash the business is ever out of pocket. "
                            "This is what decides financeability, and no total "
                            "cost figure shows it."), **modelled},
                 {'key': 'capital_out', 'label': _("Capital Modelled Out"),
                  'value': sum(approved.mapped('total_outflow')),
                  'icon': 'fa-upload', 'tone': 'warning',
                  'higher_is_better': False, **modelled},
                 {'key': 'revenue_in', 'label': _("Revenue Modelled In"),
                  'value': sum(approved.mapped('total_inflow')),
                  'icon': 'fa-download', 'tone': 'primary', **modelled},
                 {'key': 'irr_avg', 'label': _("Average IRR"),
                  'value': (sum(approved.mapped('irr')) / len(approved)) if approved else 0.0,
                  'format': 'percent', 'icon': 'fa-percent', 'tone': 'info',
                  'qualifier': 'modelled',
                  'hint': _("Unweighted mean across approved studies. A small "
                            "scheme with a spectacular IRR counts the same as "
                            "a large one here.")},
                 {'key': 'payback_avg', 'label': _("Average Payback"),
                  'value': (sum(approved.mapped('payback_period')) / len(approved)) if approved else 0.0,
                  'format': 'decimal', 'suffix': _(" yrs"),
                  'icon': 'fa-clock-o', 'tone': 'neutral',
                  'higher_is_better': False, 'qualifier': 'modelled'},
             ]},
            {'id': 'pipeline', 'title': _("Appraisal Pipeline"), 'icon': 'fa-folder-open',
             'tiles': [
                 {'key': 'studies_draft', 'label': _("Studies in Draft"),
                  'model': FEASIBILITY, 'domain': [('state', '=', 'draft')],
                  'icon': 'fa-pencil', 'tone': 'warning',
                  'hint': _("Not yet approved, so not yet a basis for a decision.")},
                 {'key': 'studies_approved', 'label': _("Approved Studies"),
                  'model': FEASIBILITY, 'domain': APPROVED,
                  'icon': 'fa-check-circle', 'tone': 'success',
                  'trend_field': 'analysis_date', 'trend_granularity': 'month'},
                 {'key': 'studies_viable', 'label': _("Modelled Viable"),
                  'value': len(positive), 'icon': 'fa-thumbs-up', 'tone': 'success',
                  'hint': _("Approved studies whose NPV is positive at their "
                            "own discount rate.")},
                 {'key': 'studies_rejected', 'label': _("Rejected"),
                  'model': FEASIBILITY, 'domain': [('state', '=', 'rejected')],
                  'icon': 'fa-times-circle', 'tone': 'danger',
                  'higher_is_better': False},
                 {'key': 'scenarios', 'label': _("Scenarios Modelled"),
                  'model': SCENARIO, 'domain': [],
                  'icon': 'fa-random', 'tone': 'info',
                  'hint': _("A study whose best, expected and worst cases "
                            "agree is not a study.")},
                 {'key': 'scenarios_worst', 'label': _("Downside Cases"),
                  'model': SCENARIO, 'domain': [('scenario_type', '=', 'worst')],
                  'icon': 'fa-arrow-down', 'tone': 'warning'},
             ]},
        ]

    # ------------------------------------------------------------------
    def _peak_funding(self, study):
        """The deepest trough of the cumulative net cash curve, as a positive.

        Running the cumulative rather than summing outflows matters: revenue
        arriving in year two offsets the spend in year three, so the gross
        outflow badly overstates what has to be financed.
        """
        if not study.cash_flow_ids:
            return 0.0
        running = 0.0
        trough = 0.0
        for flow in study.cash_flow_ids.sorted('period_index'):
            running += (flow.inflow or 0.0) - (flow.outflow or 0.0)
            trough = min(trough, running)
        return abs(trough)

    # ------------------------------------------------------------------
    def _dashboard_charts(self, scope):
        charts = []
        approved = self._approved()

        # ---- The J curve -------------------------------------------------
        # Land and build out first, sales revenue later. A study whose cash
        # flow is positive from period one is not modelling a development.
        study = approved[:1] or (self.env[FEASIBILITY].search([], limit=1)
                                 if self._can_read(FEASIBILITY) else None)
        if study and study.cash_flow_ids:
            flows = study.cash_flow_ids.sorted('period_index')
            labels, inflow, outflow, cumulative = [], [], [], []
            running = 0.0
            for flow in flows:
                labels.append(flow.period_label or str(flow.year or flow.period_index))
                inflow.append(round(flow.inflow or 0.0, 2))
                outflow.append(round(-(flow.outflow or 0.0), 2))
                running += (flow.inflow or 0.0) - (flow.outflow or 0.0)
                cumulative.append(round(running, 2))
            charts.append({
                'key': 'j_curve',
                'title': _("Cash Flow — %s", study.name),
                'subtitle': _("Cumulative dips before it recovers: that trough "
                              "is what has to be financed"),
                'type': 'bar', 'icon': 'fa-area-chart', 'span': 'o_ad_col_8',
                'labels': labels,
                'series': [
                    {'name': 'outflow', 'label': _("Out"), 'data': outflow,
                     'format': 'monetary', 'tone': 'danger'},
                    {'name': 'inflow', 'label': _("In"), 'data': inflow,
                     'format': 'monetary', 'tone': 'success'},
                    {'name': 'cumulative', 'label': _("Cumulative"),
                     'data': cumulative, 'format': 'monetary',
                     'type': 'line', 'tone': 'primary'},
                ],
            })

        # ---- Scenario spread --------------------------------------------
        if self._can_read(SCENARIO):
            scenarios = self.env[SCENARIO].search(
                [('feasibility_id.state', '=', 'approved')])
            by_type = {}
            for scenario in scenarios:
                by_type.setdefault(scenario.scenario_type, []).append(scenario.npv or 0.0)
            order = [('best', _("Best"), 'success'),
                     ('expected', _("Expected"), 'primary'),
                     ('worst', _("Worst"), 'danger')]
            keys = [item for item in order if by_type.get(item[0])]
            if keys:
                charts.append({
                    'key': 'scenario_spread',
                    'title': _("Scenario Spread"),
                    # The gap between best and worst IS the risk. A single
                    # expected-case number hides it completely.
                    'subtitle': _("The distance between best and worst is the risk"),
                    'type': 'bar', 'icon': 'fa-random', 'span': 'o_ad_col_4',
                    'labels': [label for _k, label, _t in keys],
                    'series': [{'name': 'npv', 'label': _("NPV"),
                                'format': 'monetary',
                                'data': [round(sum(by_type[k]), 2) for k, _l, _t in keys]}],
                })

        # ---- Studies by stage -------------------------------------------
        if self._can_read(FEASIBILITY):
            Model = self.env[FEASIBILITY]
            labels = dict(Model._fields['state']._description_selection(self.env))
            counts = dict(Model._read_group([], groupby=['state'],
                                            aggregates=['__count']))
            keys = [k for k in labels if counts.get(k)]
            if keys:
                charts.append({
                    'key': 'studies_by_state', 'title': _("Studies by Stage"),
                    'subtitle': _("Where appraisal work is sitting"),
                    'type': 'doughnut', 'icon': 'fa-folder-open',
                    'span': 'o_ad_col_4',
                    'labels': [labels[k] for k in keys],
                    'series': [{'name': 'count', 'label': _("Studies"),
                                'data': [counts[k] for k in keys]}],
                    'drill': [{'key': 'studies_%s' % k, 'label': labels[k],
                               'model': FEASIBILITY, 'domain': [('state', '=', k)]}
                              for k in keys],
                })
        return charts

    # ------------------------------------------------------------------
    def _dashboard_tables(self, scope):
        tables = []
        if self._can_read(FEASIBILITY):
            studies = self.env[FEASIBILITY].search([], order='analysis_date desc',
                                                   limit=15)
            rows = []
            for study in studies:
                downside = 'worst' in study.scenario_ids.mapped('scenario_type')
                rows.append({
                    'id': study.id,
                    'study': study.name,
                    'stage': dict(study._fields['state']._description_selection(
                        self.env)).get(study.state, study.state),
                    'stage_tone': {'approved': 'success', 'draft': 'warning',
                                   'rejected': 'danger'}.get(study.state, 'neutral'),
                    'npv': study.npv,
                    'irr': study.irr,
                    'payback': study.payback_period,
                    'peak': self._peak_funding(study),
                    'downside': _("Modelled") if downside else _("Missing"),
                    'downside_tone': 'success' if downside else 'danger',
                })
            tables.append({
                'key': 'studies', 'title': _("Feasibility Studies"),
                'icon': 'fa-table', 'span': 'o_ad_col_12',
                'subtitle': _("Every money column is modelled, not booked"),
                'empty_text': _("No feasibility study has been created."),
                'model': FEASIBILITY,
                'columns': [
                    {'key': 'study', 'label': _("Study")},
                    {'key': 'stage', 'label': _("Stage"), 'type': 'badge'},
                    {'key': 'npv', 'label': _("NPV"), 'numeric': True,
                     'format': 'monetary'},
                    {'key': 'irr', 'label': _("IRR"), 'numeric': True,
                     'format': 'percent'},
                    {'key': 'payback', 'label': _("Payback (yrs)"), 'numeric': True,
                     'format': 'decimal'},
                    {'key': 'peak', 'label': _("Peak Funding"), 'numeric': True,
                     'format': 'monetary'},
                    {'key': 'downside', 'label': _("Downside Case"), 'type': 'badge'},
                ],
                'rows': rows,
            })

        if self._can_read(SCENARIO):
            scenarios = self.env[SCENARIO].search(
                [('feasibility_id.state', '=', 'approved')],
                order='feasibility_id, sequence', limit=15)
            rows = [{
                'id': scenario.id,
                'scenario': scenario.name,
                'study': scenario.feasibility_id.name,
                'npv': scenario.npv,
                'irr': scenario.irr,
                'verdict': _("Viable") if scenario.is_positive else _("Not viable"),
                'verdict_tone': 'success' if scenario.is_positive else 'danger',
            } for scenario in scenarios]
            tables.append({
                'key': 'scenarios', 'title': _("Scenario Comparison"),
                'icon': 'fa-random', 'span': 'o_ad_col_12',
                'subtitle': _("Approved studies — if every case agrees, nothing "
                              "was actually tested"),
                'empty_text': _("No scenario has been modelled on an approved study."),
                'model': SCENARIO,
                'columns': [
                    {'key': 'study', 'label': _("Study")},
                    {'key': 'scenario', 'label': _("Scenario")},
                    {'key': 'npv', 'label': _("NPV"), 'numeric': True,
                     'format': 'monetary'},
                    {'key': 'irr', 'label': _("IRR"), 'numeric': True,
                     'format': 'percent'},
                    {'key': 'verdict', 'label': _("Verdict"), 'type': 'badge'},
                ],
                'rows': rows,
            })
        return tables

    # ------------------------------------------------------------------
    def _dashboard_alerts(self, scope):
        today = self._today()
        rows = []
        if self._can_read(FEASIBILITY):
            Model = self.env[FEASIBILITY]
            approved = self._approved()
            # A study approved on the expected case alone is the one that
            # surprises somebody later.
            no_downside = sum(
                1 for study in approved
                if 'worst' not in study.scenario_ids.mapped('scenario_type'))
            rows.append({
                'key': 'studies_no_downside',
                'label': _("Approved with no downside case"),
                'sublabel': _("Approved on the expected case alone"),
                'value': no_downside, 'severity': 'critical',
                'icon': 'fa-arrow-down', 'drill': False})
            rows.append({
                'key': 'studies_negative',
                'label': _("Approved but modelled negative"),
                'sublabel': _("NPV below zero at the study's own discount rate"),
                'value': len(approved.filtered(lambda s: not s.is_positive)),
                'severity': 'critical', 'icon': 'fa-exclamation-circle',
                'drill': False})
            rows.append({
                'key': 'studies_stale', 'label': _("Approved over a year ago"),
                'sublabel': _("Assumptions age; costs and prices move"),
                'value': Model.search_count(
                    APPROVED + [('analysis_date', '<=', today - timedelta(days=365))]),
                'severity': 'warning', 'icon': 'fa-clock-o'})
            rows.append({
                'key': 'studies_draft', 'label': _("Studies still in draft"),
                'sublabel': _("Not a basis for a decision until approved"),
                'value': Model.search_count([('state', '=', 'draft')]),
                'severity': 'warning', 'icon': 'fa-pencil'})
            rows.append({
                'key': 'studies_no_flows', 'label': _("Studies with no cash flow"),
                'sublabel': _("Nothing to discount, so the NPV means nothing"),
                'value': Model.search_count([('cash_flow_ids', '=', False)]),
                'icon': 'fa-ban'})
        return rows

    def _dashboard_work_queue(self, scope):
        rows = []
        if self._can_read(FEASIBILITY):
            Model = self.env[FEASIBILITY]
            rows.append({'key': 'studies_draft', 'label': _("Studies to Finish"),
                         'value': Model.search_count([('state', '=', 'draft')]),
                         'severity': 'warning', 'icon': 'fa-pencil'})
            rows.append({'key': 'studies_no_downside_q',
                         'label': _("Downside Cases to Model"),
                         'value': sum(1 for s in self._approved()
                                      if 'worst' not in s.scenario_ids.mapped('scenario_type')),
                         'icon': 'fa-random', 'drill': False})
            rows.append({'key': 'studies_approved', 'label': _("Approved Studies"),
                         'value': Model.search_count(APPROVED),
                         'icon': 'fa-check-circle'})
        return rows

    def _dashboard_quick_actions(self):
        return [{'key': 'studies', 'label': _("Feasibility Studies"),
                 'icon': 'fa-line-chart', 'model': FEASIBILITY},
                {'key': 'scenarios', 'label': _("Scenarios"),
                 'icon': 'fa-random', 'model': SCENARIO}]
