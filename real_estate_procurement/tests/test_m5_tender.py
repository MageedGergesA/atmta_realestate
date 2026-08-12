# -*- coding: utf-8 -*-
"""M5 — the five tests written before the implementation.

Each one fixes a boundary the milestone exists to hold:

```
    A  tendering moves no money
    B  invitations become native Odoo alternative RFQs, and none confirms
    C  M4 decides who may be invited, server-side
    D  what a vendor submitted survives the operational RFQ changing
    E  a tender RFQ cannot reach commitment before an M7 award exists
```
"""

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import M5Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5TenderMovesNoMoney(M5Common):
    """A — the financial invariant, asserted at every lifecycle step."""

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        # 1,000 units at 3,000 = the same 3M of authorised demand, but
        # every bid in these tests divides into a price the currency can
        # actually express. At 3,000 units a 3.2M bid is 1066.6667 a unit,
        # which rounds to a 3,200,010 total and would have these tests
        # arguing with two-decimal money instead of with the product.
        self.request = self._demand(1_000, code=self.concrete,
                                    unit=3_000.0)

    def _position(self):
        return (self._reserved(self.project, self.concrete),
                self._commitment(self.project),
                self._actual(self.project))

    def test_a_no_step_of_a_tender_moves_money(self):
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Approved demand should hold a reservation and "
                         "nothing else before the tender starts.")

        event = self._event(self.request)
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Creating a tender moved money.")

        self._publish(event, [self.vendor_a, self.vendor_b, self.vendor_c])
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Publishing a tender moved money.")

        for invitation, amount in zip(event.invitation_ids,
                                      (2_800_000.0, 3_100_000.0,
                                       2_900_000.0)):
            self._bid(invitation, amount)
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Receiving bids moved money.")

        event.action_close()
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Closing a tender moved money.")

    def test_a_a_cheap_bid_does_not_release_authorisation(self):
        """2.8M quoted against 3M authorised is not a 200k saving yet."""
        event = self._event(self.request)
        self._publish(event, [self.vendor_a])
        self._bid(event.invitation_ids, 2_800_000.0)

        self.assertEqual(self._reserved(self.project, self.concrete),
                         3_000_000.0,
                         "A bid below authorisation released reservation "
                         "before anybody awarded anything.")

    def test_a_an_expensive_bid_does_not_expand_authorisation(self):
        event = self._event(self.request)
        self._publish(event, [self.vendor_a])
        response = self._bid(event.invitation_ids, 3_500_000.0)

        self.assertEqual(self._reserved(self.project, self.concrete),
                         3_000_000.0,
                         "A bid above authorisation grew the reservation.")
        self.assertEqual(response.amount_untaxed, 3_500_000.0)
        self.assertTrue(response.above_authorisation,
                        "A bid over the authorised amount must be flagged, "
                        "not silently accommodated.")


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5NativeAlternatives(M5Common):
    """B — three invitations, three native RFQs, one native group, none confirmed."""

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        # 1,000 units at 3,000 = the same 3M of authorised demand, but
        # every bid in these tests divides into a price the currency can
        # actually express. At 3,000 units a 3.2M bid is 1066.6667 a unit,
        # which rounds to a 3,200,010 total and would have these tests
        # arguing with two-decimal money instead of with the product.
        self.request = self._demand(1_000, code=self.concrete,
                                    unit=3_000.0)

    def test_b_three_invitations_create_three_native_alternative_rfqs(self):
        event = self._event(self.request)
        self._publish(event, [self.vendor_a, self.vendor_b, self.vendor_c])

        orders = event.invitation_ids.purchase_order_id
        self.assertEqual(len(orders), 3, "One RFQ per invited vendor.")
        self.assertEqual(orders.partner_id,
                         self.vendor_a | self.vendor_b | self.vendor_c)

        # Native grouping — Odoo's own plumbing, not a field of ours.
        self.assertEqual(len(orders.purchase_group_id), 1,
                         "The three RFQs are not in one native alternative "
                         "group, so Odoo's compare view cannot see them "
                         "together.")
        for order in orders:
            self.assertEqual(order.alternative_po_ids, orders,
                             "Each RFQ must see the other alternatives.")

    def test_b_no_rfq_is_confirmed_by_sourcing(self):
        event = self._event(self.request)
        self._publish(event, [self.vendor_a, self.vendor_b, self.vendor_c])

        orders = event.invitation_ids.purchase_order_id
        self.assertEqual(set(orders.mapped('state')), {'draft'},
                         "Sourcing confirmed a purchase order.")
        self.assertEqual(self._commitment(self.project), 0.0)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5InvitationEligibility(M5Common):
    """C — M4 decides who may take part, and the decision is server-side."""

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        # 1,000 units at 3,000 = the same 3M of authorised demand, but
        # every bid in these tests divides into a price the currency can
        # actually express. At 3,000 units a 3.2M bid is 1066.6667 a unit,
        # which rounds to a 3,200,010 total and would have these tests
        # arguing with two-decimal money instead of with the product.
        self.request = self._demand(1_000, code=self.concrete,
                                    unit=3_000.0)

    def test_c_a_suspended_vendor_cannot_be_invited(self):
        self._set_vendor_policy('required_for_sourcing')
        self._qualify(self.vendor_a, category=self.trade_concrete)
        self._restrict(self.vendor_a, restriction_type='sourcing_suspension',
                       reason='Under investigation')

        event = self._event(self.request, category_id=self.trade_concrete.id)

        with self.assertRaises(UserError):
            event.action_invite_vendor(self.vendor_a)
        self.assertFalse(event.invitation_ids,
                         "A refused invitation still created a record.")
        self.assertFalse(
            self.env['purchase.order'].search(
                [('re_sourcing_event_id', '=', event.id)]),
            "A refused invitation still created an RFQ.")

    def test_c_the_invitation_snapshot_outlives_a_later_suspension(self):
        """Eligible on the day of invitation stays true afterwards."""
        self._set_vendor_policy('required_for_sourcing')
        self._qualify(self.vendor_a, category=self.trade_concrete)
        event = self._event(self.request, category_id=self.trade_concrete.id)
        invitation = event.action_invite_vendor(self.vendor_a)

        self.assertTrue(invitation.eligibility_eligible)
        self.assertEqual(invitation.eligibility_status, 'eligible')
        snapshot_date = invitation.eligibility_checked_on

        self._restrict(self.vendor_a, restriction_type='sourcing_suspension',
                       reason='Suspended after the invitation')
        invitation.invalidate_recordset()

        self.assertTrue(
            invitation.eligibility_eligible,
            "The historical invitation was rewritten by a later suspension.")
        self.assertEqual(invitation.eligibility_checked_on, snapshot_date)
        self.assertEqual(invitation.current_eligibility_status, 'suspended',
                         "Current eligibility must still tell the truth "
                         "about today.")


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5BidEvidence(M5Common):
    """D — the operational RFQ is not the record of what was submitted."""

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        # 1,000 units at 3,000 = the same 3M of authorised demand, but
        # every bid in these tests divides into a price the currency can
        # actually express. At 3,000 units a 3.2M bid is 1066.6667 a unit,
        # which rounds to a 3,200,010 total and would have these tests
        # arguing with two-decimal money instead of with the product.
        self.request = self._demand(1_000, code=self.concrete,
                                    unit=3_000.0)
        self.event = self._event(self.request)
        self._publish(self.event, [self.vendor_a])
        self.invitation = self.event.invitation_ids

    def test_d_a_recorded_bid_survives_the_rfq_being_repriced(self):
        response = self._bid(self.invitation, 3_200_000.0)
        self.assertEqual(response.amount_untaxed, 3_200_000.0)
        self.assertEqual(response.revision, 0)
        self.assertEqual(response.state, 'received')

        # The buyer re-prices the live RFQ afterwards: 3,000 a unit over
        # 1,000 units, so the operational document now says 3.0M.
        self.invitation.purchase_order_id.order_line[:1].write(
            {'price_unit': 3_000.0})
        response.invalidate_recordset()

        self.assertEqual(response.amount_untaxed, 3_200_000.0,
                         "Bid evidence followed the mutable RFQ.")
        # Per unit, 1,000 units: the line snapshot holds the vendor's unit
        # price, and it is just as frozen as the total.
        self.assertEqual(response.line_ids[:1].price_unit, 3_200.0)
        self.assertEqual(self.invitation.purchase_order_id.amount_untaxed,
                         3_000_000.0,
                         "The operational RFQ should have moved — otherwise "
                         "this test proves nothing about the snapshot.")

    def test_d_native_compare_zeroing_cannot_rewrite_the_bid(self):
        """`action_choose` clears competing quantities. Evidence must not move.

        This is the exact native behaviour audited in M5.0: choosing a line
        writes `product_qty = 0` on every competing alternative line.
        """
        response = self._bid(self.invitation, 3_200_000.0)
        line = self.invitation.purchase_order_id.order_line[:1]

        line.action_clear_quantities()
        response.invalidate_recordset()

        self.assertEqual(line.product_qty, 0.0,
                         "The native mutation did not happen, so this test "
                         "is not exercising what it claims.")
        self.assertEqual(response.amount_untaxed, 3_200_000.0)
        self.assertNotEqual(response.line_ids[:1].quantity, 0.0,
                            "A vendor's submitted quantity became zero "
                            "because a buyer used the compare view.")

    def test_d_a_revised_offer_creates_rev_1_and_keeps_rev_0(self):
        first = self._bid(self.invitation, 3_200_000.0)
        second = self._bid(self.invitation, 3_050_000.0)

        self.assertEqual(first.revision, 0)
        self.assertEqual(second.revision, 1)
        self.assertEqual(first.amount_untaxed, 3_200_000.0,
                         "Rev 0 was edited instead of superseded.")
        self.assertEqual(second.amount_untaxed, 3_050_000.0)
        self.assertEqual(first.state, 'superseded')
        self.assertEqual(second.state, 'received')
        self.assertEqual(second.previous_response_id, first)

    def test_d_a_received_bid_refuses_to_be_edited(self):
        response = self._bid(self.invitation, 3_200_000.0)
        with self.assertRaises(UserError):
            response.write({'amount_untaxed': 1.0})
        with self.assertRaises(UserError):
            response.line_ids[:1].write({'price_unit': 1.0})


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5ConfirmationBlock(M5Common):
    """E — sourcing cannot commit, and the native bypass does not help."""

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        # 1,000 units at 3,000 = the same 3M of authorised demand, but
        # every bid in these tests divides into a price the currency can
        # actually express. At 3,000 units a 3.2M bid is 1066.6667 a unit,
        # which rounds to a 3,200,010 total and would have these tests
        # arguing with two-decimal money instead of with the product.
        self.request = self._demand(1_000, code=self.concrete,
                                    unit=3_000.0)
        self.event = self._event(self.request)
        self._publish(self.event, [self.vendor_a, self.vendor_b])
        self.order = self.event.invitation_ids[0].purchase_order_id

    def test_e_a_tender_rfq_cannot_be_confirmed(self):
        with self.assertRaises(UserError):
            self.order.button_confirm()

        self.assertEqual(self.order.state, 'draft')
        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._reserved(self.project, self.concrete),
                         3_000_000.0)

    def test_e_the_native_warning_bypass_does_not_bypass_governance(self):
        """`skip_alternative_check` turns off Odoo's popup, not our control.

        Native `button_confirm` returns the alternative-RFQ warning wizard
        when live alternatives exist, and that check is switched off by this
        context key. A control that the same key disabled would be decoration.
        """
        with self.assertRaises(UserError):
            self.order.with_context(
                skip_alternative_check=True).button_confirm()

        self.assertEqual(self.order.state, 'draft')
        self.assertEqual(self._commitment(self.project), 0.0)

    def test_e_an_ordinary_project_purchase_still_confirms(self):
        """The block is for tender RFQs. Normal buying is untouched."""
        order = self._confirmed_po(self.project, self.vendor_c,
                                   [(self.concrete, 100_000.0)])

        self.assertEqual(order.state, 'purchase')
        self.assertEqual(self._commitment(self.project), 100_000.0)
