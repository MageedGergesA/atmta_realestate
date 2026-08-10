# -*- coding: utf-8 -*-
"""M9 — the five invariants, written before the Control Tower exists.

M9 adds no financial truth. Everything it shows already has an owner: M2 owns
budget, commitment and actual, M3 owns the forecast, M4 owns change, M7 owns
certification, M8 owns claims and exposure. The whole risk of a reporting layer
is that it quietly becomes a sixth opinion — a number that looks authoritative
because it is large and on a dashboard, and that reconciles to nothing.

These five tests exist to stop that.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestControlTowerInvariants(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.CostSheet = self.env['realestate.construction.cost.sheet']
        self.Tower = self.env['realestate.construction.control.tower']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')

    def _representative_project(self):
        """The M9AJ figures, built from the authoritative documents.

            Original budget      100M
            Approved budget +      5M   ->  current budget      105M
            Original commitment   80M
            Approved variation +  10M   ->  current commitment   90M
            Posted actual         40M
            ETC                   55M   ->  EAC                  95M
            Forecast variance    +10M favourable
        """
        self._baselined(self.project, 100_000_000.0, code=self.civil)
        package = self._package(self.project, self.contractor,
                                value=80_000_000.0, award=True)

        budget_change = self._change_order(
            self.project, lines=[(self.civil, 'budget', 5_000_000.0)])
        self._approve_and_implement(budget_change)

        commitment_change = self._change_order(
            self.project, lines=[(self.civil, 'commitment', 10_000_000.0)],
            package=package)
        self._approve_and_implement(commitment_change)

        self._post_bill(self.project, self.civil, 40_000_000.0)

        forecast = self._forecast(self.project)
        line = forecast.line_ids.filtered(
            lambda l: l.cost_code_id == self.civil)[:1]
        line.write({'method': 'manual', 'manual_etc': 55_000_000.0,
                    'manual_reason': 'Priced remaining scope.'})
        forecast.action_submit()
        forecast.action_approve()
        return package, forecast

    # -- A ------------------------------------------------------------------
    def test_a_the_integrated_project_position(self):
        self._representative_project()
        position = self.Tower.payload(self.project)['cost']

        self.assertEqual(position['current_budget'], 105_000_000.0)
        self.assertEqual(position['current_commitment'], 90_000_000.0)
        self.assertEqual(position['actual_cost'], 40_000_000.0)
        self.assertEqual(position['etc'], 55_000_000.0)
        self.assertEqual(position['eac'], 95_000_000.0)
        self.assertEqual(position['forecast_variance'], 10_000_000.0)

        # And the equations hold, rather than four numbers that happen to be
        # right today.
        self.assertEqual(
            position['current_budget'],
            position['original_budget'] + position['approved_budget_changes'])
        self.assertEqual(
            position['current_commitment'],
            position['original_commitment']
            + position['approved_commitment_changes'])
        self.assertEqual(position['eac'],
                         position['actual_cost'] + position['etc'])
        self.assertEqual(position['forecast_variance'],
                         position['current_budget'] - position['eac'])

    # -- B ------------------------------------------------------------------
    def test_b_a_potential_change_is_reported_apart_from_the_baseline(self):
        self._representative_project()
        self._change_event(self.project, estimated_cost=8_000_000.0)

        payload = self.Tower.payload(self.project)
        self.assertEqual(payload['cost']['current_budget'], 105_000_000.0)
        self.assertEqual(payload['cost']['current_commitment'], 90_000_000.0)
        self.assertEqual(payload['change']['potential_cost_exposure'],
                         8_000_000.0)
        self.assertNotIn(
            113_000_000.0,
            [payload['cost']['current_budget'],
             payload['cost']['current_commitment'],
             payload['cost']['eac']],
            "Possible is not authorised, and must never be added to a "
            "baseline figure.")

    # -- C ------------------------------------------------------------------
    def test_c_a_determined_claim_moves_nothing_until_m4_implements_it(self):
        package, _forecast = self._representative_project()
        before = self.Tower.payload(self.project)['cost']

        claim = self._claim(self.project, package, claimed_cost=5_000_000.0)
        claim.action_submit()
        self.env['realestate.construction.claim.cost.line'].create({
            'claim_id': claim.id, 'category': 'labor',
            'description': 'Disruption', 'cost_code_id': self.civil.id,
            'amount': 3_000_000.0})
        self.env['realestate.construction.claim.determination'].create({
            'claim_id': claim.id, 'determined_cost': 3_000_000.0,
            'reasons': '<p>Partly allowed.</p>'}).action_issue()

        payload = self.Tower.payload(self.project)
        self.assertEqual(payload['cost']['current_budget'],
                         before['current_budget'])
        self.assertEqual(payload['cost']['current_commitment'],
                         before['current_commitment'])
        self.assertEqual(payload['claims']['claimed_cost'], 5_000_000.0)
        self.assertEqual(payload['claims']['determined_cost'], 3_000_000.0)
        self.assertEqual(payload['claims']['implemented_cost'], 0.0,
                         "Determined is not implemented, and the dashboard "
                         "must show the difference.")

    # -- D ------------------------------------------------------------------
    def test_d_a_missing_forecast_is_missing_not_zero(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        mep = self._cost_code('SUB-MEP', 'MEP', 'subcontract')

        forecast = self._forecast(self.project)
        civil_line = forecast.line_ids.filtered(
            lambda l: l.cost_code_id == self.civil)[:1]
        civil_line.write({'method': 'manual', 'manual_etc': 1_000_000.0,
                          'manual_reason': 'Priced.'})
        forecast.action_submit()
        forecast.action_approve()

        # Money is committed to a cost code the approved forecast never saw.
        # M3 will not approve an incomplete forecast, so this — not an empty
        # line — is what "no ETC" actually looks like on a live project.
        self._po(self.project, self.contractor, [(mep, 2_000_000.0)])

        payload = self.Tower.payload(self.project)
        rows = {row['cost_code_id']: row
                for row in self.CostSheet.rows_for(self.project)}

        self.assertFalse(rows[mep.id]['has_etc'])
        self.assertIsNone(rows[mep.id]['etc'],
                          "A cost code nobody forecast has no ETC. Zero would "
                          "be a forecast, and nobody made one.")
        self.assertIsNone(rows[mep.id]['eac'])
        self.assertLess(payload['forecast']['coverage_pct'], 100.0)
        self.assertGreaterEqual(payload['forecast']['missing_line_count'], 1)

    # -- E ------------------------------------------------------------------
    def test_e_one_number_one_calculation(self):
        """Three surfaces, one service. Not three formulas that agree today."""
        package, _forecast = self._representative_project()

        controls = self.Controls.project_totals(self.project)
        sheet_total = self.CostSheet.totals_for(self.project)
        tower = self.Tower.payload(self.project)['cost']
        project_summary = self.project.construction_position()

        for key in ('current_budget', 'current_commitment', 'actual_cost'):
            self.assertEqual(sheet_total[key], controls[key], key)
            self.assertEqual(tower[key], controls[key], key)
            self.assertEqual(project_summary[key], controls[key], key)

        # The cost sheet's rows must add up to its own total, so a reader can
        # follow the number down to the code it came from.
        rows = self.CostSheet.rows_for(self.project)
        self.assertEqual(
            round(sum(row['current_budget'] for row in rows), 2),
            round(sheet_total['current_budget'], 2))
        self.assertEqual(
            round(sum(row['actual_cost'] for row in rows), 2),
            round(sheet_total['actual_cost'], 2))
