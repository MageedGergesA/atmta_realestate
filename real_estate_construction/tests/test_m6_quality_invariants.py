# -*- coding: utf-8 -*-
"""M6 — the four invariants quality control is built around.

Written before the models.

```
    A   a failed inspection moves no money
    B   an inspection was performed against an exact revision, forever
    C   a reinspection is a new event; the failure stays failed
    D   a daily report is evidence, not entitlement
```
"""

from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestQualityInvariants(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')

    # ------------------------------------------------------------------
    def test_a_a_failed_inspection_and_its_ncr_move_no_money(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 4_000_000.0)])
        self._post_bill(self.project, self.civil, 1_000_000.0)
        before = self.Controls.project_totals(self.project)

        inspection = self._inspection(self.project, self.contractor)
        inspection.action_start()
        inspection.action_record_result('rejected', 'Cover to reinforcement '
                                        'below specification.')
        ncr = self._ncr(self.project, self.contractor,
                        source_inspection_id=inspection.id,
                        estimated_rework_cost=500_000.0,
                        cost_impact='potential')

        after = self.Controls.project_totals(self.project)
        self.assertEqual(after, before,
                         "Quality control describes the works. It does not "
                         "spend the project's money.")
        self.assertEqual(after['current_budget'], 10_000_000.0)

        event = ncr.action_create_change_event()
        self.assertEqual(event.source, 'ncr_corrective_work')
        self.assertEqual(
            self.Controls.project_totals(self.project)['current_budget'],
            10_000_000.0,
            "Raising a change event is not approving one.")
        exposure = self.env['realestate.construction.change.event'].exposure(
            self.project)
        self.assertEqual(exposure['potential_cost'], 500_000.0)
        self.assertEqual(exposure['approved_cost'], 0.0)

    def test_b_an_inspection_records_the_revision_it_was_performed_against(self):
        document = self._document(self.project, number='A-STR-DRG-7001')
        rev_b = self._revision(document, code='B', approve=True,
                               purpose='construction')

        inspection = self._inspection(
            self.project, self.contractor,
            document_revision_id=rev_b.id)
        inspection.action_start()
        inspection.action_record_result('accepted')

        self._revision(document, code='C', approve=True,
                       purpose='construction')
        document.invalidate_recordset()
        inspection.invalidate_recordset()

        self.assertEqual(document.current_revision_id.revision_code, 'C')
        self.assertEqual(
            inspection.document_revision_id, rev_b,
            "The work was inspected against Rev B, and always was.")

    def test_c_a_reinspection_does_not_erase_the_failure(self):
        first = self._inspection(self.project, self.contractor)
        first.action_start()
        first.action_record_result('rejected', 'Honeycombing at the base.')

        second = first.action_create_reinspection()
        second.action_start()
        second.action_record_result('accepted', 'Repaired and acceptable.')
        first.invalidate_recordset()

        self.assertEqual(first.result, 'rejected')
        self.assertEqual(first.state, 'responded')
        self.assertEqual(second.result, 'accepted')
        self.assertEqual(second.parent_inspection_id, first)
        self.assertEqual(second.reinspection_sequence, 1)
        self.assertTrue(
            first.has_reinspection,
            "The chain is visible from the failure as well as the pass.")

    def test_d_a_daily_report_is_evidence_not_entitlement(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        before = self.Controls.project_totals(self.project)

        report = self._daily_report(self.project)
        self.env['realestate.construction.daily.delay'].create({
            'report_id': report.id,
            'category': 'weather',
            'description': 'Heavy rain stopped concrete pours.',
            'estimated_days': 2.0,
        })
        report.action_submit()
        report.action_review()
        report.action_close()

        after = self.Controls.project_totals(self.project)
        self.assertEqual(after, before)
        self.assertFalse(
            self.env['realestate.construction.change.order'].search(
                [('project_id', '=', self.project.id)]),
            "A recorded delay is not a change order.")
        self.assertFalse(
            self.env['realestate.construction.change.event'].search(
                [('project_id', '=', self.project.id)]),
            "Nor a change event — somebody decides that, later.")
        self.assertEqual(report.total_delay_days, 2.0)
        self.assertEqual(report.state, 'closed')
