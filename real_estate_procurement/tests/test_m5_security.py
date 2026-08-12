# -*- coding: utf-8 -*-
"""M5.14 — confidentiality, tested at the ORM and not at the view.

A competitor's price hidden by `invisible=` in a form is not hidden. Every
assertion here goes through `read`, `search`, `search_read` or a relational
traversal as the restricted user, because those are what an export, an RPC
client or a curious browser console actually use.
"""

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import M5Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5Confidentiality(M5Common):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        self.request = self._demand(1_000, code=self.concrete, unit=3_000.0)
        self.event = self._event(self.request)
        self._publish(self.event, [self.vendor_a, self.vendor_b])
        self.bid_a = self._bid(self.event.invitation_ids[0], 2_800_000.0)
        self.bid_b = self._bid(self.event.invitation_ids[1], 3_100_000.0)

        self.requester = self._purchase_user('m5.requester')
        self.requester.groups_id = [(6, 0, [
            self.env.ref('base.group_user').id,
            self.env.ref(
                'real_estate_procurement.group_procurement_requester').id,
        ])]
        self.buyer = self._purchase_user('m5.buyer')
        self.buyer.groups_id = [(4, self.env.ref(
            'real_estate_procurement.group_procurement_user').id)]
        self.event.buyer_id = self.buyer

    # ------------------------------------------------------------------
    def test_a_requester_cannot_read_a_bid_at_all(self):
        """Every route in, including the ones an export or RPC client uses.

        `search` refuses outright rather than returning an empty set, which is
        the stronger of the two acceptable answers: there is no model-level
        read right at all, so the question is rejected before any record rule
        gets a chance to filter it.
        """
        Bid = self.env['realestate.procurement.bid.response'].with_user(
            self.requester)

        with self.assertRaises(AccessError):
            Bid.browse(self.bid_a.id).read(['amount_untaxed'])
        with self.assertRaises(AccessError):
            Bid.search([])
        with self.assertRaises(AccessError):
            Bid.search_read([], ['amount_untaxed'])
        with self.assertRaises(AccessError):
            Bid.search_count([])

    def test_a_requester_cannot_read_bid_lines(self):
        Line = self.env[
            'realestate.procurement.bid.response.line'].with_user(
                self.requester)
        with self.assertRaises(AccessError):
            Line.browse(self.bid_a.line_ids[:1].id).read(['price_unit'])
        with self.assertRaises(AccessError):
            Line.search([])

    def test_a_requester_cannot_reach_bids_by_traversal(self):
        """The long way round: event → bid_response_ids → amount."""
        event = self.event.with_user(self.requester)
        with self.assertRaises(AccessError):
            event.bid_response_ids.mapped('amount_untaxed')

    def test_a_requester_cannot_read_clarifications(self):
        clarification = self.env[
            'realestate.procurement.sourcing.clarification'].create({
                'event_id': self.event.id,
                'clarification_type': 'vendor_question',
                'partner_id': self.vendor_a.id,
                'visibility': 'vendor_only',
                'question': 'Can we quote an alternative rate?',
            })
        Clarification = self.env[
            'realestate.procurement.sourcing.clarification'].with_user(
                self.requester)
        with self.assertRaises(AccessError):
            Clarification.browse(clarification.id).read(['question'])

    def test_a_bid_attachment_is_bound_to_the_bid_not_left_floating(self):
        """Attachment access follows res_model/res_id, so they must be set."""
        attachment = self.env['ir.attachment'].create({
            'name': 'vendor-a-quotation.pdf',
            'datas': b'UERGLUJZVEVT',
        })
        response = self._bid(self.event.invitation_ids[0], 2_750_000.0,
                             attachments=attachment)

        self.assertEqual(attachment.res_model,
                         'realestate.procurement.bid.response')
        self.assertEqual(attachment.res_id, response.id)
        with self.assertRaises(AccessError):
            attachment.with_user(self.requester).read(['datas'])

    def test_a_plain_purchase_user_does_not_inherit_bid_evidence(self):
        """Native Purchase rights are not ATMTA sourcing rights."""
        purchaser = self._purchase_user('m5.purchase.only')
        purchaser.groups_id = [(6, 0, [
            self.env.ref('base.group_user').id,
            self.env.ref('purchase.group_purchase_user').id,
        ])]
        Bid = self.env['realestate.procurement.bid.response'].with_user(
            purchaser)
        with self.assertRaises(AccessError):
            Bid.browse(self.bid_a.id).read(['amount_untaxed'])

    def test_a_buyer_reads_their_own_event_and_not_another(self):
        other_buyer = self._purchase_user('m5.other.buyer')
        other_buyer.groups_id = [(4, self.env.ref(
            'real_estate_procurement.group_procurement_user').id)]

        mine = self.env[
            'realestate.procurement.bid.response'].with_user(
                self.buyer).search([('event_id', '=', self.event.id)])
        theirs = self.env[
            'realestate.procurement.bid.response'].with_user(
                other_buyer).search([('event_id', '=', self.event.id)])

        self.assertTrue(mine, "The event's own buyer cannot read its bids.")
        self.assertFalse(theirs,
                         "A buyer with no part in this tender read its "
                         "competitor pricing.")

    def test_a_manager_can_audit_any_tender(self):
        manager = self._purchase_user('m5.manager')
        manager.groups_id = [(4, self.env.ref(
            'real_estate_procurement.group_procurement_manager').id)]
        found = self.env[
            'realestate.procurement.bid.response'].with_user(
                manager).search([('event_id', '=', self.event.id)])
        self.assertEqual(len(found), 2,
                         "Somebody has to be able to audit a tender they did "
                         "not run.")

    def test_no_competitor_price_is_posted_into_event_chatter(self):
        """Chatter is widely readable; bid amounts are not."""
        bodies = ' '.join(
            self.event.message_ids.mapped('body') or [''])
        for amount in ('2,800,000', '2800000', '3,100,000', '3100000'):
            self.assertNotIn(amount, bodies,
                             "A competitor's price was posted into chatter, "
                             "which is readable by followers who may not read "
                             "the bid.")


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5PublicMethodAuthority(M5Common):
    """Every RPC-callable action checks authority server-side, not in a view."""

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        self.request = self._demand(1_000, code=self.concrete, unit=3_000.0)
        self.event = self._event(self.request)
        self._publish(self.event, [self.vendor_a])
        self.invitation = self.event.invitation_ids
        self.requester = self._purchase_user('m5.rpc.requester')
        self.requester.groups_id = [(6, 0, [
            self.env.ref('base.group_user').id,
            self.env.ref(
                'real_estate_procurement.group_procurement_requester').id,
        ])]

    def test_a_requester_cannot_publish_close_or_amend(self):
        event = self.event.with_user(self.requester)
        for action, kwargs in (('action_close', {}),
                               ('action_cancel', {}),
                               ('action_issue_addendum',
                                {'reason': 'Sneaky change'})):
            with self.assertRaises(AccessError, msg=action):
                getattr(event, action)(**kwargs)

    def test_a_requester_cannot_invite_a_vendor(self):
        with self.assertRaises(AccessError):
            self.event.with_user(self.requester).action_invite_vendor(
                self.vendor_b)

    def test_a_requester_cannot_record_a_bid(self):
        with self.assertRaises(AccessError):
            self.invitation.with_user(self.requester).action_record_bid()
