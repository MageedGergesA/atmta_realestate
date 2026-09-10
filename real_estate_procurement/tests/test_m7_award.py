# -*- coding: utf-8 -*-
"""M7 — the award, and the first time in this programme money actually moves.

```
    M6 EVALUATES THE BIDS.
    M7 AWARDS THE CONTRACT, AND AWARDING IS WHAT COMMITS.
```

Every M6 test asserted that the financial position did **not** move. These
assert the opposite where it should, and only there: nothing moves at draft,
nothing moves at approval, and the reservation converts into a commitment at
the moment the award is issued and the orders confirm — computed by
Construction, from the orders, exactly as it has been since M2.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import M6Common


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class M7Common(M6Common):

    def setUp(self):
        super().setUp()
        self.Award = self.env['realestate.procurement.award']

    def _finalised_round(self):
        plan = self._plan()
        round_ = self._round(plan)
        self._advance_to_commercial(round_)
        round_.action_normalise()
        round_.action_finalise()
        return round_

    def _winner(self, round_):
        return round_.candidate_ids.filtered(lambda c: c.rank == 1)[:1]

    def _award(self, round_, candidates=None, **kwargs):
        candidates = candidates if candidates is not None \
            else self._winner(round_)
        values = {
            'sourcing_event_id': round_.sourcing_event_id.id,
            'round_id': round_.id,
            # No `amount`: it is derived from the awarded scope, which is
            # seeded from the vendor's own bid at create.
            'line_ids': [(0, 0, {'candidate_id': candidate.id})
                         for candidate in candidates],
        }
        values.update(kwargs)
        return self.Award.create(values)

    def _second_manager(self, login='m7.approver'):
        return self.env['res.users'].create({
            'name': login, 'login': login, 'email': '%s@example.com' % login,
            'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_procurement.group_procurement_manager').id])],
        })


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7AwardFoundation(M7Common):

    def test_an_award_needs_a_finalised_evaluation_behind_it(self):
        plan = self._plan()
        round_ = self._round(plan)  # opened, not finalised

        with self.assertRaises(UserError):
            self.Award.create({
                'sourcing_event_id': self.event.id,
                'round_id': round_.id,
            })

    def test_a_technically_non_responsive_bid_cannot_be_awarded(self):
        plan = self._plan()
        round_ = self._round(plan)
        failed = self._candidate(round_, self.vendor_b)
        self._score(round_, failed, rated=10.0, mandatory='fail')
        self._score_others(round_, exclude=failed)
        round_.action_finalise_technical()
        round_.action_open_commercial()
        round_.action_normalise()
        round_.action_finalise()

        award = self._award(round_, candidates=failed,
                            justification='We liked them')
        with self.assertRaises(UserError):
            award.action_submit()

    def test_awarding_away_from_the_ranking_needs_a_reason(self):
        round_ = self._finalised_round()
        runner_up = round_.candidate_ids.filtered(lambda c: c.rank == 2)[:1]
        self.assertTrue(runner_up)

        award = self._award(round_, candidates=runner_up)
        with self.assertRaises(UserError):
            award.action_submit()

        award.justification = 'Rank 1 withdrew capacity for the programme dates'
        award.action_submit()
        self.assertEqual(award.state, 'review')

    def test_awarding_rank_one_needs_no_justification(self):
        round_ = self._finalised_round()
        award = self._award(round_)

        award.action_submit()

        self.assertEqual(award.state, 'review')
        self.assertTrue(award.line_ids.is_top_ranked)
        self.assertEqual(award.line_ids.rank_awarded, 1)

    def test_the_vendor_name_is_snapshotted_on_the_award(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        original = award.line_ids.partner_name
        self.assertTrue(original)

        award.line_ids.partner_id.name = 'Renamed After The Award Ltd'
        award.line_ids.invalidate_recordset()

        self.assertEqual(award.line_ids.partner_name, original)

    def test_one_award_per_evaluation_round(self):
        import psycopg2
        from odoo.tools import mute_logger
        round_ = self._finalised_round()
        self._award(round_)

        with mute_logger('odoo.sql_db'):
            with self.assertRaises(psycopg2.IntegrityError):
                with self.env.cr.savepoint():
                    self.Award.create({
                        'sourcing_event_id': self.event.id,
                        'round_id': round_.id,
                    })


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7MakerChecker(M7Common):

    def test_the_person_who_raised_an_award_cannot_approve_it(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()

        with self.assertRaises(UserError):
            award.action_approve()
        self.assertEqual(award.state, 'review')

        award.with_user(self._second_manager()).action_approve()
        self.assertEqual(award.state, 'approved')
        self.assertNotEqual(award.approved_by_id, award.submitted_by_id)

    def test_a_buyer_cannot_approve_an_award(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        buyer = self.env['res.users'].create({
            'name': 'm7.buyeronly', 'login': 'm7.buyeronly',
            'email': 'm7.buyeronly@example.com',
            'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_procurement.group_procurement_user').id])],
        })

        with self.assertRaises(AccessError):
            award.with_user(buyer).action_approve()

    def test_an_approved_award_cannot_have_its_substance_edited(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()

        with self.assertRaises(UserError):
            award.write({'justification': 'Rewritten afterwards'})
        with self.assertRaises(UserError):
            award.unlink()


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7TheMoneyBoundary(M7Common):
    """The whole point of the milestone, asserted step by step."""

    def test_nothing_moves_until_the_award_is_issued(self):
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0))
        round_ = self._finalised_round()
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Evaluating moved money.")

        award = self._award(round_)
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Raising an award moved money.")

        award.action_submit()
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Submitting an award moved money.")

        award.with_user(self._second_manager()).action_approve()
        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Approving an award moved money. Approval "
                         "authorises; confirming commits.")

    def test_issuing_the_award_converts_reservation_into_commitment(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        reserved_before, commitment_before, actual_before = self._position()
        self.assertEqual(commitment_before, 0.0)

        award.action_issue()

        self.assertEqual(award.state, 'issued')
        order = award.line_ids.purchase_order_id
        self.assertEqual(order.state, 'purchase')
        reserved_after, commitment_after, actual_after = self._position()
        self.assertGreater(commitment_after, 0.0,
                           "Issuing the award created no commitment.")
        self.assertLess(reserved_after, reserved_before,
                        "The reservation did not convert.")
        self.assertEqual(actual_after, 0.0,
                         "Issuing an award posted an actual cost. Only a "
                         "vendor bill does that.")

    def test_a_draft_award_authorises_nothing(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        order = award.line_ids.purchase_order_id

        self.assertFalse(order._award_authorisation())
        with self.assertRaises(UserError):
            order.button_confirm()
        self.assertEqual(self._commitment(self.project), 0.0)

    def test_a_submitted_but_unapproved_award_authorises_nothing(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        order = award.line_ids.purchase_order_id

        self.assertFalse(order._award_authorisation())
        with self.assertRaises(UserError):
            order.button_confirm()
        self.assertEqual(self._commitment(self.project), 0.0)

    def test_an_approved_award_authorises_exactly_its_own_orders(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()

        awarded = award.line_ids.purchase_order_id
        self.assertTrue(awarded._award_authorisation())

        losers = (round_.candidate_ids - award.line_ids.candidate_id)
        for candidate in losers:
            other = candidate.bid_response_id.invitation_id.purchase_order_id
            self.assertFalse(
                other._award_authorisation(),
                "%s was not awarded and is still authorised."
                % other.display_name)
            with self.assertRaises(UserError):
                other.button_confirm()

    def test_cancelling_an_award_withdraws_the_authorisation(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        order = award.line_ids.purchase_order_id
        self.assertTrue(order._award_authorisation())

        award.action_cancel(reason='Programme deferred to the next budget year')

        self.assertEqual(award.state, 'cancelled')
        self.assertFalse(order._award_authorisation())
        with self.assertRaises(UserError):
            order.button_confirm()
        self.assertEqual(self._commitment(self.project), 0.0)

    def test_an_issued_award_cannot_be_cancelled(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        award.action_issue()

        with self.assertRaises(UserError):
            award.action_cancel(reason='Changed our minds')
        self.assertEqual(award.state, 'issued')

    def test_cancelling_the_order_reverses_what_issuing_committed(self):
        """M3 already does this. M7 must not have broken it."""
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        award.action_issue()
        self.assertGreater(self._commitment(self.project), 0.0)

        award.line_ids.purchase_order_id.button_cancel()

        self.assertEqual(self._commitment(self.project), 0.0,
                         "Cancelling the order left the commitment standing.")

    def test_the_same_demand_is_never_reserved_and_committed_at_once(self):
        """Phase 0's Q3, at the one moment it can actually happen.

        Construction reads a commitment from any confirmed order. M3 converts
        the reservation from the order's demand link. If the awarded tender
        order carried no demand link the first would happen and the second
        would not, and the project would hold 3,000,000 *and* owe 3,000,000
        for one requisition.
        """
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()

        order = award.line_ids.purchase_order_id
        self.assertTrue(
            order.order_line.mapped('re_material_request_line_id'),
            "The awarded tender order carries no demand link, so confirming "
            "it commits without converting the reservation.")

        award.action_issue()
        reserved, commitment, actual = self._position()

        self.assertGreater(commitment, 0.0)
        self.assertEqual(
            reserved, 0.0,
            "The reservation is still holding %s while the commitment is %s "
            "— the same demand counted twice." % (reserved, commitment))
        self.assertEqual(actual, 0.0)

    def test_issuing_cancels_the_quotations_that_lost(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        awarded = award.line_ids.purchase_order_id
        others = self.event.invitation_ids.purchase_order_id - awarded
        self.assertTrue(others)

        award.action_issue()

        self.assertEqual(awarded.state, 'purchase')
        for order in others:
            self.assertEqual(
                order.state, 'cancel',
                "%s lost the tender and is still a live quotation."
                % order.name)
        # The bid evidence itself is M5's and is untouched.
        self.assertTrue(self.event.bid_response_ids)
        self.assertTrue(all(self.event.bid_response_ids.mapped('amount_untaxed')))

    def test_an_award_cannot_be_issued_before_it_is_approved(self):
        round_ = self._finalised_round()
        award = self._award(round_)

        with self.assertRaises(UserError):
            award.action_issue()
        award.action_submit()
        with self.assertRaises(UserError):
            award.action_issue()
        self.assertEqual(self._commitment(self.project), 0.0)


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7Revalidation(M7Common):
    """An evaluation is a photograph; approval re-asks what it assumed."""

    def test_approval_records_what_it_revalidated(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()

        self.assertTrue(award.revalidation_note)

    def test_an_expired_bid_is_surfaced_rather_than_silently_awarded(self):
        # Set at the moment the bid is recorded, not afterwards: a received
        # bid is immutable M5 evidence and refuses to be edited, which is the
        # behaviour M5 exists to provide rather than something to work around.
        plan = self._plan()
        self._receive_bids()
        for response in self.event.bid_response_ids:
            response._engine().write({'validity_date': '2020-01-01'})
        round_ = self.Round.create({'sourcing_event_id': self.event.id,
                                    'plan_id': plan.id})
        round_.action_open_technical()
        self._advance_to_commercial(round_)
        round_.action_normalise()
        round_.action_finalise()
        award = self._award(round_)
        award.action_submit()

        award.with_user(self._second_manager()).action_approve()

        self.assertIn('expired', (award.revalidation_note or '').lower(),
                      "An expired offer was awarded without the approver "
                      "being told: %r" % award.revalidation_note)

    def test_a_vendor_who_became_ineligible_blocks_the_approval(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        vendor = award.line_ids.partner_id
        # Restrictions are created draft and only active ones bite, which is
        # correct — an unapproved restriction is a proposal.
        restriction = self.env[
            'realestate.procurement.vendor.restriction'].sudo().create({
                'partner_id': vendor.id,
                'company_id': self.company.id,
                'restriction_type': 'award_suspension',
                'reason': 'Under investigation',
                'effective_from': self.today,
            })
        restriction.action_activate()
        self.assertEqual(restriction.state, 'active')

        with self.assertRaises(UserError):
            award.with_user(self._second_manager()).action_approve()
        self.assertEqual(award.state, 'review')
        self.assertEqual(self._commitment(self.project), 0.0)


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7NoDuplicatedCommitment(M7Common):
    """M7 authorises. Construction computes. Neither does the other's job."""

    def test_procurement_holds_no_commitment_field_of_its_own(self):
        Award = self.env['realestate.procurement.award']
        for name, field in Award._fields.items():
            self.assertNotIn(
                'commitment', name,
                "The award carries %r. Construction computes commitment from "
                "confirmed orders; a second number here would disagree with "
                "it under exactly the conditions nobody tests." % name)

    def test_the_commitment_comes_from_construction_not_from_the_award(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        award.action_issue()

        order = award.line_ids.purchase_order_id
        by_code = self.env[
            'realestate.construction.commitment'].po_commitment_by_cost_code(
                self.project)

        self.assertTrue(by_code,
                        "Construction sees no commitment from the order.")
        self.assertAlmostEqual(sum(by_code.values()),
                               self._commitment(self.project), places=2)
        self.assertEqual(order.state, 'purchase')


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7SplitAndPartialAward(M7Common):
    """Splitting a tender, and the double-commit hiding inside it.

    M5 issues every invited vendor an RFQ for the **whole** tender scope. That
    is right at invitation time and dangerous at award time: confirming two of
    those untouched commits twice the demand. These tests exist because that
    would have been invisible — both orders confirm, both look correct, and the
    project quietly owes double.
    """

    def _tendered_qty(self, round_):
        return sum(round_.sourcing_event_id.line_ids.mapped('quantity'))

    def test_the_awarded_scope_starts_as_the_whole_bid(self):
        round_ = self._finalised_round()
        award = self._award(round_)

        allocations = award.line_ids.allocation_ids
        self.assertTrue(allocations, "No awarded scope was derived.")
        self.assertEqual(sum(allocations.mapped('quantity')),
                         self._tendered_qty(round_))
        self.assertAlmostEqual(award.amount_total,
                               sum(allocations.mapped('amount')), places=2)

    def test_a_split_award_trims_each_order_to_its_share(self):
        round_ = self._finalised_round()
        top_two = round_.candidate_ids.filtered(
            lambda c: c.rank in (1, 2))
        self.assertEqual(len(top_two), 2)
        award = self._award(round_, candidates=top_two, award_type='split',
                            justification='Capacity split across two plants')
        tendered = self._tendered_qty(round_)
        for line in award.line_ids:
            for allocation in line.allocation_ids:
                allocation.quantity = allocation.tender_quantity / 2.0

        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        award.action_issue()

        orders = award.line_ids.mapped('purchase_order_id')
        self.assertEqual(len(orders), 2)
        ordered = sum(orders.mapped('order_line').mapped('product_qty'))
        self.assertAlmostEqual(
            ordered, tendered, places=2,
            msg="The split committed %s against a tendered %s."
                % (ordered, tendered))
        for order in orders:
            self.assertEqual(order.state, 'purchase')

    def test_a_split_cannot_award_more_than_was_tendered(self):
        round_ = self._finalised_round()
        top_two = round_.candidate_ids.filtered(lambda c: c.rank in (1, 2))
        award = self._award(round_, candidates=top_two, award_type='split',
                            justification='Split')
        # Each vendor keeps the full quantity: individually valid, together
        # twice the demand.
        with self.assertRaises(UserError):
            award.action_submit()

    def test_a_partial_award_must_say_it_is_partial(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        for allocation in award.line_ids.allocation_ids:
            allocation.quantity = allocation.tender_quantity / 4.0

        with self.assertRaises(UserError):
            award.action_submit()

        award.award_type = 'partial'
        award.action_submit()
        self.assertEqual(award.state, 'review')

    def test_a_full_award_cannot_claim_to_be_partial(self):
        round_ = self._finalised_round()
        award = self._award(round_, award_type='partial')

        with self.assertRaises(UserError):
            award.action_submit()

    def test_a_partial_award_leaves_the_rest_still_reserved(self):
        """The demand did not go away because it was not awarded."""
        round_ = self._finalised_round()
        award = self._award(round_, award_type='partial')
        for allocation in award.line_ids.allocation_ids:
            allocation.quantity = allocation.tender_quantity / 4.0
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()

        award.action_issue()
        reserved, commitment, actual = self._position()

        self.assertGreater(commitment, 0.0, "Nothing was committed.")
        self.assertGreater(
            reserved, 0.0,
            "A quarter of the tender was awarded and the whole reservation "
            "was released. The rest of the demand still exists and still "
            "needs to be bought.")
        self.assertEqual(actual, 0.0)

    def test_a_vendor_awarded_nothing_is_refused_rather_than_issued_empty(self):
        round_ = self._finalised_round()
        award = self._award(round_, award_type='partial')
        for allocation in award.line_ids.allocation_ids:
            allocation.quantity = 0.0

        with self.assertRaises(UserError):
            award.action_submit()

    def test_an_allocation_cannot_exceed_its_own_tender_line(self):
        from odoo.exceptions import ValidationError
        round_ = self._finalised_round()
        award = self._award(round_)
        allocation = award.line_ids.allocation_ids[:1]

        with self.assertRaises(ValidationError):
            allocation.quantity = allocation.tender_quantity * 2.0


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7AwardReport(M7Common):
    """The document, and the word it uses for itself."""

    REPORT = 'atmta_procurement_award.report_award'

    def _html(self, award, user=None):
        Report = self.env['ir.actions.report']
        if user is not None:
            Report = Report.with_user(user)
        return Report._render_qweb_html(self.REPORT, award.ids)[0].decode()

    def test_an_unapproved_award_prints_as_a_recommendation(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()

        html = self._html(award)

        self.assertIn('Award Recommendation', html)
        self.assertNotIn('Award Decision', html)
        self.assertIn('NOT YET APPROVED', html)

    def test_an_approved_award_prints_as_a_decision(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()

        html = self._html(award)

        self.assertIn('Award Decision', html)
        self.assertIn('APPROVED, NOT YET ISSUED', html)
        self.assertIn(award.line_ids.partner_name, html)

    def test_the_report_shows_the_awarded_scope_and_authorisation(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()

        html = self._html(award)

        self.assertIn('Awarded Scope', html)
        self.assertIn('Authorisation', html)
        self.assertIn('Revalidated at Approval', html)
        self.assertIn(award.submitted_by_id.name, html)
        self.assertIn(award.approved_by_id.name, html)

    def test_the_report_says_a_partial_award_leaves_demand_reserved(self):
        round_ = self._finalised_round()
        award = self._award(round_, award_type='partial')
        for allocation in award.line_ids.allocation_ids:
            allocation.quantity = allocation.tender_quantity / 4.0
        award.action_submit()

        html = self._html(award)

        self.assertIn('Partial award', html)
        # Fragments that cannot span a line break: the template wraps these
        # sentences, so the rendered HTML carries newlines inside them.
        self.assertIn('unawarded balance', html)
        self.assertIn('still needs to be bought', html)

    def test_the_report_does_not_drift_when_a_vendor_is_renamed(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        before = self._html(award)

        award.line_ids.partner_id.name = 'Renamed After The Award Ltd'
        award.invalidate_recordset()
        award.line_ids.invalidate_recordset()

        self.assertEqual(before, self._html(award),
                         "The award document followed a vendor rename.")

    def test_the_report_creates_nothing_and_moves_no_money(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        before = self._position()

        self._html(award)

        self.assertEqual(self._position(), before)


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7IntegrityChecks(M7Common):
    """The four M7 checks, each proved to fire on the fault it names."""

    @property
    def Audit(self):
        return self.env['realestate.procurement.evaluation.audit']

    def _issued(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        award.action_issue()
        return round_, award

    def test_a_correctly_issued_award_has_no_critical_findings(self):
        self._issued()

        report = self.Audit.run(company=self.company)
        critical = [f['key'] for f in report['findings']
                    if f['severity'] == 'critical']

        self.assertEqual(critical, [], "Critical findings: %s" % critical)

    def test_it_notices_a_tender_order_confirmed_with_no_award(self):
        round_, award = self._issued()
        # Cancel the award, leaving the confirmed order behind — which is what
        # a bypass looks like from the data's point of view.
        award._engine().write({'state': 'cancelled'})

        report = self.Audit.run(company=self.company)

        self.assertIn('confirmed_tender_order_without_award',
                      [f['key'] for f in report['findings']])

    def test_it_notices_an_award_approved_by_its_own_author(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        # Rewrite history the way a data fix or SQL would.
        award._engine().write({'approved_by_id': award.submitted_by_id.id})

        report = self.Audit.run(company=self.company)

        self.assertIn('award_approved_by_its_author',
                      [f['key'] for f in report['findings']])

    def test_it_notices_more_awarded_than_tendered(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        allocation = award.line_ids.allocation_ids[:1]
        # Past the ORM constraint, the way a data fix would.
        self.env.cr.execute(
            "UPDATE realestate_procurement_award_allocation SET quantity = %s "
            "WHERE id = %s", (allocation.tender_quantity * 3.0, allocation.id))
        allocation.invalidate_recordset()

        report = self.Audit.run(company=self.company)

        self.assertIn('award_over_tender',
                      [f['key'] for f in report['findings']])

    def test_it_notices_demand_both_reserved_and_committed(self):
        """The double count, detected from the data rather than the guard."""
        round_, award = self._issued()
        reservation = self.env[
            'realestate.procurement.reservation'].sudo().search([
                ('project_id', '=', self.project.id)], limit=1)
        # Put the reservation back to holding, as if the conversion had never
        # happened — which is exactly the state the missing demand link caused.
        reservation.conversion_ids.sudo().unlink()
        reservation._engine().write({'state': 'reserved'})
        reservation.invalidate_recordset()

        report = self.Audit.run(company=self.company)

        self.assertIn('demand_reserved_and_committed',
                      [f['key'] for f in report['findings']])


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7AwardRevision(M7Common):
    """An authorisation is superseded, never edited."""

    def test_an_approved_award_is_revised_by_supersession(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        order = award.line_ids.purchase_order_id
        self.assertTrue(order._award_authorisation())

        revised = award.action_new_revision(
            reason='Vendor reduced available capacity for the programme dates')

        self.assertEqual(revised.revision, award.revision + 1)
        self.assertEqual(revised.supersedes_id, award)
        self.assertEqual(revised.state, 'draft')
        self.assertTrue(award.superseded)
        self.assertEqual(award.state, 'cancelled')
        self.assertTrue(revised.revision_reason)
        # The superseded award authorises nothing any more, and the draft
        # replacement does not authorise yet.
        self.assertFalse(order._award_authorisation())
        with self.assertRaises(UserError):
            order.button_confirm()
        self.assertEqual(self._commitment(self.project), 0.0)

    def test_a_revision_needs_a_reason(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()

        with self.assertRaises(UserError):
            award.action_new_revision()

    def test_a_revision_starts_unapproved_and_needs_two_people_again(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        approver = self._second_manager()
        award.with_user(approver).action_approve()
        revised = award.action_new_revision(reason='Quantity reduced')

        self.assertFalse(revised.submitted_by_id)
        self.assertFalse(revised.approved_by_id)
        self.assertFalse(revised.revalidation_note)
        self.assertTrue(revised.line_ids, "The revision lost its award lines.")
        self.assertTrue(revised.line_ids.allocation_ids,
                        "The revision lost its awarded scope.")

        revised.action_submit()
        with self.assertRaises(UserError):
            revised.action_approve()
        revised.with_user(approver).action_approve()
        self.assertEqual(revised.state, 'approved')

    def test_an_issued_award_cannot_be_revised(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        award.action_issue()

        with self.assertRaises(UserError):
            award.action_new_revision(reason='Too late')
        self.assertEqual(award.state, 'issued')

    def test_two_live_awards_cannot_exist_on_one_evaluation(self):
        round_ = self._finalised_round()
        self._award(round_)

        with self.assertRaises(UserError):
            self._award(round_, revision=1)


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7Migration(M7Common):
    """Upgrading describes where awarding stands. It awards nothing."""

    def test_a_finalised_evaluation_is_labelled_awaiting_an_award(self):
        round_ = self._finalised_round()

        label = self.event._classify_award_readiness()

        self.assertEqual(label, 'awaiting_award')
        self.assertFalse(self.env['realestate.procurement.award'].search(
            [('sourcing_event_id', '=', self.event.id)]),
            "Classification created an award.")
        self.assertEqual(self._commitment(self.project), 0.0)

    def test_an_unevaluated_tender_has_nothing_to_award(self):
        self._receive_bids()

        self.assertEqual(self.event._classify_award_readiness(),
                         'not_evaluated')

    def test_each_award_state_gets_its_own_label(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        self.assertEqual(self.event._classify_award_readiness(),
                         'award_drafted')

        award.action_submit()
        self.assertEqual(self.event._classify_award_readiness(),
                         'award_in_review')

        award.with_user(self._second_manager()).action_approve()
        self.assertEqual(self.event._classify_award_readiness(),
                         'award_approved')

        award.action_issue()
        self.assertEqual(self.event._classify_award_readiness(), 'awarded')

    def test_a_commitment_with_no_award_is_labelled_not_excused(self):
        """The label exists so somebody has to explain it."""
        round_, award = self._finalised_round(), None
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        award.action_issue()
        # Withdraw the authorisation, leaving the confirmed order behind.
        award._engine().write({'state': 'cancelled'})

        self.assertEqual(self.event._classify_award_readiness(),
                         'committed_without_award')

    def test_classification_is_idempotent_and_creates_nothing(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        first = self.event._classify_award_readiness()
        before = self._position()

        second = self.event._classify_award_readiness()

        self.assertEqual(first, second)
        self.assertEqual(self._position(), before)
        self.assertEqual(len(self.env['realestate.procurement.award'].search(
            [('sourcing_event_id', '=', self.event.id)])), 1)
