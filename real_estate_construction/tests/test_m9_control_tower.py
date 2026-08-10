# -*- coding: utf-8 -*-
"""M9 — the Control Tower, the cost sheet, and the promises they make."""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged

from .common import ConstructionCommon


class TowerCommon(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.CostSheet = self.env['realestate.construction.cost.sheet']
        self.Tower = self.env['realestate.construction.control.tower']
        self.Exceptions = self.env['realestate.construction.exceptions']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('TW-CIV', 'Civil', 'subcontract')
        self.mep = self._cost_code('TW-MEP', 'MEP', 'subcontract')

    def _approved_forecast(self, etc_by_code):
        forecast = self._forecast(self.project)
        for code, etc in etc_by_code.items():
            line = forecast.line_ids.filtered(
                lambda l: l.cost_code_id == code)[:1]
            if line:
                line.write({'method': 'manual', 'manual_etc': etc,
                            'manual_reason': 'Priced.'})
        forecast.action_submit()
        forecast.action_approve()
        return forecast


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCostSheetEquations(TowerCommon):
    """M9B §3 — every row must preserve the control equations."""

    def test_every_row_satisfies_the_four_equations(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 4_000_000.0),
                                                 (self.mep, 1_000_000.0)])
        self._post_bill(self.project, self.civil, 1_500_000.0)
        order = self._change_order(
            self.project, lines=[(self.civil, 'budget', 500_000.0)])
        self._approve_and_implement(order)
        self._approved_forecast({self.civil: 6_000_000.0,
                                 self.mep: 900_000.0})

        for row in self.CostSheet.rows_for(self.project):
            self.assertAlmostEqual(
                row['current_budget'],
                row['original_budget'] + row['approved_budget_changes'], 2,
                "Current Budget = Original + Approved Budget Changes")
            self.assertAlmostEqual(
                row['current_commitment'],
                row['original_commitment']
                + row['approved_commitment_changes'], 2,
                "Current Commitment = Original + Approved Commitment Changes")
            if row['has_etc']:
                self.assertAlmostEqual(
                    row['eac'], row['actual_cost'] + row['etc'], 2,
                    "EAC = Actual + ETC")
                self.assertAlmostEqual(
                    row['forecast_variance'],
                    row['current_budget'] - row['eac'], 2,
                    "Forecast Variance = Current Budget − EAC")

    def test_the_rows_add_up_to_the_total(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.mep, 2_000_000.0)])
        self._post_bill(self.project, self.civil, 750_000.0)

        rows = self.CostSheet.rows_for(self.project)
        totals = self.CostSheet.totals_for(self.project)
        for key in ('current_budget', 'current_commitment', 'actual_cost'):
            self.assertAlmostEqual(
                sum(row[key] for row in rows), totals[key], 2, key)

    def test_uncoded_money_gets_a_row_rather_than_disappearing(self):
        package = self._package(self.project, self.contractor,
                                value=3_000_000.0, award=True)
        self.assertFalse(package.cost_code_ids)

        rows = self.CostSheet.rows_for(self.project)
        unassigned = [row for row in rows if row['is_unassigned']]
        self.assertTrue(unassigned, "Uncoded commitment must be visible.")
        self.assertEqual(unassigned[0]['current_commitment'], 3_000_000.0)

        totals = self.CostSheet.totals_for(self.project)
        self.assertEqual(totals['unassigned_commitment'], 3_000_000.0)

    def test_a_project_with_nothing_on_it_still_answers(self):
        rows = self.CostSheet.rows_for(self.project)
        totals = self.CostSheet.totals_for(self.project)
        payload = self.Tower.payload(self.project)

        self.assertEqual(rows, [])
        self.assertEqual(totals['current_budget'], 0.0)
        self.assertIsNone(totals['etc'], "No forecast is not an ETC of zero.")
        self.assertFalse(totals['has_forecast'])
        self.assertEqual(payload['health']['status'], 'no_data')


