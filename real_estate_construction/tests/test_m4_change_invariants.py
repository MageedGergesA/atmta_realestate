# -*- coding: utf-8 -*-
"""M4 — the four invariants change management is built around.

Written before the models. Everything else in M4 elaborates these:

```
    A   potential ≠ approved       — an estimate moves no baseline
    B   approved budget change     — original stays, current grows
    C   approved commitment change — original stays, actual untouched
    D   forecast conversion        — anticipated + approved ≠ counted twice
```
"""

from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestChangeInvariants(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')

    # ------------------------------------------------------------------
    def test_a_potential_change_moves_no_baseline(self):
        """Current budget 10M, a potential change estimated at 2M.

        The budget is still 10M. The exposure is visible, and separate.
        """
        self._baselined(self.project, 10_000_000.0, code=self.civil)

        event = self._change_event(self.project, estimated_cost=2_000_000.0)

        totals = self.Controls.project_totals(self.project)
        self.assertEqual(totals['current_budget'], 10_000_000.0)
        self.assertEqual(totals['approved_budget_changes'], 0.0)
        self.assertEqual(totals['current_commitment'], 0.0)

        exposure = self.env['realestate.construction.change.event'].exposure(
            self.project)
        self.assertEqual(exposure['potential_cost'], 2_000_000.0)
        self.assertEqual(
            exposure['approved_cost'], 0.0,
            "An estimate is not an authorisation and must never be counted "
            "as one.")
        self.assertEqual(event.state, 'identified')

    def test_b_an_approved_budget_change_grows_current_not_original(self):
        """Original 10M + approved 2M = current 12M, original still 10M."""
        budget = self._baselined(self.project, 10_000_000.0, code=self.civil)

        order = self._change_order(
            self.project, order_type='budget_change',
            lines=[(self.civil, 'budget', 2_000_000.0)])
        self._approve_and_implement(order)

        budget.invalidate_recordset()
        totals = self.Controls.project_totals(self.project)

        self.assertEqual(budget.original_amount, 10_000_000.0)
        self.assertEqual(totals['original_budget'], 10_000_000.0)
        self.assertEqual(totals['approved_budget_changes'], 2_000_000.0)
        self.assertEqual(totals['current_budget'], 12_000_000.0)
        self.assertEqual(order.state, 'implemented')

    def test_c_an_approved_commitment_change_leaves_actual_alone(self):
        """Commitment 8M + approved variation 2M = 10M. Actual does not move."""
        self._baselined(self.project, 20_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 8_000_000.0)])
        self._post_bill(self.project, self.civil, 3_000_000.0)

        before = self.Controls.project_totals(self.project)
        self.assertEqual(before['current_commitment'], 8_000_000.0)
        self.assertEqual(before['actual_cost'], 3_000_000.0)

        order = self._change_order(
            self.project, order_type='contractor_variation',
            contractor=self.contractor,
            lines=[(self.civil, 'commitment', 2_000_000.0)])
        self._approve_and_implement(order)

        after = self.Controls.project_totals(self.project)
        self.assertEqual(after['original_commitment'], 8_000_000.0)
        self.assertEqual(after['approved_commitment_changes'], 2_000_000.0)
        self.assertEqual(after['current_commitment'], 10_000_000.0)
        self.assertEqual(
            after['actual_cost'], 3_000_000.0,
            "Approving a variation does not spend money.")

    def test_d_forecast_does_not_count_anticipated_and_approved_twice(self):
        """An anticipated 2M that later becomes an approved 2M is 2M, once.

        The approved forecast that recorded the anticipation is history and is
        left exactly as it was; only the next forecast knows the anticipation
        has become real.
        """
        self._baselined(self.project, 20_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 8_000_000.0)])

        march = self._forecast(self.project)
        line = march.line_ids.filtered(lambda l: l.cost_code_id == self.civil)
        line.method = 'remaining_commitment'
        event = self._change_event(self.project, estimated_cost=2_000_000.0)
        adjustment = self.env[
            'realestate.construction.forecast.adjustment'].create({
                'line_id': line.id,
                'adjustment_type': 'anticipated_variation',
                'description': 'Façade revision, pricing awaited',
                'amount': 2_000_000.0,
                'change_event_id': event.id,
            })
        march.action_submit()
        march.action_approve()
        march_etc = march.total_etc
        self.assertEqual(march_etc, 10_000_000.0)

        order = self._change_order(
            self.project, order_type='contractor_variation',
            contractor=self.contractor, event=event,
            lines=[(self.civil, 'commitment', 2_000_000.0)])
        self._approve_and_implement(order)

        april = self._forecast(self.project)
        april_line = april.line_ids.filtered(
            lambda l: l.cost_code_id == self.civil)
        april_line.method = 'remaining_commitment'
        april.invalidate_recordset()

        self.assertEqual(
            april_line.commitment_amount, 10_000_000.0,
            "The approved variation is in the commitment now.")
        self.assertEqual(
            april.total_etc, 10_000_000.0,
            "Not 12M — the anticipation and the approved change are the same "
            "2M, and the anticipation did not carry forward.")
        adjustment.invalidate_recordset()
        self.assertTrue(adjustment.converted_to_change_order)

        march.invalidate_recordset()
        self.assertEqual(
            march.total_etc, march_etc,
            "March's approved forecast is history and does not move.")
