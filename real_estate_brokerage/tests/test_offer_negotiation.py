# -*- coding: utf-8 -*-
"""M11 / M12 — negotiation history, the owner's floor, competing offers."""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import BrokerageCommon


@tagged('post_install', '-at_install')
class TestNegotiationHistory(BrokerageCommon):
    """Every movement is kept. 0.1 overwrote them all."""

    def test_the_opening_offer_becomes_revision_one(self):
        offer = self._offer(amount=900000.0)

        self.assertEqual(offer.revision_count, 1)
        self.assertEqual(offer.revision_ids.revision_type, 'initial')
        self.assertEqual(offer.revision_ids.amount, 900000.0)

    def test_a_full_negotiation_is_reconstructable(self):
        """buyer 900k → seller 1.05M → buyer 980k → accepted."""
        offer = self._offer(amount=900000.0)

        offer.action_counter(1050000.0, note='Owner holding out')
        offer.action_buyer_revise(980000.0)
        offer.action_accept()

        history = offer.revision_ids.sorted('sequence')
        self.assertEqual(
            [(r.revision_type, r.amount) for r in history],
            [('initial', 900000.0),
             ('seller_counter', 1050000.0),
             ('buyer_counter', 980000.0),
             ('final', 980000.0)])

    def test_the_current_amount_still_reads_as_before(self):
        """Nothing that already reads `amount` may break."""
        offer = self._offer(amount=900000.0)

        offer.action_counter(1050000.0)

        self.assertEqual(offer.amount, 1050000.0)

    def test_movement_per_round_is_computed(self):
        offer = self._offer(amount=900000.0)
        offer.action_counter(1050000.0)
        offer.action_buyer_revise(980000.0)

        movements = offer.revision_ids.sorted('sequence').mapped('movement')

        self.assertEqual(movements, [0.0, 150000.0, -70000.0])

    def test_the_opening_position_and_total_movement_are_reportable(self):
        offer = self._offer(amount=900000.0)
        offer.action_counter(1050000.0)
        offer.action_buyer_revise(980000.0)

        self.assertEqual(offer.initial_amount, 900000.0)
        self.assertEqual(offer.total_movement, 80000.0)

    def test_history_cannot_be_edited(self):
        offer = self._offer(amount=900000.0)

        with self.assertRaises(UserError):
            offer.revision_ids.amount = 1.0

    def test_history_cannot_be_deleted(self):
        offer = self._offer(amount=900000.0)

        with self.assertRaises(UserError):
            offer.revision_ids.unlink()

    def test_a_note_may_still_be_added_afterwards(self):
        """Annotation is not revision."""
        offer = self._offer(amount=900000.0)

        offer.revision_ids.note = 'Buyer called to confirm'

        self.assertEqual(offer.revision_ids.note, 'Buyer called to confirm')

    def test_a_closed_offer_cannot_be_countered(self):
        offer = self._offer(amount=900000.0)
        offer.action_withdraw()

        with self.assertRaises(UserError):
            offer.action_counter(950000.0)

    def test_an_offer_of_nothing_is_refused(self):
        with self.assertRaises(ValidationError):
            self._offer(amount=0.0)


