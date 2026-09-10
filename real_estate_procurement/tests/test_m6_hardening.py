# -*- coding: utf-8 -*-
"""M6 hardening — the leak, the public methods, the menus, the report, the audit.

A read-only audit of the finished milestone found five things wrong with it,
and this file is the regression coverage for all five. Every test here failed
before the corresponding fix.

```
    M6 EVALUATES THE BIDS.
    M6 DOES NOT AWARD THE CONTRACT.
```

### On the fixture, and why it is not a first round

The commercial fields on a candidate are all zero until the round finalises, so
a leak test written against a fresh round passes against nothing at all. Every
confidentiality test below runs against a **finalised, populated** evaluation
with three distinct financial scores, three distinct combined scores and ranks
1, 2, 3 — and `test_the_fixture_is_actually_populated` fails loudly if that
ever stops being true, because at that point the rest of the file would be
proving nothing.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import HttpCase

from .common import M6Common


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class M6HardeningCommon(M6Common):

    def _populated_round(self):
        """A finalised evaluation whose commercial outcome is real."""
        plan = self._plan(method='rated_combined', bafo_policy='all_responsive')
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        round_.action_finalise()
        return round_

    def _tech(self, login='m6h.tech', extra=()):
        """A technical evaluator, optionally holding other procurement groups."""
        user = self._evaluator(login, 'technical_evaluator')
        for xmlid in extra:
            user.groups_id |= self.env.ref(xmlid)
        return user

    @property
    def Candidate(self):
        return self.env['realestate.procurement.evaluation.candidate']


# ---------------------------------------------------------------------------
# PHASE 2 — the commercial information leak
# ---------------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6CommercialConfidentiality(M6HardeningCommon):

    def test_the_fixture_is_actually_populated(self):
        """Without this, every other test in the class proves nothing."""
        round_ = self._populated_round()
        candidates = round_.candidate_ids.filtered('in_commercial')

        self.assertEqual(len(candidates), 3)
        financial = candidates.mapped('financial_score')
        combined = candidates.mapped('combined_score')
        self.assertEqual(len(set(financial)), 3,
                         "Financial scores are not distinct: %s" % financial)
        self.assertEqual(len(set(combined)), 3,
                         "Combined scores are not distinct: %s" % combined)
        self.assertTrue(all(financial), "A financial score is still zero.")
        self.assertEqual(sorted(candidates.mapped('rank')), [1, 2, 3])

    def test_fields_get_does_not_offer_the_commercial_outcome(self):
        self._populated_round()
        tech = self._tech()

        names = self.Candidate.with_user(tech).fields_get().keys()

        for field in ('financial_score', 'combined_score', 'rank', 'is_tied',
                      'analysis_id'):
            self.assertNotIn(field, names,
                             "fields_get offered %r to a technical evaluator."
                             % field)
        # The technical half of the same record is still there to work with.
        for field in ('technical_score', 'technical_result', 'partner_name'):
            self.assertIn(field, names)

    def test_read_refuses_the_commercial_outcome(self):
        round_ = self._populated_round()
        tech = self._tech()
        candidate = round_.candidate_ids[:1].with_user(tech)

        for field in ('financial_score', 'combined_score', 'rank', 'is_tied',
                      'analysis_id'):
            with self.assertRaises(AccessError, msg=field):
                candidate.read([field])

    def test_search_read_refuses_the_commercial_outcome(self):
        self._populated_round()
        tech = self._tech()

        with self.assertRaises(AccessError):
            self.Candidate.with_user(tech).search_read([], ['rank'])
        with self.assertRaises(AccessError):
            self.Candidate.with_user(tech).search_read(
                [], ['partner_name', 'financial_score'])

    def test_a_domain_cannot_ask_what_a_read_refuses(self):
        """Odoo's expression engine never consults field groups."""
        self._populated_round()
        tech = self._tech()

        for domain in ([('rank', '=', 1)],
                       [('financial_score', '>', 90.0)],
                       [('combined_score', '!=', 0)],
                       [('is_tied', '=', True)],
                       [('analysis_id.evaluated_cost', '>', 0)]):
            with self.assertRaises(AccessError, msg=str(domain)):
                self.Candidate.with_user(tech).search(domain)

    def test_the_default_order_does_not_hand_over_the_ranking(self):
        """`_order` sorts by rank. That is the ranking, with nothing read."""
        round_ = self._populated_round()
        tech = self._tech()

        found = self.Candidate.with_user(tech).search(
            [('round_id', '=', round_.id)])

        by_id = round_.candidate_ids.sorted('id')
        by_rank = round_.candidate_ids.sorted('rank')
        self.assertNotIn('rank', self.Candidate._order,
                         "`rank` is back in _order, which is the ranking.")
        self.assertNotEqual(
            by_id.ids, by_rank.ids,
            "The fixture's id order equals its rank order, so this test "
            "cannot tell the two apart.")
        self.assertEqual(
            found.ids, by_id.ids,
            "A technical evaluator was served the candidates in rank order.")

    def test_an_explicit_order_by_rank_is_refused(self):
        round_ = self._populated_round()
        tech = self._tech()

        for order in ('rank', 'rank desc', 'round_id, rank desc, id',
                      'financial_score desc'):
            with self.assertRaises(AccessError, msg=order):
                self.Candidate.with_user(tech).search(
                    [('round_id', '=', round_.id)], order=order)

    def test_an_aggregate_cannot_ask_it_either(self):
        round_ = self._populated_round()
        tech = self._tech()
        Cand = self.Candidate.with_user(tech)

        with self.assertRaises(AccessError):
            Cand._read_group([('round_id', '=', round_.id)], ['rank'],
                             ['__count'])
        with self.assertRaises(AccessError):
            Cand._read_group([('round_id', '=', round_.id)], ['partner_id'],
                             ['financial_score:sum'])
        with self.assertRaises(AccessError):
            Cand._read_group([('rank', '=', 1)], ['partner_id'], ['__count'])

    def test_relation_traversal_reaches_no_commercial_record(self):
        round_ = self._populated_round()
        tech = self._tech()
        candidate = round_.candidate_ids[:1].with_user(tech)

        with self.assertRaises(AccessError):
            candidate.analysis_id
        with self.assertRaises(AccessError):
            candidate.mapped('analysis_id.evaluated_cost')
        with self.assertRaises(AccessError):
            self.env['realestate.procurement.commercial.analysis'].with_user(
                tech).search([])
        with self.assertRaises(AccessError):
            self.env['realestate.procurement.leveling.line'].with_user(
                tech).search([])
        with self.assertRaises(AccessError):
            self.env['realestate.procurement.commercial.adjustment'].with_user(
                tech).search([])

    def test_the_bafo_shortlist_is_commercial_too(self):
        """Who was asked to re-price, and why, is a commercial judgement."""
        round_ = self._populated_round()
        bafo = round_.action_open_bafo(reason='Above the authorised estimate')
        tech = self._tech()

        with self.assertRaises(AccessError):
            bafo.with_user(tech).read(['shortlist_reason'])
        with self.assertRaises(AccessError):
            bafo.with_user(tech).read(['invited_partner_ids'])
        # And the round is still readable — segregation is about money, not
        # about hiding that a further round exists.
        self.assertTrue(bafo.with_user(tech).read(['name', 'state']))

    def test_a_second_round_does_not_reopen_the_first(self):
        """The scenario the whole defect was about."""
        first = self._populated_round()
        bafo = first.action_open_bafo(reason='Above the authorised estimate')
        tech = self._tech()

        # Scoring the BAFO round must not let them read round one's outcome.
        self.assertEqual(bafo.round_type, 'bafo')
        with self.assertRaises(AccessError):
            first.candidate_ids.with_user(tech).read(['rank'])
        with self.assertRaises(AccessError):
            self.Candidate.with_user(tech).search([('rank', '=', 1)])

    def test_a_technical_evaluator_can_still_do_their_job(self):
        round_ = self._populated_round()
        tech = self._tech()

        rows = round_.candidate_ids.with_user(tech).read(
            ['partner_name', 'technical_score', 'technical_result',
             'exclusion_reason', 'score_spread'])

        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row['partner_name'] for row in rows))
        self.assertTrue(round_.with_user(tech).read(['name', 'state']))

    def test_another_procurement_group_does_not_reopen_the_fields(self):
        """A broad group must not widen this by accident.

        `group_procurement_requester` is the case that matters: it is a real
        procurement group and it is *not* commercial, so holding it alongside
        the technical role must change nothing.
        """
        self._populated_round()
        tech = self._tech('m6h.tech.req',
                          extra=('real_estate_procurement.'
                                 'group_procurement_requester',))

        with self.assertRaises(AccessError):
            self.Candidate.with_user(tech).search_read([], ['rank'])
        with self.assertRaises(AccessError):
            self.Candidate.with_user(tech).search([('rank', '=', 1)])
        self.assertNotIn('rank',
                         self.Candidate.with_user(tech).fields_get().keys())

    def test_a_technical_evaluator_who_is_also_a_buyer_may_see_it(self):
        """Stated rather than left implicit, because it looks like a hole.

        A Buyer reads the bid register and every price in it natively. Hiding
        the derived ranking from somebody who can already read the raw numbers
        would be theatre, not confidentiality — so the gate is "commercially
        entitled", and a Buyer is.
        """
        round_ = self._populated_round()
        both = self._tech('m6h.tech.buyer',
                          extra=('real_estate_procurement.'
                                 'group_procurement_user',))

        rows = round_.candidate_ids.with_user(both).read(['rank'])
        self.assertEqual(len(rows), 3)

    def test_the_commercial_roles_are_not_locked_out(self):
        round_ = self._populated_round()
        commercial = self._evaluator('m6h.comm', 'commercial_evaluator')
        manager = self._evaluator('m6h.mgr', 'evaluation_manager')

        for user in (commercial, manager):
            rows = round_.candidate_ids.with_user(user).read(
                ['rank', 'financial_score', 'combined_score', 'is_tied'])
            self.assertEqual(len(rows), 3, user.login)
            self.assertEqual(
                sorted(row['rank'] for row in rows), [1, 2, 3], user.login)
            ordered = self.Candidate.with_user(user).search(
                [('round_id', '=', round_.id)], order='rank')
            self.assertEqual(ordered.ids,
                             round_.candidate_ids.sorted('rank').ids)

    def test_no_m6_model_leaks_money_through_an_unrestricted_field(self):
        """A structural sweep, so a new field cannot quietly reopen this."""
        tech = self._tech('m6h.sweep')
        # `score` is deliberately absent from this list: scoring is exactly
        # what a technical evaluator does, and banning the word would push the
        # model towards worse names rather than better segregation. The words
        # here are the ones that can only mean money or placing.
        money = ('price', 'amount', 'cost', 'rate', 'financial', 'combined',
                 'rank', 'tied')
        allowed = {'administrative_status'}
        for model in ('realestate.procurement.evaluation.candidate',
                      'realestate.procurement.technical.evaluation',
                      'realestate.procurement.technical.evaluation.line',
                      'realestate.procurement.evaluation.deviation'):
            fields = self.env[model].with_user(tech).fields_get()
            for name, spec in fields.items():
                if name in allowed:
                    continue
                self.assertNotEqual(
                    spec.get('type'), 'monetary',
                    "%s.%s is monetary and readable by a technical evaluator."
                    % (model, name))
                self.assertFalse(
                    [w for w in money if w in name],
                    "%s.%s reads as commercial and is readable by a technical "
                    "evaluator." % (model, name))


