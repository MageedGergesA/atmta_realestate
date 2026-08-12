# -*- coding: utf-8 -*-
"""M5 concurrency — C1–C7.

Odoo's test harness shares one cursor, so a genuine two-process race cannot be
run here and this file does not pretend otherwise. What it proves instead is
each mechanism that makes the race safe, directly:

* the **unique index** that makes a duplicate revision fail in the database
  rather than in Python, tested by inserting the duplicate,
* the **row lock** each writer takes before reading the number it is about to
  increment, tested by asserting the statement is issued,
* the **idempotence** of an action that two people may click at once.

§53 of the M4 report says the same thing about qualifications. Naming the limit
is the honest half of testing concurrency in this harness.
"""

from datetime import timedelta

import psycopg2

from odoo import fields
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import M5Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5Concurrency(M5Common):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        self.request = self._demand(1_000, code=self.concrete, unit=3_000.0)
        self.event = self._event(self.request)

    # -- C1 / C2 -------------------------------------------------------
    @mute_logger('odoo.sql_db')
    def test_c1_a_second_rev_0_is_refused_by_the_database(self):
        self._publish(self.event, [self.vendor_a])
        self.assertEqual(self.event.current_version_id.revision, 0)

        with self.assertRaises(psycopg2.IntegrityError):
            with self.env.cr.savepoint():
                self.env['realestate.procurement.sourcing.version'].create({
                    'event_id': self.event.id,
                    'revision': 0,
                    'reason': 'A second original issue',
                })

    def test_c1_publishing_twice_is_refused_not_duplicated(self):
        self._publish(self.event, [self.vendor_a])
        with self.assertRaises(Exception):
            self.event.action_publish()
        self.assertEqual(len(self.event.version_ids), 1)

    @mute_logger('odoo.sql_db')
    def test_c2_two_addenda_cannot_both_be_rev_1(self):
        self._publish(self.event, [self.vendor_a])
        self.event.action_issue_addendum(reason='Addendum 1')
        self.assertEqual(self.event.current_version_id.revision, 1)

        with self.assertRaises(psycopg2.IntegrityError):
            with self.env.cr.savepoint():
                self.env['realestate.procurement.sourcing.version'].create({
                    'event_id': self.event.id,
                    'revision': 1,
                    'reason': 'A competing addendum',
                })

    def test_c2_the_addendum_path_locks_the_event_row(self):
        self._publish(self.event, [self.vendor_a])
        statements = []
        origin = self.env.cr.execute

        def spy(query, params=None, *args, **kwargs):
            statements.append(str(query))
            return origin(query, params, *args, **kwargs)

        self.env.cr.execute = spy
        try:
            self.event.action_issue_addendum(reason='Addendum 1')
        finally:
            self.env.cr.execute = origin

        self.assertTrue(
            [q for q in statements
             if 'realestate_procurement_sourcing_event' in q
             and 'FOR UPDATE' in q],
            "The addendum path does not lock the event row, so two buyers "
            "could both read Rev 0 as current.")

    # -- C3 ------------------------------------------------------------
    @mute_logger('odoo.sql_db')
    def test_c3_two_bid_revisions_cannot_share_a_number(self):
        self._publish(self.event, [self.vendor_a])
        invitation = self.event.invitation_ids
        self._bid(invitation, 3_200_000.0)

        with self.assertRaises(psycopg2.IntegrityError):
            with self.env.cr.savepoint():
                self.env['realestate.procurement.bid.response'].create({
                    'event_id': self.event.id,
                    'invitation_id': invitation.id,
                    'revision': 0,
                })

    def test_c3_recording_a_bid_locks_the_invitation_row(self):
        self._publish(self.event, [self.vendor_a])
        invitation = self.event.invitation_ids
        statements = []
        origin = self.env.cr.execute

        def spy(query, params=None, *args, **kwargs):
            statements.append(str(query))
            return origin(query, params, *args, **kwargs)

        self.env.cr.execute = spy
        try:
            self._bid(invitation, 3_200_000.0)
        finally:
            self.env.cr.execute = origin

        self.assertTrue(
            [q for q in statements
             if 'realestate_procurement_sourcing_invitation' in q
             and 'FOR UPDATE' in q],
            "Bid receipt does not lock the invitation, so two recordings "
            "could both decide they are the next revision.")

    # -- C4 ------------------------------------------------------------
    def test_c4_closing_twice_writes_one_close(self):
        self._publish(self.event, [self.vendor_a])
        self.event.action_close()
        first = self.event.actual_closed_datetime
        messages = len(self.event.message_ids)

        self.event.action_close()

        self.assertEqual(self.event.actual_closed_datetime, first,
                         "A second close moved the moment bidding ended.")
        self.assertEqual(len(self.event.message_ids), messages,
                         "A second close wrote a second audit entry.")

    # -- C5 / C6 -------------------------------------------------------
    def test_c5_lateness_comes_from_stored_timestamps_not_call_order(self):
        """Closing the tender after receipt does not make the bid late."""
        self._publish(self.event, [self.vendor_a])
        invitation = self.event.invitation_ids
        response = self._bid(invitation, 2_800_000.0,
                             received=self.event.close_datetime
                             - timedelta(minutes=1))
        self.event.action_close()
        response.invalidate_recordset()

        self.assertFalse(response.is_late,
                         "A bid became late because the close action ran "
                         "after it in the same transaction.")

    def test_c6_a_bid_binds_to_exactly_one_version(self):
        self._publish(self.event, [self.vendor_a])
        invitation = self.event.invitation_ids
        rev0 = self.event.current_version_id
        response = self._bid(invitation, 3_200_000.0)

        extended = self.event.close_datetime + timedelta(days=1)
        self.event.action_issue_addendum(reason='Addendum 1',
                                         close_datetime=extended)
        response.invalidate_recordset()

        self.assertEqual(response.version_id, rev0)
        self.assertEqual(response.deadline_applied,
                         rev0.effective_close_datetime,
                         "The response mixed one version's lines with "
                         "another version's deadline.")

    # -- C7 ------------------------------------------------------------
    def test_c7_an_invitation_snapshot_is_one_decision(self):
        """One call, one answer — not a field-by-field re-read."""
        self._set_vendor_policy('required_for_sourcing')
        self._qualify(self.vendor_a, category=self.trade_concrete)
        self.event.category_id = self.trade_concrete
        invitation = self.event.action_invite_vendor(self.vendor_a)

        import json
        payload = json.loads(invitation.eligibility_payload)

        self.assertEqual(payload['status'], invitation.eligibility_status)
        self.assertEqual(payload['eligible'], invitation.eligibility_eligible)
        self.assertEqual(payload['qualified'],
                         invitation.eligibility_qualified)
        self.assertEqual(payload['as_of'],
                         fields.Date.to_string(
                             invitation.eligibility_checked_on))
        self.assertEqual(payload['qualification_id'],
                         invitation.qualification_id.id)
