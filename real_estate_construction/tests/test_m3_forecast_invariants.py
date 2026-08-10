# -*- coding: utf-8 -*-
"""M3 — the three invariants forecasting is built around.

Written before the models, like M2's equation test. Everything else in M3 is
elaboration; these three are what must never be wrong.

```
    ETC              = expected future cost from the forecast date forward
    EAC              = ACTUAL + ETC
    FORECAST VARIANCE= CURRENT BUDGET − EAC     (positive = favourable)
```
"""

from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestForecastInvariants(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')

    # ------------------------------------------------------------------
    def test_1_basic_eac(self):
        """Budget 10M, commitment 8M, actual 3M, manual ETC 5M
        → EAC 8M, variance +2M favourable."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 8_000_000.0)])
        self._post_bill(self.project, self.civil, 3_000_000.0)

        forecast = self._forecast(self.project)
        line = forecast.line_ids.filtered(
            lambda l: l.cost_code_id == self.civil)
        line.write({'method': 'manual', 'manual_etc': 5_000_000.0,
                    'manual_reason': 'Remaining civil works, priced.'})
        forecast.invalidate_recordset()

        self.assertEqual(line.actual_amount, 3_000_000.0)
        self.assertEqual(line.etc_amount, 5_000_000.0)
        self.assertEqual(line.eac_amount, 8_000_000.0)
        self.assertEqual(line.current_budget, 10_000_000.0)
        self.assertEqual(
            line.forecast_variance, 2_000_000.0,
            "Positive variance is favourable — forecast under budget.")
        self.assertEqual(forecast.total_eac, 8_000_000.0)
        self.assertEqual(forecast.total_forecast_variance, 2_000_000.0)

    def test_2_forecast_overrun_does_not_move_the_budget(self):
        """Budget 10M, actual 4M, ETC 8M → EAC 12M, variance −2M,
        and the budget is still 10M."""
        budget = self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._post_bill(self.project, self.civil, 4_000_000.0)

        forecast = self._forecast(self.project)
        line = forecast.line_ids.filtered(
            lambda l: l.cost_code_id == self.civil)
        line.write({'method': 'manual', 'manual_etc': 8_000_000.0,
                    'manual_reason': 'Productivity loss and rework.'})
        forecast.invalidate_recordset()
        budget.invalidate_recordset()

        self.assertEqual(line.eac_amount, 12_000_000.0)
        self.assertEqual(
            line.forecast_variance, -2_000_000.0,
            "Negative variance is unfavourable, and is shown rather than "
            "capped.")
        self.assertEqual(
            budget.current_amount, 10_000_000.0,
            "A forecast predicts. It does not authorise.")
        self.assertEqual(
            self.env['realestate.construction.controls'].project_totals(
                self.project)['current_budget'], 10_000_000.0)

    def test_3_unawarded_scope_is_missing_not_zero(self):
        """Budget 10M, no commitment, no actual, nobody has forecast it.

        ETC must not quietly become zero — that would say the remaining 10M of
        work is free.
        """
        self._baselined(self.project, 10_000_000.0, code=self.civil)

        forecast = self._forecast(self.project)
        line = forecast.line_ids.filtered(
            lambda l: l.cost_code_id == self.civil)

        self.assertEqual(line.forecast_status, 'missing')
        self.assertFalse(
            line.has_etc,
            "No method has produced an estimate, so there is no ETC — which "
            "is a different statement from 'the ETC is zero'.")
        self.assertEqual(forecast.line_count_missing, 1)
        self.assertEqual(forecast.forecast_coverage, 0.0)
        self.assertFalse(
            forecast.eac_is_complete,
            "A total EAC over an unforecast project is not authoritative and "
            "must not be presented as though it were.")
