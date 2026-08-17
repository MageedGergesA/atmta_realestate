# -*- coding: utf-8 -*-
"""M6 — the twelve tests written before the implementation.

```
    M6 EVALUATES THE BIDS.
    M6 DOES NOT AWARD THE CONTRACT.
```

Each one fixes a boundary the milestone exists to hold: evaluation moves no
money, the basis cannot drift once evaluators start, a technical evaluator
never sees price, a knockout failure is not rescuable, a submitted score is
evidence, commercial opening is sequenced, exchange rates are frozen, raw bids
are never rewritten, and none of it authorises a purchase.
"""

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import M6Common


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6MovesNoMoney(M6Common):
    """1 — the financial invariant, across the whole evaluation."""

    def test_evaluating_and_finalising_moves_no_money(self):
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0))

        plan = self._plan()
        round_ = self._round(plan)
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Opening an evaluation moved money.")

        self._score_all(round_)
        round_.action_finalise_technical()
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Technical evaluation moved money.")

        round_.action_open_commercial()
        round_.action_normalise()
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Commercial evaluation moved money.")

        round_.action_finalise()
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Finalising an evaluation moved money.")
        self.assertTrue(round_.candidate_ids.filtered('rank'),
                        "The round produced no ranking, so this test proved "
                        "nothing about evaluation.")


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6FrozenCriteria(M6Common):
    """2 — the basis cannot drift after scoring starts."""

    def test_a_frozen_plan_refuses_to_have_its_weights_changed(self):
        plan = self._plan()
        self.assertEqual(plan.state, 'frozen')
        criterion = plan.criterion_ids.filtered(
            lambda c: c.criterion_type == 'rated')[:1]
        weight_before = criterion.weight

        with self.assertRaises(UserError):
            plan.write({'technical_weight': 35.0})
        with self.assertRaises(UserError):
            criterion.write({'weight': 90.0})

        criterion.invalidate_recordset()
        self.assertEqual(criterion.weight, weight_before)

    def test_a_plan_whose_weights_do_not_reconcile_cannot_freeze(self):
        plan = self._plan(freeze=False)
        plan.criterion_ids.filtered(
            lambda c: c.criterion_type == 'rated')[:1].weight = 5.0

        with self.assertRaises(UserError):
            plan.action_freeze()
        self.assertNotEqual(plan.state, 'frozen')

    def test_changing_the_basis_means_a_new_plan_revision(self):
        plan = self._plan()
        revised = plan.action_new_revision(reason='Weighting corrected')

        self.assertEqual(revised.revision, plan.revision + 1)
        self.assertEqual(plan.state, 'superseded')
        self.assertEqual(revised.state, 'draft')


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6TechnicalPriceBlindness(M6Common):
    """3 — a technical evaluator is not shown the money."""

    def test_a_technical_evaluator_cannot_read_commercial_values(self):
        plan = self._plan()
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        analysis = round_.candidate_ids.filtered('analysis_id')[:1].analysis_id
        self.assertTrue(analysis,
                        "No commercial analysis exists, so this test would "
                        "pass against nothing.")
        evaluator = self._evaluator('m6.tech', 'technical_evaluator')

        Analysis = self.env[
            'realestate.procurement.commercial.analysis'].with_user(evaluator)
        with self.assertRaises(AccessError):
            Analysis.search([])
        with self.assertRaises(AccessError):
            Analysis.browse(analysis.id).read(['evaluated_cost'])
        with self.assertRaises(AccessError):
            self.env['realestate.procurement.leveling.line'].with_user(
                evaluator).search([])

    def test_a_technical_sheet_exposes_no_price_field(self):
        """Judged on field *type*, not on the letters in the name.

        `weighted_total` is a score out of 100 and is exactly what a technical
        sheet should carry; a lexical ban on "total" flagged it and would have
        pushed the model towards worse names rather than better segregation.
        What actually matters is that no field holds money.
        """
        plan = self._plan()
        round_ = self._round(plan)
        sheet = self._sheet(round_, round_.candidate_ids[:1])

        for name, field in sheet._fields.items():
            self.assertNotEqual(
                field.type, 'monetary',
                "The technical sheet carries monetary field %r." % name)
            self.assertFalse(
                field.type == 'many2one'
                and field.comodel_name == 'res.currency',
                "The technical sheet carries currency field %r." % name)
            self.assertFalse(
                [word for word in ('price', 'amount', 'cost') if word in name],
                "The technical sheet carries %r, which reads as commercial."
                % name)
        for name, field in sheet.line_ids._fields.items():
            self.assertNotEqual(field.type, 'monetary',
                                "A sheet line carries monetary field %r."
                                % name)

    def test_a_technical_evaluator_cannot_reach_the_bid_through_m6(self):
        plan = self._plan()
        round_ = self._round(plan)
        evaluator = self._evaluator('m6.tech2', 'technical_evaluator')
        candidate = round_.candidate_ids[:1].with_user(evaluator)

        with self.assertRaises(AccessError):
            candidate.bid_response_id.read(['amount_untaxed'])


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6Knockout(M6Common):
    """4 — a mandatory failure is not rescuable by a high score."""

    def test_a_knockout_failure_is_non_responsive_whatever_the_score(self):
        plan = self._plan()
        round_ = self._round(plan)
        candidate = self._candidate(round_, self.vendor_b)

        self._score(round_, candidate, rated=10.0, mandatory='fail')
        self._score_others(round_, exclude=candidate)
        round_.action_finalise_technical()
        candidate.invalidate_recordset()

        self.assertEqual(candidate.technical_result, 'non_responsive')
        self.assertTrue(candidate.technical_result_reason)
        self.assertFalse(candidate.rank,
                         "A knockout failure was given a competitive rank.")


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6SheetImmutability(M6Common):
    """5 — a submitted score is an evaluator's evidence."""

    def test_a_submitted_sheet_refuses_an_ordinary_write(self):
        plan = self._plan()
        round_ = self._round(plan)
        sheet = self._sheet(round_, round_.candidate_ids[:1], submit=True)

        with self.assertRaises(UserError):
            sheet.line_ids[:1].write({'score': 1.0})
        with self.assertRaises(UserError):
            sheet.write({'state': 'draft'})

    def test_a_manager_cannot_silently_rewrite_a_submitted_score(self):
        plan = self._plan()
        round_ = self._round(plan)
        sheet = self._sheet(round_, round_.candidate_ids[:1], submit=True)
        manager = self._evaluator('m6.mgr', 'evaluation_manager')

        with self.assertRaises(UserError):
            sheet.with_user(manager).line_ids[:1].write({'score': 10.0})

        # A correction is possible, but only as a reopen with a reason.
        sheet.with_user(manager).action_reopen(reason='Transcription error')
        self.assertEqual(sheet.state, 'reopened')
        self.assertTrue(sheet.reopen_reason)


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6CommercialSequencing(M6Common):
    """6 — commercial opening waits for technical finalisation."""

    def test_commercial_cannot_open_before_technical_is_final(self):
        plan = self._plan()
        round_ = self._round(plan)

        with self.assertRaises(UserError):
            round_.action_open_commercial()

        self._score_all(round_)
        round_.action_finalise_technical()
        round_.action_open_commercial()
        self.assertEqual(round_.state, 'commercial_open')

    def test_a_non_responsive_bid_is_excluded_from_commercial(self):
        plan = self._plan()
        round_ = self._round(plan)
        failed = self._candidate(round_, self.vendor_b)
        self._score(round_, failed, rated=10.0, mandatory='fail')
        self._score_others(round_, exclude=failed)
        round_.action_finalise_technical()
        round_.action_open_commercial()

        self.assertNotIn(failed, round_.commercial_candidate_ids,
                         "A technically failed bid reached commercial "
                         "evaluation.")
        self.assertFalse(failed.analysis_id)


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6FxSnapshot(M6Common):
    """7 — a finalised evaluation does not move when a rate does."""

    def test_a_later_rate_change_does_not_move_a_finalised_evaluation(self):
        usd = self.env.ref('base.USD')
        self._rate(usd, '2026-08-01', 0.02)
        plan = self._plan(evaluation_currency=self.company.currency_id,
                          rate_date='2026-08-01')
        round_ = self._round(plan, foreign_bid=(self.vendor_c, usd, 100_000.0))
        self._advance_to_commercial(round_)
        round_.action_normalise()
        round_.action_finalise()

        analysis = round_.candidate_ids.filtered(
            lambda c: c.partner_id == self.vendor_c).analysis_id
        frozen_amount = analysis.converted_amount
        frozen_rate = analysis.rate_used
        ranking = {c.partner_id.id: c.rank for c in round_.candidate_ids}

        self._rate(usd, '2026-08-05', 0.10)
        analysis.invalidate_recordset()
        round_.candidate_ids.invalidate_recordset()

        self.assertEqual(analysis.rate_used, frozen_rate)
        self.assertEqual(analysis.converted_amount, frozen_amount,
                         "A finalised evaluation followed a later exchange "
                         "rate.")
        self.assertEqual({c.partner_id.id: c.rank
                          for c in round_.candidate_ids}, ranking,
                         "A later exchange rate re-ranked a finalised "
                         "evaluation.")

    def test_a_missing_rate_is_refused_rather_than_treated_as_one_to_one(self):
        """Odoo's rate lookup COALESCEs to 1.0. That is not a conversion."""
        exotic = self.env['res.currency'].create({
            'name': 'XTS', 'symbol': 'XTS', 'rounding': 0.01,
        })
        plan = self._plan(evaluation_currency=self.company.currency_id)
        round_ = self._round(
            plan, foreign_bid=(self.vendor_c, exotic, 100_000.0))
        self._advance_to_commercial(round_)

        with self.assertRaises(UserError):
            round_.action_normalise()


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6RawBidUntouched(M6Common):
    """8 — normalisation never writes back into M5 evidence."""

    def test_an_adjustment_changes_the_evaluated_cost_and_nothing_else(self):
        plan = self._plan()
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        candidate = round_.candidate_ids.filtered(
            lambda c: c.partner_id == self.vendor_a)
        bid = candidate.bid_response_id
        raw_before = bid.amount_untaxed
        line_before = bid.line_ids[:1].price_unit

        candidate.analysis_id.action_add_adjustment(
            adjustment_type='freight', amount=100_000.0,
            rationale='Freight to site, excluded from the quoted rate')
        bid.invalidate_recordset()

        self.assertEqual(bid.amount_untaxed, raw_before,
                         "An evaluation adjustment rewrote the submitted "
                         "bid.")
        self.assertEqual(bid.line_ids[:1].price_unit, line_before)
        self.assertEqual(candidate.analysis_id.evaluated_cost,
                         raw_before + 100_000.0)
        self.assertEqual(candidate.analysis_id.raw_amount, raw_before)

    def test_an_adjustment_without_a_rationale_is_refused(self):
        plan = self._plan()
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        analysis = round_.candidate_ids[:1].analysis_id

        with self.assertRaises(UserError):
            analysis.action_add_adjustment(adjustment_type='freight',
                                           amount=50_000.0, rationale=False)


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6Ranking(M6Common):
    """9 and 11 — who is ranked, and what happens when two tie."""

    def test_only_responsive_bids_are_ranked(self):
        plan = self._plan()
        round_ = self._round(plan)
        failed = self._candidate(round_, self.vendor_b)
        self._score(round_, failed, rated=10.0, mandatory='fail')
        self._score_others(round_, exclude=failed)
        round_.action_finalise_technical()
        round_.action_open_commercial()
        round_.action_normalise()
        round_.action_finalise()

        ranked = round_.candidate_ids.filtered('rank')
        self.assertNotIn(failed, ranked)
        self.assertEqual(sorted(ranked.mapped('rank')), [1, 2])

    def test_a_genuine_tie_is_flagged_and_not_broken_by_id(self):
        plan = self._plan()
        round_ = self._round(plan, equal_bids=True)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        round_.action_finalise()

        tied = round_.candidate_ids.filtered(lambda c: c.rank == 1)
        self.assertGreater(len(tied), 1,
                           "Equal evaluated costs did not produce a tie, so "
                           "something broke it silently.")
        self.assertTrue(all(c.is_tied for c in tied))
        self.assertEqual(round_.result_status, 'tie_requires_decision')


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6DoesNotAuthorisePurchase(M6Common):
    """10 — finalising an evaluation is not an award."""

    def test_a_finalised_evaluation_still_cannot_confirm_a_tender_rfq(self):
        plan = self._plan()
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        round_.action_finalise()

        winner = round_.candidate_ids.filtered(lambda c: c.rank == 1)[:1]
        order = winner.bid_response_id.invitation_id.purchase_order_id

        with self.assertRaises(UserError):
            order.button_confirm()
        with self.assertRaises(UserError):
            order.with_context(skip_alternative_check=True).button_confirm()

        self.assertEqual(order.state, 'draft')
        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._reserved(self.project, self.concrete),
                         3_000_000.0)

    def test_m6_exposes_no_award_action(self):
        forbidden = ('action_award', 'action_confirm_award', 'award_vendor',
                     'action_create_winning_po')
        for model in ('realestate.procurement.evaluation.round',
                      'realestate.procurement.evaluation.candidate'):
            for name in forbidden:
                self.assertFalse(
                    hasattr(self.env[model], name),
                    "%s exposes %s. M6 evaluates; M7 awards."
                    % (model, name))


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6Bafo(M6Common):
    """12 — a best and final offer never rewrites the first one."""

    def test_a_bafo_revision_leaves_the_initial_bid_intact(self):
        plan = self._plan(bafo_policy='selective')
        first = self._round(plan)
        self._advance_to_commercial(first)
        first.action_normalise()
        first.action_finalise()

        candidate = first.candidate_ids.filtered(
            lambda c: c.partner_id == self.vendor_b)
        initial_bid = candidate.bid_response_id
        initial_amount = initial_bid.amount_untaxed

        bafo = first.action_open_bafo(
            reason='Prices above the authorised estimate',
            partners=first.candidate_ids.filtered(
                lambda c: c.technical_result == 'responsive').partner_id)
        revised = self._bid(candidate.bid_response_id.invitation_id,
                            2_950_000.0)

        initial_bid.invalidate_recordset()
        self.assertEqual(initial_bid.amount_untaxed, initial_amount,
                         "The BAFO rewrote the initial offer.")
        self.assertEqual(initial_bid.state, 'superseded')
        self.assertEqual(revised.revision, initial_bid.revision + 1)
        self.assertEqual(bafo.round_type, 'bafo')
        self.assertEqual(bafo.previous_round_id, first)
        self.assertEqual(self._commitment(self.project), 0.0)

    def test_a_bafo_shortlist_records_why_those_vendors(self):
        plan = self._plan(bafo_policy='selective')
        first = self._round(plan)
        self._advance_to_commercial(first)
        first.action_normalise()
        first.action_finalise()

        with self.assertRaises(UserError):
            first.action_open_bafo(reason=False, partners=self.vendor_a)

        bafo = first.action_open_bafo(reason='Above estimate',
                                      partners=self.vendor_a | self.vendor_c)
        self.assertTrue(bafo.shortlist_reason)
        self.assertEqual(len(bafo.invited_partner_ids), 2)

    def test_a_bafo_is_not_allowed_unless_the_plan_says_so(self):
        """The default is no. Re-opening prices is a decision, not a habit."""
        plan = self._plan()
        self.assertEqual(plan.bafo_policy, 'not_allowed')
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        round_.action_finalise()

        with self.assertRaises(UserError):
            round_.action_open_bafo(reason='We would like better prices',
                                    partners=self.vendor_a)
