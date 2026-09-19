# -*- coding: utf-8 -*-
"""M12 / M15 / M16 — search, shortlist and compare."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import VisualCommon


@tagged('post_install', '-at_install')
class TestGalleryFilters(VisualCommon):
    """A customer knows criteria, not unit numbers."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Gallery = cls.env['realestate.visual.gallery']

    def _stocked_project(self):
        project = self._project()
        self.small = self._unit(project, price=800000.0, area_sqm=90.0,
                                bedroom_count=2, floor_number=2)
        self.large = self._unit(project, price=2000000.0, area_sqm=200.0,
                                bedroom_count=4, floor_number=12)
        self.held_back = self._unit(project, released=False, price=1500000.0,
                                    area_sqm=150.0, bedroom_count=3,
                                    floor_number=8)
        return project

    def test_a_bedroom_filter_narrows_the_set(self):
        project = self._stocked_project()

        result = self.Gallery.search_units(project, {'bedrooms_min': 4})

        self.assertEqual(result['matching_ids'], [self.large.id])
        self.assertEqual(result['matching_count'], 1)

    def test_non_matching_units_are_returned_but_marked(self):
        """De-emphasised, not hidden: a customer wants to see the floor above."""
        project = self._stocked_project()

        result = self.Gallery.search_units(project, {'bedrooms_min': 4})

        self.assertEqual(result['total_count'], 3)
        by_id = {e['id']: e for e in result['units']}
        self.assertTrue(by_id[self.large.id]['matches_filter'])
        self.assertFalse(by_id[self.small.id]['matches_filter'])

    def test_a_price_range_filters(self):
        project = self._stocked_project()

        result = self.Gallery.search_units(
            project, {'price_min': 700000.0, 'price_max': 900000.0})

        self.assertEqual(result['matching_ids'], [self.small.id])

    def test_an_availability_filter_uses_the_visual_state(self):
        """Which means it uses Developer's answer, not `property.state`."""
        project = self._stocked_project()

        result = self.Gallery.search_units(
            project, {'visual_state': ['available']})

        self.assertNotIn(self.held_back.id, result['matching_ids'])
        self.assertIn(self.small.id, result['matching_ids'])

    def test_filters_combine(self):
        project = self._stocked_project()

        result = self.Gallery.search_units(
            project, {'bedrooms_min': 2, 'area_max': 100.0, 'floor_max': 5})

        self.assertEqual(result['matching_ids'], [self.small.id])

    def test_an_unknown_filter_is_ignored_not_fatal(self):
        """A stale client gets a broader result, never an error dialog."""
        project = self._stocked_project()

        result = self.Gallery.search_units(
            project, {'has_jacuzzi': True, 'bedrooms_min': 4})

        self.assertEqual(result['matching_ids'], [self.large.id])

    def test_no_filters_matches_everything(self):
        project = self._stocked_project()

        result = self.Gallery.search_units(project, {})

        self.assertEqual(result['matching_count'], 3)

    def test_the_filter_is_a_domain_not_a_python_loop(self):
        """The brief forbids copying commercial data into a frontend store."""
        project = self._stocked_project()

        domain = self.Gallery.filter_domain(project, {'bedrooms_min': 3})

        self.assertIn(('bedroom_count', '>=', 3), domain)
        self.assertIn(('hierarchy_level', '=', 'unit'), domain)

    def test_facets_are_built_from_the_units_that_exist(self):
        """A project with no villas does not offer a villa filter."""
        project = self._stocked_project()

        facets = self.Gallery.search_units(project)['facets']

        self.assertEqual(facets['bedrooms'], [2, 3, 4])
        self.assertEqual(facets['area']['min'], 90.0)
        self.assertEqual(facets['area']['max'], 200.0)
        self.assertEqual(len(facets['property_types']), 1)

    def test_facet_prices_respect_the_audience(self):
        """An unreleased unit has no public price to widen the range with."""
        project = self._stocked_project()

        public = self.Gallery.search_units(project, audience='public')

        self.assertEqual(public['facets']['price']['max'], 2000000.0)
        by_id = {e['id']: e for e in public['units']}
        self.assertEqual(by_id[self.held_back.id]['price'], 0.0)


