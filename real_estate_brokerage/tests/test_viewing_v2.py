# -*- coding: utf-8 -*-
"""M10 — the viewing lifecycle, reschedule chain and structured outcome."""

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import BrokerageCommon


@tagged('post_install', '-at_install')
class TestViewingLifecycle(BrokerageCommon):

    def test_a_suggestion_needs_no_date(self):
        """The lifecycle starts before the appointment exists."""
        lead = self._opportunity()
        listing = self._listing()

        viewing = self.Viewing.create({
            'listing_id': listing.id,
            'crm_lead_id': lead.id,
            'state': 'suggested',
        })

        self.assertFalse(viewing.scheduled_at)

    def test_anything_past_a_suggestion_needs_a_time(self):
        listing = self._listing()

        with self.assertRaises(UserError):
            self.Viewing.create({
                'listing_id': listing.id, 'state': 'scheduled',
            })

    def test_the_full_happy_path(self):
        viewing = self._viewing(state='suggested',
                                scheduled_at=fields.Datetime.now())

        viewing.action_schedule()
        self.assertEqual(viewing.state, 'scheduled')

        viewing.action_confirm()
        self.assertEqual(viewing.state, 'confirmed')
        self.assertTrue(viewing.confirmed_on)
        self.assertEqual(viewing.confirmed_by_id, self.env.user)

        viewing.outcome = 'interested'
        viewing.action_mark_completed()
        self.assertEqual(viewing.state, 'completed')
        self.assertTrue(viewing.completed_on)

    def test_confirmation_is_distinct_from_scheduling(self):
        """The gap between the two is where no-shows are born."""
        viewing = self._viewing()

        self.assertEqual(viewing.state, 'scheduled')
        self.assertFalse(viewing.confirmed_on,
                         "Scheduling silently counted as confirmation.")

    def test_completing_demands_a_verdict(self):
        viewing = self._viewing()

        with self.assertRaises(UserError):
            viewing.action_mark_completed()

    def test_a_completed_viewing_is_history(self):
        viewing = self._viewing(outcome='interested')
        viewing.action_mark_completed()

        with self.assertRaises(UserError):
            viewing.action_cancel()

    def test_a_cancelled_viewing_cannot_be_revived(self):
        viewing = self._viewing()
        viewing.action_cancel()

        with self.assertRaises(UserError):
            viewing.action_confirm()

    def test_a_suggestion_cannot_skip_to_completed(self):
        viewing = self._viewing(state='suggested',
                                scheduled_at=fields.Datetime.now(),
                                outcome='interested')

        with self.assertRaises(UserError):
            viewing.action_mark_completed()

    def test_no_show_is_reachable_from_confirmed(self):
        """The most expensive no-show is the one they had confirmed."""
        viewing = self._viewing()
        viewing.action_confirm()

        viewing.action_mark_no_show()

        self.assertEqual(viewing.state, 'no_show')


@tagged('post_install', '-at_install')
class TestReschedule(BrokerageCommon):
    """Rescheduling supersedes; it never edits."""

    def test_rescheduling_creates_a_successor(self):
        viewing = self._viewing()
        later = fields.Datetime.add(viewing.scheduled_at, days=3)

        successor = viewing.action_reschedule(later)

        self.assertEqual(viewing.state, 'rescheduled')
        self.assertEqual(viewing.rescheduled_to_id, successor)
        self.assertEqual(successor.rescheduled_from_id, viewing)
        self.assertEqual(successor.state, 'scheduled')
        self.assertEqual(successor.scheduled_at, later)

    def test_the_original_appointment_is_not_overwritten(self):
        """"They moved it four times" is a fact worth keeping."""
        viewing = self._viewing()
        original_time = viewing.scheduled_at

        viewing.action_reschedule(
            fields.Datetime.add(original_time, days=3))

        self.assertEqual(viewing.scheduled_at, original_time)

    def test_the_chain_counts_itself(self):
        first = self._viewing()
        second = first.action_reschedule(
            fields.Datetime.add(first.scheduled_at, days=1))
        third = second.action_reschedule(
            fields.Datetime.add(second.scheduled_at, days=1))

        self.assertEqual(first.reschedule_count, 0)
        self.assertEqual(second.reschedule_count, 1)
        self.assertEqual(third.reschedule_count, 2)

    def test_a_successor_starts_with_a_clean_verdict(self):
        """A confirmation for the old slot is not one for the new slot."""
        viewing = self._viewing()
        viewing.action_confirm()

        successor = viewing.action_reschedule(
            fields.Datetime.add(viewing.scheduled_at, days=2))

        self.assertFalse(successor.confirmed_on)
        self.assertFalse(successor.outcome)

    def test_rescheduling_needs_a_new_time(self):
        viewing = self._viewing()

        with self.assertRaises(UserError):
            viewing.action_reschedule()

    def test_a_rescheduled_viewing_is_closed(self):
        viewing = self._viewing()
        viewing.action_reschedule(
            fields.Datetime.add(viewing.scheduled_at, days=1))

        with self.assertRaises(UserError):
            viewing.action_confirm()


@tagged('post_install', '-at_install')
class TestViewingFeedbackLoop(BrokerageCommon):
    """M9 ↔ M10 — the verdict flows back without anybody retyping it."""

    def test_a_viewing_can_be_raised_from_a_shortlist_entry(self):
        lead = self._opportunity()
        unit = self._unit(price=1000000.0)
        self._listing(unit=unit)
        match = lead._re_run_matching().filtered(
            lambda m: m.property_id == unit)

        match.action_suggest_viewing()

        self.assertEqual(match.viewing_count, 1)
        self.assertEqual(match.viewing_ids.state, 'suggested')
        self.assertEqual(match.viewing_ids.crm_lead_id, lead)

    def test_a_completed_viewing_answers_the_shortlist(self):
        match, viewing = self._match_and_viewing()

        viewing.outcome = 'very_interested'
        viewing.action_mark_completed()

        self.assertEqual(match.customer_response, 'favorite')

    def test_a_rejection_carries_its_reason_across(self):
        match, viewing = self._match_and_viewing()

        viewing.write({'outcome': 'not_interested',
                       'rejection_reason': 'layout'})
        viewing.action_mark_completed()

        self.assertEqual(match.customer_response, 'rejected')
        self.assertEqual(match.rejection_reason, 'layout')

    def test_an_agents_summary_does_not_overwrite_the_customer(self):
        """The customer's own words outrank an agent's summary of them."""
        match, viewing = self._match_and_viewing()
        match.customer_response = 'favorite'

        viewing.outcome = 'not_interested'
        viewing.action_mark_completed()

        self.assertEqual(match.customer_response, 'favorite')

    def test_a_property_with_no_listing_cannot_be_viewed(self):
        lead = self._opportunity()
        unit = self._unit(price=1000000.0)
        match = lead._re_run_matching().filtered(
            lambda m: m.property_id == unit)

        with self.assertRaises(UserError):
            match.action_suggest_viewing()

    # ------------------------------------------------------------------
    def _match_and_viewing(self):
        lead = self._opportunity()
        unit = self._unit(price=1000000.0)
        self._listing(unit=unit)
        match = lead._re_run_matching().filtered(
            lambda m: m.property_id == unit)
        viewing = self.Viewing._create_from_match(
            match, scheduled_at=fields.Datetime.now())
        return match, viewing
