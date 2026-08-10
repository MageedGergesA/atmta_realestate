# -*- coding: utf-8 -*-
"""M10G/H — Construction Actual reconciles to the Odoo ledger, exactly.

The claim this module makes about actual cost is narrow and testable: it is
the posted ledger cost attributable to the project's analytic dimensions,
tax-exclusive, gross of retention, and nothing else. Every test below is one
way that claim could be false.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestAccountingReconciliation(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Analytic = self.env['realestate.construction.analytic']
        self.Sheet = self.env['realestate.construction.cost.sheet']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('RC-CIV', 'Civil', 'subcontract')
        self.mep = self._cost_code('RC-MEP', 'MEP', 'subcontract')

    def _actual(self, code=None):
        totals = self.Analytic.actual_by_cost_code(self.project)
        return totals.get(code.id, 0.0) if code else sum(totals.values())

    def test_a_posted_vendor_bill_is_the_actual_cost(self):
        self._post_bill(self.project, self.civil, 250_000.0)
        self.assertEqual(self._actual(self.civil), 250_000.0)

    def test_a_draft_bill_is_not_cost_until_it_is_posted(self):
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.contractor.partner_id.id,
            'invoice_date': self.today, 'date': self.today,
            'company_id': self.company.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Not yet posted', 'quantity': 1,
                'price_unit': 500_000.0, 'tax_ids': [(5, 0, 0)],
                'analytic_distribution': self.Analytic.distribution_for(
                    self.project, self.civil)})],
        })
        self.assertEqual(self._actual(), 0.0,
                         "A draft bill is somebody's intention.")
        bill.action_post()
        self.assertEqual(self._actual(self.civil), 500_000.0)

    def test_tax_is_excluded(self):
        tax = self.env['account.tax'].create({
            'name': 'RC VAT', 'amount': 14.0, 'amount_type': 'percent',
            'type_tax_use': 'purchase', 'company_id': self.company.id})
        self._post_bill(self.project, self.civil, 100_000.0, tax=tax)
        self.assertEqual(self._actual(self.civil), 100_000.0)

    def test_a_multi_cost_code_bill_splits_exactly(self):
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.contractor.partner_id.id,
            'invoice_date': self.today, 'date': self.today,
            'company_id': self.company.id,
            'invoice_line_ids': [
                (0, 0, {'name': 'Civil', 'quantity': 1, 'price_unit': 70_000.0,
                        'tax_ids': [(5, 0, 0)],
                        'analytic_distribution': self.Analytic
                        .distribution_for(self.project, self.civil)}),
                (0, 0, {'name': 'MEP', 'quantity': 1, 'price_unit': 30_000.0,
                        'tax_ids': [(5, 0, 0)],
                        'analytic_distribution': self.Analytic
                        .distribution_for(self.project, self.mep)}),
            ],
        })
        bill.action_post()
        self.assertEqual(self._actual(self.civil), 70_000.0)
        self.assertEqual(self._actual(self.mep), 30_000.0)
        self.assertEqual(self._actual(), 100_000.0)

    def test_a_credit_note_reduces_actual_cost(self):
        bill = self._post_bill(self.project, self.civil, 400_000.0)
        self.assertEqual(self._actual(self.civil), 400_000.0)

        reversal = bill._reverse_moves([{
            'invoice_date': self.today, 'date': self.today}])
        reversal.action_post()

        self.assertEqual(
            self._actual(self.civil), 0.0,
            "The ledger reversed it, so construction actual follows.")

    def test_a_cancelled_bill_is_not_cost(self):
        bill = self._post_bill(self.project, self.civil, 150_000.0)
        bill.button_draft()
        bill.button_cancel()
        self.assertEqual(self._actual(self.civil), 0.0)

    def test_an_unrelated_bill_is_excluded(self):
        other_project = self._project()
        self._post_bill(other_project, self.civil, 900_000.0)
        self.assertEqual(self._actual(), 0.0,
                         "Another project's cost is not this project's cost.")

    def test_a_manual_journal_entry_with_construction_analytics_counts(self):
        """The ledger is the source, not the document type that wrote it."""
        expense = self.env['account.account'].search(
            [('account_type', '=', 'expense'),
             ('company_ids', 'in', self.company.id)], limit=1)
        payable = self.env['account.account'].search(
            [('account_type', '=', 'liability_payable'),
             ('company_ids', 'in', self.company.id)], limit=1)
        journal = self.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', self.company.id)],
            limit=1)
        entry = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': journal.id,
            'date': self.today, 'company_id': self.company.id,
            'line_ids': [
                (0, 0, {'account_id': expense.id, 'debit': 75_000.0,
                        'credit': 0.0, 'name': 'Site accrual',
                        'analytic_distribution': self.Analytic
                        .distribution_for(self.project, self.civil)}),
                (0, 0, {'account_id': payable.id, 'debit': 0.0,
                        'credit': 75_000.0, 'name': 'Site accrual'}),
            ],
        })
        entry.action_post()
        self.assertEqual(self._actual(self.civil), 75_000.0)

    def test_retention_end_to_end(self):
        """M10G §10 — pinned to the exact figures."""
        self._configure_construction_accounts()
        certificate = self._certificate(
            self.project, self.contractor, amount=100_000.0,
            retention_pct=5.0, cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()
        bill = certificate.vendor_bill_id

        self.assertEqual(self._actual(self.civil), 100_000.0)
        self.assertEqual(bill.amount_total, 95_000.0)

        retention_account = self.company.construction_retention_account_id
        held = bill.line_ids.filtered(
            lambda l: l.account_id == retention_account)
        self.assertEqual(sum(held.mapped('balance')), -5_000.0)

        Retention = self.env['realestate.construction.retention']
        self.assertEqual(
            Retention.balance(project=self.project,
                              contractor=self.contractor), 5_000.0)

        release = self._retention_release(self.project, self.contractor,
                                          amount=5_000.0)
        release.action_confirm()

        self.assertEqual(
            Retention.balance(project=self.project,
                              contractor=self.contractor), 0.0)
        self.assertEqual(self._actual(self.civil), 100_000.0,
                         "Releasing retention is not a second cost event.")
        self.assertEqual(certificate.certified_amount, 100_000.0,
                         "The historic certificate is untouched.")
        self.assertEqual(release.vendor_bill_id.amount_total, 5_000.0)

    def test_advance_end_to_end(self):
        """M10G §11 — pinned to the exact figures."""
        self._configure_construction_accounts()
        advance = self._advance(self.project, self.contractor, 1_000_000.0)
        advance.action_confirm()
        advance.action_create_vendor_bill()

        self.assertEqual(advance.amount, 1_000_000.0)
        self.assertEqual(advance.outstanding_amount, 1_000_000.0)
        self.assertEqual(self._actual(), 0.0)

        first = self._certificate(self.project, self.contractor,
                                  amount=3_000_000.0, retention_pct=0.0,
                                  cost_code=self.civil)
        first.write({'recovery_line_ids': [(0, 0, {
            'advance_id': advance.id, 'amount': 200_000.0})]})
        first.action_certify()
        first.action_create_vendor_bill()
        advance.invalidate_recordset()

        self.assertEqual(advance.recovered_amount, 200_000.0)
        self.assertEqual(advance.outstanding_amount, 800_000.0)
        self.assertEqual(first.net_payable, 2_800_000.0)

        second = self._certificate(self.project, self.contractor,
                                   amount=3_000_000.0, retention_pct=0.0,
                                   cost_code=self.civil)
        second.write({'recovery_line_ids': [(0, 0, {
            'advance_id': advance.id, 'amount': 900_000.0})]})
        with self.assertRaises(UserError):
            second.action_certify()

        advance.invalidate_recordset()
        self.assertGreaterEqual(advance.outstanding_amount, 0.0,
                                "Outstanding never goes negative.")

    def test_every_surface_agrees_after_the_full_cycle(self):
        """M10H — one economic reality, one number, wherever it is asked."""
        self._configure_construction_accounts()
        self._baselined(self.project, 20_000_000.0, code=self.civil)
        package = self._package(self.project, self.contractor,
                                value=15_000_000.0, award=True)
        self._approve_and_implement(self._change_order(
            self.project, lines=[(self.civil, 'commitment', 1_000_000.0)],
            package=package))
        certificate = self._certificate(
            self.project, self.contractor, amount=4_000_000.0,
            retention_pct=5.0, cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        controls = self.env[
            'realestate.construction.controls'].project_totals(self.project)
        sheet = self.Sheet.totals_for(self.project)
        tower = self.env[
            'realestate.construction.control.tower'].payload(
                self.project)['cost']
        report = self.env[
            'realestate.construction.cost.report'].build_for(self.project)

        for key in ('current_budget', 'current_commitment', 'actual_cost'):
            self.assertEqual(sheet[key], controls[key], key)
            self.assertEqual(tower[key], controls[key], key)
        self.assertEqual(report.actual_cost, controls['actual_cost'])
        self.assertEqual(report.current_budget, controls['current_budget'])
        self.assertEqual(controls['actual_cost'], 4_000_000.0)
        self.assertEqual(controls['current_commitment'], 16_000_000.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestEquationsUnderManyCombinations(ConstructionCommon):
    """M10H §13 — deterministic combinations, not random flakiness."""

    def test_the_equations_hold_across_a_generated_matrix(self):
        Sheet = self.env['realestate.construction.cost.sheet']
        combinations = [
            # (budget, budget change, commitment, variation, actual, etc)
            (10_000_000.0, 500_000.0, 8_000_000.0, 250_000.0, 3_000_000.0,
             6_000_000.0),
            (5_000_000.0, -250_000.0, 4_000_000.0, 500_000.0, 4_500_000.0,
             1_000_000.0),
            (1_000_000.0, 0.0, 900_000.0, 0.0, 0.0, 900_000.0),
            (25_000_000.0, 2_500_000.0, 20_000_000.0, -1_000_000.0,
             12_000_000.0, 14_000_000.0),
        ]
        for index, (budget, budget_change, commitment, variation, actual,
                    etc) in enumerate(combinations):
            project = self._project()
            contractor = self._contractor()
            code = self._cost_code('MX-%02d' % index, 'Works', 'subcontract')

            self._baselined(project, budget, code=code)
            package = self._package(project, contractor, value=commitment,
                                    award=True)
            if budget_change:
                self._approve_and_implement(self._change_order(
                    project, lines=[(code, 'budget', budget_change)]))
            if variation:
                self._approve_and_implement(self._change_order(
                    project, lines=[(code, 'commitment', variation)],
                    package=package))
            if actual:
                self._post_bill(project, code, actual)

            forecast = self._forecast(project)
            line = forecast.line_ids.filtered(
                lambda l: l.cost_code_id == code)[:1]
            line.write({'method': 'manual', 'manual_etc': etc,
                        'manual_reason': 'Generated combination.'})
            forecast.action_submit()
            forecast.action_approve()

            totals = Sheet.totals_for(project)
            self.assertAlmostEqual(totals['current_budget'],
                                   budget + budget_change, 2, str(index))
            self.assertAlmostEqual(totals['current_commitment'],
                                   commitment + variation, 2, str(index))
            self.assertAlmostEqual(totals['actual_cost'], actual, 2,
                                   str(index))
            self.assertAlmostEqual(totals['eac'], actual + etc, 2, str(index))
            self.assertAlmostEqual(
                totals['forecast_variance'],
                (budget + budget_change) - (actual + etc), 2, str(index))