# ---------------------------------------------------------------------------
# PHASE 3 — public method security
# ---------------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6PublicMethodSecurity(M6HardeningCommon):

    def test_the_autoscoring_shortcut_is_gone_from_the_model(self):
        """It was public, so it was callable over RPC.

        One call gave every bid in a live tender a submitted passing sheet
        that no evaluator had written.
        """
        for model in ('realestate.procurement.evaluation.round',
                      'realestate.procurement.evaluation.candidate'):
            for name in ('action_open_commercial_after_technical',
                         '_autoscore_pass'):
                self.assertFalse(
                    hasattr(self.env[model], name),
                    "%s still exposes %s." % (model, name))

    def test_a_technical_evaluator_cannot_drive_the_lifecycle(self):
        plan = self._plan()
        round_ = self._round(plan, open_=False)
        tech = self._tech('m6h.driver')

        with self.assertRaises(AccessError):
            round_.with_user(tech).action_open_technical()
        with self.assertRaises(AccessError):
            round_.with_user(tech).action_finalise_technical()
        with self.assertRaises(AccessError):
            round_.with_user(tech).action_open_commercial()
        with self.assertRaises(AccessError):
            round_.with_user(tech).action_finalise()
        self.assertEqual(round_.state, 'draft')

    def test_a_buyer_cannot_open_the_prices_or_close_the_evaluation(self):
        """Write access to the round is not authority over it."""
        plan = self._plan()
        round_ = self._round(plan)
        buyer = self.env['res.users'].create({
            'name': 'm6h.buyer', 'login': 'm6h.buyer',
            'email': 'm6h.buyer@example.com',
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_procurement.group_procurement_user').id])],
        })

        with self.assertRaises(AccessError):
            round_.with_user(buyer).action_open_commercial()
        with self.assertRaises(AccessError):
            round_.with_user(buyer).action_finalise()
        with self.assertRaises(AccessError):
            plan.with_user(buyer).action_new_revision(reason='Reweighted')

    def test_a_plan_cannot_be_frozen_by_somebody_who_may_only_draft_it(self):
        plan = self._plan(freeze=False)
        tech = self._tech('m6h.freezer')

        with self.assertRaises(AccessError):
            plan.with_user(tech).action_freeze()
        self.assertNotEqual(plan.state, 'frozen')

    def test_a_round_cannot_be_cancelled_silently_or_after_it_finished(self):
        round_ = self._populated_round()

        with self.assertRaises(UserError):
            round_.action_cancel(reason='Changed our minds')
        self.assertEqual(round_.state, 'finalised')

        # Same plan — a second plan on one event collides with the
        # `unique(sourcing_event_id, revision)` index, which is correct.
        draft = self.Round.create({'sourcing_event_id': self.event.id,
                                   'plan_id': round_.plan_id.id})
        with self.assertRaises(UserError):
            draft.action_cancel()
        draft.action_cancel(reason='Tender reissued with a revised scope')
        self.assertEqual(draft.state, 'cancelled')
        self.assertTrue(draft.reopen_reason)

    def test_a_conflict_declaration_cannot_be_made_for_somebody_else(self):
        plan = self._plan()
        round_ = self._round(plan)
        one = self._evaluator('m6h.ev1', 'technical_evaluator', round_=round_)
        two = self._evaluator('m6h.ev2', 'technical_evaluator', round_=round_)
        theirs = round_.assignment_ids.filtered(lambda a: a.user_id == one)

        with self.assertRaises(AccessError):
            theirs.with_user(two).action_declare(conflict=False)
        # Their own, and the manager recording what they were told, are fine.
        theirs.with_user(one).action_declare(conflict=False)
        self.assertEqual(theirs.conflict_status, 'none')

    def test_clearing_a_conflict_is_a_management_act(self):
        plan = self._plan()
        round_ = self._round(plan)
        one = self._evaluator('m6h.cf1', 'technical_evaluator', round_=round_)
        two = self._evaluator('m6h.cf2', 'technical_evaluator', round_=round_)
        theirs = round_.assignment_ids.filtered(lambda a: a.user_id == one)
        theirs.with_user(one).action_declare(
            conflict=True, details='Former employer')

        with self.assertRaises(AccessError):
            theirs.with_user(two).action_clear_conflict(note='Looks fine')
        theirs.action_clear_conflict(note='Employment ended nine years ago')
        self.assertEqual(theirs.conflict_status, 'cleared')

    def test_a_sheet_cannot_be_submitted_by_somebody_else(self):
        plan = self._plan()
        round_ = self._round(plan)
        owner = self._evaluator('m6h.own', 'technical_evaluator', round_=round_)
        other = self._evaluator('m6h.oth', 'technical_evaluator', round_=round_)
        sheet = self._sheet(round_, round_.candidate_ids[:1], evaluator=owner)

        with self.assertRaises(AccessError):
            sheet.with_user(other).action_submit()
        self.assertEqual(sheet.state, 'draft')
        sheet.with_user(owner).action_submit()
        self.assertEqual(sheet.state, 'submitted')

    def test_the_integrity_audit_is_not_callable_by_everybody(self):
        self._populated_round()
        tech = self._tech('m6h.auditor')
        Audit = self.env['realestate.procurement.evaluation.audit']

        with self.assertRaises(AccessError):
            Audit.with_user(tech).run(company=self.company)
        # And it still answers the people who are accountable for it.
        self.assertIn('counts', Audit.run(company=self.company))

    def test_an_adjustment_cannot_be_approved_after_the_ranking_is_struck(self):
        plan = self._plan()
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        analysis = round_.candidate_ids.filtered('analysis_id')[:1].analysis_id
        adjustment = analysis.action_add_adjustment(
            adjustment_type='freight', amount=10_000.0,
            rationale='Freight to site')
        round_.action_finalise()

        with self.assertRaises(UserError):
            adjustment.action_approve()
        with self.assertRaises(UserError):
            analysis.action_flag_for_review(note='Looks low')

    def test_a_finalised_evaluation_still_authorises_no_purchase(self):
        """Re-asserted here because every change above touched the lifecycle."""
        round_ = self._populated_round()
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


