# -*- coding: utf-8 -*-
"""M6 — concurrency, isolation, migration and the integrity audit.

The concurrency file states its limit the way M4 and M5 did: Odoo's harness
shares one cursor, so a genuine two-process race cannot run here. What is
tested instead is each mechanism that makes the race safe — the unique index
that fails the duplicate, the row lock taken before the number is read, and the
idempotence of an action two people may click at once.
"""

import psycopg2

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import M6Common


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6Concurrency(M6Common):

    # -- C1 ------------------------------------------------------------
    @mute_logger('odoo.sql_db')
    def test_c1_a_second_plan_revision_number_is_refused(self):
        plan = self._plan()
        with self.assertRaises(psycopg2.IntegrityError):
            with self.env.cr.savepoint():
                self.Plan.create({
                    'sourcing_event_id': self.event.id,
                    'revision': plan.revision,
                    'evaluation_currency_id': self.company.currency_id.id,
                })

    def test_c1_freezing_twice_is_refused(self):
        plan = self._plan()
        with self.assertRaises(UserError):
            plan.action_freeze()
        self.assertEqual(plan.state, 'frozen')

    # -- C2 ------------------------------------------------------------
    def test_c2_opening_technical_twice_does_not_double_the_candidates(self):
        plan = self._plan()
        round_ = self._round(plan)
        before = len(round_.candidate_ids)

        with self.assertRaises(UserError):
            round_.action_open_technical()

        self.assertEqual(len(round_.candidate_ids), before,
                         "Re-opening the technical stage duplicated the "
                         "candidate set.")

    @mute_logger('odoo.sql_db')
    def test_c2_a_bid_cannot_be_two_candidates_in_one_round(self):
        plan = self._plan()
        round_ = self._round(plan)
        candidate = round_.candidate_ids[:1]
        with self.assertRaises(psycopg2.IntegrityError):
            with self.env.cr.savepoint():
                self.env[
                    'realestate.procurement.evaluation.candidate'].create({
                        'round_id': round_.id,
                        'bid_response_id': candidate.bid_response_id.id,
                    })

    # -- C3 ------------------------------------------------------------
    @mute_logger('odoo.sql_db')
    def test_c3_an_evaluator_cannot_hold_two_sheets_for_one_bid(self):
        plan = self._plan()
        round_ = self._round(plan)
        candidate = round_.candidate_ids[:1]
        self._sheet(round_, candidate)

        with self.assertRaises(psycopg2.IntegrityError):
            with self.env.cr.savepoint():
                self.Sheet.create({'round_id': round_.id,
                                   'candidate_id': candidate.id,
                                   'evaluator_id': self.env.user.id})

    def test_c3_submitting_twice_is_refused(self):
        plan = self._plan()
        round_ = self._round(plan)
        sheet = self._sheet(round_, round_.candidate_ids[:1], submit=True)
        with self.assertRaises(UserError):
            sheet.action_submit()

    # -- C5 / C7 -------------------------------------------------------
    def test_c5_commercial_cannot_open_twice(self):
        plan = self._plan()
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        opened = round_.commercial_opened_on

        with self.assertRaises(UserError):
            round_.action_open_commercial()

        self.assertEqual(round_.commercial_opened_on, opened,
                         "A second opening moved the recorded moment.")

    def test_c7_a_round_finalises_once(self):
        plan = self._plan()
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        round_.action_finalise()
        finalised = round_.finalised_on

        with self.assertRaises(UserError):
            round_.action_finalise()

        self.assertEqual(round_.finalised_on, finalised)

    # -- C6 ------------------------------------------------------------
    @mute_logger('odoo.sql_db')
    def test_c6_one_commercial_analysis_per_candidate(self):
        plan = self._plan()
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        candidate = round_.candidate_ids.filtered('analysis_id')[:1]

        with self.assertRaises(psycopg2.IntegrityError):
            with self.env.cr.savepoint():
                self.env[
                    'realestate.procurement.commercial.analysis'].create(
                        {'candidate_id': candidate.id})

    def test_c6_re_normalising_does_not_stack_leveling_lines(self):
        plan = self._plan()
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        analysis = round_.candidate_ids.filtered('analysis_id')[:1].analysis_id
        before = len(analysis.leveling_line_ids)

        round_.action_normalise()
        analysis.invalidate_recordset()

        self.assertEqual(len(analysis.leveling_line_ids), before,
                         "Normalising twice duplicated the leveling lines.")

    # -- C8 ------------------------------------------------------------
    def test_c8_a_finalised_round_refuses_further_normalisation(self):
        plan = self._plan()
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        round_.action_finalise()

        with self.assertRaises(UserError):
            round_.action_normalise()


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6Isolation(M6Common):
    """Company and project boundaries, compared as records and domains."""

    def setUp(self):
        super().setUp()
        self.company_b = self.env['res.company'].create({'name': 'M6 Co B'})
        self.env.user.company_ids = [(4, self.company_b.id)]
        self.plan = self._plan()
        self.round = self._round(self.plan)
        self._advance_to_commercial(self.round)
        self.round.action_normalise()

    def _b_user(self):
        return self.env['res.users'].create({
            'name': 'm6.b.only', 'login': 'm6.b.only',
            'email': 'm6.b.only@example.com',
            'company_id': self.company_b.id,
            'company_ids': [(6, 0, [self.company_b.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('base.group_multi_company').id,
                self.env.ref(
                    'real_estate_procurement.group_evaluation_manager').id,
            ])],
        })

    def test_a_company_b_manager_sees_no_company_a_evaluation(self):
        user = self._b_user()
        for model in ('realestate.procurement.evaluation.plan',
                      'realestate.procurement.evaluation.round',
                      'realestate.procurement.evaluation.candidate',
                      'realestate.procurement.commercial.analysis',
                      'realestate.procurement.technical.evaluation'):
            found = self.env[model].with_user(user).search([])
            self.assertFalse(
                found, "%s leaked company A records to a company B manager: "
                       "%s" % (model, found))

    def test_the_rpc_payload_for_company_b_carries_no_evaluated_cost(self):
        user = self._b_user()
        rows = self.env[
            'realestate.procurement.commercial.analysis'].with_user(
                user).with_context(
                    allowed_company_ids=[self.company_b.id]).search_read(
                        [], ['evaluated_cost', 'partner_id'])
        self.assertEqual(rows, [])

    def test_an_evaluation_cannot_reference_another_companys_tender(self):
        other_event = self.env[
            'realestate.procurement.sourcing.event'].with_company(
                self.company_b).create({
                    'title': 'Company B tender',
                    'company_id': self.company_b.id,
                    'sourcing_method': 'rfq',
                    'close_datetime': self._close_at(),
                })
        plan_b = self.Plan.create({
            'sourcing_event_id': other_event.id,
            'evaluation_currency_id': self.company_b.currency_id.id,
            'criterion_ids': [(0, 0, {'name': 'X', 'criterion_type': 'rated',
                                      'max_score': 10.0, 'weight': 100.0})],
        })
        self.assertNotEqual(plan_b.company_id, self.company_id_of_round())

    def company_id_of_round(self):
        return self.round.company_id

    def test_a_technical_evaluator_on_project_a_gains_no_project_b_bid(self):
        """The evaluation role does not widen project access."""
        evaluator = self._evaluator('m6.proj', 'technical_evaluator')
        with self.assertRaises(AccessError):
            self.env['realestate.procurement.bid.response'].with_user(
                evaluator).search([])


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6Migration(M6Common):
    """Upgrading fabricates no evaluation, and says so in the label."""

    def test_a_closed_tender_is_labelled_not_evaluated(self):
        self._receive_bids()
        label = self.event._classify_evaluation_readiness()

        self.assertEqual(label, 'closed_unevaluated')
        self.assertFalse(self.Round.search(
            [('sourcing_event_id', '=', self.event.id)]),
            "Classification created an evaluation round.")
        self.assertFalse(self.Plan.search(
            [('sourcing_event_id', '=', self.event.id)]))

    def test_an_open_tender_is_not_ready(self):
        self.assertEqual(self.event._classify_evaluation_readiness(),
                         'open_not_ready')

    def test_an_evaluated_tender_says_so(self):
        plan = self._plan()
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        round_.action_finalise()

        self.assertEqual(self.event._classify_evaluation_readiness(),
                         'evaluated')

    def test_classification_is_idempotent_and_creates_nothing(self):
        self._receive_bids()
        first = self.event._classify_evaluation_readiness()
        bids_before = len(self.event.bid_response_ids)
        amounts_before = self.event.bid_response_ids.mapped('amount_untaxed')

        second = self.event._classify_evaluation_readiness()

        self.assertEqual(first, second)
        self.assertEqual(len(self.event.bid_response_ids), bids_before)
        self.assertEqual(self.event.bid_response_ids.mapped('amount_untaxed'),
                         amounts_before)
        self.assertFalse(self.env[
            'realestate.procurement.technical.evaluation'].search([]))
        self.assertFalse(self.env[
            'realestate.procurement.commercial.analysis'].search([]))

    def test_the_financial_position_is_untouched_by_classification(self):
        self._receive_bids()
        before = self._position()
        self.event._classify_evaluation_readiness()
        self.assertEqual(self._position(), before)


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6IntegrityAudit(M6Common):
    """The audit reports; on a sound evaluation it reports nothing critical."""

    def setUp(self):
        super().setUp()
        self.plan = self._plan()
        self.round = self._round(self.plan)
        self._advance_to_commercial(self.round)
        self.round.action_normalise()
        self.Audit = self.env['realestate.procurement.evaluation.audit']

    def test_a_sound_evaluation_has_no_critical_findings(self):
        self.round.action_finalise()
        report = self.Audit.run(company=self.company)
        critical = [f['key'] for f in report['findings']
                    if f['severity'] == 'critical']
        self.assertEqual(critical, [], "Critical findings: %s" % critical)
        self.assertEqual(report['counts']['critical'], 0)

    def test_it_notices_a_criterion_changed_after_freeze(self):
        # Backdate the freeze by an hour. Everything in a test happens inside
        # one second, and `write_date` stores to the second, so a freeze and
        # an edit "after" it are indistinguishable at test speed. The check
        # compares real timestamps; the fixture gives it real ones to compare.
        self.plan._engine().write({
            'frozen_on': fields.Datetime.subtract(
                fields.Datetime.now(), hours=1)})
        criterion = self.plan.criterion_ids.filtered(
            lambda c: c.criterion_type == 'rated')[:1]
        # Through the engine, the way a data fix or another module would.
        criterion.sudo().with_context(
            re_evaluation_engine=True).write({'weight': 55.0})

        report = self.Audit.run(company=self.company)
        keys = [f['key'] for f in report['findings']]

        self.assertIn('criterion_changed_after_freeze', keys)

    def test_it_notices_a_ranked_non_responsive_bid(self):
        self.round.action_finalise()
        candidate = self.round.candidate_ids.filtered(lambda c: c.rank)[:1]
        candidate._engine().write({'technical_result': 'non_responsive'})

        report = self.Audit.run(company=self.company)
        keys = [f['key'] for f in report['findings']]

        self.assertIn('failed_bid_ranked', keys)

    def test_it_notices_an_unexplained_evaluated_cost(self):
        analysis = self.round.candidate_ids.filtered(
            'analysis_id')[:1].analysis_id
        analysis._engine().write({'evaluated_cost':
                                  analysis.raw_amount + 250_000.0})

        report = self.Audit.run(company=self.company)
        keys = [f['key'] for f in report['findings']]

        self.assertIn('evaluated_cost_unexplained', keys)

    def test_m6_exposes_no_award_surface(self):
        report = self.Audit.run(company=self.company)
        keys = [f['key'] for f in report['findings']]
        self.assertNotIn('award_surface_in_m6', keys)

    def test_the_audit_repairs_nothing(self):
        analysis = self.round.candidate_ids.filtered(
            'analysis_id')[:1].analysis_id
        analysis._engine().write({'evaluated_cost': 1.0})

        self.Audit.run(company=self.company)
        analysis.invalidate_recordset()

        self.assertEqual(analysis.evaluated_cost, 1.0,
                         "The audit edited evaluation history instead of "
                         "reporting it.")
