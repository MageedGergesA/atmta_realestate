# -*- coding: utf-8 -*-
"""M2 — the cost control equation, end to end.

This file exists before the models it tests. It is the brief's first action and
the whole point of the milestone: one worked case that fixes the meaning of
every term, so that the models are built to satisfy an equation rather than the
equation being described after the fact to match whatever the models did.

```
    Budget                10,000,000
    Confirmed PO           8,000,000  + 1,200,000 VAT
    Vendor bill posted     3,000,000  +   450,000 VAT

    Original Budget    = 10,000,000
    Current Budget     = 10,000,000
    Current Commitment =  8,000,000      not 9,200,000
    Actual             =  3,000,000      not 3,450,000

    and "total cost" is NOT 11,000,000 — commitment and actual are two views
    of the same money, not two piles of it.
```
"""

from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCostControlEquation(ConstructionCommon):
    """The worked case from the brief, asserted term by term."""

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.project = self._project(budget=0.0)
        self.civil = self._cost_code('SUB-CIV', 'Civil Subcontract',
                                     'subcontract')
        self.wbs = self._wbs(self.project, code='03', name='Concrete')
        self.contractor = self._contractor()

    def _baselined_budget(self, amount=10_000_000.0):
        budget = self.env['realestate.construction.budget'].create({
            'project_id': self.project.id,
            'line_ids': [(0, 0, {
                'wbs_id': self.wbs.id,
                'cost_code_id': self.civil.id,
                'description': 'Civil works',
                'amount_mode': 'lumpsum',
                'original_amount': amount,
            })],
        })
        budget.action_submit()
        budget.action_approve()
        budget.action_baseline()
        return budget

    def _confirmed_po(self, untaxed=8_000_000.0, tax_pct=15.0):
        tax = self.env['account.tax'].create({
            'name': 'VAT %s' % tax_pct,
            'amount': tax_pct,
            'amount_type': 'percent',
            'type_tax_use': 'purchase',
            'company_id': self.company.id,
        })
        product = self.contractor._ensure_service_product()
        po = self.env['purchase.order'].create({
            'partner_id': self.contractor.partner_id.id,
            're_project_id': self.project.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'name': 'Civil works',
                'product_qty': 1.0,
                'price_unit': untaxed,
                'taxes_id': [(6, 0, tax.ids)],
                're_cost_code_id': self.civil.id,
                're_wbs_id': self.wbs.id,
            })],
        })
        po.button_confirm()
        return po, tax

    def _posted_bill(self, tax, untaxed=3_000_000.0):
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.contractor.partner_id.id,
            'invoice_date': self.today,
            'company_id': self.company.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Civil works progress',
                'quantity': 1,
                'price_unit': untaxed,
                'tax_ids': [(6, 0, tax.ids)],
                'analytic_distribution': self.env[
                    'realestate.construction.analytic'].distribution_for(
                        self.project, self.civil),
            })],
        })
        bill.action_post()
        return bill

    # ------------------------------------------------------------------
    def test_the_whole_equation(self):
        self._baselined_budget(10_000_000.0)
        po, tax = self._confirmed_po(8_000_000.0, 15.0)
        bill = self._posted_bill(tax, 3_000_000.0)

        totals = self.Controls.project_totals(self.project)

        # Budget
        self.assertEqual(totals['original_budget'], 10_000_000.0)
        self.assertEqual(totals['approved_budget_changes'], 0.0)
        self.assertEqual(totals['current_budget'], 10_000_000.0)

        # Commitment — untaxed, so 8,000,000 and never 9,200,000.
        self.assertEqual(po.amount_total, 9_200_000.0)
        self.assertEqual(
            totals['current_commitment'], 8_000_000.0,
            "VAT must not inflate a commitment that is compared against an "
            "untaxed budget.")

        # Actual — the posted ledger, untaxed, and only what posted.
        self.assertEqual(bill.amount_total, 3_450_000.0)
        self.assertEqual(
            totals['actual_cost'], 3_000_000.0,
            "Actual is the posted expense, not the gross invoice.")

        # The two balances answer different questions and are both offered.
        self.assertEqual(totals['available_before_commitment'], 2_000_000.0)
        self.assertEqual(totals['budget_remaining_vs_actual'], 7_000_000.0)

    def test_commitment_and_actual_are_never_added_together(self):
        """§25 — the project has not incurred 11,000,000."""
        self._baselined_budget(10_000_000.0)
        po, tax = self._confirmed_po(8_000_000.0)
        self._posted_bill(tax, 3_000_000.0)

        totals = self.Controls.project_totals(self.project)

        self.assertNotIn(
            'total_cost', totals,
            "There is no such thing as commitment plus actual. Offering the "
            "number at all invites somebody to use it.")
        self.assertEqual(
            totals['current_commitment'] + totals['actual_cost'],
            11_000_000.0,
            "The sum exists arithmetically and means nothing — which is "
            "exactly why it is not exposed.")

    def test_a_confirmed_po_commits_without_touching_actual(self):
        self._baselined_budget()
        before = self.Controls.project_totals(self.project)
        self._confirmed_po(8_000_000.0)

        after = self.Controls.project_totals(self.project)

        self.assertEqual(before['current_commitment'], 0.0)
        self.assertEqual(after['current_commitment'], 8_000_000.0)
        self.assertEqual(after['actual_cost'], before['actual_cost'], 0.0)

    def test_a_draft_bill_changes_nothing(self):
        self._baselined_budget()
        po, tax = self._confirmed_po(8_000_000.0)
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.contractor.partner_id.id,
            'invoice_date': self.today,
            'company_id': self.company.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Not posted yet',
                'quantity': 1,
                'price_unit': 1_000_000.0,
                'analytic_distribution': self.env[
                    'realestate.construction.analytic'].distribution_for(
                        self.project, self.civil),
            })],
        })

        self.assertEqual(bill.state, 'draft')
        self.assertEqual(
            self.Controls.project_totals(self.project)['actual_cost'], 0.0,
            "A draft bill is an intention, not a cost.")

    def test_paying_a_bill_does_not_count_the_cost_twice(self):
        self._baselined_budget()
        po, tax = self._confirmed_po(8_000_000.0)
        bill = self._posted_bill(tax, 3_000_000.0)
        actual_after_posting = self.Controls.project_totals(
            self.project)['actual_cost']

        self.env['realestate.account.tools'].register_payment(bill)

        self.assertEqual(bill.payment_state, 'paid')
        self.assertEqual(
            self.Controls.project_totals(self.project)['actual_cost'],
            actual_after_posting,
            "Paying an expense does not incur it a second time.")

    def test_cancelling_the_po_removes_the_commitment(self):
        self._baselined_budget()
        po, tax = self._confirmed_po(8_000_000.0)
        self.assertEqual(
            self.Controls.project_totals(self.project)['current_commitment'],
            8_000_000.0)

        po.button_cancel()

        self.assertEqual(
            self.Controls.project_totals(self.project)['current_commitment'],
            0.0,
            "A cancelled order commits nothing — while the order itself "
            "remains on file.")
        self.assertTrue(po.exists())

    def test_an_rfq_is_not_a_commitment(self):
        """§17 — a request for quotation is potential procurement."""
        self._baselined_budget()
        product = self.contractor._ensure_service_product()
        rfq = self.env['purchase.order'].create({
            'partner_id': self.contractor.partner_id.id,
            're_project_id': self.project.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'name': 'Maybe civil works',
                'product_qty': 1.0,
                'price_unit': 5_000_000.0,
                're_cost_code_id': self.civil.id,
            })],
        })

        self.assertEqual(rfq.state, 'draft')
        self.assertEqual(
            self.Controls.project_totals(self.project)['current_commitment'],
            0.0)
