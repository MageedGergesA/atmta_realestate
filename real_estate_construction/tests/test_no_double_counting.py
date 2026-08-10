# -*- coding: utf-8 -*-
"""The permanent double-count regression suite.

Every test here corresponds to a defect this programme actually found. They
are collected in one file on purpose: double counting is the failure mode this
system is most prone to, because the same economic event legitimately appears
in several registers, and each appearance is individually correct.

The question each test asks is the same one:

    Does one economic event appear exactly once, in the metric where it
    belongs?
"""

from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestNoDoubleCounting(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.Commitment = self.env['realestate.construction.commitment']
        self.Analytic = self.env['realestate.construction.analytic']
        self.Exposure = self.env['realestate.construction.exposure']
        self.Sheet = self.env['realestate.construction.cost.sheet']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('DC-CIV', 'Civil', 'subcontract')

    def _commitment(self):
        return sum(
            self.Commitment.current_commitment_by_cost_code(
                self.project).values())

    # -- 1 ------------------------------------------------------------------
    def test_a_package_and_its_purchase_order_are_one_commitment(self):
        """M2: the order speaks for the package once it exists."""
        package = self._package(self.project, self.contractor,
                                value=5_000_000.0, award=True)
        self.assertEqual(self._commitment(), 5_000_000.0)

        self._po(self.project, self.contractor, [(self.civil, 4_500_000.0)],
                 package=package)
        self.assertEqual(
            self._commitment(), 4_500_000.0,
            "Once orders exist they represent the package. 9,500,000 was the "
            "shape of the original defect.")

    # -- 2 ------------------------------------------------------------------
    def test_a_package_and_its_variation_are_one_commitment(self):
        """M8 found this: the package's current value already includes it."""
        package = self._package(self.project, self.contractor,
                                value=10_000_000.0, award=True)
        self._approve_and_implement(self._change_order(
            self.project, lines=[(self.civil, 'commitment', 3_000_000.0)],
            package=package))
        package.invalidate_recordset()

        self.assertEqual(self._commitment(), 13_000_000.0,
                         "16,000,000 was the defect.")
        self.assertEqual(package.current_contract_value, 13_000_000.0)

    # -- 3 ------------------------------------------------------------------
    def test_a_purchase_order_and_its_bill_are_not_added_together(self):
        """Commitment and actual are different questions about the same money."""
        self._po(self.project, self.contractor, [(self.civil, 2_000_000.0)])
        self._post_bill(self.project, self.civil, 800_000.0)

        totals = self.Controls.project_totals(self.project)
        self.assertEqual(totals['current_commitment'], 2_000_000.0)
        self.assertEqual(totals['actual_cost'], 800_000.0)
        self.assertNotIn(2_800_000.0,
                         [totals['current_commitment'], totals['actual_cost']])
        self.assertNotIn('total_cost', totals,
                         "There is deliberately no field adding them.")

    # -- 4 ------------------------------------------------------------------
    def test_tax_never_reaches_a_control_figure(self):
        """Tax lines inherit the base line's analytic distribution."""
        tax = self.env['account.tax'].create({
            'name': 'DC VAT 14', 'amount': 14.0, 'amount_type': 'percent',
            'type_tax_use': 'purchase', 'company_id': self.company.id})
        self._post_bill(self.project, self.civil, 1_000_000.0, tax=tax)

        actual = self.Analytic.actual_by_cost_code(self.project)
        self.assertEqual(
            actual.get(self.civil.id), 1_000_000.0,
            "1,140,000 would mean VAT had become construction cost.")

    # -- 5 ------------------------------------------------------------------
    def test_retention_is_not_subtracted_from_actual_cost(self):
        self._configure_construction_accounts()
        certificate = self._certificate(
            self.project, self.contractor, amount=1_000_000.0,
            retention_pct=10.0, cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        actual = self.Analytic.actual_by_cost_code(self.project)
        self.assertEqual(actual.get(self.civil.id), 1_000_000.0,
                         "900,000 was the Phase 0 defect.")

    # -- 6 ------------------------------------------------------------------
    def test_an_advance_and_its_recovery_never_become_cost(self):
        self._configure_construction_accounts()
        advance = self._advance(self.project, self.contractor, 1_000_000.0)
        advance.action_confirm()
        advance.action_create_vendor_bill()

        certificate = self._certificate(
            self.project, self.contractor, amount=2_000_000.0,
            retention_pct=0.0, cost_code=self.civil)
        certificate.write({'recovery_line_ids': [(0, 0, {
            'advance_id': advance.id, 'amount': 200_000.0})]})
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        actual = self.Analytic.actual_by_cost_code(self.project)
        self.assertEqual(
            actual.get(self.civil.id), 2_000_000.0,
            "Neither the advance nor its recovery is the cost of work.")

    # -- 7 ------------------------------------------------------------------
    def test_risk_issue_change_and_claim_are_one_exposure(self):
        risk = self._risk(self.project, cost_exposure=2_000_000.0)
        risk.action_include_in_forecast()
        self.assertEqual(
            self.Exposure.for_project(self.project)['potential_commercial'],
            2_000_000.0)

        issue = risk.action_materialise(reason='It happened.')
        issue.estimated_cost_impact = 2_000_000.0
        event = issue.action_create_change_event()
        package = self._package(self.project, self.contractor,
                                value=1_000_000.0, award=True)
        claim = self._claim(self.project, package, claimed_cost=2_000_000.0,
                            change_event_ids=[(6, 0, event.ids)])
        claim.action_submit()

        exposure = self.Exposure.for_project(self.project)
        self.assertEqual(exposure['potential_commercial'], 2_000_000.0,
                         "Four registers, one problem, one number. 8,000,000 "
                         "is what a naive sum would report.")

    # -- 8 ------------------------------------------------------------------
    def test_an_approved_change_leaves_the_potential_exposure_behind(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        event = self._change_event(self.project, estimated_cost=1_500_000.0)

        sheet = self.Sheet.totals_for(self.project)
        self.assertEqual(sheet['potential_cost_exposure'], 1_500_000.0)
        self.assertEqual(sheet['current_budget'], 10_000_000.0)

        event.action_close() if hasattr(event, 'action_close') else None
        order = self._change_order(
            self.project, lines=[(self.civil, 'budget', 1_500_000.0)],
            event=event)
        self._approve_and_implement(order)

        sheet = self.Sheet.totals_for(self.project)
        self.assertEqual(sheet['current_budget'], 11_500_000.0)
        self.assertNotEqual(
            sheet['current_budget'] + sheet['potential_cost_exposure'],
            13_000_000.0,
            "Authorised money must not still be reported as exposure.")

    # -- 9 ------------------------------------------------------------------
    def test_owner_revenue_is_never_netted_against_contractor_cost(self):
        self._configure_construction_accounts()
        self._post_bill(self.project, self.civil, 3_000_000.0)
        billing = self._owner_billing(self.project, amount=5_000_000.0)
        billing.action_certify()
        billing.action_create_customer_invoice()

        payload = self.env[
            'realestate.construction.control.tower'].payload(self.project)
        self.assertEqual(payload['cost']['actual_cost'], 3_000_000.0)
        self.assertEqual(payload['commercial']['owner']['billed'],
                         5_000_000.0)
        self.assertNotIn(
            'margin', payload['commercial'],
            "Margin needs an authoritative revenue basis; billing minus cost "
            "is not one.")

    # -- 10 -----------------------------------------------------------------
    def test_the_same_bill_cannot_be_counted_by_two_cost_codes(self):
        mep = self._cost_code('DC-MEP', 'MEP', 'subcontract')
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.contractor.partner_id.id,
            'invoice_date': self.today,
            'date': self.today,
            'company_id': self.company.id,
            'invoice_line_ids': [
                (0, 0, {'name': 'Civil', 'quantity': 1,
                        'price_unit': 600_000.0, 'tax_ids': [(5, 0, 0)],
                        'analytic_distribution': self.Analytic
                        .distribution_for(self.project, self.civil)}),
                (0, 0, {'name': 'MEP', 'quantity': 1,
                        'price_unit': 400_000.0, 'tax_ids': [(5, 0, 0)],
                        'analytic_distribution': self.Analytic
                        .distribution_for(self.project, mep)}),
            ],
        })
        bill.action_post()

        actual = self.Analytic.actual_by_cost_code(self.project)
        self.assertEqual(actual.get(self.civil.id), 600_000.0)
        self.assertEqual(actual.get(mep.id), 400_000.0)
        self.assertEqual(sum(actual.values()), 1_000_000.0,
                         "2,000,000 would mean the multi-plan distribution "
                         "had regressed to two full-amount keys.")
