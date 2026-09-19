# -*- coding: utf-8 -*-
"""Regressions for what the procurement lifecycle run found in Sourcing."""
from datetime import timedelta

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import SourcingLifecycleCommon


@tagged('post_install', '-at_install', 'atmta_sourcing')
class TestSourcingLifecycleFindings(SourcingLifecycleCommon):

    # -- item 5: a partial allocation presents the allocated quantity -------
    def test_a_partial_allocation_tenders_only_the_allocated_quantity(self):
        request = self._request(qty=100)
        line = request.line_ids
        event = self._event()
        event.action_allocate_request(request, quantities={line.id: 40})

        self.assertEqual(event.allocation_ids.mapped('quantity'), [40.0])
        self.assertEqual(
            event.line_ids.mapped('quantity'), [40.0],
            "40 of 100 was allocated but the market is asked to price 100.")

        # The rest goes to a second tender, and each shows its own share.
        second = self._event()
        second.action_allocate_request(request, quantities={line.id: 60})
        self.assertEqual(second.line_ids.mapped('quantity'), [60.0])

    # -- item 4: a bid cannot be received before the tender existed --------
    def test_a_bid_cannot_be_recorded_as_received_before_the_issue(self):
        event = self._published(vendors=self.vendors[:1])
        invitation = event.invitation_ids
        self._price(invitation, 90.0)
        before_issue = event.issue_datetime - timedelta(days=30)

        with self.assertRaises(UserError):
            invitation.action_record_bid(received_datetime=before_issue)
        self.assertFalse(event.bid_response_ids)

    def test_a_bid_received_after_the_issue_is_still_recorded(self):
        event = self._published(vendors=self.vendors[:1])
        invitation = event.invitation_ids
        self._price(invitation, 90.0)
        response = invitation.action_record_bid(
            received_datetime=event.issue_datetime)
        self.assertEqual(response.state, 'received')
        self.assertFalse(response.is_late)

    # -- item 13: a requisition already enquired by RFQ ---------------------
    def _enquired(self, qty=100, enquired=60):
        """Approved demand, enquired through the RFQ path for `enquired`."""
        self._require_rfqs()
        request = self._request(qty=qty)
        rfq = request.action_create_rfqs(vendors=self.vendors[:1])
        rfq.order_line.product_qty = enquired
        self.assertEqual(request.state, 'sourcing')
        return request, rfq

    def test_demand_under_rfq_can_tender_what_the_rfqs_do_not_cover(self):
        request, _rfq = self._enquired(qty=100, enquired=60)
        event = self._event()
        event.action_allocate_request(
            request, quantities={request.line_ids.id: 40})
        self.assertEqual(event.allocation_ids.mapped('quantity'), [40.0])

    def test_demand_under_rfq_cannot_tender_what_the_rfqs_cover(self):
        request, _rfq = self._enquired(qty=100, enquired=60)
        event = self._event()
        with self.assertRaises(UserError):
            event.action_allocate_request(
                request, quantities={request.line_ids.id: 50})
        with self.assertRaises(UserError):
            event.action_allocate_request(request)

    def test_a_cancelled_rfq_no_longer_covers_the_demand(self):
        request, rfq = self._enquired(qty=100, enquired=100)
        rfq.button_cancel()
        event = self._event()
        event.action_allocate_request(request)
        self.assertEqual(event.allocation_ids.mapped('quantity'), [100.0])

    # -- UX: the event form reaches the actions that exist ------------------
    def test_the_event_form_offers_the_actions_that_take_no_input(self):
        """Only methods callable without an argument can be a button; the ones
        that need a requisition, a vendor or a reason have no wizard yet."""
        arch = self.env['realestate.procurement.sourcing.event'].get_view(
            view_type='form')['arch']
        for name in ('action_record_bid', 'action_acknowledge',
                     'action_approve_late_exception', 'action_withdraw',
                     'action_answer', 'action_issue_addendum'):
            self.assertIn('name="%s"' % name, arch,
                          "%s is not reachable from the event form" % name)
