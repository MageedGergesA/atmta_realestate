# -*- coding: utf-8 -*-
"""M8 / M9 — matching, scoring and the shortlist."""

from odoo.tests import tagged

from .common import BrokerageCommon


@tagged('post_install', '-at_install')
class TestMatchingFilter(BrokerageCommon):
    """The database pre-filter — what never reaches Python."""

    def test_unavailable_inventory_is_not_a_candidate(self):
        """Availability is hard: an unreleased unit is not a near miss."""
        lead = self._opportunity()
        held_back = self._unit(released=False, price=1000000.0)

        candidates = lead._re_candidate_properties()

        self.assertNotIn(held_back, candidates,
                         "An unreleased unit was offered to a customer.")

    def test_the_filter_is_a_domain_not_a_python_loop(self):
        """M29 — candidates come back from a search, bounded by `limit`.

        Guards the actual requirement: pre-filter in the database. If somebody
        later replaces the search with a browse-everything-and-filter, the
        limit stops being honoured and this fails.
        """
        lead = self._opportunity()
        for _i in range(5):
            self._unit(price=1000000.0)

        self.assertEqual(len(lead._re_candidate_properties(limit=3)), 3)

    def test_a_wildly_overpriced_unit_is_filtered_out(self):
        lead = self._opportunity()  # budget 800k – 1.2M
        self._unit(price=1000000.0)
        too_dear = self._unit(price=9000000.0)

        self.assertNotIn(too_dear, lead._re_candidate_properties())

    def test_slightly_over_budget_still_reaches_scoring(self):
        """A 10% stretch is a conversation, not a disqualification."""
        lead = self._opportunity()
        stretch = self._unit(price=1300000.0)  # ceiling 1.2M, +8%

        self.assertIn(stretch, lead._re_candidate_properties())

    def test_named_projects_exclude_everything_else(self):
        other_project = self.env['realestate.project'].create({
            'name': 'Other Project', 'code': 'OTH',
            'company_id': self.company.id, 'commercial_state': 'selling',
        })
        lead = self._opportunity(re_project_ids=[(6, 0, other_project.ids)])
        ours = self._unit(price=1000000.0)

        self.assertNotIn(ours, lead._re_candidate_properties(),
                         "A customer who named a project was shown another.")


@tagged('post_install', '-at_install')
class TestMatchScoring(BrokerageCommon):
    """The score, and the sentence that explains it."""

    def test_a_perfect_fit_scores_a_hundred(self):
        lead = self._opportunity(re_area_min=100.0, re_area_max=150.0)
        unit = self._unit(price=1000000.0)  # 120 sqm, 3 bed

        score, criteria = lead._re_score_property(unit)

        self.assertEqual(score, 100.0)
        self.assertTrue(all(c['verdict'] == 'hit' for c in criteria.values()))

    def test_every_criterion_carries_its_own_sentence(self):
        """Explainability is the requirement, not a nicety."""
        lead = self._opportunity()
        unit = self._unit(price=1000000.0)

        _score, criteria = lead._re_score_property(unit)

        for name, entry in criteria.items():
            self.assertTrue(entry.get('label'), "%s has no label" % name)
            self.assertTrue(entry.get('detail'), "%s explains nothing" % name)
            self.assertIn(entry['verdict'], ('hit', 'partial', 'miss'))

    def test_scoring_is_deterministic(self):
        """Same inputs, same score — no randomness, no learned weights."""
        lead = self._opportunity()
        unit = self._unit(price=1000000.0)

        first, _c = lead._re_score_property(unit)
        second, _c = lead._re_score_property(unit)

        self.assertEqual(first, second)

    def test_an_unexpressed_criterion_costs_the_property_nothing(self):
        """A sparse brief must not drag every score down.

        The denominator is only what the customer actually asked for, so a
        two-line brief and a ten-line brief are equally satisfiable.
        """
        lead = self._opportunity(re_floor_min=0, re_bathrooms_min=0)
        unit = self._unit(price=1000000.0)

        score, criteria = lead._re_score_property(unit)

        self.assertNotIn('floor', criteria)
        self.assertNotIn('bathrooms', criteria)
        self.assertEqual(score, 100.0)

    def test_a_miss_costs_exactly_its_weight(self):
        """Arithmetic, verifiable by hand — that is what explainable means."""
        lead = self._opportunity(re_bedrooms_min=5)   # weight 15
        unit = self._unit(price=1000000.0)            # 3 bedrooms → miss

        score, criteria = lead._re_score_property(unit)

        self.assertEqual(criteria['bedrooms']['verdict'], 'miss')
        # budget 30 + type 10 earned, bedrooms 15 lost, of 55 available.
        self.assertEqual(score, round(40 / 55 * 100, 1))

    def test_a_near_miss_earns_partial_credit(self):
        lead = self._opportunity(re_bedrooms_min=4)   # unit has 3
        unit = self._unit(price=1000000.0)

        _score, criteria = lead._re_score_property(unit)

        self.assertEqual(criteria['bedrooms']['verdict'], 'partial',
                         "One bedroom short was treated as a flat rejection.")

    def test_a_criterion_the_inventory_cannot_answer_is_not_a_miss(self):
        """No model in the suite records a view, so view cannot be scored.

        Scoring it a miss would punish every unit for a data point the system
        does not hold and skew every score the same way. It drops out instead —
        and the gap is reported, not hidden.
        """
        lead = self._opportunity(re_view_preference='sea')
        unit = self._unit(price=1000000.0)

        score, criteria = lead._re_score_property(unit)

        self.assertNotIn('view', criteria)
        self.assertEqual(score, 100.0)

    def test_an_empty_brief_scores_zero_and_says_why(self):
        lead = self.Lead.create({
            'name': 'No brief', 'type': 'opportunity',
            'partner_id': self.buyer.id,
        })
        unit = self._unit(price=1000000.0)

        score, criteria = lead._re_score_property(unit)

        self.assertEqual(score, 0.0)
        self.assertIn('_note', criteria)


