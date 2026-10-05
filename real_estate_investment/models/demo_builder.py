# -*- coding: utf-8 -*-
"""Demo layer: a feasibility study with scenarios that disagree.

A feasibility study whose best, expected and worst cases all return the same
number is not a study. These carry genuinely different assumptions -- sales
pace, price escalation and build cost -- so the scenario comparison on the
dashboard has something to compare, and the cash-flow curve shows the J shape
a development actually has: land and build out first, sales revenue later.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

MODULE = 'real_estate_investment'
SENTINEL = '%s.demo_feasibility_1' % MODULE

#: (scenario type, label, revenue multiplier, cost multiplier)
SCENARIOS = [
    ('best', 'Best Case — fast absorption', 1.12, 0.96),
    ('expected', 'Expected — base plan', 1.00, 1.00),
    ('worst', 'Worst Case — slow absorption, cost inflation', 0.86, 1.14),
]

#: One period per year. Negative is money going out.
#: (land + construction outflow, sales inflow) in millions.
BASE_CURVE = [
    (-420.0, 0.0),
    (-360.0, 180.0),
    (-280.0, 420.0),
    (-150.0, 560.0),
    (-40.0, 480.0),
    (0.0, 250.0),
]


class InvestmentDemoBuilder(models.AbstractModel):
    _name = 'realestate.demo.investment'
    _description = 'Demo Builder — Feasibility and Scenarios'

    @api.model
    def _xmlid(self, suffix, record):
        self.env['ir.model.data']._update_xmlids([{
            'xml_id': '%s.%s' % (MODULE, suffix), 'record': record,
            'noupdate': True}])
        return record

    @api.model
    def _build(self):
        if self.env.ref(SENTINEL, raise_if_not_found=False):
            return True
        Feasibility = self.env['realestate.investment.feasibility']
        Scenario = self.env['realestate.investment.scenario']
        CashFlow = self.env['realestate.investment.cash.flow']
        today = fields.Date.context_today(self)
        project = self.env.ref('real_estate_developer.demo_tmr_project',
                               raise_if_not_found=False)

        studies = [
            ('Teklines Marina Residences — Phase 1 Feasibility', 'approved', project),
            ('Teklines Marina Residences — Phase 2 Appraisal', 'draft', project),
        ]
        for index, (name, state, linked) in enumerate(studies, start=1):
            vals = {
                'name': name,
                'state': state,
                'analysis_date': today - relativedelta(months=index * 3),
                'analysis_period_years': len(BASE_CURVE),
                'base_year': today.year,
                # A discount rate is not decoration: it is what turns a pile
                # of future cash into a number you can compare to the cost of
                # the land today.
                'discount_rate': 14.0,
            }
            if linked and 'project_id' in Feasibility._fields:
                vals['project_id'] = linked.id
            try:
                with self.env.cr.savepoint():
                    feasibility = Feasibility.create(vals)
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: feasibility %s failed: %s", name, error)
                continue
            self._xmlid('demo_feasibility_%d' % index, feasibility)

            for scenario_type, label, revenue_factor, cost_factor in SCENARIOS:
                try:
                    with self.env.cr.savepoint():
                        Scenario.create({
                            'feasibility_id': feasibility.id,
                            'name': label,
                            'scenario_type': scenario_type,
                            # The factors are the whole scenario. Without
                            # them all three cases return the same NPV and
                            # the comparison is theatre.
                            'inflow_factor': revenue_factor,
                            'outflow_factor': cost_factor,
                        })
                except Exception as error:      # noqa: BLE001 - demo only
                    _logger.warning("Demo: scenario %s failed: %s", label, error)

            # The J curve: land and build out first, revenue later. A study
            # whose cash flow is positive from period one is not modelling a
            # development, it is modelling a wish.
            for period, (outflow, inflow) in enumerate(BASE_CURVE):
                flow_vals = {'feasibility_id': feasibility.id,
                             'period_index': period}
                for field, value in (('outflow', abs(outflow) * 1_000_000),
                                     ('inflow', inflow * 1_000_000),
                                     ('net_flow', (inflow + outflow) * 1_000_000),
                                     ('period_label', str(today.year + period))):
                    if field in CashFlow._fields:
                        flow_vals[field] = value
                try:
                    with self.env.cr.savepoint():
                        CashFlow.create(flow_vals)
                except Exception as error:      # noqa: BLE001 - demo only
                    _logger.warning("Demo: cash flow period %s failed: %s",
                                    period, error)
        return True
