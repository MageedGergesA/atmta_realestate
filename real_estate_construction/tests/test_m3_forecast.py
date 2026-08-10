# -*- coding: utf-8 -*-
"""M3 — the forecast test matrix (§50).

Every method, every edge, and the rules that keep a forecast honest: missing is
not zero, approved is frozen, and a forecast never moves the budget.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ConstructionCommon


class ForecastCommon(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Forecast = self.env['realestate.construction.forecast']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        self.labor = self._cost_code('LAB-GEN', 'General Labour', 'labor')

    def _line_for(self, forecast, code):
        return forecast.line_ids.filtered(lambda l: l.cost_code_id == code)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestForecastMethods(ForecastCommon):

    def test_manual_needs_a_stated_basis(self):
        self._baselined(self.project, 1_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)

        line.write({'method': 'manual', 'manual_etc': 400_000.0})

        self.assertEqual(line.forecast_status, 'insufficient_data')
        self.assertFalse(line.has_etc)

        line.manual_reason = 'Priced from the remaining drawings.'

        self.assertEqual(line.forecast_status, 'forecasted')
        self.assertEqual(line.etc_amount, 400_000.0)

    def test_remaining_commitment(self):
        """Commitment 8M, attributable actual 3M → ETC 5M, EAC 8M."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 8_000_000.0)])
        self._post_bill(self.project, self.civil, 3_000_000.0)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)

        line.method = 'remaining_commitment'

        self.assertEqual(line.committed_remaining, 5_000_000.0)
        self.assertEqual(line.etc_amount, 5_000_000.0)
        self.assertEqual(line.eac_amount, 8_000_000.0)

    def test_remaining_commitment_refuses_when_nothing_is_committed(self):
        """It must not quietly forecast zero for unawarded scope."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)

        line.method = 'remaining_commitment'

        self.assertEqual(line.forecast_status, 'insufficient_data')
        self.assertFalse(line.has_etc)
        self.assertIn('nothing is committed', line.status_reason.lower())

    def test_finish_at_budget_is_named_as_an_assumption(self):
        """Budget 10M, actual 3M → ETC 7M, and the label says what it is."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._post_bill(self.project, self.civil, 3_000_000.0)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)

        line.method = 'finish_at_budget'

        self.assertEqual(line.etc_amount, 7_000_000.0)
        self.assertEqual(line.eac_amount, 10_000_000.0)
        self.assertIn('finish at budget', line.status_reason.lower())

    def test_percent_complete(self):
        """Actual 4M at 40% → EAC 10M, ETC 6M."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._post_bill(self.project, self.civil, 4_000_000.0)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)

        line.write({'method': 'percent_complete', 'progress_pct': 40.0})

        self.assertAlmostEqual(line.eac_amount, 10_000_000.0, places=2)
        self.assertAlmostEqual(line.etc_amount, 6_000_000.0, places=2)

    def test_percent_complete_refuses_impossible_progress(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._post_bill(self.project, self.civil, 4_000_000.0)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.method = 'percent_complete'

        for progress in (0.0, -10.0, 101.0):
            line.progress_pct = progress
            self.assertEqual(
                line.forecast_status, 'insufficient_data',
                "Progress of %s cannot forecast anything." % progress)
            self.assertFalse(line.has_etc)

    def test_percent_complete_needs_posted_cost_to_measure(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)

        line.write({'method': 'percent_complete', 'progress_pct': 40.0})

        self.assertEqual(line.forecast_status, 'insufficient_data')

    def test_time_based_forecast(self):
        self._baselined(self.project, 1_200_000.0, code=self.labor)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.labor)

        line.write({'method': 'date_range', 'periods_remaining': 8.0,
                    'period_rate': 50_000.0})

        self.assertEqual(line.etc_amount, 400_000.0)

    def test_the_boq_method_answers_only_from_a_priced_authorised_boq(self):
        """§34 — was: withheld entirely while certified quantities were
        known-bad. M7 fixed the input; the method now answers, and still
        refuses when there is nothing trustworthy to answer from.
        """
        self._baselined(self.project, 1_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)

        line.write({'method': 'remaining_boq'})

        self.assertEqual(line.forecast_status, 'insufficient_data')
        self.assertFalse(line.has_etc)
        self.assertIn('authorised quantity', line.status_reason)

        forecast.action_submit()
        with self.assertRaises(UserError):
            forecast.action_approve()

    def test_no_forecast_required_is_a_decision_not_a_gap(self):
        self._baselined(self.project, 0.0, code=self.civil)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)

        line.method = 'not_required'

        self.assertEqual(line.forecast_status, 'not_required')
        self.assertFalse(line.has_etc)
        self.assertEqual(forecast.line_count_missing, 0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestForecastOverrideAndAdjustments(ForecastCommon):

    def test_an_override_keeps_the_calculated_value(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._post_bill(self.project, self.civil, 3_000_000.0)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.method = 'finish_at_budget'
        self.assertEqual(line.calculated_etc, 7_000_000.0)

        line.write({'override_etc': 9_000_000.0,
                    'override_reason': 'Management view: rework expected.'})

        self.assertEqual(line.etc_amount, 9_000_000.0)
        self.assertEqual(
            line.calculated_etc, 7_000_000.0,
            "The system's number survives beside management's.")
        self.assertTrue(line.is_overridden)
        self.assertEqual(line.overridden_by_id, self.env.user)
        self.assertTrue(line.override_date)

    def test_an_override_without_a_reason_blocks_approval(self):
        self._baselined(self.project, 1_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.write({'method': 'finish_at_budget', 'override_etc': 500_000.0})
        forecast.action_submit()

        with self.assertRaises(UserError):
            forecast.action_approve()

    def test_adjustments_move_the_forecast_and_nothing_else(self):
        budget = self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 8_000_000.0)])
        self._post_bill(self.project, self.civil, 3_000_000.0)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.method = 'remaining_commitment'
        self.assertEqual(line.etc_amount, 5_000_000.0)

        self.env['realestate.construction.forecast.adjustment'].create({
            'line_id': line.id,
            'adjustment_type': 'productivity',
            'description': 'Slower than planned on the podium',
            'amount': 750_000.0,
        })
        line.invalidate_recordset()
        budget.invalidate_recordset()

        self.assertEqual(line.etc_amount, 5_750_000.0)
        self.assertEqual(line.adjustment_total, 750_000.0)
        self.assertEqual(
            budget.current_amount, 10_000_000.0,
            "A forecast adjustment authorises nothing.")
        totals = self.env['realestate.construction.controls'].project_totals(
            self.project)
        self.assertEqual(totals['current_commitment'], 8_000_000.0)
        self.assertEqual(totals['actual_cost'], 3_000_000.0)

    def test_a_negative_adjustment_records_an_expected_saving(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.write({'method': 'finish_at_budget'})
        self.env['realestate.construction.forecast.adjustment'].create({
            'line_id': line.id, 'adjustment_type': 'market_rate',
            'description': 'Steel bought below estimate', 'amount': -500_000.0,
        })
        line.invalidate_recordset()

        self.assertEqual(line.etc_amount, 9_500_000.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestForecastSnapshot(ForecastCommon):
    """§4, §10, §29 — the cutoff, and the freeze."""

    def test_actual_excludes_postings_after_the_cutoff(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        march = self.today
        april = self.today + relativedelta(months=1)
        self._post_bill(self.project, self.civil, 3_000_000.0)

        forecast = self._forecast(self.project, as_of_date=march)
        line = self._line_for(forecast, self.civil)
        self.assertEqual(line.actual_amount, 3_000_000.0)

        # Dated April at creation: a posted move's date is readonly, which is
        # Odoo protecting the ledger and is exactly right.
        self._post_bill(self.project, self.civil, 2_000_000.0, date=april)

        fresh = self._forecast(self.project, as_of_date=march)
        self.assertEqual(
            self._line_for(fresh, self.civil).actual_amount, 3_000_000.0,
            "A bill dated April is not March's cost.")

    def test_an_approved_forecast_does_not_move_when_accounting_does(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._post_bill(self.project, self.civil, 3_000_000.0)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.write({'method': 'manual', 'manual_etc': 5_000_000.0,
                    'manual_reason': 'Priced'})
        forecast.action_submit()
        forecast.action_approve()
        approved_eac = forecast.total_eac

        self._post_bill(self.project, self.civil, 4_000_000.0)
        forecast.invalidate_recordset()

        self.assertEqual(forecast.total_eac, approved_eac)
        self.assertEqual(forecast.total_actual, 3_000_000.0)

    def test_an_approved_forecast_cannot_be_edited_or_refreshed(self):
        self._baselined(self.project, 1_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.write({'method': 'finish_at_budget'})
        forecast.action_submit()
        forecast.action_approve()

        with self.assertRaises(UserError):
            line.write({'manual_etc': 1.0})
        with self.assertRaises(UserError):
            forecast.action_refresh()
        with self.assertRaises(UserError):
            forecast.action_reset_to_draft()

    def test_only_one_approved_forecast_per_period(self):
        self._baselined(self.project, 1_000_000.0, code=self.civil)
        first = self._forecast(self.project)
        self._line_for(first, self.civil).method = 'finish_at_budget'
        first.action_submit()
        first.action_approve()

        second = self._forecast(self.project)
        self._line_for(second, self.civil).method = 'finish_at_budget'
        second.action_submit()

        with self.assertRaises(UserError):
            second.action_approve()

    def test_a_draft_forecast_may_be_refreshed_without_losing_manual_input(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.write({'method': 'manual', 'manual_etc': 6_000_000.0,
                    'manual_reason': 'Priced from drawings'})

        self._post_bill(self.project, self.civil, 2_000_000.0)
        forecast.action_refresh()
        line.invalidate_recordset()

        self.assertEqual(line.actual_amount, 2_000_000.0)
        self.assertEqual(line.manual_etc, 6_000_000.0,
                         "A refresh updates facts, not judgement.")
        self.assertEqual(line.eac_amount, 8_000_000.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestForecastCopyForwardAndMovement(ForecastCommon):

    def _approved(self, as_of, etc, method='manual'):
        forecast = self._forecast(self.project, as_of_date=as_of)
        line = self._line_for(forecast, self.civil)
        line.write({'method': method, 'manual_etc': etc,
                    'manual_reason': 'Carried estimate'})
        forecast.action_submit()
        forecast.action_approve()
        return forecast

    def test_the_next_forecast_carries_method_and_reasoning_not_actuals(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._post_bill(self.project, self.civil, 2_000_000.0)
        march = self._approved(self.today, 6_000_000.0)

        self._post_bill(self.project, self.civil, 1_000_000.0)
        april = self._forecast(self.project,
                               as_of_date=self.today + relativedelta(months=1))
        line = self._line_for(april, self.civil)

        self.assertEqual(line.method, 'manual')
        self.assertEqual(line.manual_etc, 6_000_000.0)
        self.assertEqual(line.manual_reason, 'Carried estimate')
        self.assertEqual(
            line.actual_amount, 3_000_000.0,
            "Actual is refreshed from the ledger, never copied.")
        self.assertEqual(april.previous_forecast_id, march)

    def test_eac_movement_between_periods(self):
        self._baselined(self.project, 100_000_000.0, code=self.civil)
        february = self._approved(self.today - relativedelta(months=1),
                                  100_000_000.0)
        march = self._forecast(self.project, as_of_date=self.today)
        line = self._line_for(march, self.civil)
        line.write({'method': 'manual', 'manual_etc': 105_000_000.0,
                    'manual_reason': 'Revised'})
        march.invalidate_recordset()

        self.assertEqual(march.previous_eac, 100_000_000.0)
        self.assertEqual(
            march.eac_movement, 5_000_000.0,
            "A growing EAC is adverse and is shown as a positive movement.")

    def test_the_movement_bridge_explains_what_it_can_and_admits_the_rest(self):
        self._baselined(self.project, 100_000_000.0, code=self.civil)
        self._approved(self.today - relativedelta(months=1), 100_000_000.0)
        march = self._forecast(self.project, as_of_date=self.today)
        line = self._line_for(march, self.civil)
        line.write({'method': 'manual', 'manual_etc': 103_000_000.0,
                    'manual_reason': 'Revised'})
        self.env['realestate.construction.forecast.adjustment'].create({
            'line_id': line.id, 'adjustment_type': 'productivity',
            'description': 'Slower podium', 'amount': 2_000_000.0})
        march.invalidate_recordset()

        bridge = self.Forecast.movement_bridge(march)

        self.assertEqual(bridge['previous_eac'], 100_000_000.0)
        self.assertEqual(bridge['explained'], 2_000_000.0)
        self.assertEqual(
            bridge['unexplained'], bridge['total_movement'] - 2_000_000.0,
            "Unexplained movement is shown, not hidden.")


@tagged('post_install', '-at_install', 'atmta_construction')
class TestForecastCompleteness(ForecastCommon):
    """§23-§25 — missing is not zero, and a partial total says so."""

    def test_a_missing_line_contributes_no_etc(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        budget = self.env['realestate.construction.budget'].current_for(
            self.project)
        budget.sudo().write({'state': 'draft'})
        self.env['realestate.construction.budget.line'].create({
            'budget_id': budget.id, 'cost_code_id': self.labor.id,
            'amount_mode': 'lumpsum', 'original_amount': 5_000_000.0})
        budget.sudo().write({'state': 'baselined'})

        forecast = self._forecast(self.project)
        self._line_for(forecast, self.civil).write(
            {'method': 'manual', 'manual_etc': 8_000_000.0,
             'manual_reason': 'Priced'})
        forecast.invalidate_recordset()

        self.assertEqual(forecast.total_etc, 8_000_000.0)
        self.assertEqual(forecast.line_count_missing, 1)
        self.assertFalse(forecast.eac_is_complete)

    def test_coverage_is_weighted_by_budget(self):
        budget = self.env['realestate.construction.budget'].create({
            'project_id': self.project.id,
            'line_ids': [
                (0, 0, {'cost_code_id': self.civil.id,
                        'amount_mode': 'lumpsum',
                        'original_amount': 9_000_000.0}),
                (0, 0, {'cost_code_id': self.labor.id,
                        'amount_mode': 'lumpsum',
                        'original_amount': 1_000_000.0}),
            ]})
        budget.action_submit()
        budget.action_approve()
        budget.action_baseline()

        forecast = self._forecast(self.project)
        self._line_for(forecast, self.civil).write(
            {'method': 'finish_at_budget'})
        forecast.invalidate_recordset()

        self.assertEqual(
            forecast.forecast_coverage, 90.0,
            "Nine tenths of the money is forecast, not half the lines.")

    def test_an_incomplete_forecast_cannot_be_approved(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        forecast.action_submit()

        with self.assertRaises(UserError):
            forecast.action_approve()

    def test_a_fully_forecast_project_is_complete(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        self._line_for(forecast, self.civil).write(
            {'method': 'finish_at_budget'})
        forecast.invalidate_recordset()

        self.assertTrue(forecast.eac_is_complete)
        self.assertEqual(forecast.forecast_coverage, 100.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestForecastEdges(ForecastCommon):
    """§36-§40 — the awkward shapes real projects take."""

    def test_actual_above_commitment_is_flagged_not_negated(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 2_000_000.0)])
        self._post_bill(self.project, self.civil, 3_000_000.0)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)

        line.method = 'remaining_commitment'

        self.assertTrue(line.actual_exceeds_commitment)
        self.assertEqual(
            line.committed_remaining, 0.0,
            "Floored at zero — a negative remaining commitment is not a "
            "forecast, it is a contradiction.")
        self.assertEqual(line.etc_amount, 0.0)

    def test_actual_above_budget_still_forecasts(self):
        """Budget 5M, actual 6M, ETC 1M → EAC 7M, variance −2M."""
        self._baselined(self.project, 5_000_000.0, code=self.civil)
        self._post_bill(self.project, self.civil, 6_000_000.0)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)

        line.write({'method': 'manual', 'manual_etc': 1_000_000.0,
                    'manual_reason': 'Remaining works'})

        self.assertEqual(line.eac_amount, 7_000_000.0)
        self.assertEqual(line.forecast_variance, -2_000_000.0)
        self.assertTrue(line.eac_exceeds_budget)

    def test_unawarded_scope_is_identified(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.write({'method': 'manual', 'manual_etc': 10_000_000.0,
                    'manual_reason': 'Not yet tendered'})

        self.assertTrue(line.is_unawarded)
        self.assertEqual(line.uncommitted_etc, 10_000_000.0)

    def test_uncommitted_etc_shows_what_still_needs_procuring(self):
        """ETC 5M against 3M of remaining commitment → 2M uncommitted."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 5_000_000.0)])
        self._post_bill(self.project, self.civil, 2_000_000.0)
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)

        line.write({'method': 'manual', 'manual_etc': 5_000_000.0,
                    'manual_reason': 'Priced'})

        self.assertEqual(line.committed_remaining, 3_000_000.0)
        self.assertEqual(line.uncommitted_etc, 2_000_000.0)

    def test_etc_may_exceed_remaining_commitment(self):
        """§12 — forecasts are allowed to be worse than the contract."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 4_000_000.0)])
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)

        line.write({'method': 'manual', 'manual_etc': 9_000_000.0,
                    'manual_reason': 'Anticipated variation and inflation'})

        self.assertEqual(line.etc_amount, 9_000_000.0)
        self.assertEqual(line.uncommitted_etc, 5_000_000.0)

    def test_a_zero_budget_line_forecasts_without_dividing_by_it(self):
        project = self.project
        code = self._cost_code('UNB-01', 'Unbudgeted scope', 'other')
        self._baselined(project, 0.0, code=code)
        self._post_bill(project, code, 250_000.0)
        forecast = self._forecast(project)
        line = self._line_for(forecast, code)

        line.write({'method': 'manual', 'manual_etc': 100_000.0,
                    'manual_reason': 'Emergent works'})

        self.assertEqual(line.current_budget, 0.0)
        self.assertEqual(line.eac_amount, 350_000.0)
        self.assertEqual(line.forecast_variance, -350_000.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestForecastGovernance(ForecastCommon):

    def test_a_preparer_cannot_approve_their_own_forecast(self):
        preparer = self.env['res.users'].create({
            'name': 'Cost Controller',
            'login': 'forecast_preparer_%d' % self._next(),
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_construction.group_construction_user').id])],
        })
        self._baselined(self.project, 1_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        self._line_for(forecast, self.civil).method = 'finish_at_budget'
        forecast.sudo().write({'prepared_by_id': preparer.id})
        forecast.action_submit()

        with self.assertRaises(UserError):
            forecast.with_user(preparer).action_approve()

    def test_forecast_does_not_alter_the_m2_equation(self):
        """§51 — forecast is additive. The control values do not move."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        vat = self._vat(15.0)
        self._po(self.project, self.contractor,
                 [(self.civil, 8_000_000.0)], tax=vat)
        self._post_bill(self.project, self.civil, 3_000_000.0, tax=vat)

        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.write({'method': 'manual', 'manual_etc': 5_000_000.0,
                    'manual_reason': 'Priced'})
        forecast.action_submit()
        forecast.action_approve()

        totals = self.env['realestate.construction.controls'].project_totals(
            self.project)
        self.assertEqual(totals['current_budget'], 10_000_000.0)
        self.assertEqual(totals['current_commitment'], 8_000_000.0)
        self.assertEqual(totals['actual_cost'], 3_000_000.0)
        self.assertEqual(forecast.total_eac, 8_000_000.0)

    def test_another_companys_project_is_isolated(self):
        other_company = self.env['res.company'].create({'name': 'FC Other'})
        other_project = self._project()
        other_project.company_id = other_company
        self._baselined(self.project, 10_000_000.0, code=self.civil)

        forecast = self._forecast(other_project)

        self.assertFalse(forecast.line_ids)
        self.assertEqual(forecast.total_current_budget, 0.0)

    def test_the_retention_warning_follows_the_forecast(self):
        """§52 — the same disclosure the Cost Report carries."""
        self._configure_construction_accounts()
        certificate = self._certificate(
            self.project, self.contractor, contract_value=1_000_000.0,
            pct=10.0)
        certificate.action_certify()
        certificate.action_create_vendor_bill()
        # A pre-M7 posting, which is what the disclosure now describes.
        certificate.sudo().write({'retention_posted_correctly': False})
        self._baselined(self.project, 10_000_000.0, code=self.civil)

        forecast = self._forecast(self.project)

        self.assertIn('understated', forecast.retention_warning)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCostReportV2(ForecastCommon):
    """§21 — the cost report gains forecast columns without losing its rules."""

    def _approved_forecast(self, etc):
        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.write({'method': 'manual', 'manual_etc': etc,
                    'manual_reason': 'Priced'})
        forecast.action_submit()
        forecast.action_approve()
        return forecast

    def test_the_report_carries_etc_eac_and_variance(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 8_000_000.0)])
        self._post_bill(self.project, self.civil, 3_000_000.0)
        self._approved_forecast(5_000_000.0)

        report = self.env['realestate.construction.cost.report'].build_for(
            self.project)
        line = report.line_ids[0]

        self.assertTrue(line.has_forecast)
        self.assertEqual(line.etc_amount, 5_000_000.0)
        self.assertEqual(line.eac_amount, 8_000_000.0)
        self.assertEqual(line.forecast_variance, 2_000_000.0)
        self.assertEqual(line.forecast_method, 'manual')
        self.assertEqual(report.total_eac, 8_000_000.0)

    def test_a_code_outside_the_forecast_shows_no_forecast_not_zero(self):
        # The forecast is approved first, then new scope is committed against
        # a code it never covered — which is how this happens in life.
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._approved_forecast(5_000_000.0)
        self._po(self.project, self.contractor, [(self.labor, 1_000_000.0)])

        report = self.env['realestate.construction.cost.report'].build_for(
            self.project)
        labor_line = report.line_ids.filtered(
            lambda l: l.cost_code_id == self.labor)

        self.assertFalse(
            labor_line.has_forecast,
            "The forecast does not cover this code, and the report says so "
            "rather than printing a confident zero.")

    def test_a_draft_forecast_never_reaches_the_cost_report(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        forecast = self._forecast(self.project)
        self._line_for(forecast, self.civil).write(
            {'method': 'manual', 'manual_etc': 5_000_000.0,
             'manual_reason': 'Working paper'})

        report = self.env['realestate.construction.cost.report'].build_for(
            self.project)

        self.assertFalse(report.line_ids[0].has_forecast)
        self.assertEqual(report.forecast_state, 'none')

    def test_the_report_still_refuses_to_add_commitment_and_actual(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 8_000_000.0)])
        self._post_bill(self.project, self.civil, 3_000_000.0)
        self._approved_forecast(5_000_000.0)

        report = self.env['realestate.construction.cost.report'].build_for(
            self.project)

        self.assertNotIn('total_cost', report._fields)
        self.assertNotIn('cost_to_date', report._fields)

    def test_forecast_freshness_is_visible(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._approved_forecast(5_000_000.0)
        self.project.invalidate_recordset()

        self.assertEqual(self.project.ctrl_forecast_state, 'complete')
        self.assertEqual(self.project.ctrl_forecast_age_days, 0)
        self.assertFalse(self.project.ctrl_forecast_is_stale)

    def test_a_stale_forecast_says_so(self):
        from dateutil.relativedelta import relativedelta
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        old = self._forecast(self.project,
                             as_of_date=self.today - relativedelta(months=4))
        self._line_for(old, self.civil).write(
            {'method': 'manual', 'manual_etc': 5_000_000.0,
             'manual_reason': 'Priced'})
        old.action_submit()
        old.action_approve()
        self.project.invalidate_recordset()

        self.assertTrue(self.project.ctrl_forecast_is_stale)
        self.assertEqual(self.project.ctrl_forecast_state, 'stale')
        self.assertGreater(self.project.ctrl_forecast_age_days, 35)

    def test_a_project_with_no_forecast_reports_none_rather_than_zero_eac(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self.project.invalidate_recordset()

        self.assertEqual(self.project.ctrl_forecast_state, 'none')
        self.assertFalse(self.project.ctrl_forecast_id)

    def test_committed_remaining_is_na_when_it_cannot_be_attributed(self):
        """§14 — N/A and 0 are different statements."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._approved_forecast(5_000_000.0)

        report = self.env['realestate.construction.cost.report'].build_for(
            self.project)
        line = report.line_ids[0]

        self.assertTrue(line.committed_remaining_known)
        self.assertEqual(line.committed_remaining, 0.0,
                         "Nothing committed, and that is known — not unknown.")