@tagged('post_install', '-at_install')
class TestMatchRecords(BrokerageCommon):
    """The match record — what was shown, and when."""

    def test_running_matching_records_what_was_found(self):
        lead = self._opportunity()
        unit = self._unit(price=1000000.0)

        matches = lead._re_run_matching()

        self.assertIn(unit, matches.property_id)
        match = matches.filtered(lambda m: m.property_id == unit)
        self.assertTrue(match.criteria_json,
                        "The reasoning was not preserved.")
        self.assertTrue(match.matched_on)

    def test_the_criteria_snapshot_survives_the_brief_changing(self):
        """The question is 'what did you offer them in March?'

        A recomputed list answers a different question, so the snapshot must
        not silently follow the requirement when it moves.
        """
        lead = self._opportunity()
        unit = self._unit(price=1000000.0)
        match = lead._re_run_matching().filtered(
            lambda m: m.property_id == unit)
        original = dict(match.criteria_json)

        lead.re_bedrooms_min = 9

        self.assertEqual(match.criteria_json, original)

    def test_rerunning_does_not_discard_the_customers_answer(self):
        """The customer's own verdict outranks a fresh score."""
        lead = self._opportunity()
        unit = self._unit(price=1000000.0)
        match = lead._re_run_matching().filtered(
            lambda m: m.property_id == unit)
        match.write({'customer_response': 'rejected',
                     'rejection_reason': 'layout'})

        lead._re_run_matching()

        self.assertEqual(match.customer_response, 'rejected')
        self.assertEqual(match.rejection_reason, 'layout')

    def test_a_property_is_matched_to_a_lead_only_once(self):
        lead = self._opportunity()
        self._unit(price=1000000.0)

        first = lead._re_run_matching()
        second = lead._re_run_matching()

        self.assertEqual(first.ids, second.ids,
                         "Re-running created duplicate matches.")

    def test_the_explanation_reads_as_sentences(self):
        lead = self._opportunity()
        unit = self._unit(price=1000000.0)
        match = lead._re_run_matching().filtered(
            lambda m: m.property_id == unit)

        text = match.explanation

        self.assertIn('Budget', text)
        self.assertIn('✓', text)

    def test_an_external_listing_match_remembers_its_listing(self):
        lead = self._opportunity()
        listing = self._mandated_listing()
        listing.action_activate()

        matches = lead._re_run_matching()
        match = matches.filtered(lambda m: m.property_id == listing.property_id)

        self.assertEqual(match.listing_id, listing)


@tagged('post_install', '-at_install')
class TestShortlist(BrokerageCommon):
    """M9 — the shortlist spans both inventory channels."""

    def test_shortlisting_stamps_when(self):
        lead = self._opportunity()
        self._unit(price=1000000.0)
        match = lead._re_run_matching()[0]

        match.action_shortlist()

        self.assertTrue(match.shortlisted)
        self.assertTrue(match.shortlisted_on)

    def test_a_shortlist_holds_developer_units_and_external_listings(self):
        """One list, both channels — that is the requirement."""
        lead = self._opportunity()
        developer_unit = self._unit(price=1000000.0)
        external = self._mandated_listing()
        external.action_activate()

        matches = lead._re_run_matching()
        matches.action_shortlist()

        shortlisted = matches.filtered('shortlisted')
        self.assertIn(developer_unit, shortlisted.property_id)
        self.assertIn(external.property_id, shortlisted.property_id)

    def test_lead_counters_are_grouped_not_per_record(self):
        lead_a = self._opportunity()
        lead_b = self._opportunity(partner=self.other_buyer)
        self._unit(price=1000000.0)
        lead_a._re_run_matching().action_shortlist()
        lead_b._re_run_matching()

        (lead_a | lead_b).invalidate_recordset()

        self.assertEqual(lead_a.shortlist_count, 1)
        self.assertEqual(lead_b.shortlist_count, 0)
        self.assertTrue(lead_b.match_count)