# ---------------------------------------------------------------------------
# PHASE 4 — navigation
# ---------------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6Navigation(M6HardeningCommon):

    def _ref(self, xmlid):
        return self.env.ref('real_estate_procurement.%s' % xmlid)

    def test_the_evaluation_menus_exist(self):
        for xmlid in ('menu_evaluation', 'menu_evaluation_rounds',
                      'menu_evaluation_plans', 'menu_technical_evaluations',
                      'menu_evaluation_integrity_audit'):
            self.assertTrue(self._ref(xmlid), xmlid)
        self.assertEqual(self._ref('menu_evaluation').parent_id,
                         self._ref('menu_procurement_root'))

    def test_the_menus_reuse_the_existing_actions(self):
        self.assertEqual(self._ref('menu_evaluation_rounds').action.id,
                         self._ref('action_evaluation_round').id)
        self.assertEqual(self._ref('menu_evaluation_plans').action.id,
                         self._ref('action_evaluation_plan').id)
        self.assertEqual(self._ref('menu_technical_evaluations').action.id,
                         self._ref('action_technical_evaluation').id)

    def test_the_audit_menu_is_for_managers_only(self):
        groups = self._ref('menu_evaluation_integrity_audit').groups_id
        self.assertEqual(
            set(groups.ids),
            {self._ref('group_evaluation_manager').id,
             self._ref('group_procurement_manager').id})

    def test_a_technical_evaluator_can_reach_the_evaluation_menu(self):
        tech = self._tech('m6h.nav')
        visible = self.env['ir.ui.menu'].with_user(
            tech)._visible_menu_ids()

        self.assertIn(self._ref('menu_evaluation').id, visible,
                      "A technical evaluator cannot see the Evaluation menu, "
                      "so the milestone is unreachable for them.")
        self.assertIn(self._ref('menu_evaluation_rounds').id, visible)
        self.assertIn(self._ref('menu_procurement_root').id, visible)

    def test_a_technical_evaluator_sees_no_commercial_menu(self):
        tech = self._tech('m6h.nav2')
        visible = self.env['ir.ui.menu'].with_user(
            tech)._visible_menu_ids()

        for xmlid in ('menu_evaluation_integrity_audit', 'menu_bid_responses',
                      'menu_sourcing_events', 'menu_purchase_orders',
                      'menu_procurement_reservations'):
            self.assertNotIn(self._ref(xmlid).id, visible, xmlid)

    def test_a_requester_gets_no_evaluation_menu(self):
        requester = self.env['res.users'].create({
            'name': 'm6h.req', 'login': 'm6h.req',
            'email': 'm6h.req@example.com',
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self._ref('group_procurement_requester').id])],
        })
        visible = self.env['ir.ui.menu'].with_user(
            requester)._visible_menu_ids()

        for xmlid in ('menu_evaluation', 'menu_evaluation_rounds',
                      'menu_evaluation_plans', 'menu_technical_evaluations',
                      'menu_evaluation_integrity_audit'):
            self.assertNotIn(self._ref(xmlid).id, visible, xmlid)

    def test_a_generic_purchase_user_gets_no_evaluation_menu(self):
        buyer = self.env['res.users'].create({
            'name': 'm6h.pur', 'login': 'm6h.pur',
            'email': 'm6h.pur@example.com',
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('purchase.group_purchase_user').id])],
        })
        visible = self.env['ir.ui.menu'].with_user(buyer)._visible_menu_ids()

        for xmlid in ('menu_evaluation', 'menu_evaluation_rounds',
                      'menu_evaluation_integrity_audit'):
            self.assertNotIn(self._ref(xmlid).id, visible, xmlid)

    def test_an_evaluation_manager_reaches_the_whole_section(self):
        manager = self._evaluator('m6h.navmgr', 'evaluation_manager')
        manager.groups_id |= self._ref('group_procurement_manager')
        visible = self.env['ir.ui.menu'].with_user(manager)._visible_menu_ids()

        for xmlid in ('menu_evaluation', 'menu_evaluation_rounds',
                      'menu_evaluation_plans', 'menu_technical_evaluations',
                      'menu_evaluation_integrity_audit'):
            self.assertIn(self._ref(xmlid).id, visible, xmlid)


