# -*- coding: utf-8 -*-
"""M5 — RFI, submittal, document control and transmittal test matrix."""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import ConstructionCommon


class InformationCommon(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')


@tagged('post_install', '-at_install', 'atmta_construction')
class TestRFI(InformationCommon):

    def test_an_rfi_is_numbered_and_opens(self):
        rfi = self._rfi(self.project)

        self.assertTrue(rfi.name.startswith('RFI/'))
        self.assertEqual(rfi.state, 'draft')
        rfi.action_open()
        self.assertEqual(rfi.state, 'open')
        self.assertTrue(rfi.submitted_date)

    def test_the_ball_in_court_says_who_must_act(self):
        consultant = self.env['res.partner'].create({'name': 'Consultant'})
        rfi = self._rfi(self.project, reviewer_partner_id=consultant.id)

        self.assertEqual(rfi.ball_in_court, 'originator')
        rfi.action_open()
        self.assertEqual(rfi.ball_in_court, 'consultant')
        rfi.write({'official_response': '<p>Use 200mm.</p>'})
        rfi.action_answer()
        self.assertEqual(rfi.ball_in_court, 'originator')
        rfi.action_close()
        self.assertEqual(rfi.ball_in_court, 'none')

    def test_an_rfi_cannot_be_answered_without_an_official_response(self):
        rfi = self._rfi(self.project)
        rfi.action_open()

        with self.assertRaises(UserError):
            rfi.action_answer()

    def test_closing_without_an_answer_needs_a_stated_reason(self):
        rfi = self._rfi(self.project)
        rfi.action_open()

        with self.assertRaises(UserError):
            rfi.action_close()

        rfi.closure_reason = 'Withdrawn by the contractor.'
        rfi.action_close()
        self.assertEqual(rfi.state, 'closed')

    def test_a_revised_response_never_erases_the_one_it_replaces(self):
        rfi = self._rfi(self.project)
        rfi.action_open()
        rfi.write({'official_response': '<p>Use 200mm.</p>'})
        rfi.action_answer()

        rfi.action_reopen()
        rfi.write({'official_response': '<p>Correction: use 250mm.</p>'})
        rfi.action_answer()

        history = rfi.response_history_ids
        self.assertEqual(len(history), 2)
        self.assertTrue(any('200mm' in h.response for h in history))
        with self.assertRaises(UserError):
            history[0].write({'response': '<p>rewritten</p>'})
        with self.assertRaises(UserError):
            history[0].unlink()

    def test_overdue_is_measured_against_the_required_date(self):
        from dateutil.relativedelta import relativedelta
        rfi = self._rfi(
            self.project,
            required_response_date=self.today - relativedelta(days=5))
        rfi.action_open()

        self.assertTrue(rfi.is_overdue)
        self.assertEqual(rfi.overdue_days, 5)

    def test_response_time_uses_real_dates_not_today(self):
        from dateutil.relativedelta import relativedelta
        rfi = self._rfi(self.project)
        rfi.action_open()
        rfi.write({
            'submitted_date': self.today - relativedelta(days=10),
            'official_response': '<p>Answered.</p>',
            'actual_response_date': self.today - relativedelta(days=4),
        })
        rfi.action_answer()

        self.assertEqual(rfi.response_time_days, 6)
        self.assertFalse(
            rfi.is_overdue,
            "An answered RFI does not keep accruing overdue days.")

    def test_an_answered_rfi_cannot_be_voided(self):
        rfi = self._rfi(self.project)
        rfi.action_open()
        rfi.write({'official_response': '<p>Answered.</p>'})
        rfi.action_answer()

        with self.assertRaises(UserError):
            rfi.action_void()

    def test_an_rfi_creates_at_most_one_change_event(self):
        rfi = self._rfi(self.project, cost_impact='potential',
                        estimated_cost_impact=500_000.0)
        rfi.action_open()
        rfi.action_create_change_event()

        with self.assertRaises(UserError):
            rfi.action_create_change_event()

    def test_an_rfi_cannot_cross_companies(self):
        other = self.env['res.company'].create({'name': 'RFI Other Co'})
        other_project = self._project()
        other_project.company_id = other

        with self.assertRaises(ValidationError):
            self.env['realestate.construction.rfi'].create({
                'subject': 'Cross-company', 'question': '<p>?</p>',
                'project_id': other_project.id,
                'company_id': self.company.id,
            })


@tagged('post_install', '-at_install', 'atmta_construction')
class TestDocumentControl(InformationCommon):

    def test_a_document_carries_its_identity_across_revisions(self):
        document = self._document(self.project, number='A-ARC-DRG-2001')
        rev_a = self._revision(document, code='A')
        rev_b = self._revision(document, code='B')
        document.invalidate_recordset()

        self.assertEqual(document.revision_count, 2)
        self.assertEqual(document.current_revision_id, rev_b)
        self.assertEqual(rev_a.state, 'superseded')
        self.assertEqual(rev_a.superseded_by_id, rev_b)
        self.assertEqual(rev_b.supersedes_id, rev_a)

    def test_revision_order_is_by_sequence_not_by_string(self):
        """'10' sorts before '9' — the trap this exists to avoid."""
        document = self._document(self.project, number='A-ARC-DRG-2002')
        for code in ('8', '9', '10'):
            self._revision(document, code=code)
        document.invalidate_recordset()

        self.assertEqual(document.current_revision_id.revision_code, '10')

    def test_three_currents_are_three_different_questions(self):
        document = self._document(self.project, number='A-ARC-DRG-2003')
        rev_a = self._revision(document, code='A', purpose='construction')
        rev_a.action_approve()
        rev_b = self._revision(document, code='B', purpose='review')
        rev_b.action_issue_for_review()
        document.invalidate_recordset()

        self.assertEqual(document.current_revision_id, rev_b)
        self.assertEqual(
            document.current_approved_revision_id, rev_a,
            "The latest revision is not the latest approved one.")
        self.assertEqual(
            document.current_issued_revision_id, rev_a,
            "And the site is still building from Rev A.")

    def test_status_and_purpose_are_separate(self):
        document = self._document(self.project)
        revision = self._revision(document, code='A', purpose='construction')
        revision.action_approve()

        self.assertEqual(revision.state, 'approved')
        self.assertEqual(revision.purpose_of_issue, 'construction')

    def test_an_issued_revision_cannot_have_its_file_replaced(self):
        document = self._document(self.project)
        revision = self._revision(document, code='A', issue=True)
        replacement = self._attachment('sneaky.pdf', b'DIFFERENT')

        with self.assertRaises(UserError):
            revision.write({'attachment_id': replacement.id})
        with self.assertRaises(UserError):
            revision.write({'revision_code': 'A2'})

    def test_a_draft_revision_may_have_its_file_corrected(self):
        """Rule 3 — a typo fix before issue is a file version, not Rev B."""
        document = self._document(self.project)
        revision = self._revision(document, code='A')
        self.assertEqual(revision.file_version, 1)

        revision.write({'attachment_id': self._attachment('fixed.pdf').id})

        self.assertEqual(revision.file_version, 2)
        self.assertEqual(
            revision.revision_code, 'A',
            "Correcting a title block does not tell the project there is a "
            "new revision.")

    def test_an_issued_revision_cannot_be_deleted(self):
        document = self._document(self.project)
        revision = self._revision(document, code='A', issue=True)

        with self.assertRaises(UserError):
            revision.unlink()

    def test_a_revision_without_a_file_cannot_be_issued(self):
        document = self._document(self.project)
        revision = self.env[
            'realestate.construction.document.revision'].create({
                'document_id': document.id, 'revision_code': 'A'})

        with self.assertRaises(UserError):
            revision.action_issue_for_review()

    def test_a_transmitted_revision_cannot_be_voided(self):
        document = self._document(self.project)
        revision = self._revision(document, code='A', issue=True)
        transmittal = self._transmittal(self.project, revisions=revision)
        transmittal.action_send()

        with self.assertRaises(UserError):
            revision.action_void()

    def test_document_numbers_are_unique_within_a_project(self):
        self._document(self.project, number='A-ARC-DRG-3001')

        with self.assertRaises(Exception):
            self._document(self.project, number='A-ARC-DRG-3001')
            self.env.flush_all()


@tagged('post_install', '-at_install', 'atmta_construction')
class TestSubmittal(InformationCommon):

    def test_a_submittal_starts_with_revision_zero(self):
        submittal = self._submittal(self.project, self.contractor)

        self.assertEqual(submittal.revision_count, 1)
        self.assertEqual(submittal.current_revision_code, '0')
        self.assertEqual(submittal.state, 'draft')

    def test_state_and_response_are_different_things(self):
        submittal = self._submittal(self.project, self.contractor)
        submittal.action_submit()
        submittal.current_revision_id.action_respond('revise_resubmit')

        self.assertEqual(submittal.state, 'responded')
        self.assertEqual(submittal.final_response, 'revise_resubmit')

    def test_a_reviewed_revision_cannot_be_resubmitted(self):
        submittal = self._submittal(self.project, self.contractor)
        submittal.action_submit()
        submittal.current_revision_id.action_respond('revise_resubmit')

        with self.assertRaises(UserError):
            submittal.action_submit()

    def test_a_revision_cannot_be_responded_to_twice(self):
        submittal = self._submittal(self.project, self.contractor)
        submittal.action_submit()
        revision = submittal.current_revision_id
        revision.action_respond('approved')

        with self.assertRaises(UserError):
            revision.action_respond('rejected')

    def test_a_new_revision_needs_the_previous_one_answered(self):
        submittal = self._submittal(self.project, self.contractor)
        submittal.action_submit()

        with self.assertRaises(UserError):
            submittal.action_create_revision()

    def test_parallel_reviewers_must_all_reply_before_finalising(self):
        submittal = self._submittal(self.project, self.contractor)
        submittal.action_submit()
        revision = submittal.current_revision_id
        Review = self.env['realestate.construction.submittal.review']
        first = Review.create({'revision_id': revision.id, 'role': 'Structural'})
        Review.create({'revision_id': revision.id, 'role': 'MEP'})
        first.action_respond('approved')
        revision.invalidate_recordset()

        self.assertEqual(revision.reviews_responded, 1)
        self.assertEqual(revision.reviews_required, 2)
        self.assertFalse(revision.reviews_complete)
        with self.assertRaises(UserError):
            revision.action_respond('approved')

    def test_a_manager_may_finalise_deliberately(self):
        submittal = self._submittal(self.project, self.contractor)
        submittal.action_submit()
        revision = submittal.current_revision_id
        self.env['realestate.construction.submittal.review'].create(
            {'revision_id': revision.id, 'role': 'Structural'})

        revision.action_respond('approved', force=True)

        self.assertEqual(revision.response, 'approved')

    def test_an_approved_submittal_approves_its_controlled_revision(self):
        """§45 — but only when it explicitly references a registered one."""
        document = self._document(self.project)
        doc_revision = self._revision(document, code='A', issue=True)
        submittal = self._submittal(self.project, self.contractor)
        submittal.current_revision_id.document_revision_id = doc_revision
        submittal.action_submit()

        submittal.current_revision_id.action_respond('approved_as_noted')
        doc_revision.invalidate_recordset()

        self.assertEqual(doc_revision.state, 'approved_with_comments')
        self.assertTrue(doc_revision.approved_on)

    def test_a_submittal_awaiting_resubmission_cannot_be_closed(self):
        submittal = self._submittal(self.project, self.contractor)
        submittal.action_submit()
        submittal.current_revision_id.action_respond('revise_resubmit')

        with self.assertRaises(UserError):
            submittal.action_close()

    def test_a_responded_revisions_submission_is_immutable(self):
        submittal = self._submittal(self.project, self.contractor)
        submittal.action_submit()
        revision = submittal.current_revision_id
        revision.action_respond('rejected')

        with self.assertRaises(UserError):
            revision.write({'submitted_date': self.today})

    def test_a_package_groups_submittals_without_owning_their_responses(self):
        package = self.env[
            'realestate.construction.submittal.package'].create({
                'name': 'MEP Shop Drawings', 'project_id': self.project.id})
        first = self._submittal(self.project, self.contractor,
                                submittal_package_id=package.id)
        self._submittal(self.project, self.contractor,
                        submittal_package_id=package.id)
        first.action_submit()
        first.current_revision_id.action_respond('approved')
        package.invalidate_recordset()

        self.assertEqual(package.submittal_count, 2)
        self.assertEqual(package.responded_count, 1)
        self.assertEqual(first.final_response, 'approved')


@tagged('post_install', '-at_install', 'atmta_construction')
class TestTransmittal(InformationCommon):

    def test_a_transmittal_needs_documents_and_recipients(self):
        empty = self._transmittal(self.project, revisions=None)

        with self.assertRaises(UserError):
            empty.action_send()

    def test_sending_freezes_the_content(self):
        document = self._document(self.project, number='A-STR-DRG-4001')
        revision = self._revision(document, code='B', issue=True)
        transmittal = self._transmittal(self.project, revisions=revision)

        transmittal.action_send()

        self.assertEqual(transmittal.state, 'sent')
        self.assertTrue(transmittal.sent_date)
        with self.assertRaises(UserError):
            transmittal.line_ids.write({'copies': 5})
        with self.assertRaises(UserError):
            transmittal.line_ids.unlink()
        with self.assertRaises(UserError):
            transmittal.action_cancel()

    def test_a_draft_transmittal_may_be_cancelled(self):
        document = self._document(self.project)
        revision = self._revision(document, code='A', issue=True)
        transmittal = self._transmittal(self.project, revisions=revision)

        transmittal.action_cancel()

        self.assertEqual(transmittal.state, 'cancelled')

    def test_acknowledgement_is_receipt_not_approval(self):
        document = self._document(self.project)
        revision = self._revision(document, code='A', issue=True)
        recipient = self.env['res.partner'].create({'name': 'Consultant Ltd'})
        transmittal = self._transmittal(self.project, revisions=revision,
                                        recipients=recipient)
        transmittal.action_send()

        transmittal.action_acknowledge(partner=recipient,
                                       comments='Received 12 March.')

        self.assertEqual(transmittal.state, 'acknowledged')
        self.assertEqual(transmittal.acknowledged_by_id, recipient)
        self.assertEqual(
            revision.state, 'for_review',
            "Acknowledging receipt did not approve anything.")

    def test_an_overdue_acknowledgement_is_visible(self):
        from dateutil.relativedelta import relativedelta
        document = self._document(self.project)
        revision = self._revision(document, code='A', issue=True)
        transmittal = self._transmittal(
            self.project, revisions=revision,
            response_due_date=self.today - relativedelta(days=7))
        transmittal.action_send()
        transmittal.invalidate_recordset()

        self.assertTrue(transmittal.is_acknowledgement_overdue)
        self.assertEqual(transmittal.acknowledgement_overdue_days, 7)

    def test_several_revisions_may_be_transmitted_together(self):
        first = self._revision(self._document(self.project), code='A',
                               issue=True)
        second = self._revision(self._document(self.project), code='B',
                                issue=True)
        transmittal = self._transmittal(self.project,
                                        revisions=first | second)

        transmittal.action_send()

        self.assertEqual(transmittal.document_count, 2)
        self.assertEqual(
            set(transmittal.line_ids.mapped('revision_code')), {'A', 'B'})


@tagged('post_install', '-at_install', 'atmta_construction')
class TestInformationToChange(InformationCommon):
    """M5P — the cross-module flow, pinned permanently."""

    def test_drawing_to_rfi_to_change_event_to_approved_change(self):
        budget = self._baselined(self.project, 10_000_000.0, code=self.civil)
        document = self._document(self.project, number='A-ARC-DRG-5001')
        rev_b = self._revision(document, code='B', issue=True)

        # 1. A question about Rev B.
        rfi = self._rfi(
            self.project, cost_impact='potential',
            estimated_cost_impact=2_000_000.0,
            cost_code_id=self.civil.id,
            document_revision_ids=[(6, 0, rev_b.ids)])
        rfi.action_open()
        rfi.write({'official_response':
                   '<p>Thicker section required throughout.</p>'})
        rfi.action_answer()

        self.assertEqual(
            self.Controls.project_totals(self.project)['current_budget'],
            10_000_000.0,
            "An answered RFI has changed no money.")

        # 2. Somebody decides it is commercial.
        event = rfi.action_create_change_event()
        self.assertEqual(event.source, 'rfi')
        self.assertEqual(event.source_model, rfi._name)
        self.assertEqual(event.source_id, rfi.id)
        self.assertEqual(
            self.Controls.project_totals(self.project)['current_budget'],
            10_000_000.0,
            "Raising a change event has still changed no money.")

        # 3. M4 approves and implements it — and only now does anything move.
        order = self._change_order(
            self.project, order_type='budget_change', event=event,
            lines=[(self.civil, 'budget', 2_000_000.0)])
        self._approve_and_implement(order)
        budget.invalidate_recordset()

        totals = self.Controls.project_totals(self.project)
        self.assertEqual(totals['original_budget'], 10_000_000.0)
        self.assertEqual(totals['approved_budget_changes'], 2_000_000.0)
        self.assertEqual(totals['current_budget'], 12_000_000.0)

        # 4. And the question still refers to the revision it was asked about.
        self._revision(document, code='C', issue=True)
        rfi.invalidate_recordset()
        self.assertEqual(rfi.document_revision_ids, rev_b)

    def test_submittal_end_to_end_with_a_transmittal(self):
        """M5Q — Rev 0 rejected, Rev 1 approved, Rev 1 transmitted."""
        document = self._document(self.project, number='M-MEP-SHD-6001')
        doc_rev_0 = self._revision(document, code='0', issue=True)
        submittal = self._submittal(self.project, self.contractor)
        submittal.current_revision_id.document_revision_id = doc_rev_0
        submittal.action_submit()
        submittal.current_revision_id.action_respond(
            'revise_resubmit', 'Duct sizes missing.')

        rev1 = submittal.action_create_revision()
        doc_rev_1 = self._revision(document, code='1', issue=True)
        rev1.document_revision_id = doc_rev_1
        submittal.action_submit()
        rev1.action_respond('approved', 'Fit for construction.')

        transmittal = self._transmittal(self.project, revisions=doc_rev_1)
        transmittal.action_send()
        document.invalidate_recordset()

        self.assertEqual(submittal.final_response, 'approved')
        self.assertEqual(document.current_approved_revision_id, doc_rev_1)
        self.assertEqual(transmittal.line_ids.revision_code, '1')
        self.assertEqual(
            submittal.revision_ids[0].response, 'revise_resubmit',
            "Rev 0 keeps the comment that caused Rev 1.")
        self.assertEqual(doc_rev_0.state, 'superseded')

    def test_information_records_never_touch_the_ledger(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 3_000_000.0)])
        self._post_bill(self.project, self.civil, 1_000_000.0)
        before = self.Controls.project_totals(self.project)

        document = self._document(self.project)
        revision = self._revision(document, code='A', issue=True)
        rfi = self._rfi(self.project, estimated_cost_impact=5_000_000.0)
        rfi.action_open()
        submittal = self._submittal(self.project, self.contractor)
        submittal.action_submit()
        submittal.current_revision_id.action_respond('rejected')
        transmittal = self._transmittal(self.project, revisions=revision)
        transmittal.action_send()

        after = self.Controls.project_totals(self.project)
        self.assertEqual(after, before,
                         "Information control describes the project; it does "
                         "not spend its money.")