@tagged('post_install', '-at_install')
class TestOwnerFloor(BrokerageCommon):
    """The mandate names a minimum. 0.1 ignored it."""

    def test_an_offer_under_the_floor_needs_approval(self):
        listing = self._mandated_listing(minimum_price=950000.0)

        offer = self._offer(listing=listing, amount=900000.0)

        self.assertTrue(offer.below_floor)
        self.assertEqual(offer.approval_state, 'pending')

    def test_an_offer_over_the_floor_needs_none(self):
        listing = self._mandated_listing(minimum_price=950000.0)

        offer = self._offer(listing=listing, amount=1000000.0)

        self.assertFalse(offer.below_floor)
        self.assertEqual(offer.approval_state, 'not_required')

    def test_accepting_under_the_floor_unapproved_is_refused(self):
        listing = self._mandated_listing(minimum_price=950000.0)
        offer = self._offer(listing=listing, amount=900000.0)

        with self.assertRaises(UserError):
            offer.action_accept()

    def test_a_manager_can_approve_and_then_accept(self):
        listing = self._mandated_listing(minimum_price=950000.0)
        listing.action_activate()
        offer = self._offer(listing=listing, amount=900000.0)

        offer.action_approve(note='Owner agreed by phone')
        offer.action_accept()

        self.assertEqual(offer.state, 'accepted')
        self.assertEqual(offer.approved_by_id, self.env.user)

    def test_an_agent_cannot_approve_their_own_below_floor_offer(self):
        listing = self._mandated_listing(minimum_price=950000.0)
        offer = self._offer(listing=listing, amount=900000.0)

        with self.assertRaises(UserError):
            offer.with_user(self.agent).action_approve()

    def test_dropping_back_under_the_floor_voids_the_approval(self):
        """An approval given for 960k is not an approval for 900k."""
        listing = self._mandated_listing(minimum_price=950000.0)
        offer = self._offer(listing=listing, amount=940000.0)
        offer.action_approve()

        offer.action_buyer_revise(900000.0)

        self.assertEqual(offer.approval_state, 'pending')
        self.assertFalse(offer.approved_by_id)

    def test_rising_above_the_floor_clears_the_requirement(self):
        listing = self._mandated_listing(minimum_price=950000.0)
        offer = self._offer(listing=listing, amount=900000.0)

        offer.action_buyer_revise(1000000.0)

        self.assertEqual(offer.approval_state, 'not_required')

    def test_the_floor_itself_is_hidden_from_an_agent(self):
        """M22 — telling a buyer's agent the floor gives the margin away."""
        listing = self._mandated_listing(minimum_price=950000.0)
        offer = self._offer(listing=listing, amount=900000.0)

        as_agent = offer.with_user(self.agent)

        self.assertNotIn('floor_price', as_agent.read(['below_floor'])[0])
        self.assertTrue(as_agent.below_floor,
                        "The agent must still know approval is needed.")

    def test_a_mandate_that_refers_everything_cannot_accept_at_all(self):
        """No internal approval substitutes for authority never granted."""
        listing = self._mandated_listing(negotiation_authority='none')
        offer = self._offer(listing=listing, amount=1200000.0)

        with self.assertRaises(UserError):
            offer.action_accept()

    def test_a_manager_cannot_approve_past_a_refer_everything_mandate(self):
        listing = self._mandated_listing(negotiation_authority='none',
                                         minimum_price=950000.0)
        offer = self._offer(listing=listing, amount=900000.0)

        # Nothing to approve: the question is not seniority, it is authority.
        with self.assertRaises(UserError):
            offer.action_approve()

    def test_a_limited_mandate_with_no_stated_minimum_needs_approval(self):
        """"You may accept at or above the minimum" with no minimum given
        authorises nothing on its own."""
        listing = self._mandated_listing(
            negotiation_authority='above_minimum', minimum_price=0.0)

        offer = self._offer(listing=listing, amount=1200000.0)

        self.assertEqual(offer.approval_state, 'pending')


@tagged('post_install', '-at_install')
class TestCompetingOffers(BrokerageCommon):

    def test_open_offers_on_a_listing_see_each_other(self):
        listing = self._listing(activate=True)
        low = self._offer(listing=listing, amount=900000.0)
        high = self._offer(listing=listing, amount=1000000.0,
                           partner=self.other_buyer)

        self.assertEqual(low.competing_offer_count, 1)
        self.assertEqual(low.best_competing_amount, 1000000.0)
        self.assertFalse(low.is_best_offer)
        self.assertTrue(high.is_best_offer)

    def test_accepting_the_lower_offer_is_blocked(self):
        """0.1 rejected the higher one silently. That is how you lose a client."""
        listing = self._listing(activate=True)
        low = self._offer(listing=listing, amount=900000.0)
        self._offer(listing=listing, amount=1000000.0,
                    partner=self.other_buyer)

        with self.assertRaises(UserError):
            low.action_accept()

    def test_it_can_be_done_deliberately_with_a_reason(self):
        listing = self._listing(activate=True)
        low = self._offer(listing=listing, amount=900000.0)
        self._offer(listing=listing, amount=1000000.0,
                    partner=self.other_buyer)

        low.action_accept_over_higher('Cash buyer, no chain, 14-day close')

        self.assertEqual(low.state, 'accepted')
        self.assertTrue(any('Cash buyer' in (m.body or '')
                            for m in low.message_ids))

    def test_the_reason_is_not_optional(self):
        listing = self._listing(activate=True)
        low = self._offer(listing=listing, amount=900000.0)
        self._offer(listing=listing, amount=1000000.0,
                    partner=self.other_buyer)

        with self.assertRaises(UserError):
            low.action_accept_over_higher('')

    def test_accepting_the_highest_offer_needs_no_ceremony(self):
        listing = self._listing(activate=True)
        self._offer(listing=listing, amount=900000.0)
        high = self._offer(listing=listing, amount=1000000.0,
                           partner=self.other_buyer)

        high.action_accept()

        self.assertEqual(high.state, 'accepted')

    def test_accepting_closes_the_others(self):
        listing = self._listing(activate=True)
        loser = self._offer(listing=listing, amount=900000.0)
        winner = self._offer(listing=listing, amount=1000000.0,
                             partner=self.other_buyer)

        winner.action_accept()

        self.assertEqual(loser.state, 'rejected')
        self.assertEqual(loser.rejection_reason, 'other_offer_accepted')