# ---------------------------------------------------------------------------
# PHASE 5 — the evaluation report
# ---------------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6EvaluationReport(M6HardeningCommon):

    REPORT = 'atmta_procurement_evaluation.report_evaluation'

    def _html(self, round_, user=None):
        Report = self.env['ir.actions.report']
        if user is not None:
            Report = Report.with_user(user)
        return Report._render_qweb_html(self.REPORT, round_.ids)[0].decode()

    def test_a_finalised_round_renders(self):
        round_ = self._populated_round()
        html = self._html(round_)

        self.assertIn('Bid Evaluation Report', html)
        self.assertIn(round_.name, html)
        self.assertIn(round_.plan_id.name, html)


    def test_the_report_shows_the_frozen_figures(self):
        round_ = self._populated_round()
        html = self._html(round_).replace(',', '')

        winner = round_.candidate_ids.filtered(lambda c: c.rank == 1)[:1]
        self.assertIn('2800000', html,
                      "The raw bid as submitted is not on the report.")
        self.assertIn(winner.partner_name, html)
        for candidate in round_.candidate_ids:
            self.assertIn(candidate.partner_name, html)

    def test_the_report_states_the_ranking_and_the_tie_position(self):
        round_ = self._populated_round()
        html = self._html(round_)

        self.assertIn('Final Evaluation', html)
        # Matched on a fragment that cannot span a line break: the template
        # wraps this sentence, so the rendered HTML carries a newline and
        # indentation in the middle of it.
        self.assertIn('Rank 1 is an evaluation outcome', html)
        self.assertIn('authorises no purchase', html)
        self.assertEqual(round_.result_status, 'ranked')
        for rank in ('1', '2', '3'):
            self.assertIn('>%s</span>' % rank, html.replace(' ', ''))

    def test_the_report_uses_no_award_language(self):
        round_ = self._populated_round()
        html = self._html(round_).lower()

        for word in ('winner', 'awarded', 'award recommendation',
                     'approved vendor', 'contract award', 'selected supplier'):
            self.assertNotIn(word, html,
                             "The evaluation report says %r." % word)
        # The denial itself is allowed, and is the point.
        self.assertIn('no award is created by this report',
                      self._html(round_).lower())

    def test_the_report_does_not_drift_when_the_world_moves(self):
        """Reproducibility, tested by moving the world.

        A vendor renames itself and a new exchange rate is published. Neither
        may change a document that states what was decided, and on what basis,
        on a date that has passed.
        """
        usd = self.env.ref('base.USD')
        self._rate(usd, '2026-08-01', 0.02)
        plan = self._plan(method='rated_combined',
                          evaluation_currency=self.company.currency_id,
                          rate_date='2026-08-01')
        round_ = self._round(plan, foreign_bid=(self.vendor_c, usd, 100_000.0))
        self._advance_to_commercial(round_)
        round_.action_normalise()
        round_.action_finalise()

        before = self._html(round_)
        original_name = self.vendor_c.name

        self.vendor_c.name = 'Renamed After The Evaluation LLC'
        self._rate(usd, '2026-08-05', 0.10)
        round_.invalidate_recordset()
        round_.candidate_ids.invalidate_recordset()

        after = self._html(round_)

        self.assertIn(original_name, after,
                      "The report followed a vendor rename and no longer says "
                      "who was actually evaluated.")
        self.assertNotIn('Renamed After The Evaluation LLC', after)
        self.assertEqual(before, after,
                         "The evaluation report changed after the evaluation "
                         "was finalised.")

    def test_a_technical_evaluator_cannot_issue_the_report(self):
        round_ = self._populated_round()
        tech = self._tech('m6h.report')

        with self.assertRaises(AccessError):
            round_.with_user(tech).action_print_evaluation_report()

    def test_no_route_produces_this_document_for_a_technical_evaluator(self):
        """Two independent guards, and the test names both.

        `action_print_evaluation_report` refuses to issue it. Rendering the
        template directly — the path the `/report/...` HTTP route takes, which
        performs no group check of its own — fails as well, because the header
        alone reads the sourcing event and a technical evaluator has no access
        to the tender. And underneath both, `groups=` on the commercial
        sections means QWeb would never have compiled the figures into the
        output even if it had got that far.
        """
        round_ = self._populated_round()
        tech = self._tech('m6h.report2')

        with self.assertRaises(AccessError):
            round_.with_user(tech).action_print_evaluation_report()
        with self.assertRaises(Exception):
            self._html(round_, user=tech)

        template = self.env.ref(
            'atmta_procurement_evaluation.report_evaluation_document').arch
        self.assertIn('group_evaluation_commercial', template,
                      "The commercial sections carry no groups= guard.")

    def test_the_report_cannot_be_issued_before_the_result_exists(self):
        plan = self._plan()
        round_ = self._round(plan)

        with self.assertRaises(UserError):
            round_.action_print_evaluation_report()

    def test_an_authorised_user_can_issue_it(self):
        round_ = self._populated_round()
        manager = self._evaluator('m6h.rpt.mgr', 'evaluation_manager')

        action = round_.with_user(manager).action_print_evaluation_report()

        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(action['report_name'],
                         'atmta_procurement_evaluation.report_evaluation')

    def test_the_report_creates_nothing_and_moves_no_money(self):
        round_ = self._populated_round()
        before = self._position()

        self._html(round_)

        self.assertEqual(self._position(), before)
        self.assertEqual(round_.state, 'finalised')