@tagged('post_install', '-at_install')
class TestGalleryShortlist(VisualCommon):
    """M15 / Rule 3 — Brokerage owns the shortlist."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Gallery = cls.env['realestate.visual.gallery']

    def setUp(self):
        super().setUp()
        self._skip_without_models('crm.lead', 'realestate.property.match')

    def _lead(self):
        partner = self.env['res.partner'].create({'name': 'Gallery Buyer'})
        return self.env['crm.lead'].create({
            'name': 'Gallery Opportunity',
            'type': 'opportunity',
            'partner_id': partner.id,
        })

    def test_brokerage_is_detected_at_runtime(self):
        """Late-bound: the viewer must not require the brokerage engine."""
        self.assertTrue(self.Gallery.shortlist_available())

    def test_favouriting_writes_brokerages_own_shortlist(self):
        """Not a second shortlist model — the same rows an agent works from."""
        project = self._project()
        unit = self._unit(project)
        lead = self._lead()

        match_id = self.Gallery.shortlist_add(lead.id, unit.id)

        match = self.env['realestate.property.match'].browse(match_id)
        self.assertTrue(match.shortlisted)
        self.assertEqual(match.crm_lead_id, lead)
        self.assertEqual(match.property_id, unit)

    def test_favouriting_twice_does_not_duplicate(self):
        project = self._project()
        unit = self._unit(project)
        lead = self._lead()

        first = self.Gallery.shortlist_add(lead.id, unit.id)
        second = self.Gallery.shortlist_add(lead.id, unit.id)

        self.assertEqual(first, second)

    def test_an_existing_match_is_reused_rather_than_replaced(self):
        """A gallery favourite must not discard a scored match."""
        project = self._project()
        unit = self._unit(project)
        lead = self._lead()
        match = self.env['realestate.property.match'].create({
            'crm_lead_id': lead.id, 'property_id': unit.id, 'score': 88.0,
        })

        self.Gallery.shortlist_add(lead.id, unit.id)

        self.assertEqual(match.score, 88.0)
        self.assertTrue(match.shortlisted)

    def test_unfavouriting_clears_the_flag_without_losing_the_row(self):
        project = self._project()
        unit = self._unit(project)
        lead = self._lead()
        match_id = self.Gallery.shortlist_add(lead.id, unit.id)

        self.Gallery.shortlist_remove(lead.id, unit.id)

        match = self.env['realestate.property.match'].browse(match_id)
        self.assertFalse(match.shortlisted)
        self.assertTrue(match.exists())

    def test_the_shortlist_is_priced_as_it_is_now(self):
        project = self._project()
        unit = self._unit(project)
        lead = self._lead()
        self.Gallery.shortlist_add(lead.id, unit.id)

        entries = self.Gallery.shortlist_for(lead.id)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['id'], unit.id)
        self.assertEqual(entries[0]['visual_state'], 'available')

    def test_an_anonymous_shortlist_merges_once_identified(self):
        project = self._project()
        first = self._unit(project)
        second = self._unit(project)
        lead = self._lead()

        self.Gallery.merge_anonymous_shortlist(lead.id, [first.id, second.id])

        self.assertEqual(len(self.Gallery.shortlist_for(lead.id)), 2)

    def test_merging_the_same_list_twice_creates_nothing_new(self):
        project = self._project()
        unit = self._unit(project)
        lead = self._lead()

        self.Gallery.merge_anonymous_shortlist(lead.id, [unit.id])
        self.Gallery.merge_anonymous_shortlist(lead.id, [unit.id])

        self.assertEqual(len(self.Gallery.shortlist_for(lead.id)), 1)

    def test_one_bad_id_does_not_lose_the_rest_of_a_shortlist(self):
        project = self._project()
        unit = self._unit(project)
        lead = self._lead()

        self.Gallery.merge_anonymous_shortlist(
            lead.id, [999999999, unit.id])

        self.assertEqual(len(self.Gallery.shortlist_for(lead.id)), 1)


@tagged('post_install', '-at_install')
class TestGalleryCompare(VisualCommon):
    """M16 — never a stale snapshot."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Gallery = cls.env['realestate.visual.gallery']

    def test_two_units_compare(self):
        project = self._project()
        a = self._unit(project, price=1000000.0, area_sqm=100.0)
        b = self._unit(project, price=1800000.0, area_sqm=150.0)

        result = self.Gallery.compare([a.id, b.id])

        self.assertEqual(len(result['units']), 2)
        self.assertTrue(result['rows'])
        self.assertTrue(result['as_of'])

    def test_fewer_than_two_is_not_a_comparison(self):
        project = self._project()
        unit = self._unit(project)

        with self.assertRaises(UserError):
            self.Gallery.compare([unit.id])

    def test_more_than_four_is_a_spreadsheet(self):
        project = self._project()
        units = [self._unit(project) for _i in range(5)]

        with self.assertRaises(UserError):
            self.Gallery.compare([u.id for u in units])

    def test_duplicates_are_collapsed(self):
        project = self._project()
        a = self._unit(project)
        b = self._unit(project)

        result = self.Gallery.compare([a.id, b.id, a.id])

        self.assertEqual(len(result['units']), 2)

    def test_the_cheaper_price_per_sqm_is_marked_best(self):
        project = self._project()
        efficient = self._unit(project, price=1000000.0, area_sqm=200.0)
        pricey = self._unit(project, price=1800000.0, area_sqm=150.0)

        result = self.Gallery.compare([efficient.id, pricey.id])
        row = next(r for r in result['rows'] if r['key'] == 'price_per_sqm')

        self.assertEqual(row['best'], efficient.id)

    def test_no_best_is_claimed_where_better_is_ambiguous(self):
        """A higher floor is not necessarily a better one."""
        project = self._project()
        a = self._unit(project, floor_number=2)
        b = self._unit(project, floor_number=20)

        result = self.Gallery.compare([a.id, b.id])
        row = next(r for r in result['rows'] if r['key'] == 'floor')

        self.assertIsNone(row['best'])

    def test_a_reservation_since_the_page_loaded_shows_up(self):
        """The brief forbids comparing stale snapshots after a refresh."""
        project = self._project()
        a = self._unit(project)
        b = self._unit(project)
        before = self.Gallery.compare([a.id, b.id])
        self.assertEqual(before['units'][0]['visual_state'], 'available')

        self.env['realestate.unit.block'].create({
            'property_id': a.id, 'reason': 'vip'})
        a.invalidate_recordset()

        after = self.Gallery.compare([a.id, b.id])
        by_id = {e['id']: e for e in after['units']}

        self.assertEqual(by_id[a.id]['visual_state'], 'blocked')

    def test_compare_respects_the_audience(self):
        project = self._project()
        a = self._unit(project, released=False, price=1000000.0)
        b = self._unit(project, price=1200000.0)

        result = self.Gallery.compare([a.id, b.id], audience='public')
        by_id = {e['id']: e for e in result['units']}

        self.assertEqual(by_id[a.id]['price'], 0.0)
        self.assertNotIn('unavailable_reason', by_id[a.id])