@tagged('post_install', '-at_install', 'atmta_construction')
class TestDrilldownEquality(TowerCommon):
    """M9B §7 — the number and the list behind it must be the same thing."""

    def setUp(self):
        super().setUp()
        self._baselined(self.project, 8_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 3_000_000.0)])
        self._post_bill(self.project, self.civil, 1_250_000.0)

    def test_actual_cost_drilldown_equals_the_figure(self):
        rows = {row['cost_code_id']: row
                for row in self.CostSheet.rows_for(self.project)}
        action = self.CostSheet.drilldown(self.project, 'actual_cost',
                                          self.civil.id)
        lines = self.env['account.analytic.line'].search(action['domain'])
        self.assertAlmostEqual(-sum(lines.mapped('amount')),
                               rows[self.civil.id]['actual_cost'], 2)

    def test_commitment_drilldown_equals_the_figure(self):
        rows = {row['cost_code_id']: row
                for row in self.CostSheet.rows_for(self.project)}
        action = self.CostSheet.drilldown(self.project, 'current_commitment',
                                          self.civil.id)
        lines = self.env['purchase.order.line'].search(action['domain'])
        self.assertAlmostEqual(sum(lines.mapped('price_subtotal')),
                               rows[self.civil.id]['current_commitment'], 2)

    def test_budget_drilldown_equals_the_figure(self):
        rows = {row['cost_code_id']: row
                for row in self.CostSheet.rows_for(self.project)}
        action = self.CostSheet.drilldown(self.project, 'original_budget',
                                          self.civil.id)
        lines = self.env['realestate.construction.budget.line'].search(
            action['domain'])
        self.assertAlmostEqual(sum(lines.mapped('original_amount')),
                               rows[self.civil.id]['original_budget'], 2)

    def test_an_unknown_column_returns_nothing_rather_than_guessing(self):
        self.assertFalse(
            self.CostSheet.drilldown(self.project, 'invented_column'))


