# -*- coding: utf-8 -*-
"""M6 — ITP, inspections, observations, NCRs and daily reports."""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import ConstructionCommon


class QualityCommon(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.Inspection = self.env['realestate.construction.inspection']
        self.NCR = self.env['realestate.construction.ncr']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')


@tagged('post_install', '-at_install', 'atmta_construction')
class TestITP(QualityCommon):

    def test_an_itp_walks_to_active(self):
        itp = self._itp(self.project, self.contractor)

        itp.action_submit()
        self.assertEqual(itp.state, 'review')
        itp.action_approve()
        itp.action_activate()

        self.assertEqual(itp.state, 'active')
        self.assertTrue(itp.effective_date)
        self.assertEqual(itp.revision, 0)

    def test_an_empty_plan_cannot_be_submitted(self):
        itp = self.env['realestate.construction.itp'].create({
            'title': 'Empty', 'project_id': self.project.id})

        with self.assertRaises(UserError):
            itp.action_submit()

    def test_an_active_plan_cannot_be_edited(self):
        itp = self._active_itp(self.project, self.contractor)

        with self.assertRaises(UserError):
            itp.write({'item_ids': [(0, 0, {'activity': 'Sneaked in'})]})

    def test_a_revision_leaves_the_active_plan_alone(self):
        itp = self._active_itp(self.project, self.contractor)

        revision = itp.action_create_revision()

        self.assertEqual(revision.revision, 1)
        self.assertEqual(revision.state, 'draft')
        self.assertEqual(len(revision.item_ids), len(itp.item_ids))
        self.assertEqual(itp.state, 'active')

    def test_superseding_moves_the_active_revision(self):
        itp = self._active_itp(self.project, self.contractor)
        revision = itp.action_create_revision()
        revision.action_submit()
        revision.action_approve()

        itp.action_supersede()

        self.assertEqual(itp.state, 'superseded')
        self.assertEqual(revision.state, 'active')
        self.assertEqual(itp.superseded_by_id, revision)

    def test_a_hold_point_declares_that_work_needs_release(self):
        itp = self._active_itp(self.project, self.contractor,
                               points=(('hold',), ('witness',), ('normal',)))

        hold = itp.item_ids.filtered(
            lambda i: i.inspection_point == 'hold')
        witness = itp.item_ids.filtered(
            lambda i: i.inspection_point == 'witness')

        self.assertTrue(hold.work_release_required)
        self.assertFalse(witness.work_release_required)
        self.assertEqual(itp.hold_point_count, 1)

    def test_an_itp_cannot_cross_companies(self):
        other = self.env['res.company'].create({'name': 'ITP Other Co'})
        other_project = self._project()
        other_project.company_id = other

        with self.assertRaises(ValidationError):
            self.env['realestate.construction.itp'].create({
                'title': 'Cross', 'project_id': other_project.id,
                'company_id': self.company.id})


@tagged('post_install', '-at_install', 'atmta_construction')
class TestInspection(QualityCommon):

    def test_a_request_records_its_lead_time(self):
        request = self._inspection_request(
            self.project, self.contractor,
            required_datetime=fields.Datetime.now() + relativedelta(hours=48))

        self.assertAlmostEqual(request.lead_time_hours, 48.0, places=0)
        self.assertFalse(request.lead_time_shortfall)

    def test_short_notice_is_reported_not_blocked(self):
        """Sites work. The shortfall is visible; the request still stands."""
        request = self._inspection_request(
            self.project, self.contractor,
            required_datetime=fields.Datetime.now() + relativedelta(hours=2))

        request.action_request()

        self.assertTrue(request.lead_time_shortfall)
        self.assertEqual(request.state, 'requested')

    def test_a_request_produces_an_inspection_carrying_its_context(self):
        itp = self._active_itp(self.project, self.contractor,
                               points=(('hold',),))
        document = self._document(self.project)
        revision = self._revision(document, code='A', approve=True)
        request = self._inspection_request(
            self.project, self.contractor, itp_id=itp.id,
            itp_item_id=itp.item_ids[0].id,
            document_revision_id=revision.id)
        request.action_request()
        request.action_schedule()

        inspection = request.action_create_inspection()

        self.assertEqual(inspection.itp_id, itp)
        self.assertEqual(inspection.itp_revision, itp.revision)
        self.assertEqual(inspection.document_revision_id, revision)
        self.assertEqual(inspection.inspection_point, 'hold')
        self.assertEqual(request.state, 'in_progress')

    def test_a_checklist_is_copied_not_referenced(self):
        """Editing the company template later must not change this sheet."""
        template = self.env[
            'realestate.construction.checklist.template'].create({
                'name': 'Concrete pour',
                'line_ids': [(0, 0, {'name': 'Formwork clean',
                                     'item_type': 'pass_fail'}),
                             (0, 0, {'name': 'Slump',
                                     'item_type': 'measurement',
                                     'minimum_value': 80.0,
                                     'maximum_value': 120.0})],
            })
        itp = self._active_itp(self.project, self.contractor)
        itp.item_ids[0].sudo().checklist_template_id = template
        inspection = self._inspection(
            self.project, self.contractor, itp_item_id=itp.item_ids[0].id)

        inspection.action_start()
        template.line_ids[0].name = 'Renamed next year'

        self.assertEqual(len(inspection.checklist_line_ids), 2)
        self.assertEqual(
            inspection.checklist_line_ids[0].name, 'Formwork clean',
            "The sheet keeps the wording it was judged against.")

    def test_a_measurement_knows_whether_it_is_in_tolerance(self):
        inspection = self._inspection(self.project, self.contractor)
        line = self.env['realestate.construction.inspection.line'].create({
            'inspection_id': inspection.id, 'name': 'Slump',
            'item_type': 'measurement', 'minimum_value': 80.0,
            'maximum_value': 120.0, 'measured_value': 95.0})

        self.assertTrue(line.within_tolerance)

        line.measured_value = 140.0
        self.assertFalse(line.within_tolerance)

    def test_results_are_richer_than_pass_or_fail(self):
        for result in ('accepted', 'accepted_with_comments', 'rejected',
                       'reinspection_required', 'not_applicable'):
            inspection = self._inspection(self.project, self.contractor)
            inspection.action_start()
            inspection.action_record_result(result)
            self.assertEqual(inspection.result, result)

    def test_accepted_with_comments_is_not_rejected(self):
        inspection = self._inspection(self.project, self.contractor)
        inspection.action_start()

        inspection.action_record_result('accepted_with_comments',
                                        'Touch up the edges.')

        self.assertEqual(inspection.result, 'accepted_with_comments')
        self.assertEqual(
            self.Inspection.is_quality_released(project=self.project), False,
            "This one was not a hold point, so nothing was released.")

    def test_an_inspection_cannot_be_accepted_on_unanswered_checks(self):
        inspection = self._inspection(self.project, self.contractor)
        self.env['realestate.construction.inspection.line'].create({
            'inspection_id': inspection.id, 'name': 'Formwork clean',
            'is_mandatory': True})
        inspection.action_start()

        with self.assertRaises(UserError):
            inspection.action_record_result('accepted')

    def test_a_recorded_result_cannot_be_rewritten(self):
        inspection = self._inspection(self.project, self.contractor)
        inspection.action_start()
        inspection.action_record_result('rejected')

        with self.assertRaises(UserError):
            inspection.write({'result': 'accepted'})
        with self.assertRaises(UserError):
            inspection.action_record_result('accepted')

    def test_an_accepted_inspection_cannot_be_reinspected(self):
        inspection = self._inspection(self.project, self.contractor)
        inspection.action_start()
        inspection.action_record_result('accepted')

        with self.assertRaises(UserError):
            inspection.action_create_reinspection()

    def test_a_reinspection_chain_numbers_itself(self):
        first = self._inspection(self.project, self.contractor)
        first.action_start()
        first.action_record_result('rejected')
        second = first.action_create_reinspection()
        second.action_start()
        second.action_record_result('rejected')
        third = second.action_create_reinspection()

        self.assertEqual(second.reinspection_sequence, 1)
        self.assertEqual(third.reinspection_sequence, 2)
        self.assertEqual(third.parent_inspection_id, first,
                         "The chain hangs off the original, not the last one.")
        self.assertTrue(first.is_first_inspection)
        self.assertFalse(second.is_first_inspection)

    def test_a_passed_hold_point_releases_the_work(self):
        itp = self._active_itp(self.project, self.contractor,
                               points=(('hold',),))
        wbs = self._wbs(self.project, code='03', name='Concrete')
        inspection = self._inspection(
            self.project, self.contractor, wbs_id=wbs.id,
            itp_id=itp.id, itp_item_id=itp.item_ids[0].id)
        inspection.action_start()

        self.assertFalse(
            self.Inspection.is_quality_released(
                wbs=wbs, project=self.project))

        inspection.action_record_result('accepted')

        self.assertTrue(
            self.Inspection.is_quality_released(
                wbs=wbs, project=self.project),
            "Quality release is a fact M7's certification may consult — it is "
            "not payment certification itself.")

    def test_a_failed_hold_point_releases_nothing(self):
        itp = self._active_itp(self.project, self.contractor,
                               points=(('hold',),))
        wbs = self._wbs(self.project, code='03', name='Concrete')
        inspection = self._inspection(
            self.project, self.contractor, wbs_id=wbs.id,
            itp_id=itp.id, itp_item_id=itp.item_ids[0].id)
        inspection.action_start()
        inspection.action_record_result('rejected')

        self.assertFalse(
            self.Inspection.is_quality_released(
                wbs=wbs, project=self.project))

    def test_a_failed_check_must_say_what_happened_next(self):
        inspection = self._inspection(self.project, self.contractor)
        line = self.env['realestate.construction.inspection.line'].create({
            'inspection_id': inspection.id, 'name': 'Sealant',
            'is_mandatory': False})
        inspection.action_start()
        line.passed = 'fail'

        with self.assertRaises(UserError):
            inspection.action_record_result('rejected')

        line.disposition = 'corrected'
        line.disposition_reason = 'Sealant applied during the inspection.'
        inspection.action_record_result('accepted_with_comments')
        self.assertEqual(inspection.result, 'accepted_with_comments')

    def test_a_material_inspection_does_not_touch_inventory(self):
        product = self.env['product.product'].create(
            {'name': 'Rebar T16', 'type': 'consu'})
        request = self._inspection_request(
            self.project, self.contractor, inspection_type='material',
            product_id=product.id, quantity=50.0)
        request.action_request()
        inspection = request.action_create_inspection()
        inspection.action_start()

        pickings_before = self.env['stock.picking'].search_count([])
        inspection.action_record_result('rejected', 'Mill certificate missing.')

        self.assertEqual(
            self.env['stock.picking'].search_count([]), pickings_before,
            "Quality records what it found. Inventory owns the goods.")


@tagged('post_install', '-at_install', 'atmta_construction')
class TestObservationAndNCR(QualityCommon):

    def test_an_observation_is_verified_before_it_closes(self):
        observation = self._observation(self.project, self.contractor,
                                        due_date=self.today)

        observation.action_require_action()
        with self.assertRaises(UserError):
            observation.action_ready_for_verification()

        observation.corrective_action = 'Sealant applied.'
        observation.action_ready_for_verification()
        observation.action_verify()

        self.assertEqual(observation.state, 'closed')
        self.assertEqual(observation.verified_by_id, self.env.user)
        self.assertTrue(observation.completed_date)

    def test_an_overdue_observation_is_visible(self):
        observation = self._observation(
            self.project, self.contractor,
            due_date=self.today - relativedelta(days=3))

        self.assertTrue(observation.is_overdue)

    def test_escalating_an_observation_keeps_the_observation(self):
        observation = self._observation(self.project, self.contractor,
                                        severity='major')

        ncr = observation.action_escalate_to_ncr(reason='Recurring defect.')

        self.assertTrue(observation.exists())
        self.assertEqual(observation.ncr_id, ncr)
        self.assertEqual(ncr.source_observation_id, observation)
        self.assertEqual(ncr.severity, 'major')
        with self.assertRaises(UserError):
            observation.action_escalate_to_ncr()

    def test_a_failed_inspection_does_not_create_an_ncr_by_itself(self):
        """The rule that keeps the NCR register meaningful."""
        inspection = self._inspection(self.project, self.contractor)
        inspection.action_start()

        inspection.action_record_result('rejected', 'Minor honeycombing.')

        self.assertFalse(
            self.NCR.search([('source_inspection_id', '=', inspection.id)]),
            "Auto-raising an NCR for every rejection buries the ones that "
            "matter.")

    def test_an_ncr_needs_a_root_cause_before_a_disposition(self):
        ncr = self._ncr(self.project, self.contractor)
        ncr.action_investigate()
        ncr.proposed_disposition = 'rework'

        with self.assertRaises(UserError):
            ncr.action_propose_disposition()

        ncr.root_cause_category = 'workmanship'
        ncr.action_propose_disposition()
        self.assertEqual(ncr.state, 'disposition_proposed')

    def test_use_as_is_needs_a_manager(self):
        site_user = self.env['res.users'].create({
            'name': 'Site Engineer', 'login': 'qa_site_%d' % self._next(),
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_construction.group_construction_user').id])],
        })
        ncr = self._ncr(self.project, self.contractor)
        ncr.action_investigate()
        ncr.write({'proposed_disposition': 'use_as_is',
                   'root_cause_category': 'design'})

        with self.assertRaises(UserError):
            ncr.with_user(site_user).action_propose_disposition()

    def test_the_full_ncr_lifecycle(self):
        ncr = self._ncr(self.project, self.contractor,
                        target_completion_date=self.today)
        ncr.action_investigate()
        ncr.write({'proposed_disposition': 'rework',
                   'root_cause_category': 'workmanship',
                   'root_cause': 'Pour rushed before formwork check.'})
        ncr.action_propose_disposition()
        ncr.action_approve_disposition()

        with self.assertRaises(UserError):
            ncr.action_ready_for_verification()

        ncr.corrective_action = 'Section cut out and recast.'
        ncr.action_ready_for_verification()
        ncr.action_verify()
        ncr.action_close()

        self.assertEqual(ncr.state, 'closed')
        self.assertTrue(ncr.verified_by_id)
        self.assertTrue(ncr.closure_date)
        self.assertEqual(ncr.closure_time_days, 0)

    def test_an_ncr_cannot_close_without_verification(self):
        ncr = self._ncr(self.project, self.contractor)

        with self.assertRaises(UserError):
            ncr.action_close()

    def test_the_person_who_did_the_work_does_not_verify_it(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.allow_self_approval', 'False')
        ncr = self._ncr(self.project, self.contractor, severity='critical')
        ncr.assigned_to_id = self.env.user
        ncr.action_investigate()
        ncr.write({'proposed_disposition': 'rework',
                   'root_cause_category': 'workmanship'})
        ncr.action_propose_disposition()
        ncr.action_approve_disposition()
        ncr.corrective_action = 'Recast.'
        ncr.action_ready_for_verification()

        with self.assertRaises(UserError):
            ncr.action_verify()

    def test_a_closed_ncr_is_evidence(self):
        ncr = self._ncr(self.project, self.contractor)
        ncr.action_investigate()
        ncr.write({'proposed_disposition': 'repair',
                   'root_cause_category': 'material'})
        ncr.action_propose_disposition()
        ncr.action_approve_disposition()
        ncr.corrective_action = 'Repaired.'
        ncr.action_ready_for_verification()
        ncr.action_verify()
        ncr.action_close()

        with self.assertRaises(UserError):
            ncr.write({'description': 'Rewritten later'})
        with self.assertRaises(UserError):
            ncr.action_void()
        with self.assertRaises(UserError):
            ncr.action_reopen()

        ncr.action_reopen(reason='Defect recurred in the same bay.')
        self.assertEqual(ncr.state, 'reopened')
        self.assertEqual(ncr.reopen_reason,
                         'Defect recurred in the same bay.')
        self.assertTrue(ncr.closure_date, "The previous closure is preserved.")

    def test_quality_exposure_is_offered_to_forecasting_never_inserted(self):
        self._ncr(self.project, self.contractor,
                  estimated_rework_cost=250_000.0, cost_impact='potential')
        self._ncr(self.project, self.contractor,
                  estimated_rework_cost=100_000.0, cost_impact='potential')
        self._baselined(self.project, 10_000_000.0, code=self.civil)

        exposure = self.NCR.quality_exposure(self.project)
        forecast = self._forecast(self.project)

        self.assertEqual(exposure['potential_rework_cost'], 350_000.0)
        self.assertEqual(exposure['open_ncr_count'], 2)
        self.assertEqual(
            forecast.total_etc, 0.0,
            "A forecast does not absorb quality exposure on its own — the "
            "cost controller decides.")

    def test_an_ncr_cannot_cross_companies(self):
        other = self.env['res.company'].create({'name': 'NCR Other Co'})
        other_project = self._project()
        other_project.company_id = other

        with self.assertRaises(ValidationError):
            self.env['realestate.construction.ncr'].create({
                'title': 'Cross', 'description': 'x',
                'project_id': other_project.id,
                'company_id': self.company.id})


@tagged('post_install', '-at_install', 'atmta_construction')
class TestDailyReport(QualityCommon):

    def test_a_project_has_one_report_per_date_and_shift(self):
        self._daily_report(self.project)

        with self.assertRaises(Exception):
            self._daily_report(self.project)
            self.env.flush_all()

    def test_manpower_comes_from_the_labour_logs(self):
        report = self._daily_report(self.project)
        Labor = self.env['realestate.construction.labor.log']
        Labor.create({
            'name': 'Steel fixers', 'project_id': self.project.id,
            'date': self.today, 'workers': 12, 'hours': 9.0,
            'hourly_rate': 25.0, 'contractor_id': self.contractor.id,
            'daily_report_id': report.id})
        Labor.create({
            'name': 'Carpenters', 'project_id': self.project.id,
            'date': self.today, 'workers': 8, 'hours': 9.0,
            'hourly_rate': 25.0, 'contractor_id': self.contractor.id,
            'daily_report_id': report.id})
        report.invalidate_recordset()

        self.assertEqual(report.total_workers, 20)
        self.assertEqual(report.total_labor_hours, 180.0)

    def test_closing_freezes_the_manpower_summary(self):
        report = self._daily_report(self.project)
        log = self.env['realestate.construction.labor.log'].create({
            'name': 'Steel fixers', 'project_id': self.project.id,
            'date': self.today, 'workers': 12, 'hours': 9.0,
            'hourly_rate': 25.0, 'contractor_id': self.contractor.id,
            'daily_report_id': report.id})
        report.action_submit()
        report.action_review()
        report.action_close()
        snapshot = report.labor_summary_snapshot

        log.workers = 99
        report.invalidate_recordset()

        self.assertIn('12 workers', snapshot)
        self.assertEqual(
            report.labor_summary_snapshot, snapshot,
            "The day's evidence does not change when somebody edits a log "
            "afterwards.")

    def test_a_closed_report_needs_an_amendment_reason(self):
        report = self._daily_report(self.project)
        report.action_submit()
        report.action_close()

        with self.assertRaises(UserError):
            report.write({'overall_notes': '<p>Rewritten</p>'})
        with self.assertRaises(UserError):
            report.action_amend()

        report.action_amend(reason='Weather entry corrected.')
        self.assertEqual(report.state, 'submitted')
        self.assertEqual(report.amendment_reason, 'Weather entry corrected.')

    def test_reported_production_does_not_certify_anything(self):
        boq = self._boq(self.project, quantities=((100.0, 1_000.0),))
        line = boq.line_ids[0]
        report = self._daily_report(self.project)
        self.env['realestate.construction.daily.work'].create({
            'report_id': report.id,
            'description': 'Slab pour, grid A-C',
            'quantity': 40.0,
            'boq_line_id': line.id,
        })
        report.action_submit()
        report.action_close()
        line.invalidate_recordset()

        self.assertEqual(
            line.certified_qty, 0.0,
            "Production observed on site is not quantity certified for "
            "payment — that is a separate process with its own controls.")

    def test_a_delay_may_become_a_change_event_only_deliberately(self):
        report = self._daily_report(self.project)
        delay = self.env['realestate.construction.daily.delay'].create({
            'report_id': report.id, 'category': 'access',
            'description': 'No access to level 3.', 'estimated_days': 1.0})

        self.assertFalse(delay.change_event_id)

        event = delay.action_create_change_event()

        self.assertEqual(event.source, 'delay')
        self.assertEqual(event.estimated_schedule_days, 1)
        self.assertEqual(
            self.env['realestate.construction.change.order'].search_count(
                [('project_id', '=', self.project.id)]), 0,
            "A change event is not a change order.")

    def test_equipment_and_deliveries_are_recorded_not_accounted(self):
        report = self._daily_report(self.project)
        self.env['realestate.construction.daily.equipment'].create({
            'report_id': report.id, 'name': 'Tower crane TC-1',
            'working_hours': 8.0, 'idle_hours': 2.0})
        self.env['realestate.construction.daily.delivery'].create({
            'report_id': report.id, 'description': '30 tonnes of rebar',
            'quantity': 30.0})
        before = self.Controls.project_totals(self.project)
        report.action_submit()
        report.action_close()

        self.assertEqual(self.Controls.project_totals(self.project), before)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestQualityCrossMilestone(QualityCommon):

    def test_the_full_quality_to_change_path(self):
        """Rejected inspection → NCR → change event → M4 approval."""
        budget = self._baselined(self.project, 10_000_000.0, code=self.civil)
        document = self._document(self.project, number='S-STR-DRG-9001')
        rev_b = self._revision(document, code='B', approve=True,
                               purpose='construction')

        inspection = self._inspection(
            self.project, self.contractor, document_revision_id=rev_b.id)
        inspection.action_start()
        inspection.action_record_result('rejected', 'Cover insufficient.')

        ncr = self._ncr(
            self.project, self.contractor,
            source_inspection_id=inspection.id,
            cost_code_id=self.civil.id,
            estimated_rework_cost=500_000.0, cost_impact='potential')
        event = ncr.action_create_change_event()

        self.assertEqual(
            self.Controls.project_totals(self.project)['current_budget'],
            10_000_000.0)

        order = self._change_order(
            self.project, order_type='budget_change', event=event,
            lines=[(self.civil, 'budget', 500_000.0)])
        self._approve_and_implement(order)
        budget.invalidate_recordset()

        totals = self.Controls.project_totals(self.project)
        self.assertEqual(totals['original_budget'], 10_000_000.0)
        self.assertEqual(totals['approved_budget_changes'], 500_000.0)
        self.assertEqual(totals['current_budget'], 10_500_000.0)

        # And the inspection still cites the revision it was performed against.
        self._revision(document, code='C', approve=True,
                       purpose='construction')
        inspection.invalidate_recordset()
        self.assertEqual(inspection.document_revision_id, rev_b)

    def test_an_ncr_verification_inspection_continues_the_chain(self):
        inspection = self._inspection(self.project, self.contractor)
        inspection.action_start()
        inspection.action_record_result('rejected')
        ncr = self._ncr(self.project, self.contractor,
                        source_inspection_id=inspection.id)

        verification = ncr.action_create_reinspection()
        verification.action_start()
        verification.action_record_result('accepted')

        self.assertEqual(verification.parent_inspection_id, inspection)
        self.assertEqual(ncr.reinspection_id, verification)
        self.assertEqual(inspection.result, 'rejected')

    def test_quality_records_never_move_the_control_equations(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 3_000_000.0)])
        self._post_bill(self.project, self.civil, 1_000_000.0)
        before = self.Controls.project_totals(self.project)

        itp = self._active_itp(self.project, self.contractor,
                               points=(('hold',),))
        request = self._inspection_request(self.project, self.contractor,
                                           itp_id=itp.id)
        request.action_request()
        inspection = request.action_create_inspection()
        inspection.action_start()
        inspection.action_record_result('rejected')
        self._observation(self.project, self.contractor,
                          inspection_id=inspection.id)
        self._ncr(self.project, self.contractor,
                  source_inspection_id=inspection.id,
                  estimated_rework_cost=750_000.0)
        report = self._daily_report(self.project)
        report.action_submit()
        report.action_close()

        self.assertEqual(self.Controls.project_totals(self.project), before)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestM6FormButtons(QualityCommon):
    """A button cannot pass an argument. These are the doors the form uses."""

    def test_each_inspection_result_has_its_own_button(self):
        itp = self._active_itp(self.project, self.contractor)
        for method, expected in (
                ('action_accept', 'accepted'),
                ('action_accept_with_comments', 'accepted_with_comments'),
                ('action_reject_result', 'rejected'),
                ('action_require_reinspection', 'reinspection_required'),
                ('action_not_applicable', 'not_applicable')):
            inspection = self._inspection(self.project, self.contractor,
                                          itp_id=itp.id)
            inspection.action_start()
            getattr(inspection, method)()
            self.assertEqual(inspection.result, expected, method)

    def test_a_reason_prompt_carries_the_reason_to_the_record(self):
        ncr = self._ncr(self.project, self.contractor)
        ncr.action_investigate()
        ncr.proposed_disposition = 'repair'
        ncr.root_cause_category = 'workmanship'
        ncr.action_propose_disposition()
        ncr.action_approve_disposition()
        ncr.corrective_action = 'Broken section cut out and recast.'
        ncr.action_ready_for_verification()
        ncr.action_verify()
        ncr.action_close()

        action = ncr.action_open_reason_wizard()
        wizard = self.env['realestate.construction.reason.wizard'].with_context(
            **action['context']).create({'reason': '   '})
        self.assertEqual(wizard.mode, 'reopen_ncr')
        self.assertEqual(wizard.res_id, ncr.id)

        with self.assertRaises(UserError):
            wizard.action_confirm()

        wizard.reason = 'Client rejected the repair on site.'
        wizard.action_confirm()
        self.assertEqual(ncr.state, 'reopened')
        self.assertEqual(ncr.reopen_reason, 'Client rejected the repair on site.')

    def test_amending_a_closed_report_through_the_prompt(self):
        report = self._daily_report(self.project)
        report.action_submit()
        report.action_close()
        action = report.action_open_reason_wizard()
        wizard = self.env['realestate.construction.reason.wizard'].with_context(
            **action['context']).create({'reason': 'Night pour was missed.'})
        wizard.action_confirm()
        self.assertEqual(report.state, 'submitted')
        self.assertEqual(report.amendment_reason, 'Night pour was missed.')
