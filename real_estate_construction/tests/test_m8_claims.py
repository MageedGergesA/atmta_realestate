# -*- coding: utf-8 -*-
"""M8 — delay events, notices, claims, EOT, risk and issues."""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import ConstructionCommon


class ClaimCommon(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.Exposure = self.env['realestate.construction.exposure']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        self.completion = fields.Date.to_date('2027-12-31')
        self.package = self._package(
            self.project, self.contractor, value=10_000_000.0, award=True,
            current_completion_date=self.completion)

    def _determination(self, claim, cost=0.0, days=0.0, issue=True):
        determination = self.env[
            'realestate.construction.claim.determination'].create({
                'claim_id': claim.id,
                'determined_cost': cost,
                'determined_days': days,
                'reasons': '<p>Assessed against the contract and the facts.</p>',
                'authority_role': 'Engineer',
            })
        if issue:
            determination.action_issue()
        return determination


@tagged('post_install', '-at_install', 'atmta_construction')
class TestDelayEvents(ClaimCommon):

    def test_a_delay_may_run_without_an_end_date(self):
        event = self._delay_event(self.project, self.package)
        event.action_open()
        event.action_monitor()

        self.assertEqual(event.state, 'monitoring')
        self.assertTrue(event.is_ongoing)
        self.assertFalse(event.end_date,
                         "A delay that is still happening has no end date, "
                         "and demanding one invents a fact.")

    def test_the_event_type_carries_no_liability(self):
        """`weather` is a description of an event, not an excuse for one."""
        event = self._delay_event(self.project, self.package,
                                  event_type='weather')
        self.assertEqual(event.cause_category, 'undetermined',
                         "Who caused it is assessed by a person, later.")

    def test_an_event_cannot_be_assessed_without_naming_a_cause(self):
        event = self._delay_event(self.project, self.package)
        event.action_open()
        event.action_end()
        with self.assertRaises(UserError):
            event.action_assess()

        event.cause_category = 'employer'
        event.action_assess()
        self.assertEqual(event.state, 'assessed')

    def test_many_daily_reports_are_evidence_of_one_event(self):
        event = self._delay_event(self.project, self.package)
        event.action_open()
        for offset in range(3):
            report = self._daily_report(
                self.project, report_date=self.today + relativedelta(days=offset))
            self.env['realestate.construction.daily.delay'].create({
                'report_id': report.id,
                'category': 'access',
                'description': 'Access restricted at gate 2.',
                'estimated_days': 1.0,
                'delay_event_id': event.id,
            })

        event.invalidate_recordset()
        self.assertEqual(event.daily_record_count, 3,
                         "Three days of evidence for one delay — not three "
                         "delays.")

    def test_a_daily_delay_raises_an_event_and_nothing_else(self):
        report = self._daily_report(self.project)
        line = self.env['realestate.construction.daily.delay'].create({
            'report_id': report.id,
            'category': 'access',
            'description': 'Crane blocked by third-party works.',
            'estimated_days': 2.0,
        })
        event = line.action_create_delay_event()

        self.assertEqual(event.state, 'open')
        self.assertEqual(line.delay_event_id, event)
        self.assertFalse(event.notice_ids, "It issues no notice.")
        self.assertFalse(event.claim_ids, "It opens no claim.")
        self.assertEqual(self.package.approved_eot_days, 0.0,
                         "And it grants no days.")

    def test_an_event_a_claim_relies_on_cannot_be_voided(self):
        event = self._delay_event(self.project, self.package)
        event.action_open()
        claim = self._claim(self.project, self.package, claimed_days=10.0,
                            delay_event_ids=[(6, 0, event.ids)])
        claim.action_submit()

        with self.assertRaises(UserError):
            event.action_void()


@tagged('post_install', '-at_install', 'atmta_construction')
class TestNotices(ClaimCommon):

    def setUp(self):
        super().setUp()
        self.package.write({'notice_required': True, 'notice_period_days': 28})
        self.awareness = fields.Date.to_date('2027-03-01')

    def test_the_deadline_comes_from_this_contract(self):
        notice = self._notice(self.project, self.package,
                              awareness_date=self.awareness)
        self.assertTrue(notice.has_deadline)
        self.assertEqual(notice.deadline_date,
                         self.awareness + relativedelta(days=28))

    def test_a_contract_with_no_period_has_no_deadline_not_a_deadline_of_today(self):
        self.package.notice_period_days = 0
        notice = self._notice(self.project, self.package,
                              awareness_date=self.awareness)
        self.assertFalse(notice.has_deadline)
        self.assertFalse(notice.deadline_date)
        self.assertNotEqual(notice.status, 'overdue')

    def test_a_contract_that_requires_no_notice_says_so(self):
        self.package.notice_required = False
        notice = self._notice(self.project, self.package,
                              awareness_date=self.awareness)
        self.assertEqual(notice.status, 'not_required')

    def test_business_days_are_honoured_when_the_contract_uses_them(self):
        self.package.write({'notice_day_basis': 'business',
                            'notice_period_days': 10})
        notice = self._notice(self.project, self.package,
                              awareness_date=fields.Date.to_date('2027-03-01'))
        # 1 Mar 2027 is a Monday; ten business days later is 15 March.
        self.assertEqual(notice.deadline_date,
                         fields.Date.to_date('2027-03-15'))

    def test_a_notice_in_time_is_simply_issued(self):
        notice = self._notice(
            self.project, self.package, awareness_date=self.awareness,
            notice_date=self.awareness + relativedelta(days=10))
        self.assertEqual(notice.status, 'issued')
        self.assertFalse(notice.is_late)
        self.assertFalse(notice.deadline_warning)

    def test_a_late_notice_states_a_fact_about_a_date(self):
        notice = self._notice(
            self.project, self.package, awareness_date=self.awareness,
            notice_date=self.awareness + relativedelta(days=35))
        self.assertEqual(notice.status, 'late')
        self.assertIn('matter for the contract', notice.deadline_warning,
                      "The warning must not conclude anything about "
                      "entitlement.")

    def test_an_issued_notice_is_not_edited(self):
        notice = self._notice(
            self.project, self.package, awareness_date=self.awareness,
            notice_date=self.awareness + relativedelta(days=5))
        with self.assertRaises(UserError):
            notice.notice_date = self.awareness

    def test_a_notice_can_be_acknowledged_and_disputed(self):
        notice = self._notice(
            self.project, self.package, awareness_date=self.awareness,
            notice_date=self.awareness + relativedelta(days=5))
        notice.action_acknowledge()
        self.assertEqual(notice.status, 'acknowledged')
        notice.action_dispute(reason='Received out of hours.')
        self.assertEqual(notice.status, 'disputed')


@tagged('post_install', '-at_install', 'atmta_construction')
class TestClaimLifecycle(ClaimCommon):

    def test_a_cost_only_claim(self):
        claim = self._claim(self.project, self.package,
                            claimed_cost=5_000_000.0)
        claim.action_submit()
        self.assertEqual(claim.claimed_days, 0.0)
        self.assertTrue(claim.cost_claimed_known)

    def test_a_time_only_claim_is_not_rejected_for_having_no_money(self):
        claim = self._claim(self.project, self.package, claimed_days=60.0)
        claim.action_submit()
        self.assertEqual(claim.claimed_cost, 0.0)
        self.assertFalse(claim.cost_claimed_known,
                         "No cost claimed is different from a cost of zero.")
        self.assertEqual(claim.state, 'submitted')

    def test_a_claim_for_nothing_is_refused(self):
        claim = self._claim(self.project, self.package)
        with self.assertRaises(ValidationError):
            claim.action_submit()

    def test_a_claimed_cost_of_zero_is_a_statement(self):
        claim = self._claim(self.project, self.package, claimed_days=30.0,
                            cost_claimed_known=True, claimed_cost=0.0)
        claim.action_submit()
        self.assertTrue(claim.cost_claimed_known)
        self.assertEqual(claim.claimed_cost, 0.0)

    def test_the_full_walk_to_determination(self):
        claim = self._claim(self.project, self.package,
                            claimed_cost=5_000_000.0, claimed_days=60.0)
        claim.action_submit()
        claim.action_review()
        claim.assessed_cost = 3_800_000.0
        claim.assessed_days = 40.0
        claim.action_assess()
        claim.action_request_determination()
        self._determination(claim, cost=3_500_000.0, days=30.0)

        self.assertEqual(claim.state, 'determined')
        self.assertEqual(claim.claimed_cost, 5_000_000.0)
        self.assertEqual(claim.assessed_cost, 3_800_000.0)
        self.assertEqual(claim.determined_cost, 3_500_000.0)
        self.assertEqual(claim.claimed_days, 60.0)
        self.assertEqual(claim.assessed_days, 40.0)
        self.assertEqual(claim.determined_days, 30.0)

    def test_a_partial_determination_is_not_an_approve_reject_switch(self):
        claim = self._claim(self.project, self.package,
                            claimed_cost=5_000_000.0, claimed_days=60.0)
        claim.action_submit()
        self._determination(claim, cost=3_000_000.0, days=30.0)

        self.assertEqual(claim.determined_cost, 3_000_000.0)
        self.assertEqual(claim.determined_days, 30.0)
        self.assertNotIn(claim.state, ('rejected',))

    def test_a_determination_needs_reasons(self):
        claim = self._claim(self.project, self.package,
                            claimed_cost=1_000_000.0)
        claim.action_submit()
        determination = self.env[
            'realestate.construction.claim.determination'].create({
                'claim_id': claim.id, 'determined_cost': 500_000.0})
        with self.assertRaises(UserError):
            determination.action_issue()

    def test_an_issued_determination_is_superseded_not_edited(self):
        claim = self._claim(self.project, self.package,
                            claimed_cost=5_000_000.0)
        claim.action_submit()
        first = self._determination(claim, cost=3_000_000.0)

        with self.assertRaises(UserError):
            first.determined_cost = 4_000_000.0

        second = first.action_supersede({'determined_cost': 4_000_000.0,
                                         'reasons': '<p>Revised.</p>'})
        second.action_issue()
        claim.invalidate_recordset()

        self.assertEqual(first.state, 'superseded')
        self.assertEqual(first.determined_cost, 3_000_000.0,
                         "The superseded determination still says what it "
                         "said.")
        self.assertEqual(claim.determined_cost, 4_000_000.0)

    def test_settlement_keeps_every_earlier_figure(self):
        claim = self._claim(self.project, self.package,
                            claimed_cost=5_000_000.0)
        claim.action_submit()
        claim.assessed_cost = 3_800_000.0
        self._determination(claim, cost=3_500_000.0)
        claim.action_settle(cost=3_700_000.0)

        self.assertEqual(claim.claimed_cost, 5_000_000.0)
        self.assertEqual(claim.assessed_cost, 3_800_000.0)
        self.assertEqual(claim.determined_cost, 3_500_000.0)
        self.assertEqual(claim.settled_cost, 3_700_000.0)

    def test_a_claim_can_be_rejected_withdrawn_or_disputed(self):
        rejected = self._claim(self.project, self.package,
                               claimed_cost=100_000.0)
        rejected.action_submit()
        rejected.action_reject(reason='No contractual basis identified.')
        self.assertEqual(rejected.state, 'rejected')

        withdrawn = self._claim(self.project, self.package,
                                claimed_cost=100_000.0)
        withdrawn.action_submit()
        withdrawn.action_withdraw()
        self.assertEqual(withdrawn.state, 'withdrawn')

        disputed = self._claim(self.project, self.package,
                               claimed_cost=5_000_000.0)
        disputed.action_submit()
        self._determination(disputed, cost=1_000_000.0)
        disputed.action_dispute(reason='Assessment ignores the RFI record.')
        self.assertEqual(disputed.state, 'disputed')
        self.assertEqual(disputed.disputed_cost, 4_000_000.0)

    def test_an_employer_claim_runs_the_same_administration(self):
        claim = self._claim(self.project, self.package, side='employer',
                            claimed_cost=250_000.0, claim_type='damage')
        claim.action_submit()
        self.assertEqual(claim.side, 'employer')
        self.assertEqual(claim.state, 'submitted')

    def test_a_claim_with_determined_money_cannot_close_without_a_change_order(self):
        claim = self._claim(self.project, self.package,
                            claimed_cost=2_000_000.0)
        claim.action_submit()
        self._determination(claim, cost=2_000_000.0)

        with self.assertRaises(UserError):
            claim.action_close()


@tagged('post_install', '-at_install', 'atmta_construction')
class TestClaimSubmissions(ClaimCommon):

    def test_revisions_are_kept_side_by_side(self):
        claim = self._claim(self.project, self.package)
        Submission = self.env['realestate.construction.claim.submission']
        rev0 = Submission.create({'claim_id': claim.id,
                                  'claimed_cost': 5_000_000.0,
                                  'claimed_days': 60.0})
        rev0.action_issue()
        claim.invalidate_recordset()
        self.assertEqual(claim.claimed_cost, 5_000_000.0)
        self.assertEqual(claim.revision, 0)

        rev1 = Submission.create({'claim_id': claim.id,
                                  'claimed_cost': 6_000_000.0,
                                  'claimed_days': 75.0})
        rev1.action_issue()
        claim.invalidate_recordset()

        self.assertEqual(rev0.claimed_cost, 5_000_000.0,
                         "Revision 0 says what revision 0 said.")
        self.assertEqual(rev0.state, 'superseded')
        self.assertEqual(claim.revision, 1)
        self.assertEqual(claim.claimed_cost, 6_000_000.0)
        self.assertEqual(claim.current_submission_id, rev1)

    def test_an_issued_revision_is_not_rewritten(self):
        claim = self._claim(self.project, self.package)
        submission = self.env[
            'realestate.construction.claim.submission'].create({
                'claim_id': claim.id, 'claimed_cost': 1_000_000.0})
        submission.action_issue()
        with self.assertRaises(UserError):
            submission.claimed_cost = 2_000_000.0

    def test_an_ongoing_claim_can_be_updated_before_the_event_ends(self):
        event = self._delay_event(self.project, self.package)
        event.action_open()
        claim = self._claim(self.project, self.package, claimed_days=30.0,
                            delay_event_ids=[(6, 0, event.ids)])
        claim.action_submit()

        Submission = self.env['realestate.construction.claim.submission']
        Submission.create({'claim_id': claim.id, 'claimed_days': 45.0,
                           'claimed_cost': 0.0}).action_issue()

        self.assertTrue(event.is_ongoing)
        claim.invalidate_recordset()
        self.assertEqual(claim.claimed_days, 45.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestClaimQuantumAndEvidence(ClaimCommon):

    def test_quantum_lines_keep_each_stage(self):
        claim = self._claim(self.project, self.package,
                            claimed_cost=1_000_000.0)
        line = self.env['realestate.construction.claim.cost.line'].create({
            'claim_id': claim.id,
            'category': 'site_overhead',
            'description': 'Site establishment, 30 days',
            'cost_code_id': self.civil.id,
            'quantity': 30.0,
            'rate': 20_000.0,
            'claimed_amount': 600_000.0,
            'assessed_amount': 450_000.0,
            'basis': 'Monthly site running cost / 30.',
        })
        self.assertEqual(line.amount, 600_000.0)
        self.assertEqual(line.claimed_amount, 600_000.0)
        self.assertEqual(line.assessed_amount, 450_000.0)
        self.assertEqual(line.determined_amount, 0.0)

    def test_actual_cost_may_be_cited_but_is_not_entitlement(self):
        self._post_bill(self.project, self.civil, 400_000.0)
        actual = self.env[
            'realestate.construction.analytic'].actual_by_cost_code(
                self.project)
        claim = self._claim(self.project, self.package,
                            claimed_cost=actual[self.civil.id])
        line = self.env['realestate.construction.claim.cost.line'].create({
            'claim_id': claim.id,
            'category': 'labor',
            'description': 'Idle labour, posted cost',
            'cost_code_id': self.civil.id,
            'amount': actual[self.civil.id],
            'uses_actual_cost': True,
        })
        claim.action_submit()

        self.assertTrue(line.uses_actual_cost)
        totals = self.Controls.project_totals(self.project)
        self.assertEqual(totals['actual_cost'], 400_000.0,
                         "Citing actual cost in a claim does not change it.")

    def test_evidence_records_what_was_cited_and_when(self):
        report = self._daily_report(self.project)
        report.action_submit()
        report.action_close()
        claim = self._claim(self.project, self.package, claimed_days=5.0)

        evidence = self.env['realestate.construction.claim.evidence'].create({
            'claim_id': claim.id,
            'category': 'daily_report',
            'record_ref': '%s,%s' % (report._name, report.id),
            'relevance': 'Records the access restriction.',
        })

        self.assertEqual(evidence.reference, report.display_name)
        self.assertEqual(evidence.record_date, report.report_date)
        self.assertEqual(evidence.record_ref, report)

    def test_evidence_pins_the_revision_it_cited(self):
        document = self._document(self.project)
        revision_b = self._revision(document, code='B', issue=True)
        claim = self._claim(self.project, self.package, claimed_cost=1.0)

        evidence = self.env['realestate.construction.claim.evidence'].create({
            'claim_id': claim.id,
            'category': 'document',
            'record_ref': '%s,%s' % (revision_b._name, revision_b.id),
        })
        cited_label = evidence.revision_label

        # A later revision must not rewrite what the claim relied on.
        self._revision(document, code='C', issue=True)
        evidence.invalidate_recordset()

        self.assertEqual(evidence.revision_label, cited_label)
        self.assertEqual(evidence.record_ref, revision_b)

    def test_evidence_is_not_re_pointed(self):
        report = self._daily_report(self.project)
        claim = self._claim(self.project, self.package, claimed_cost=1.0)
        evidence = self.env['realestate.construction.claim.evidence'].create({
            'claim_id': claim.id,
            'category': 'daily_report',
            'record_ref': '%s,%s' % (report._name, report.id),
        })
        with self.assertRaises(UserError):
            evidence.reference = 'Something else'

    def test_a_closed_daily_report_stays_linked_and_uncopied(self):
        report = self._daily_report(self.project)
        report.action_submit()
        report.action_close()
        claim = self._claim(self.project, self.package, claimed_days=3.0)
        self.env['realestate.construction.claim.evidence'].create({
            'claim_id': claim.id,
            'category': 'daily_report',
            'record_ref': '%s,%s' % (report._name, report.id),
        })

        self.assertEqual(report.state, 'closed')
        self.assertEqual(claim.evidence_count, 1)
        self.assertEqual(
            self.env['realestate.construction.daily.report'].search_count(
                [('project_id', '=', self.project.id)]), 1,
            "Citing a record must not duplicate it.")


@tagged('post_install', '-at_install', 'atmta_construction')
class TestEOT(ClaimCommon):

    def _implemented(self, days, claimed=None):
        claim = self._claim(self.project, self.package,
                            claimed_days=claimed or days)
        claim.action_submit()
        eot = self._eot(claim, claimed_days=claimed or days,
                        determined_days=days)
        eot.action_determine()
        eot.action_implement()
        self.package.invalidate_recordset()
        return eot

    def test_one_extension(self):
        self._implemented(30.0)
        self.assertEqual(self.package.approved_eot_days, 30.0)
        self.assertEqual(self.package.current_completion_date,
                         fields.Date.to_date('2028-01-30'))
        self.assertEqual(self.package.original_completion_date,
                         self.completion)

    def test_two_extensions_accumulate_without_overwriting(self):
        first = self._implemented(30.0)
        second = self._implemented(15.0)

        self.assertEqual(self.package.approved_eot_days, 45.0)
        self.assertEqual(self.package.current_completion_date,
                         fields.Date.to_date('2028-02-14'))
        self.assertEqual(self.package.original_completion_date,
                         self.completion)
        self.assertEqual(first.completion_date_before, self.completion)
        self.assertEqual(first.completion_date_after,
                         fields.Date.to_date('2028-01-30'))
        self.assertEqual(second.completion_date_before,
                         fields.Date.to_date('2028-01-30'))

    def test_claimed_days_are_reported_apart_from_approved_days(self):
        self._implemented(30.0)
        claim = self._claim(self.project, self.package, claimed_days=45.0)
        claim.action_submit()
        self.package.invalidate_recordset()

        self.assertEqual(self.package.approved_eot_days, 30.0)
        self.assertEqual(self.package.claimed_eot_days, 45.0)
        self.assertEqual(self.package.current_completion_date,
                         fields.Date.to_date('2028-01-30'),
                         "Unapproved days never reach the contract date.")

    def test_an_implemented_extension_is_corrected_not_edited(self):
        eot = self._implemented(30.0)
        with self.assertRaises(UserError):
            eot.determined_days = 10.0

        correction = eot.action_create_correction(
            -10.0, reason='Concurrency reassessed on review.')
        correction.action_determine()
        correction.action_implement()
        self.package.invalidate_recordset()

        self.assertEqual(eot.determined_days, 30.0,
                         "The original determination still says 30.")
        self.assertEqual(self.package.approved_eot_days, 20.0)
        self.assertEqual(self.package.current_completion_date,
                         fields.Date.to_date('2028-01-20'))

    def test_negative_days_need_a_correction_record(self):
        claim = self._claim(self.project, self.package, claimed_days=10.0)
        with self.assertRaises(ValidationError):
            self._eot(claim, determined_days=-5.0)

    def test_implementing_is_idempotent(self):
        eot = self._implemented(30.0)
        eot.action_implement()
        self.package.invalidate_recordset()
        self.assertEqual(self.package.approved_eot_days, 30.0)

    def test_an_extension_needs_a_package_with_a_contract_date(self):
        bare = self._package(self.project, self.contractor, value=100_000.0)
        claim = self._claim(self.project, bare, claimed_days=10.0)
        eot = self._eot(claim, determined_days=10.0)
        eot.action_determine()
        with self.assertRaises(UserError):
            eot.action_implement()


@tagged('post_install', '-at_install', 'atmta_construction')
class TestRisk(ClaimCommon):

    def test_score_and_severity(self):
        risk = self._risk(self.project, cost_exposure=500_000.0)
        self.assertEqual(risk.overall_impact, 4)
        self.assertEqual(risk.inherent_score, 12)
        self.assertEqual(risk.severity, 'high')

    def test_a_human_rating_overrides_the_arithmetic(self):
        risk = self._risk(self.project)
        risk.management_rating = 'critical'
        self.assertEqual(risk.severity, 'critical')

    def test_cost_and_schedule_impact_stay_separate(self):
        risk = self._risk(self.project, cost_impact_score=1,
                          schedule_impact_score=5)
        self.assertEqual(risk.cost_impact_score, 1)
        self.assertEqual(risk.schedule_impact_score, 5)
        self.assertEqual(risk.overall_impact, 5,
                         "A risk that is catastrophic for time is a severe "
                         "risk even when it costs nothing.")

    def test_the_scale_is_configurable(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.risk_scale_maximum', '10')
        risk = self._risk(self.project, probability=8)
        self.assertEqual(risk.probability, 8)

        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.risk_scale_maximum', '5')
        with self.assertRaises(ValidationError):
            self._risk(self.project, probability=8)

    def test_mitigation_needs_a_plan(self):
        risk = self._risk(self.project, mitigation_plan=False)
        risk.action_assess()
        with self.assertRaises(UserError):
            risk.action_plan_response()

    def test_overdue_mitigation_actions_are_counted(self):
        risk = self._risk(self.project)
        Action = self.env['realestate.construction.risk.action']
        Action.create({'risk_id': risk.id, 'name': 'Order long-lead plant',
                       'due_date': self.today - relativedelta(days=5)})
        Action.create({'risk_id': risk.id, 'name': 'Second source',
                       'due_date': self.today + relativedelta(days=30)})
        risk.invalidate_recordset()

        self.assertEqual(risk.open_action_count, 2)
        self.assertEqual(risk.overdue_action_count, 1)

    def test_residual_score_is_recorded_separately(self):
        risk = self._risk(self.project)
        risk.write({'residual_probability': 1, 'residual_impact': 2})
        self.assertEqual(risk.inherent_score, 12)
        self.assertEqual(risk.residual_score, 2)

    def test_a_risk_is_not_in_the_forecast_until_somebody_puts_it_there(self):
        risk = self._risk(self.project, cost_exposure=1_000_000.0)
        self.assertFalse(risk.include_in_forecast)

        exposure = self.Exposure.for_project(self.project)
        self.assertEqual(exposure['risk_identified'], 1_000_000.0)
        self.assertEqual(exposure['risk_only'], 0.0,
                         "Identified is not the same as carried.")

        risk.action_include_in_forecast()
        exposure = self.Exposure.for_project(self.project)
        self.assertEqual(exposure['risk_only'], 1_000_000.0)

    def test_a_materialised_risk_is_not_closed_away(self):
        risk = self._risk(self.project, cost_exposure=100_000.0)
        risk.action_materialise(reason='It happened.')
        with self.assertRaises(UserError):
            risk.action_close()

    def test_the_snapshot_answers_did_we_see_it_coming(self):
        risk = self._risk(self.project, cost_exposure=750_000.0)
        risk.action_assess()
        risk.action_plan_response()
        risk.action_monitor()
        risk.action_materialise(reason='Supplier entered administration.')

        self.assertIn('monitoring', risk.materialised_snapshot)
        self.assertIn('750000', risk.materialised_snapshot.replace('.0', ''))
        self.assertIn('Supplier entered administration',
                      risk.materialised_snapshot)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestIssue(ClaimCommon):

    def test_an_issue_closes_on_a_resolution_not_a_date(self):
        issue = self._issue(self.project,
                            due_date=self.today - relativedelta(days=10))
        self.assertTrue(issue.is_overdue)
        with self.assertRaises(UserError):
            issue.action_close()

        issue.resolution = 'Utility diverted; access restored.'
        issue.action_close()
        self.assertEqual(issue.state, 'closed')
        self.assertEqual(issue.resolved_on, self.today)

    def test_escalation_raises_an_activity(self):
        issue = self._issue(self.project, priority='critical')
        issue.action_escalate('project_manager')

        self.assertEqual(issue.escalation_level, 'project_manager')
        self.assertTrue(issue.activity_ids,
                        "Escalation uses Odoo's activities rather than a "
                        "second workflow engine.")

    def test_an_issue_may_reference_an_ncr_without_replacing_it(self):
        ncr = self._ncr(self.project, self.contractor)
        issue = self._issue(self.project, source_ncr_id=ncr.id,
                            category='quality')
        self.assertEqual(issue.source_ncr_id, ncr)
        self.assertEqual(ncr.state, 'open',
                         "The NCR keeps its own specialised process.")

    def test_an_issue_creates_a_change_event_and_no_money_moves(self):
        self._baselined(self.project, 5_000_000.0, code=self.civil)
        before = self.Controls.project_totals(self.project)

        issue = self._issue(self.project, estimated_cost_impact=300_000.0)
        event = issue.action_create_change_event()

        after = self.Controls.project_totals(self.project)
        self.assertEqual(event.source, 'issue')
        self.assertEqual(event.source_id, issue.id)
        self.assertEqual(after['current_budget'], before['current_budget'])
        self.assertEqual(after['current_commitment'],
                         before['current_commitment'])

    def test_an_issue_may_raise_a_delay_event(self):
        issue = self._issue(self.project)
        event = issue.action_create_delay_event()
        self.assertEqual(issue.delay_event_id, event)
        self.assertEqual(event.project_id, self.project)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestExposureHierarchy(ClaimCommon):
    """M8W — one event, five names, counted once."""

    def test_the_ladder_counts_each_event_at_its_furthest_rung(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)

        risk = self._risk(self.project, cost_exposure=2_000_000.0)
        risk.action_include_in_forecast()
        self.assertEqual(
            self.Exposure.for_project(self.project)['potential_commercial'],
            2_000_000.0)

        issue = risk.action_materialise(reason='It happened.')
        issue.estimated_cost_impact = 2_000_000.0
        self.assertEqual(
            self.Exposure.for_project(self.project)['potential_commercial'],
            2_000_000.0, "Risk → issue is still one exposure.")

        event = issue.action_create_change_event()
        self.assertEqual(
            self.Exposure.for_project(self.project)['potential_commercial'],
            2_000_000.0, "Risk → issue → change event is still one.")

        claim = self._claim(self.project, self.package,
                            claimed_cost=2_000_000.0,
                            change_event_ids=[(6, 0, event.ids)])
        claim.action_submit()
        exposure = self.Exposure.for_project(self.project)
        self.assertEqual(exposure['potential_commercial'], 2_000_000.0,
                         "Four registers, one problem, one number.")
        self.assertEqual(exposure['claim_only'], 2_000_000.0)
        self.assertEqual(exposure['change_only'], 0.0)
        self.assertEqual(exposure['issue_only'], 0.0)
        self.assertEqual(exposure['risk_only'], 0.0)

    def test_approved_change_is_baseline_not_exposure(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        totals_before = self.Controls.project_totals(self.project)

        order = self._change_order(
            self.project, lines=[(self.civil, 'budget', 2_000_000.0)])
        self._approve_and_implement(order)

        totals_after = self.Controls.project_totals(self.project)
        self.assertEqual(
            totals_after['current_budget'],
            totals_before['current_budget'] + 2_000_000.0,
            "Approved change moves the baseline, once, through M4.")

    def test_the_explanation_says_what_the_number_is_not(self):
        self._risk(self.project, cost_exposure=1_000_000.0)
        text = self.Exposure.explain(self.project)
        self.assertIn('counted once', text)
        self.assertIn('Authorised change is not included', text)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestClaimToChange(ClaimCommon):
    """M8J — determined money reaches the baseline only through M4."""

    def test_a_determination_creates_a_draft_change_order_only(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        before = self.Controls.project_totals(self.project)

        claim = self._claim(self.project, self.package,
                            claimed_cost=5_000_000.0)
        claim.action_submit()
        self.env['realestate.construction.claim.cost.line'].create({
            'claim_id': claim.id, 'category': 'labor',
            'description': 'Idle labour', 'cost_code_id': self.civil.id,
            'amount': 3_000_000.0})
        self._determination(claim, cost=3_000_000.0)

        order = claim.action_create_change_order()
        after = self.Controls.project_totals(self.project)

        self.assertEqual(order.state, 'draft',
                         "Created, never silently authorised.")
        self.assertEqual(claim.change_order_id, order)
        self.assertEqual(after['current_budget'], before['current_budget'])
        self.assertEqual(after['current_commitment'],
                         before['current_commitment'])

        self._approve_and_implement(order)
        final = self.Controls.project_totals(self.project)
        self.assertEqual(final['current_commitment'],
                         before['current_commitment'] + 3_000_000.0)

    def test_a_change_order_is_not_created_twice(self):
        claim = self._claim(self.project, self.package,
                            claimed_cost=1_000_000.0)
        claim.action_submit()
        self.env['realestate.construction.claim.cost.line'].create({
            'claim_id': claim.id, 'category': 'other',
            'description': 'Extra', 'cost_code_id': self.civil.id,
            'amount': 1_000_000.0})
        self._determination(claim, cost=1_000_000.0)
        claim.action_create_change_order()
        with self.assertRaises(UserError):
            claim.action_create_change_order()

    def test_nothing_determined_means_nothing_to_authorise(self):
        claim = self._claim(self.project, self.package,
                            claimed_cost=1_000_000.0)
        claim.action_submit()
        with self.assertRaises(UserError):
            claim.action_create_change_order()


@tagged('post_install', '-at_install', 'atmta_construction')
class TestClaimGovernance(ClaimCommon):

    def test_a_claim_is_not_determined_by_the_person_who_submitted_it(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.allow_self_determination', 'False')
        claim = self._claim(self.project, self.package,
                            claimed_cost=1_000_000.0)
        claim.action_submit()
        determination = self.env[
            'realestate.construction.claim.determination'].create({
                'claim_id': claim.id, 'determined_cost': 900_000.0,
                'reasons': '<p>Because.</p>'})

        with self.assertRaises(UserError):
            determination.action_issue()

    def test_configuration_may_permit_it(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.allow_self_determination', 'True')
        claim = self._claim(self.project, self.package,
                            claimed_cost=1_000_000.0)
        claim.action_submit()
        self._determination(claim, cost=900_000.0)
        self.assertEqual(claim.determined_cost, 900_000.0)
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.allow_self_determination', 'False')


@tagged('post_install', '-at_install', 'atmta_construction')
class TestClaimSecurity(ClaimCommon):

    def _site_engineer(self):
        user = self.env['res.users'].create({
            'name': 'Site Engineer', 'login': 'site.engineer.m8',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_construction.group_construction_user').id,
            ])],
        })
        return user

    def test_a_site_engineer_cannot_read_claims(self):
        claim = self._claim(self.project, self.package,
                            claimed_cost=5_000_000.0)
        engineer = self._site_engineer()
        with self.assertRaises(AccessError):
            claim.with_user(engineer).read(['title'])

    def test_a_site_engineer_still_records_the_facts(self):
        engineer = self._site_engineer()
        event = self._delay_event(self.project, self.package)
        event.with_user(engineer).read(['title'])

        risk = self._risk(self.project)
        risk.with_user(engineer).read(['title'])

        issue = self._issue(self.project)
        issue.with_user(engineer).read(['title'])

    def test_commercial_fields_are_restricted_server_side(self):
        """Hiding a field in a view leaves it readable through a relation.

        Wave 17 moved claims below this module, so the field can no longer
        name the legacy commercial group: doing so would make the claims
        module depend on the one that depends on it. It names the canonical
        role instead, and the Wave 12 bridge gives every legacy commercial
        holder that role, so the same people read the same fields. What this
        test guards is that the restriction is on the *field*, server side,
        and not merely on a view.
        """
        claim = self._claim(self.project, self.package,
                            claimed_cost=5_000_000.0)
        canonical = 'atmta_roles.group_construction_commercial_manager'
        self.assertEqual(claim._fields['assessed_cost'].groups, canonical)
        self.assertEqual(
            claim._fields['internal_position'].groups, canonical)

        # And the restriction bites, through the bridge: a user holding only
        # the legacy commercial group reads them, because that group implies
        # the canonical role; a site engineer does not.
        commercial = self.env['res.users'].create({
            'name': 'Commercial Manager', 'login': 'commercial.w17',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_construction.group_construction_commercial').id,
            ])],
        })
        claim.with_user(commercial).read(['assessed_cost'])
        with self.assertRaises(AccessError):
            claim.with_user(self._site_engineer()).read(['assessed_cost'])


@tagged('post_install', '-at_install', 'atmta_construction')
class TestMultiCompanyIsolation(ClaimCommon):

    def test_a_claim_cannot_cross_a_company_boundary(self):
        other = self.env['res.company'].create({'name': 'Other Co M8'})
        with self.assertRaises(ValidationError):
            self._claim(self.project, self.package, claimed_cost=1.0,
                        company_id=other.id)

    def test_an_eot_cannot_extend_another_projects_package(self):
        other_project = self._project()
        claim = self._claim(other_project, claimed_days=10.0)
        with self.assertRaises(ValidationError):
            self.env['realestate.construction.eot'].create({
                'project_id': other_project.id,
                'package_id': self.package.id,
                'claim_id': claim.id,
                'determined_days': 10.0,
            })

    def test_a_risk_and_an_issue_carry_their_company(self):
        risk = self._risk(self.project)
        issue = self._issue(self.project)
        self.assertEqual(risk.company_id, self.company)
        self.assertEqual(issue.company_id, self.company)