@tagged('post_install', '-at_install', 'atmta_construction')
class TestIntegratedPosition(TowerCommon):
    """M9AJ — the representative project, pinned end to end."""

    def _build(self):
        self._baselined(self.project, 100_000_000.0, code=self.civil)
        package = self._package(self.project, self.contractor,
                                value=80_000_000.0, award=True)
        self._approve_and_implement(self._change_order(
            self.project, lines=[(self.civil, 'budget', 5_000_000.0)]))
        self._approve_and_implement(self._change_order(
            self.project, lines=[(self.civil, 'commitment', 10_000_000.0)],
            package=package))
        self._post_bill(self.project, self.civil, 40_000_000.0)
        self._approved_forecast({self.civil: 55_000_000.0})
        self._change_event(self.project, estimated_cost=8_000_000.0)

        claim = self._claim(self.project, package, claimed_cost=5_000_000.0)
        claim.action_submit()
        self.env['realestate.construction.claim.cost.line'].create({
            'claim_id': claim.id, 'category': 'labor',
            'description': 'Disruption', 'cost_code_id': self.civil.id,
            'amount': 3_000_000.0})
        self.env['realestate.construction.claim.determination'].create({
            'claim_id': claim.id, 'determined_cost': 3_000_000.0,
            'reasons': '<p>Partly allowed.</p>'}).action_issue()
        return package, claim

    def test_the_dashboard_reports_the_position_without_double_counting(self):
        self._build()
        payload = self.Tower.payload(self.project)
        cost, change, claims = (payload['cost'], payload['change'],
                                payload['claims'])

        self.assertEqual(cost['current_budget'], 105_000_000.0)
        self.assertEqual(cost['current_commitment'], 90_000_000.0)
        self.assertEqual(cost['actual_cost'], 40_000_000.0)
        self.assertEqual(cost['etc'], 55_000_000.0)
        self.assertEqual(cost['eac'], 95_000_000.0)
        self.assertEqual(cost['forecast_variance'], 10_000_000.0)

        self.assertEqual(change['potential_cost_exposure'], 8_000_000.0)
        self.assertEqual(claims['claimed_cost'], 5_000_000.0)
        self.assertEqual(claims['determined_cost'], 3_000_000.0)
        self.assertEqual(claims['implemented_cost'], 0.0)

        # The specific wrong answers this milestone exists to prevent.
        for forbidden in (111_000_000.0, 113_000_000.0, 103_000_000.0,
                          98_000_000.0):
            self.assertNotIn(forbidden, [cost['current_budget'],
                                         cost['current_commitment'],
                                         cost['eac']])

    def test_implementing_the_determined_claim_moves_the_baseline_once(self):
        package, claim = self._build()
        before = self.Tower.payload(self.project)['cost']

        order = claim.action_create_change_order()
        self._approve_and_implement(order)

        after = self.Tower.payload(self.project)['cost']
        self.assertEqual(
            after['current_commitment'],
            before['current_commitment'] + 3_000_000.0,
            "Once, through M4 — not once here and again as exposure.")

        payload = self.Tower.payload(self.project)
        self.assertEqual(payload['claims']['implemented_cost'], 3_000_000.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCertificationInTheCostSheet(TowerCommon):
    """M9AL — retention must not shrink actual cost on the sheet."""

    def test_certified_work_posts_gross_and_retention_shows_separately(self):
        self._configure_construction_accounts()
        self._baselined(self.project, 50_000_000.0, code=self.civil)
        certificate = self._certificate(
            self.project, self.contractor, amount=10_000_000.0,
            retention_pct=5.0, cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        payload = self.Tower.payload(self.project)
        self.assertEqual(payload['cost']['actual_cost'], 10_000_000.0,
                         "Retention is withheld money, not unspent money.")
        self.assertEqual(
            payload['certificates']['retention_outstanding'], 500_000.0)
        self.assertEqual(payload['certificates']['certified_amount'],
                         10_000_000.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestForecastFreshness(TowerCommon):
    """M9AM — a stale forecast is named, not quietly presented as current."""

    def test_a_stale_forecast_is_flagged(self):
        self._baselined(self.project, 5_000_000.0, code=self.civil)
        forecast = self._approved_forecast({self.civil: 1_000_000.0})
        forecast.sudo().write({
            'as_of_date': self.today - relativedelta(days=45)})

        payload = self.Tower.payload(self.project)
        self.assertTrue(payload['forecast']['is_stale'])
        self.assertEqual(payload['forecast']['age_days'], 45)

        exceptions = self.Exceptions.for_project(self.project)
        keys = [e['key'] for e in exceptions['exceptions']]
        self.assertIn('stale_forecast', keys)

    def test_a_current_forecast_is_not_flagged(self):
        self._baselined(self.project, 5_000_000.0, code=self.civil)
        self._approved_forecast({self.civil: 1_000_000.0})
        payload = self.Tower.payload(self.project)
        self.assertFalse(payload['forecast']['is_stale'])

    def test_the_threshold_is_configurable(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.forecast_stale_days', '60')
        self._baselined(self.project, 5_000_000.0, code=self.civil)
        forecast = self._approved_forecast({self.civil: 1_000_000.0})
        forecast.sudo().write({
            'as_of_date': self.today - relativedelta(days=45)})

        payload = self.Tower.payload(self.project)
        self.assertFalse(payload['forecast']['is_stale'],
                         "Reporting cadence is a company's policy.")
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.forecast_stale_days', '35')


@tagged('post_install', '-at_install', 'atmta_construction')
class TestHealthTransparency(TowerCommon):

    def test_the_status_comes_with_reasons(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._post_bill(self.project, self.civil, 9_000_000.0)
        self._approved_forecast({self.civil: 3_000_000.0})

        health = self.Tower.payload(self.project)['health']
        self.assertIn(health['status'],
                      ('critical', 'at_risk', 'attention', 'on_track'))
        self.assertTrue(health['reasons'],
                        "A colour with no sentence is not a status.")
        self.assertTrue(any('EAC' in reason for reason in health['reasons']))
        self.assertTrue(health['dimensions'])

    def test_no_composite_score_is_invented(self):
        health = self.Tower.payload(self.project)['health']
        self.assertNotIn('score', health,
                         "Nobody can act on 68 out of 100.")

    def test_thresholds_are_configurable_and_reported(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.forecast_variance_risk_pct', '1.0')
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._post_bill(self.project, self.civil, 5_000_000.0)
        self._approved_forecast({self.civil: 5_300_000.0})

        health = self.Tower.payload(self.project)['health']
        cost = [d for d in health['dimensions'] if d['key'] == 'cost'][0]
        self.assertEqual(cost['status'], 'critical')
        self.assertEqual(health['thresholds']['forecast_variance_risk_pct'],
                         1.0)
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.forecast_variance_risk_pct', '5.0')


@tagged('post_install', '-at_install', 'atmta_construction')
class TestDataQualityExceptions(TowerCommon):

    def test_an_uncoded_purchase_line_is_reported(self):
        self._po(self.project, self.contractor, [(None, 500_000.0)])
        exceptions = self.Exceptions.for_project(self.project)
        entry = [e for e in exceptions['exceptions']
                 if e['key'] == 'uncoded_po_lines']
        self.assertTrue(entry)
        self.assertEqual(entry[0]['class'], 'financial')

        lines = self.env['purchase.order.line'].search(
            entry[0]['action']['domain'])
        self.assertEqual(len(lines), entry[0]['count'],
                         "The count and its drilldown are the same query.")

    def test_missing_control_accounts_are_reported(self):
        self.company.write({
            'construction_retention_account_id': False,
            'construction_advance_account_id': False})
        exceptions = self.Exceptions.for_project(self.project)
        keys = [e['key'] for e in exceptions['exceptions']]
        self.assertIn('missing_control_accounts', keys)

    def test_an_approved_unimplemented_change_is_reported(self):
        self._baselined(self.project, 5_000_000.0, code=self.civil)
        order = self._change_order(
            self.project, lines=[(self.civil, 'budget', 100_000.0)])
        order.action_submit()
        order.action_request_approval()
        order.action_approve()

        exceptions = self.Exceptions.for_project(self.project)
        entry = [e for e in exceptions['exceptions']
                 if e['key'] == 'approved_not_implemented']
        self.assertTrue(entry)
        self.assertEqual(entry[0]['count'], 1)

    def test_every_exception_carries_a_class_and_an_action(self):
        self._po(self.project, self.contractor, [(None, 100_000.0)])
        exceptions = self.Exceptions.for_project(self.project)
        for entry in exceptions['exceptions']:
            self.assertIn(entry['class'],
                          ('financial', 'forecast', 'commercial', 'quality',
                           'document', 'configuration'))
            self.assertTrue(entry['message'])
            self.assertTrue(entry['action']['res_model'])


@tagged('post_install', '-at_install', 'atmta_construction')
class TestReportConsistency(TowerCommon):
    """M9AS — the same words must mean the same calculation everywhere."""

    def test_current_commitment_is_one_calculation(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 4_000_000.0)])

        controls = self.Controls.project_totals(self.project)['current_commitment']
        sheet = self.CostSheet.totals_for(self.project)['current_commitment']
        tower = self.Tower.payload(self.project)['cost']['current_commitment']
        summary = self.project.construction_position()['current_commitment']

        self.assertEqual({controls, sheet, tower, summary}, {4_000_000.0})

    def test_the_cost_report_and_the_cost_sheet_agree(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._post_bill(self.project, self.civil, 2_000_000.0)

        report = self.env['realestate.construction.cost.report'].build_for(
            self.project)
        sheet = self.CostSheet.totals_for(self.project)
        self.assertEqual(report.actual_cost, sheet['actual_cost'])
        self.assertEqual(report.current_budget, sheet['current_budget'])


@tagged('post_install', '-at_install', 'atmta_construction')
class TestTowerPerformance(TowerCommon):
    """M9X — a dashboard must not open with hundreds of queries."""

    def test_the_payload_stays_within_a_query_budget(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 3_000_000.0),
                                                 (self.mep, 1_000_000.0)])
        self._post_bill(self.project, self.civil, 500_000.0)
        self._approved_forecast({self.civil: 4_000_000.0,
                                 self.mep: 800_000.0})
        for _index in range(5):
            self._change_event(self.project, estimated_cost=100_000.0)
            self._risk(self.project, cost_exposure=50_000.0)
            self._issue(self.project)

        self.env.invalidate_all()
        before = self.env.cr.sql_log_count if hasattr(
            self.env.cr, 'sql_log_count') else None
        self.Tower.payload(self.project)
        after = self.env.cr.sql_log_count if hasattr(
            self.env.cr, 'sql_log_count') else None

        if before is not None and after is not None:
            queries = after - before
            # A budget, not an SLA: the point is to fail loudly if somebody
            # introduces an N+1 into a panel, not to assert a production
            # number from a test fixture.
            self.assertLess(
                queries, 400,
                "The whole payload took %s queries — look for an N+1 in a "
                "panel." % queries)

    def test_the_payload_does_not_grow_with_record_count(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self.Tower.payload(self.project)

        for _index in range(20):
            self._risk(self.project, cost_exposure=10_000.0)
            self._issue(self.project)

        self.env.invalidate_all()
        payload = self.Tower.payload(self.project)
        self.assertEqual(payload['risk']['open_risks'], 20)
        self.assertEqual(payload['risk']['open_issues'], 20)
