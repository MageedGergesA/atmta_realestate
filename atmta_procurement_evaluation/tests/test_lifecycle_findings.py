# -*- coding: utf-8 -*-
"""Regressions for what the procurement lifecycle run found in Evaluation."""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import EvaluationLifecycleCommon


@tagged('post_install', '-at_install', 'atmta_evaluation')
class TestEvaluationLifecycleFindings(EvaluationLifecycleCommon):

    # -- item 3: an unapproved adjustment cannot decide the ranking ---------
    def test_the_round_does_not_finalise_on_an_unapproved_adjustment(self):
        round_ = self._commercial_round()
        dearest = round_.candidate_ids.filtered('in_commercial').sorted(
            lambda c: -c.analysis_id.evaluated_cost)[:1]
        adjustment = dearest.analysis_id.action_add_adjustment(
            'discount', 50.0 * 1000, rationale='Early payment (unverified)')
        # Visible during analysis: the figure is what is being reviewed.
        self.assertLess(dearest.analysis_id.evaluated_cost,
                        dearest.analysis_id.converted_amount)

        with self.assertRaises(UserError):
            round_.action_finalise()
        self.assertNotEqual(round_.state, 'finalised')

        reviewer = self._user('lcf.adjustment.reviewer',
                              'atmta_roles.group_procurement_evaluation_manager')
        adjustment.with_user(reviewer).action_approve()
        round_.action_finalise()
        self.assertEqual(round_.state, 'finalised')

    # -- item 7: the manager running the round need not be on the tender ----
    def test_an_evaluation_manager_off_the_tender_team_opens_technical(self):
        event = self._closed_with_bids()
        manager = self._user('lcf.evalmgr.buyer',
                             'atmta_roles.group_procurement_evaluation_manager',
                             'atmta_roles.group_procurement_buyer')
        self.assertNotIn(manager, event.team_ids | event.buyer_id)
        round_ = self._round(event, open_=False)

        round_.with_user(manager).action_open_technical()

        self.assertEqual(round_.state, 'technical_open')
        self.assertEqual(len(round_.candidate_ids.filtered('in_technical')), 3)

    # -- item 6: role access ------------------------------------------------
    def test_a_pure_evaluation_manager_opens_technical(self):
        event = self._closed_with_bids()
        manager = self._user('lcf.evalmgr.pure',
                             'atmta_roles.group_procurement_evaluation_manager')
        round_ = self._round(event, open_=False)
        round_.invalidate_recordset()

        round_.with_user(manager).action_open_technical()

        self.assertEqual(round_.state, 'technical_open')
        # The event is on the round form; the manager has to be able to see
        # which tender they are evaluating.
        self.assertTrue(event.with_user(manager).read(['name', 'state']))

    def test_a_pure_commercial_evaluator_normalises(self):
        round_ = self._pass_technical(self._round())
        round_.action_open_commercial()
        evaluator = self._user(
            'lcf.comm.pure', 'atmta_roles.group_procurement_commercial_evaluator')
        self.env.invalidate_all()

        round_.with_user(evaluator).action_normalise()

        analyses = round_.candidate_ids.filtered('in_commercial').analysis_id
        self.assertEqual(len(analyses), 3)
        self.assertTrue(all(a.raw_amount for a in analyses))
        self.assertTrue(analyses.leveling_line_ids)

    def test_a_technical_evaluator_still_cannot_normalise(self):
        round_ = self._pass_technical(self._round())
        round_.action_open_commercial()
        tech = self._user('lcf.tech.pure',
                          'atmta_roles.group_procurement_technical_evaluator')
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            round_.with_user(tech).action_normalise()

    # -- UX ------------------------------------------------------------------
    def test_the_sheet_form_shows_the_candidate_it_scores(self):
        arch = self.env['realestate.procurement.technical.evaluation'].get_view(
            view_type='form')['arch']
        self.assertIn('name="candidate_id"', arch)

    def test_the_commercial_analysis_form_lists_its_adjustments(self):
        view = self.env['realestate.procurement.commercial.analysis'].get_view(
            view_type='form')
        self.assertIn('name="adjustment_ids"', view['arch'])
        self.assertIn('name="action_approve"', view['arch'])

    def test_an_adjustment_cannot_be_added_to_a_finalised_round_directly(self):
        round_ = self._finalised_round()
        analysis = round_.candidate_ids.filtered('in_commercial')[:1].analysis_id
        with self.assertRaises(UserError):
            self.env['realestate.procurement.commercial.adjustment'].create({
                'analysis_id': analysis.id, 'adjustment_type': 'freight',
                'amount': 1000.0, 'rationale': 'After the ranking'})