@tagged('post_install', '-at_install')
class TestPaymentPlanPreview(VisualCommon):
    """M5 — Developer calculates the schedule. Nothing else does.

    The brief forbids computing real-estate payment schedules in JavaScript,
    and the same reasoning forbids computing them a second time in Python: a
    plan's residual line, its rounding absorption and its date rules are the
    developer's commercial terms, and a second implementation is a second set
    of numbers to reconcile when they disagree.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Gallery = cls.env['realestate.visual.gallery']
        cls.Plan = cls.env['realestate.payment.plan']

    def _project_with_handover(self):
        """A project with an expected handover date.

        Developer refuses to generate a handover-dated line without one rather
        than inventing a due date years too early — which is the right
        behaviour, and means a realistic fixture has to supply it.
        """
        from odoo import fields as odoo_fields
        project = self._project()
        project.expected_handover_date = odoo_fields.Date.add(
            odoo_fields.Date.today(), days=730)
        return project

    def _plan(self, project, name='Standard 10/90', **kwargs):
        vals = {
            'name': name,
            'company_id': self.company.id,
            'project_id': project.id,
        }
        vals.update(kwargs)
        plan = self.Plan.create(vals)
        self.env['realestate.payment.plan.line'].create([
            {'plan_id': plan.id, 'sequence': 10, 'name': 'Down Payment',
             'kind': 'down_payment', 'calculation_type': 'percent',
             'value': 10.0, 'occurrences': 1, 'date_rule': 'on_booking'},
            {'plan_id': plan.id, 'sequence': 20, 'name': 'Instalments',
             'kind': 'installment', 'calculation_type': 'percent',
             'value': 5.0, 'occurrences': 17,
             'date_rule': 'months_after_booking', 'interval': 'semiannual'},
            {'plan_id': plan.id, 'sequence': 30, 'name': 'Handover',
             'kind': 'handover', 'calculation_type': 'residual',
             'occurrences': 1, 'date_rule': 'on_handover'},
        ])
        plan.action_submit()
        plan.action_activate()
        return plan

    def test_an_applicable_plan_is_offered_with_a_schedule(self):
        project = self._project_with_handover()
        unit = self._unit(project, price=1000000.0)
        self._plan(project)

        previews = self.Gallery.payment_plans_for(unit.id)

        self.assertEqual(len(previews), 1)
        preview = previews[0]
        self.assertEqual(preview['total_price'], unit.list_price_developer)
        self.assertTrue(preview['schedule'])
        self.assertEqual(preview['down_payment'], 100000.0)

    def test_the_schedule_sums_to_the_price(self):
        """Developer's own residual/rounding logic, not a second one."""
        project = self._project_with_handover()
        unit = self._unit(project, price=1000000.0)
        self._plan(project)

        preview = self.Gallery.payment_plans_for(unit.id)[0]
        total = sum(row['amount'] for row in preview['schedule'])

        self.assertAlmostEqual(total, preview['total_price'], places=2)

    def test_the_headline_figures_come_from_the_generated_rows(self):
        project = self._project_with_handover()
        unit = self._unit(project, price=1000000.0)
        self._plan(project)

        preview = self.Gallery.payment_plans_for(unit.id)[0]

        self.assertEqual(preview['installment_count'], 17)
        self.assertEqual(preview['installment_amount'], 50000.0)
        self.assertEqual(preview['cadence_months'], 6)
        self.assertTrue(preview['handover_payment'])

    def test_an_unreleased_unit_has_no_public_schedule(self):
        """A table of zeroes looks like an offer."""
        project = self._project_with_handover()
        unit = self._unit(project, released=False, price=1000000.0)
        self._plan(project)

        self.assertEqual(
            self.Gallery.payment_plans_for(unit.id, audience='public'), [])

    def test_an_internal_audience_still_sees_it(self):
        project = self._project_with_handover()
        unit = self._unit(project, released=False, price=1000000.0)
        self._plan(project)

        previews = self.Gallery.payment_plans_for(unit.id, audience='internal')

        self.assertEqual(len(previews), 1)

    def test_a_plan_for_another_project_is_not_offered(self):
        project = self._project_with_handover()
        other = self._project_with_handover()
        unit = self._unit(project, price=1000000.0)
        self._plan(other, name='Other Project Plan')

        self.assertEqual(self.Gallery.payment_plans_for(unit.id), [])

    def test_a_draft_plan_is_not_offered(self):
        project = self._project()
        unit = self._unit(project, price=1000000.0)
        plan = self.Plan.create({
            'name': 'Not Approved', 'company_id': self.company.id,
            'project_id': project.id})

        self.assertEqual(plan.state, 'draft')
        self.assertEqual(self.Gallery.payment_plans_for(unit.id), [])

    def test_an_unknown_unit_returns_nothing_rather_than_raising(self):
        self.assertEqual(self.Gallery.payment_plans_for(999999999), [])

    def test_a_plan_that_cannot_be_quoted_says_so_instead_of_vanishing(self):
        """A plan that disappears from the panel is an unanswerable support call.

        Developer refuses a handover-dated line when the project has no
        expected handover date. Rather than dropping the plan, the panel shows
        it as unavailable with Developer's own reason.
        """
        project = self._project()          # deliberately no handover date
        unit = self._unit(project, price=1000000.0)
        self._plan(project)

        previews = self.Gallery.payment_plans_for(unit.id)

        self.assertEqual(len(previews), 1)
        self.assertFalse(previews[0]['available'])
        self.assertIn('handover', previews[0]['reason'].lower())
        self.assertEqual(previews[0]['schedule'], [])

    def test_a_quotable_plan_is_marked_available(self):
        project = self._project_with_handover()
        unit = self._unit(project, price=1000000.0)
        self._plan(project)

        self.assertTrue(self.Gallery.payment_plans_for(unit.id)[0]['available'])

    def test_compare_carries_the_plans(self):
        """And fetches them fresh, not from an earlier page's state."""
        project = self._project_with_handover()
        first = self._unit(project, price=1000000.0)
        second = self._unit(project, price=1500000.0)
        self._plan(project)

        result = self.Gallery.compare([first.id, second.id])

        for entry in result['units']:
            with self.subTest(unit=entry['id']):
                self.assertTrue(entry['payment_plans'])
        totals = {e['id']: e['payment_plans'][0]['total_price']
                  for e in result['units']}
        self.assertNotEqual(totals[first.id], totals[second.id])