# ---------------------------------------------------------------------------
# PHASE 6 — the integrity audit user interface
# ---------------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6IntegrityAuditWizard(M6HardeningCommon):

    @property
    def Wizard(self):
        return self.env['realestate.procurement.evaluation.audit.wizard']

    def test_a_manager_can_run_it_and_gets_counts(self):
        round_ = self._populated_round()
        wizard = self.Wizard.create({'company_id': self.company.id})

        wizard.action_run()

        self.assertEqual(wizard.state, 'done')
        self.assertTrue(wizard.run_on)
        self.assertGreaterEqual(wizard.round_count, 1)
        self.assertEqual(wizard.critical_count, 0,
                         "Critical findings on a sound evaluation: %s"
                         % wizard.finding_ids.mapped('key'))
        self.assertIn(self.company.name, wizard.scope)
        self.assertIn(str(wizard.round_count), wizard.summary)
        self.assertEqual(round_.state, 'finalised')

    def test_it_can_be_scoped_to_one_round(self):
        round_ = self._populated_round()
        wizard = self.Wizard.create({'company_id': self.company.id,
                                     'round_id': round_.id})
        wizard.action_run()

        self.assertEqual(wizard.round_count, 1)
        self.assertEqual(wizard.scope, round_.display_name)

    def test_it_surfaces_a_real_finding_with_its_severity(self):
        round_ = self._populated_round()
        candidate = round_.candidate_ids.filtered(lambda c: c.rank)[:1]
        candidate._engine().write({'technical_result': 'non_responsive'})

        wizard = self.Wizard.create({'company_id': self.company.id})
        wizard.action_run()

        keys = wizard.finding_ids.mapped('key')
        self.assertIn('failed_bid_ranked', keys)
        self.assertGreaterEqual(wizard.critical_count, 1)
        finding = wizard.finding_ids.filtered(
            lambda f: f.key == 'failed_bid_ranked')
        self.assertEqual(finding.severity, 'critical')
        self.assertTrue(finding.remediation)
        self.assertTrue(finding.references)
        # Worst first.
        self.assertEqual(wizard.finding_ids[0].severity, 'critical')

    def test_it_repairs_nothing(self):
        round_ = self._populated_round()
        analysis = round_.candidate_ids.filtered(
            'analysis_id')[:1].analysis_id
        analysis._engine().write({'evaluated_cost': 1.0})

        wizard = self.Wizard.create({'company_id': self.company.id})
        wizard.action_run()
        analysis.invalidate_recordset()

        self.assertEqual(analysis.evaluated_cost, 1.0,
                         "The audit wizard edited evaluation history.")

    def test_running_it_twice_replaces_rather_than_accumulates(self):
        self._populated_round()
        wizard = self.Wizard.create({'company_id': self.company.id})
        wizard.action_run()
        first = len(wizard.finding_ids)
        wizard.action_run()

        self.assertEqual(len(wizard.finding_ids), first)

    def test_the_audit_now_guards_the_commercial_restriction_itself(self):
        """Structural: removing a `groups=` must fail the build."""
        self._populated_round()
        wizard = self.Wizard.create({'company_id': self.company.id})
        wizard.action_run()

        self.assertNotIn('commercial_outcome_unrestricted',
                         wizard.finding_ids.mapped('key'))
        Candidate = self.Candidate
        for name in ('financial_score', 'combined_score', 'rank', 'is_tied',
                     'analysis_id'):
            self.assertTrue(Candidate._fields[name].groups,
                            "%s lost its group restriction." % name)
        self.assertNotIn('rank', Candidate._order)

    def test_it_notices_a_sheet_edited_after_submission(self):
        from odoo import fields as odoo_fields
        round_ = self._populated_round()
        sheet = round_.sheet_ids[:1]
        sheet._engine().write({
            'submitted_on': odoo_fields.Datetime.subtract(
                odoo_fields.Datetime.now(), hours=2)})
        sheet._engine().write({'overall_comment': 'Rewritten afterwards'})

        wizard = self.Wizard.create({'company_id': self.company.id})
        wizard.action_run()

        self.assertIn('submitted_sheet_edited', wizard.finding_ids.mapped('key'))

    def test_it_notices_a_candidate_edited_after_finalisation(self):
        from odoo import fields as odoo_fields
        round_ = self._populated_round()
        round_._engine().write({
            'finalised_on': odoo_fields.Datetime.subtract(
                odoo_fields.Datetime.now(), hours=2)})
        round_.candidate_ids[:1]._engine().write({'consensus_note': 'Later'})

        wizard = self.Wizard.create({'company_id': self.company.id})
        wizard.action_run()

        self.assertIn('finalised_round_edited', wizard.finding_ids.mapped('key'))

    def test_a_technical_evaluator_cannot_reach_the_wizard(self):
        self._populated_round()
        tech = self._tech('m6h.wiz')

        with self.assertRaises(AccessError):
            self.Wizard.with_user(tech).create(
                {'company_id': self.company.id})

    def test_an_empty_scope_says_so_rather_than_reporting_clean(self):
        empty = self.env['res.company'].create({'name': 'M6 Empty Co'})
        self.env.user.company_ids = [(4, empty.id)]
        wizard = self.Wizard.create({'company_id': empty.id})

        wizard.action_run()

        self.assertEqual(wizard.round_count, 0)
        self.assertIn('nothing to audit', wizard.summary)
        self.assertIn('not the same as a clean result', wizard.summary)


