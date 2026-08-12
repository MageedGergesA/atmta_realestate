# -*- coding: utf-8 -*-
"""M5.7–M5.13 — deadlines, late bids, non-offers, withdrawal, addenda."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import M5Common


class M5ExecutionCommon(M5Common):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        self.request = self._demand(1_000, code=self.concrete, unit=3_000.0)
        self.event = self._event(self.request)
        self.close = self.event.close_datetime


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5Deadlines(M5ExecutionCommon):
    """M5.7 — the boundary, and which deadline a bid is judged against."""

    def test_the_boundary_is_inclusive_to_the_second(self):
        self._publish(self.event, [self.vendor_a, self.vendor_b,
                                   self.vendor_c])
        on_time = self._bid(self.event.invitation_ids[0], 2_800_000.0,
                            received=self.close - timedelta(seconds=1))
        exactly = self._bid(self.event.invitation_ids[1], 2_900_000.0,
                            received=self.close)
        late = self._bid(self.event.invitation_ids[2], 3_100_000.0,
                         received=self.close + timedelta(seconds=1))

        self.assertFalse(on_time.is_late, "11:59:59 must be on time.")
        self.assertFalse(exactly.is_late,
                         "12:00:00 is the deadline itself and the rule is "
                         "'accepted up to and including' — it is on time.")
        self.assertTrue(late.is_late, "12:00:01 must be late.")
        self.assertEqual(late.lateness_seconds, 1)

    def test_the_deadline_applied_is_the_versions_not_todays(self):
        """An addendum must not retrospectively re-time an earlier bid."""
        self._publish(self.event, [self.vendor_a])
        invitation = self.event.invitation_ids
        early = self._bid(invitation, 2_800_000.0,
                          received=self.close - timedelta(days=1))
        self.assertEqual(early.deadline_applied, self.close)

        extended = self.close + timedelta(days=2)
        self.event.action_issue_addendum(reason='Scope clarified',
                                         close_datetime=extended)
        early.invalidate_recordset()

        self.assertEqual(early.deadline_applied, self.close,
                         "The bid was re-timed against a deadline that did "
                         "not exist when it was submitted.")
        self.assertFalse(early.is_late)
        self.assertEqual(self.event.original_close_datetime, self.close,
                         "The original close was overwritten by the "
                         "extension.")
        self.assertEqual(self.event.close_datetime, extended)

    def test_the_same_instant_classifies_the_same_in_any_timezone(self):
        """Stored UTC decides. Never a localised string."""
        self._publish(self.event, [self.vendor_a, self.vendor_b,
                                   self.vendor_c])
        instant = self.close + timedelta(seconds=30)
        results = []
        for index, tz in enumerate(('Africa/Cairo', 'UTC',
                                    'America/New_York')):
            self.env.user.tz = tz
            response = self._bid(self.event.invitation_ids[index],
                                 2_800_000.0, received=instant)
            results.append((tz, response.is_late, response.lateness_seconds))

        self.assertEqual({(late, secs) for _tz, late, secs in results},
                         {(True, 30)},
                         "The same UTC instant classified differently "
                         "depending on the reader's timezone: %s" % results)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5LateBidPolicy(M5ExecutionCommon):
    """M5.8 — three policies, and none of them judges the offer."""

    def _late_bid(self, policy, invitation=None):
        self.company.procurement_late_bid_policy = policy
        return self._bid(invitation or self.event.invitation_ids[0],
                         2_800_000.0,
                         received=self.close + timedelta(minutes=5))

    def test_reject_records_evidence_but_refuses_to_make_it_evaluable(self):
        self._publish(self.event, [self.vendor_a])
        response = self._late_bid('reject')

        self.assertEqual(response.state, 'received',
                         "A rejected late bid is still evidence that it "
                         "arrived.")
        self.assertFalse(response.is_evaluable)
        self.assertEqual(response.administrative_status, 'late_rejected')
        self.assertEqual(response.policy_applied, 'reject')

    def test_exception_required_holds_until_a_manager_accepts(self):
        self._publish(self.event, [self.vendor_a])
        response = self._late_bid('exception_required')

        self.assertEqual(response.administrative_status,
                         'late_exception_pending')
        self.assertFalse(response.is_evaluable)
        self.assertTrue(response.exception_required)

        # A different manager, because the fixture user recorded the bid and
        # self-approval is off by default — which is the next test.
        approver = self._purchase_user('m5.late.approver')
        approver.groups_id = [(4, self.env.ref(
            'real_estate_procurement.group_procurement_manager').id)]
        response.with_user(approver).action_approve_late_exception(
            reason='Courier delay proven')
        response.invalidate_recordset()

        self.assertTrue(response.is_evaluable)
        self.assertTrue(response.exception_approved_on)
        self.assertEqual(response.exception_approved_by_id, approver)
        self.assertTrue(response.is_late,
                        "Accepting a late bid does not make it on time.")

    def test_allow_with_warning_stays_permanently_marked(self):
        self._publish(self.event, [self.vendor_a])
        response = self._late_bid('allow_with_warning')

        self.assertTrue(response.is_evaluable)
        self.assertTrue(response.is_late)
        self.assertEqual(response.policy_applied, 'allow_with_warning')

    def test_the_policy_at_receipt_is_what_stays_on_the_record(self):
        """Changing company policy later must not rewrite history."""
        self._publish(self.event, [self.vendor_a])
        response = self._late_bid('allow_with_warning')
        self.company.procurement_late_bid_policy = 'reject'
        response.invalidate_recordset()

        self.assertEqual(response.policy_applied, 'allow_with_warning',
                         "A policy change rewrote how an old submission was "
                         "handled.")

    def test_a_buyer_cannot_approve_their_own_late_exception(self):
        self._publish(self.event, [self.vendor_a])
        response = self._late_bid('exception_required')
        self.assertFalse(self.company.procurement_allow_self_late_exception)

        with self.assertRaises(UserError):
            response.action_approve_late_exception(reason='Mine')

        self.company.procurement_allow_self_late_exception = True
        response.action_approve_late_exception(reason='Configured')
        self.assertTrue(response.is_evaluable)

    def test_late_never_becomes_a_technical_verdict(self):
        self._publish(self.event, [self.vendor_a])
        response = self._late_bid('reject')
        statuses = dict(
            response._fields['administrative_status'].selection)

        for forbidden in ('technical', 'compliant', 'recommended', 'best'):
            self.assertFalse(
                [key for key in statuses if forbidden in key],
                "M5 must not carry a technical or commercial verdict: %s"
                % statuses)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5NonOffers(M5ExecutionCommon):
    """M5.9 — decline, no-bid and silence are three different things."""

    def test_none_of_them_creates_a_zero_priced_bid(self):
        self._publish(self.event, [self.vendor_a, self.vendor_b,
                                   self.vendor_c])
        first, second, _third = self.event.invitation_ids

        first.action_decline(category='capacity', narrative='Plant committed')
        second.action_no_bid(category='specification',
                             narrative='Cannot meet the spec')

        self.assertFalse(self.event.bid_response_ids,
                         "A refusal to bid was recorded as an offer.")
        self.assertEqual(first.response_status, 'declined')
        self.assertEqual(second.response_status, 'no_bid')
        self.assertEqual(first.decline_reason_category, 'capacity')
        self.assertEqual(first.recorded_by_id, self.env.user)

    def test_silence_is_awaiting_until_the_deadline_passes(self):
        self._publish(self.event, [self.vendor_a])
        invitation = self.event.invitation_ids

        self.assertEqual(invitation.response_status, 'awaiting',
                         "A vendor who has not answered yet is not a "
                         "no-response while the tender is still open.")

        self.event.close_datetime = fields.Datetime.now() - timedelta(hours=1)
        invitation.invalidate_recordset()
        self.assertEqual(invitation.response_status, 'no_response')

    def test_declining_cancels_the_rfq_but_keeps_the_invitation(self):
        self._publish(self.event, [self.vendor_a])
        invitation = self.event.invitation_ids
        order = invitation.purchase_order_id

        invitation.action_decline(category='no_interest')

        self.assertEqual(order.state, 'cancel')
        self.assertTrue(invitation.exists())
        self.assertTrue(invitation.eligibility_checked_on,
                        "The eligibility snapshot must survive a decline.")


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5Withdrawal(M5ExecutionCommon):
    """M5.10 — withdrawing removes the offer, never the evidence."""

    def setUp(self):
        super().setUp()
        self._publish(self.event, [self.vendor_a])
        self.invitation = self.event.invitation_ids

    def test_withdrawal_keeps_every_fact_about_the_submission(self):
        response = self._bid(self.invitation, 2_800_000.0)
        received = response.received_datetime

        response.action_withdraw(reason='Vendor withdrew in writing')

        self.assertEqual(response.state, 'withdrawn')
        self.assertEqual(response.amount_untaxed, 2_800_000.0)
        self.assertEqual(response.received_datetime, received)
        self.assertTrue(response.line_ids)
        self.assertEqual(response.withdrawn_by_id, self.env.user)
        self.assertTrue(response.withdrawn_before_close)

    def test_withdrawing_rev_1_does_not_revive_rev_0(self):
        first = self._bid(self.invitation, 3_200_000.0)
        second = self._bid(self.invitation, 3_050_000.0)

        second.action_withdraw(reason='Withdrawn')
        self.invitation.invalidate_recordset()

        self.assertFalse(self.invitation.current_response_id,
                         "Withdrawing the current offer silently promoted a "
                         "quotation the vendor had already replaced.")
        self.assertEqual(first.state, 'superseded')
        self.assertEqual(first.amount_untaxed, 3_200_000.0)

    def test_a_withdrawal_after_close_is_recorded_as_such(self):
        response = self._bid(self.invitation, 2_800_000.0)
        self.event.close_datetime = fields.Datetime.now() - timedelta(hours=2)
        self.event.action_close()

        response.action_withdraw(reason='Late withdrawal')

        self.assertFalse(response.withdrawn_before_close)
        self.assertEqual(response.deadline_applied, self.close,
                         "Moving the close must not rewrite the deadline the "
                         "bid was received against.")


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5Addenda(M5ExecutionCommon):
    """M5.12 — addenda, acknowledgement and resubmission."""

    def setUp(self):
        super().setUp()
        self._publish(self.event, [self.vendor_a])
        self.invitation = self.event.invitation_ids

    def test_an_addendum_creates_rev_1_and_seals_rev_0(self):
        rev0 = self.event.current_version_id
        self.assertEqual(rev0.revision, 0)

        rev1 = self.event.action_issue_addendum(reason='Quantity increased')

        self.assertEqual(rev1.revision, 1)
        self.assertEqual(self.event.current_version_id, rev1)
        self.assertEqual(rev0.state, 'superseded')
        with self.assertRaises(UserError):
            rev0.write({'reason': 'Rewritten'})

    def test_an_addendum_does_not_invent_a_bid_revision(self):
        self._bid(self.invitation, 3_200_000.0)
        before = len(self.event.bid_response_ids)

        self.event.action_issue_addendum(reason='Specification changed')

        self.assertEqual(len(self.event.bid_response_ids), before,
                         "Publishing a tender revision fabricated a vendor "
                         "bid nobody submitted.")
        self.assertTrue(self.invitation.resubmission_required)
        self.assertEqual(self.invitation.current_response_id.amount_untaxed,
                         3_200_000.0)

    def test_a_bid_against_a_superseded_basis_is_not_evaluable(self):
        response = self._bid(self.invitation, 3_200_000.0)
        self.event.action_issue_addendum(reason='Scope changed')
        response.invalidate_recordset()

        self.assertFalse(response.is_evaluable)
        self.assertEqual(response.administrative_status,
                         'resubmission_required')
        self.assertEqual(response.amount_untaxed, 3_200_000.0,
                         "The offer itself must be untouched.")

    def test_required_acknowledgement_holds_a_response_until_recorded(self):
        self.company.procurement_addendum_ack_policy = \
            'required_before_response'
        self.event.action_issue_addendum(reason='Addendum 1')

        response = self._bid(self.invitation, 3_100_000.0)
        self.assertFalse(response.is_evaluable)
        self.assertEqual(response.administrative_status, 'incomplete')

        self.invitation.action_acknowledge(method='email_received')
        response.invalidate_recordset()

        self.assertTrue(response.is_evaluable)
        self.assertEqual(response.administrative_status, 'complete')

    def test_two_addenda_require_the_latest_acknowledgement(self):
        self.company.procurement_addendum_ack_policy = \
            'required_before_response'
        rev1 = self.event.action_issue_addendum(reason='Addendum 1')
        self.invitation.action_acknowledge(version=rev1)
        self.event.action_issue_addendum(reason='Addendum 2')

        response = self._bid(self.invitation, 3_050_000.0)

        self.assertFalse(response.is_evaluable,
                         "Acknowledging Rev 1 was accepted as covering "
                         "Rev 2.")
        self.invitation.action_acknowledge()
        response.invalidate_recordset()
        self.assertTrue(response.is_evaluable)

    def test_acknowledgement_records_how_it_was_obtained(self):
        self.event.action_issue_addendum(reason='Addendum 1')
        self.invitation.action_acknowledge(method='signed_document',
                                           notes='Countersigned page 4')

        ack = self.invitation.acknowledgement_ids
        self.assertEqual(len(ack), 1)
        self.assertEqual(ack.method, 'signed_document')
        self.assertEqual(ack.recorded_by_id, self.env.user)
        self.assertIn('future_portal',
                      dict(ack._fields['method'].selection),
                      "A portal method must exist so buyer-recorded "
                      "acknowledgement stays distinguishable from one.")


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5Completeness(M5ExecutionCommon):
    """M5.13 — administrative only, and provably so."""

    def setUp(self):
        super().setUp()
        self._publish(self.event, [self.vendor_a])
        self.invitation = self.event.invitation_ids

    def test_missing_validity_is_incomplete_when_required(self):
        self.event.require_validity_date = True
        response = self._bid(self.invitation, 2_800_000.0)
        self.assertFalse(response.is_evaluable)
        self.assertEqual(response.administrative_status, 'incomplete')
        self.assertIn('validity', response.completeness_notes.lower())

    def test_supplying_validity_completes_it(self):
        self.event.require_validity_date = True
        response = self._bid(self.invitation, 2_800_000.0,
                             validity_date=fields.Date.add(self.today,
                                                           days=90))
        self.assertTrue(response.is_evaluable)
        self.assertEqual(response.administrative_status, 'complete')

    def test_a_complete_bid_makes_no_claim_about_quality(self):
        response = self._bid(self.invitation, 2_800_000.0)
        self.assertTrue(response.is_evaluable)
        self.assertIn('merits', response.completeness_notes)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5Clarifications(M5ExecutionCommon):
    """M5.11 — answering versus changing."""

    def setUp(self):
        super().setUp()
        self._publish(self.event, [self.vendor_a, self.vendor_b])
        self.Clarification = self.env[
            'realestate.procurement.sourcing.clarification']

    def _clarification(self, **kwargs):
        values = {
            'event_id': self.event.id,
            'clarification_type': 'vendor_question',
            'partner_id': self.vendor_a.id,
            'question': 'Is the rebar rate inclusive of fixing?',
        }
        values.update(kwargs)
        return self.Clarification.create(values)

    def test_an_ordinary_clarification_is_answered_in_place(self):
        clarification = self._clarification()
        clarification.action_answer('Yes, fixing is included.')

        self.assertEqual(clarification.state, 'answered')
        self.assertEqual(clarification.answered_by_id, self.env.user)
        self.assertEqual(self.event.current_version_id.revision, 0,
                         "Answering a question issued a tender revision.")

    def test_a_material_clarification_refuses_to_answer_without_an_addendum(self):
        clarification = self._clarification(is_material=True,
                                            material_subject='quantity')

        with self.assertRaises(UserError):
            clarification.action_answer('Quantity is now 1,200 units.')

        self.assertEqual(clarification.state, 'open')
        self.assertEqual(self.event.current_version_id.revision, 0)

    def test_a_material_clarification_goes_out_as_an_addendum(self):
        clarification = self._clarification(is_material=True,
                                            material_subject='quantity')
        version = clarification.action_issue_addendum(
            reason='Quantity increased to 1,200')

        self.assertEqual(version.revision, 1)
        self.assertEqual(clarification.addendum_version_id, version)
        clarification.action_answer('Quantity is now 1,200 units.')
        self.assertEqual(clarification.state, 'answered')

    def test_the_asked_against_version_is_recorded(self):
        clarification = self._clarification()
        self.assertEqual(clarification.version_id,
                         self.event.current_version_id)