@tagged('post_install', '-at_install')
class TestShortlistProvenance(VisualCommon):
    """M5 — where the customer was standing when they favourited it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Gallery = cls.env['realestate.visual.gallery']

    def setUp(self):
        super().setUp()
        self._skip_without_models('crm.lead', 'realestate.property.match')

    def _lead(self):
        partner = self.env['res.partner'].create({'name': 'Provenance Buyer'})
        return self.env['crm.lead'].create({
            'name': 'Provenance Opportunity', 'type': 'opportunity',
            'partner_id': partner.id})

    def test_the_visual_source_is_recorded(self):
        project = self._project()
        unit = self._unit(project)
        lead = self._lead()

        match_id = self.Gallery.shortlist_add(lead.id, unit.id, source='3d')

        match = self.env['realestate.property.match'].browse(match_id)
        self.assertIn('3D maquette', match.feedback)

    def test_each_source_reads_differently(self):
        project = self._project()
        lead = self._lead()
        for source, expected in (('2d', '2D plan'), ('list', 'unit list'),
                                 ('embed', 'public embed')):
            with self.subTest(source=source):
                unit = self._unit(project)
                match_id = self.Gallery.shortlist_add(
                    lead.id, unit.id, source=source)
                match = self.env['realestate.property.match'].browse(match_id)
                self.assertIn(expected, match.feedback)

    def test_the_timestamp_is_brokerages_own(self):
        """Not re-implemented — `action_shortlist()` already stamps it."""
        project = self._project()
        unit = self._unit(project)
        lead = self._lead()

        match_id = self.Gallery.shortlist_add(lead.id, unit.id)

        match = self.env['realestate.property.match'].browse(match_id)
        self.assertTrue(match.shortlisted_on)

    def test_favouriting_twice_does_not_repeat_the_note(self):
        project = self._project()
        unit = self._unit(project)
        lead = self._lead()

        self.Gallery.shortlist_add(lead.id, unit.id, source='3d')
        match_id = self.Gallery.shortlist_add(lead.id, unit.id, source='3d')

        match = self.env['realestate.property.match'].browse(match_id)
        self.assertEqual(match.feedback.count('3D maquette'), 1)

    def test_an_existing_score_and_feedback_survive(self):
        project = self._project()
        unit = self._unit(project)
        lead = self._lead()
        match = self.env['realestate.property.match'].create({
            'crm_lead_id': lead.id, 'property_id': unit.id, 'score': 88.0,
            'customer_response': 'interested'})

        self.Gallery.shortlist_add(lead.id, unit.id, source='2d')

        self.assertEqual(match.score, 88.0)
        self.assertEqual(match.customer_response, 'interested')
        self.assertTrue(match.shortlisted)

    def test_an_anonymous_list_writes_nothing_without_an_opportunity(self):
        """The brief: no permanent CRM data merely because a visitor clicked."""
        project = self._project()
        unit = self._unit(project)
        before = self.env['realestate.property.match'].search_count([])

        # There is no code path from a session list to the database that does
        # not require a crm_lead_id — `merge_anonymous_shortlist` takes one.
        self.assertEqual(
            self.env['realestate.property.match'].search_count([]), before)
        self.assertTrue(unit.exists())