# ---------------------------------------------------------------------------
# Project scope — what is true, and what is not
# ---------------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6ProjectScope(M6HardeningCommon):
    """The project boundary, asserted at the level it actually exists.

    A release gate asked whether a "Project A-only user" can read Project B's
    evaluation evidence. They cannot be tested here, because **no such user
    exists in this architecture**: there is no project-membership model and no
    project-scoped `ir.rule` anywhere in the ATMTA suite — not in M6, and not
    in M2 through M5 either. Isolation is drawn at the company.

    Inventing project-level access control inside M6 would give evaluation a
    security model no other milestone has, and would be a large architectural
    change smuggled in as hardening. So this class asserts the boundary that
    does exist, and pins the two things a future project rule would need:
    `project_id` stored and indexed on every M6 model, and a refusal to reach
    across companies. The gap itself is reported rather than papered over.
    """

    M6_MODELS = (
        'realestate.procurement.evaluation.round',
        'realestate.procurement.evaluation.candidate',
        'realestate.procurement.commercial.analysis',
        'realestate.procurement.leveling.line',
    )

    def test_every_m6_record_carries_its_project(self):
        """So a project rule can be added later without a migration."""
        for model in self.M6_MODELS:
            field = self.env[model]._fields.get('project_id')
            self.assertTrue(field, "%s has no project_id." % model)
            self.assertTrue(field.store, "%s.project_id is not stored." % model)
            self.assertTrue(field.index, "%s.project_id is not indexed." % model)

    def test_an_evaluation_inherits_the_tender_project(self):
        round_ = self._populated_round()

        # `mapped` on a many2one returns a deduplicated recordset, not a
        # list — comparing it to [project] * 3 was the probe being wrong, not
        # the data.
        self.assertEqual(round_.project_id, self.project)
        self.assertEqual(round_.candidate_ids.mapped('project_id'),
                         self.project)
        analyses = round_.candidate_ids.filtered('in_commercial').analysis_id
        self.assertEqual(analyses.mapped('project_id'), self.project)
        self.assertEqual(analyses.leveling_line_ids.mapped('project_id'),
                         self.project)

    def test_an_evaluation_cannot_reach_another_companys_project(self):
        """The boundary that is really enforced: the company."""
        other = self.env['res.company'].create({'name': 'M6 Project Co B'})
        self.env.user.company_ids = [(4, other.id)]
        project_b = self.env['realestate.project'].create({
            'name': 'Other company project',
            'code': 'OCP001',
            'company_id': other.id,
            'expected_budget': 1_000_000.0,
        })

        with self.assertRaises(Exception):
            self.env[
                'realestate.procurement.sourcing.event'].create({
                    'title': 'Cross-company tender',
                    'company_id': self.company.id,
                    'project_id': project_b.id,
                    'sourcing_method': 'competitive_tender',
                    'close_datetime': self._close_at(),
                })

    def test_project_level_access_control_does_not_exist_yet(self):
        """A characterisation test, so the gap cannot be forgotten.

        If somebody adds a project rule to M6, this fails and tells them to
        update the claim in the report rather than leaving it stale.
        """
        rules = self.env['ir.rule'].sudo().search([
            ('model_id.model', 'in', list(self.M6_MODELS)),
        ])
        self.assertTrue(rules, "M6 has no record rules at all.")
        for rule in rules:
            self.assertNotIn(
                'project_id', rule.domain_force,
                "%s scopes by project. M6 gained project-level access control "
                "— update the release report, which states it has none."
                % rule.name)


# ---------------------------------------------------------------------------
# The report as a PDF — an HttpCase, because wkhtmltopdf needs a live server
# ---------------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6EvaluationReportPdf(M6HardeningCommon, HttpCase):
    """HTML is what the assertions read; PDF is what a person files.

    Two things have to be true for this to test anything. Odoo falls back to
    HTML whenever `test_enable` is set, so the render is forced with
    `force_report_rendering`. And wkhtmltopdf resolves the asset URLs in the
    document over HTTP, so this has to be an `HttpCase` with a server actually
    listening — as a plain `TransactionCase` it returned HTML and the
    assertion caught it.
    """

    def test_the_evaluation_report_produces_a_real_pdf(self):
        round_ = self._populated_round()

        pdf, kind = self.env['ir.actions.report'].with_context(
            force_report_rendering=True)._render_qweb_pdf(
                'atmta_procurement_evaluation.report_evaluation', round_.ids)

        self.assertEqual(kind, 'pdf', "The report fell back to HTML.")
        self.assertTrue(pdf.startswith(b'%PDF'),
                        "The evaluation report did not produce a PDF.")
        self.assertGreater(len(pdf), 5000)
