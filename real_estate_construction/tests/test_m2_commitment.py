# -*- coding: utf-8 -*-
"""M2 — contract packages, the commitment engine, and the Cost Report.

The single most important assertion in this file is that a package and its
purchase order are **one** commitment. An 8M package executed by an 8M order is
8M committed, not 16M — and a system that got that wrong would make every
project look ruined.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestContractPackage(ConstructionCommon):

    def test_a_package_walks_draft_to_closed(self):
        package = self._package(self._project(), value=5_000_000.0)

        package.action_tender()
        self.assertEqual(package.state, 'tender')
        package.action_award()
        self.assertEqual(package.state, 'awarded')
        package.action_activate()
        self.assertEqual(package.state, 'active')
        package.action_substantially_complete()
        package.action_complete()
        package.action_close()
        self.assertEqual(package.state, 'closed')

    def test_awarding_freezes_the_original_value(self):
        package = self._package(self._project(), value=5_000_000.0)

        package.action_award()

        self.assertEqual(package.original_contract_value, 5_000_000.0)
        self.assertEqual(package.current_contract_value, 5_000_000.0)
        self.assertTrue(package.awarded_on)
        self.assertEqual(package.awarded_by_id, self.env.user)

    def test_the_original_value_is_never_recalculated_from_a_later_document(self):
        """Phase 0's defect: commitment read live PO totals, so "original"
        changed whenever somebody edited an order."""
        project = self._project()
        contractor = self._contractor()
        package = self._package(project, contractor, value=5_000_000.0,
                                award=True)
        code = self._cost_code('SUB-CIV', 'Civil', 'subcontract')

        self._po(project, contractor, [(code, 7_000_000.0)], package=package)
        package.invalidate_recordset()

        self.assertEqual(
            package.original_contract_value, 5_000_000.0,
            "What was agreed is a fact about the past.")

    def test_current_value_is_original_plus_approved_variations(self):
        package = self._package(self._project(), value=5_000_000.0,
                                award=True)

        package.sudo().write({'approved_variation_amount': 750_000.0})

        self.assertEqual(package.current_contract_value, 5_750_000.0)
        self.assertEqual(package.original_contract_value, 5_000_000.0)

    def test_a_package_cannot_be_awarded_without_a_contractor(self):
        package = self.env[
            'realestate.construction.contract.package'].create({
                'title': 'Nobody', 'project_id': self._project().id,
                'tender_value': 1_000.0})

        with self.assertRaises(UserError):
            package.action_award()

    def test_a_package_worth_nothing_cannot_be_awarded(self):
        package = self._package(self._project(), value=0.0)

        with self.assertRaises(UserError):
            package.action_award()

    def test_an_awarded_package_cannot_be_reopened(self):
        package = self._package(self._project(), value=1_000.0, award=True)

        with self.assertRaises(UserError):
            package.action_reset_to_draft()

    def test_a_package_with_confirmed_orders_cannot_be_cancelled(self):
        project = self._project()
        contractor = self._contractor()
        package = self._package(project, contractor, value=1_000_000.0,
                                award=True)
        code = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        self._po(project, contractor, [(code, 1_000_000.0)], package=package)

        with self.assertRaises(UserError):
            package.action_cancel()

    def test_a_contractor_from_another_company_cannot_hold_a_package(self):
        other = self.env['res.company'].create({'name': 'Package Other Co'})
        contractor = self._contractor()
        contractor.partner_id.company_id = other

        with self.assertRaises(ValidationError):
            self._package(self._project(), contractor, value=1_000.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCommitmentEngine(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Commitment = self.env['realestate.construction.commitment']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        self.mep = self._cost_code('SUB-MEP', 'MEP', 'subcontract')

    def test_a_confirmed_order_commits(self):
        self._po(self.project, self.contractor, [(self.civil, 2_000_000.0)])

        self.assertEqual(
            self.Commitment.current_commitment(self.project), 2_000_000.0)

    def test_an_rfq_does_not(self):
        self._po(self.project, self.contractor, [(self.civil, 2_000_000.0)],
                 confirm=False)

        self.assertEqual(
            self.Commitment.current_commitment(self.project), 0.0)

    def test_cancelling_removes_the_commitment_and_keeps_the_order(self):
        po = self._po(self.project, self.contractor,
                      [(self.civil, 2_000_000.0)])

        po.button_cancel()

        self.assertEqual(
            self.Commitment.current_commitment(self.project), 0.0)
        self.assertTrue(po.exists())
        self.assertEqual(po.state, 'cancel')

    def test_commitment_is_untaxed(self):
        """§20 — pinned permanently. Phase 0 found this measured tax-inclusive.

        Budget 1,000,000; PO 1,000,000 + 150,000 VAT; commitment 1,000,000.
        """
        vat = self._vat(15.0)
        po = self._po(self.project, self.contractor,
                      [(self.civil, 1_000_000.0)], tax=vat)

        self.assertEqual(po.amount_untaxed, 1_000_000.0)
        self.assertEqual(po.amount_total, 1_150_000.0)
        self.assertEqual(
            self.Commitment.current_commitment(self.project), 1_000_000.0,
            "VAT is not 15% more building.")

    def test_commitment_is_aggregated_per_cost_code(self):
        """§19 — one order, several codes, and no arbitrary allocation."""
        plant = self._cost_code('EQP-01', 'Plant', 'equipment')
        self._po(self.project, self.contractor, [
            (self.civil, 2_000_000.0),
            (self.mep, 3_000_000.0),
            (plant, 1_000_000.0),
        ])

        by_code = self.Commitment.current_commitment_by_cost_code(self.project)

        self.assertEqual(by_code[self.civil.id], 2_000_000.0)
        self.assertEqual(by_code[self.mep.id], 3_000_000.0)
        self.assertEqual(by_code[plant.id], 1_000_000.0)

    def test_an_uncoded_line_is_reported_not_dropped(self):
        """Money committed against a project with no cost code is a fact
        somebody needs to see, not a rounding error."""
        self._po(self.project, self.contractor, [(None, 500_000.0)])

        by_code = self.Commitment.current_commitment_by_cost_code(self.project)

        self.assertEqual(by_code[False], 500_000.0)
        self.assertEqual(
            self.Commitment.current_commitment(self.project), 500_000.0)

    def test_a_package_and_its_po_are_one_commitment(self):
        """THE double-count test. 8M package + 8M order = 8M, never 16M."""
        package = self._package(self.project, self.contractor,
                                value=8_000_000.0, award=True)
        self._po(self.project, self.contractor, [(self.civil, 8_000_000.0)],
                 package=package)
        package.invalidate_recordset()

        self.assertEqual(
            self.Commitment.current_commitment(self.project), 8_000_000.0)
        self.assertEqual(package.commitment_source, 'purchase_orders')
        self.assertEqual(package.committed_amount, 8_000_000.0)

    def test_an_awarded_package_without_orders_commits_its_own_value(self):
        package = self._package(self.project, self.contractor,
                                value=4_000_000.0, award=True)
        package.cost_code_ids = self.civil

        self.assertEqual(
            self.Commitment.current_commitment(self.project), 4_000_000.0)
        self.assertEqual(package.commitment_source, 'package')

    def test_a_draft_package_commits_nothing(self):
        self._package(self.project, self.contractor, value=4_000_000.0)

        self.assertEqual(
            self.Commitment.current_commitment(self.project), 0.0)

    def test_orders_take_over_from_the_package_the_moment_they_exist(self):
        """The precedence rule, watched as it switches."""
        package = self._package(self.project, self.contractor,
                                value=8_000_000.0, award=True)
        package.cost_code_ids = self.civil
        self.assertEqual(
            self.Commitment.current_commitment(self.project), 8_000_000.0)

        self._po(self.project, self.contractor, [(self.civil, 6_000_000.0)],
                 package=package)
        package.invalidate_recordset()

        self.assertEqual(
            self.Commitment.current_commitment(self.project), 6_000_000.0,
            "The order is the operative document; the package's own value "
            "becomes a comparison, not an addition.")

    def test_a_package_with_several_codes_and_no_orders_splits_evenly(self):
        """A stated convention rather than a guess dressed as precision."""
        package = self._package(self.project, self.contractor,
                                value=6_000_000.0, award=True)
        package.cost_code_ids = self.civil | self.mep

        by_code = self.Commitment.current_commitment_by_cost_code(self.project)

        self.assertEqual(by_code[self.civil.id], 3_000_000.0)
        self.assertEqual(by_code[self.mep.id], 3_000_000.0)

    def test_recomputing_commitment_creates_no_rows(self):
        """§36 — the engine reads. Repeating it changes nothing."""
        self._po(self.project, self.contractor, [(self.civil, 2_000_000.0)])
        before = self.env['purchase.order.line'].search_count([])

        for _ in range(5):
            self.Commitment.current_commitment(self.project)

        self.assertEqual(
            self.env['purchase.order.line'].search_count([]), before)

    def test_another_projects_orders_are_not_counted(self):
        other = self._project()
        self._po(other, self.contractor, [(self.civil, 9_000_000.0)])

        self.assertEqual(
            self.Commitment.current_commitment(self.project), 0.0)

    def test_the_register_explains_the_total(self):
        """§33 — rows that add up to the number, with their sources."""
        self._po(self.project, self.contractor, [
            (self.civil, 2_000_000.0), (self.mep, 3_000_000.0)])

        rows = self.Commitment.commitment_rows(self.project)

        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(r['amount'] for r in rows), 5_000_000.0)
        self.assertEqual({r['source'] for r in rows}, {'purchase_order'})


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCostReport(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        self.mep = self._cost_code('SUB-MEP', 'MEP', 'subcontract')

    def _budgeted(self, *amounts):
        """`(cost_code, amount)` pairs — recordsets cannot be keyword names."""
        lines = [(0, 0, {'cost_code_id': code.id, 'amount_mode': 'lumpsum',
                         'original_amount': amount})
                 for code, amount in amounts]
        budget = self.env['realestate.construction.budget'].create({
            'project_id': self.project.id, 'line_ids': lines})
        budget.action_submit()
        budget.action_approve()
        budget.action_baseline()
        return budget

    def test_a_row_per_cost_code_with_the_three_readings(self):
        self._budgeted((self.civil,  5_000_000.0), (self.mep,  3_000_000.0))
        self._po(self.project, self.contractor, [(self.civil, 4_000_000.0)])
        self._post_bill(self.project, self.civil, 1_000_000.0)

        rows = {r['cost_code_id']: r
                for r in self.Controls.cost_report_rows(self.project)}

        civil = rows[self.civil.id]
        self.assertEqual(civil['current_budget'], 5_000_000.0)
        self.assertEqual(civil['current_commitment'], 4_000_000.0)
        self.assertEqual(civil['actual_cost'], 1_000_000.0)
        self.assertEqual(civil['available_before_commitment'], 1_000_000.0)
        self.assertEqual(civil['budget_remaining_vs_actual'], 4_000_000.0)

        mep = rows[self.mep.id]
        self.assertEqual(mep['current_budget'], 3_000_000.0)
        self.assertEqual(mep['current_commitment'], 0.0)

    def test_the_report_never_offers_commitment_plus_actual(self):
        self._budgeted((self.civil,  5_000_000.0))
        self._po(self.project, self.contractor, [(self.civil, 4_000_000.0)])
        self._post_bill(self.project, self.civil, 1_000_000.0)

        row = self.Controls.cost_report_rows(self.project)[0]

        self.assertNotIn('total_cost', row)
        self.assertNotIn('cost_to_date', row)

    def test_forecast_columns_are_blank_not_zero(self):
        """A zero ETC reads as "nothing left to spend"."""
        self._budgeted((self.civil,  5_000_000.0))

        row = self.Controls.cost_report_rows(self.project)[0]

        self.assertIsNone(row['etc'])
        self.assertIsNone(row['eac'])
        self.assertIsNone(row['forecast_variance'])

    def test_a_commitment_with_no_budget_still_gets_a_row(self):
        """Exactly the kind of thing a cost report exists to show."""
        self._budgeted((self.civil,  5_000_000.0))
        self._po(self.project, self.contractor, [(self.mep, 2_000_000.0)])

        rows = {r['cost_code_id']: r
                for r in self.Controls.cost_report_rows(self.project)}

        self.assertIn(self.mep.id, rows)
        self.assertEqual(rows[self.mep.id]['current_budget'], 0.0)
        self.assertEqual(rows[self.mep.id]['current_commitment'], 2_000_000.0)
        self.assertEqual(
            rows[self.mep.id]['available_before_commitment'], -2_000_000.0)

    def test_over_commitment_is_flagged_and_not_blocked(self):
        """§28 — warn and record; do not stop the project."""
        self._budgeted((self.civil,  1_000_000.0))
        self._po(self.project, self.contractor, [(self.civil, 1_500_000.0)])

        totals = self.Controls.project_totals(self.project)

        self.assertTrue(totals['over_committed'])
        self.assertEqual(totals['available_before_commitment'], -500_000.0)

    def test_the_rendered_report_totals_match_the_rows(self):
        self._budgeted((self.civil,  5_000_000.0), (self.mep,  3_000_000.0))
        self._po(self.project, self.contractor, [(self.civil, 4_000_000.0)])
        self._post_bill(self.project, self.civil, 1_000_000.0)

        report = self.env['realestate.construction.cost.report'].build_for(
            self.project)

        self.assertEqual(len(report.line_ids), 2)
        self.assertEqual(report.current_budget, 8_000_000.0)
        self.assertEqual(report.current_commitment, 4_000_000.0)
        self.assertEqual(report.actual_cost, 1_000_000.0)
        self.assertEqual(sum(report.line_ids.mapped('current_budget')),
                         report.current_budget)

    def test_every_figure_drills_into_its_records(self):
        """§29 — no opaque totals."""
        self._budgeted((self.civil,  5_000_000.0))
        po = self._po(self.project, self.contractor,
                      [(self.civil, 4_000_000.0)])
        self._post_bill(self.project, self.civil, 1_000_000.0)
        report = self.env['realestate.construction.cost.report'].build_for(
            self.project)
        line = report.line_ids[0]

        budget_action = line.action_drill_budget()
        commitment_action = line.action_drill_commitment()
        actual_action = line.action_drill_actual()

        self.assertEqual(
            self.env['realestate.construction.budget.line'].search_count(
                budget_action['domain']), 1)
        self.assertIn(po.id, commitment_action['domain'][0][2])
        self.assertEqual(
            self.env['account.analytic.line'].search_count(
                actual_action['domain']), 1)

    def test_the_project_summary_matches_the_report(self):
        self._budgeted((self.civil,  5_000_000.0))
        self._po(self.project, self.contractor, [(self.civil, 4_000_000.0)])
        self._post_bill(self.project, self.civil, 1_000_000.0)
        self.project.invalidate_recordset()

        self.assertEqual(self.project.ctrl_current_budget, 5_000_000.0)
        self.assertEqual(self.project.ctrl_current_commitment, 4_000_000.0)
        self.assertEqual(self.project.ctrl_actual_cost, 1_000_000.0)
        self.assertEqual(
            self.project.ctrl_available_before_commitment, 1_000_000.0)
        self.assertEqual(
            self.project.ctrl_budget_remaining_vs_actual, 4_000_000.0)

    def test_another_companys_project_contributes_nothing(self):
        other_company = self.env['res.company'].create({'name': 'CR Other'})
        other_project = self._project()
        other_project.company_id = other_company
        self._budgeted((self.civil,  5_000_000.0))

        totals = self.env[
            'realestate.construction.controls'].project_totals(other_project)

        self.assertEqual(totals['current_budget'], 0.0)
        self.assertEqual(totals['current_commitment'], 0.0)
        self.assertEqual(totals['actual_cost'], 0.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestRetentionDisclosure(ConstructionCommon):
    """§23 — the defect is isolated and disclosed, not silently patched."""

    def test_a_legacy_posting_understates_cost_and_the_report_says_so(self):
        project = self._project()
        self._configure_construction_accounts()
        contractor = self._contractor(retention=5.0)
        certificate = self._certificate(
            project, contractor, contract_value=1_000_000.0, pct=10.0)
        certificate.action_certify()
        certificate.action_create_vendor_bill()
        # M7 posts retention to a liability account and stamps the
        # certificate. The disclosure now describes pre-M7 history only, so a
        # legacy posting is what has to be reproduced to test it.
        certificate.sudo().write({'retention_posted_correctly': False})

        Disclosure = self.env[
            'realestate.construction.retention.disclosure']

        self.assertEqual(Disclosure.understatement_for(project), 5_000.0)
        warning = Disclosure.warning_for(project)
        self.assertIn('understated', warning)
        self.assertIn('retention liability', warning)

    def test_a_certificate_posted_the_new_way_is_not_disclosed(self):
        """The disclosure describes a defect. Once it is fixed it must stop
        talking, or it becomes noise nobody reads."""
        project = self._project()
        self._configure_construction_accounts()
        contractor = self._contractor(retention=5.0)
        certificate = self._certificate(
            project, contractor, contract_value=1_000_000.0, pct=10.0)
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        Disclosure = self.env[
            'realestate.construction.retention.disclosure']
        self.assertTrue(certificate.retention_posted_correctly)
        self.assertEqual(Disclosure.understatement_for(project), 0.0)
        self.assertEqual(Disclosure.warning_for(project), '')

    def test_a_project_without_retention_gets_no_warning(self):
        project = self._project()

        self.assertEqual(
            self.env['realestate.construction.retention.disclosure']
            .warning_for(project), '')

    def test_the_cost_report_carries_the_warning(self):
        project = self._project()
        self._configure_construction_accounts()
        contractor = self._contractor(retention=10.0)
        certificate = self._certificate(
            project, contractor, contract_value=500_000.0, pct=20.0)
        certificate.action_certify()
        certificate.action_create_vendor_bill()
        # M7 posts retention to a liability account and stamps the
        # certificate. The disclosure now describes pre-M7 history only, so a
        # legacy posting is what has to be reproduced to test it.
        certificate.sudo().write({'retention_posted_correctly': False})

        report = self.env['realestate.construction.cost.report'].build_for(
            project)

        self.assertIn('understated', report.retention_warning)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestPackageVariationIsCountedOnce(ConstructionCommon):
    """Found by M8: a package with no purchase order counted a variation twice.

    The package's `current_contract_value` already includes approved
    variations, and `approved_change_by_cost_code()` counts every variation no
    purchase order has absorbed. With no order in existence both were true at
    once, so a 13,000,000 obligation reported as 16,000,000.
    """

    def test_a_variation_on_an_orderless_package_moves_commitment_once(self):
        project = self._project()
        contractor = self._contractor()
        civil = self._cost_code('DBL-CIV', 'Civil', 'subcontract')
        package = self._package(project, contractor, value=10_000_000.0,
                                award=True)
        Commitment = self.env['realestate.construction.commitment']

        before = sum(
            Commitment.current_commitment_by_cost_code(project).values())
        self.assertEqual(before, 10_000_000.0)

        order = self._change_order(
            project, lines=[(civil, 'commitment', 3_000_000.0)],
            package=package)
        self._approve_and_implement(order)
        package.invalidate_recordset()

        after = sum(
            Commitment.current_commitment_by_cost_code(project).values())
        self.assertEqual(after, 13_000_000.0,
                         "One variation, counted once.")
        self.assertEqual(package.current_contract_value, 13_000_000.0,
                         "The contract is still worth what it is worth.")

    def test_a_package_with_orders_is_unaffected(self):
        project = self._project()
        contractor = self._contractor()
        civil = self._cost_code('DBL-CIV2', 'Civil', 'subcontract')
        package = self._package(project, contractor, value=10_000_000.0,
                                award=True)
        self._po(project, contractor, [(civil, 9_000_000.0)], package=package)
        Commitment = self.env['realestate.construction.commitment']

        totals = Commitment.current_commitment_by_cost_code(project)
        self.assertEqual(sum(totals.values()), 9_000_000.0,
                         "The order speaks for the package, as M2 decided.")


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCostReportWithUnassignedCommitment(ConstructionCommon):
    """Found by M10: the cost report crashed on uncoded commitment.

    `cost_report_rows()` appends an "Unassigned" row for commitment that
    carries no cost code — the row deliberately exists so uncoded money stays
    visible. It omitted the forecast keys every coded row carries, so
    `build_for()` raised `KeyError: 'previous_eac'` for any project with an
    orderless package or an uncoded purchase line.
    """

    def test_the_report_builds_when_commitment_is_uncoded(self):
        project = self._project()
        contractor = self._contractor()
        self._package(project, contractor, value=2_000_000.0, award=True)

        report = self.env['realestate.construction.cost.report'].build_for(
            project)

        unassigned = report.line_ids.filtered(lambda l: not l.cost_code_id)
        self.assertTrue(unassigned, "Uncoded commitment stays visible.")
        self.assertEqual(unassigned.current_commitment, 2_000_000.0)
        self.assertFalse(unassigned.has_forecast)
        self.assertEqual(unassigned.previous_eac, 0.0)
