# -*- coding: utf-8 -*-
"""Regressions for the Codex review findings (14 Sep 2026).

* P1 — the auto-invoicing cron rolled back the whole transaction when one
  instalment could not be invoiced, discarding invoices already created in the
  same run while still counting them.
* P2 — a unit swap onto a dearer unit, when every instalment was already
  invoiced or paid, raised the sale price but created nothing to bill the
  difference.
* P2 — overdue state and ageing are stored but depend on today's date, so they
  went stale as days passed without a write.
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .test_contract import ContractCommon
from .test_contract_changes import ChangeCommon


@tagged('post_install', '-at_install', 'atmta_developer')
class TestAutoInvoiceCronIsolation(ContractCommon):

    def test_one_unbillable_instalment_does_not_undo_the_others(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        today = fields.Date.context_today(contract)
        lines = contract.installment_ids.sorted(lambda i: (i.date_due, i.sequence))
        (lines[0] | lines[1]).write({'date_due': today})
        ours = set((lines[0] | lines[1]).ids)

        Installment = type(self.env['realestate.sale.installment'])
        original = Installment.action_generate_invoice
        calls = []

        def second_of_ours_fails(records):
            if set(records.ids) & ours:
                calls.append(records)
                if len(calls) == 2:
                    raise UserError("Unit has no linked product.")
            return original(records)

        with patch.object(Installment, 'action_generate_invoice', second_of_ours_fails):
            count = self.env['realestate.sale.installment'].cron_auto_invoice_due_installments()

        self.assertEqual(len(calls), 2, "both of this contract's instalments were attempted")
        billed, failed = calls
        self.env.invalidate_all()
        self.assertTrue(billed.exists() and billed.move_id,
                        "the invoice raised before the failure must survive the run")
        self.assertEqual(billed.move_id.state, 'posted')
        self.assertFalse(failed.move_id)
        self.assertEqual(failed.state, 'pending')
        self.assertEqual(
            self.env['account.move'].search_count([('id', '=', billed.move_id.id)]), 1)
        self.assertGreaterEqual(count, 1)


@tagged('post_install', '-at_install', 'atmta_developer')
class TestUnitSwapWithNothingOpen(ChangeCommon):

    def setUp(self):
        super().setUp()
        self.target = self.units[1]
        self._release(self.target)
        self.target.base_price = 1200000.0

    def test_the_increase_is_billed_when_everything_is_invoiced(self):
        contract = self._signed_contract()
        for line in contract.installment_ids.sorted(lambda i: (i.date_due, i.sequence)):
            line.action_generate_invoice()
        self.assertFalse(contract._open_installments())
        before = set(contract.installment_ids.ids)
        new_price = self.target.list_price_developer
        self.assertGreater(new_price, contract.sale_price)

        wizard = self.env['realestate.contract.unit.swap'].create({
            'contract_id': contract.id,
            'new_property_id': self.target.id,
            'reason': 'Dearer unit after everything was invoiced',
        })
        wizard.action_apply()

        contract.invalidate_recordset()
        live = contract.installment_ids.filtered(lambda i: not i.is_cancelled)
        added = contract.installment_ids.filtered(lambda i: i.id not in before)
        self.assertEqual(contract.sale_price, new_price)
        self.assertEqual(len(added), 1, "one balancing instalment is raised")
        self.assertAlmostEqual(added.current_amount, new_price - 1000000.0, places=2)
        self.assertEqual(added.state, 'pending')
        self.assertAlmostEqual(sum(live.mapped('current_amount')), new_price, places=2,
                               msg="the schedule adds up to the new price")

    def test_a_cheaper_or_equal_swap_raises_nothing(self):
        contract = self._signed_contract()
        for line in contract.installment_ids:
            line.action_generate_invoice()
        before = set(contract.installment_ids.ids)
        self.target.base_price = 1000000.0
        wizard = self.env['realestate.contract.unit.swap'].create({
            'contract_id': contract.id,
            'new_property_id': self.target.id,
            'reason': 'Same price',
            'new_price': 1000000.0,
        })
        wizard.action_apply()
        contract.invalidate_recordset()
        self.assertFalse(contract.installment_ids.filtered(lambda i: i.id not in before))


@tagged('post_install', '-at_install', 'atmta_developer')
class TestInstallmentAgingRefresh(ContractCommon):

    def _let_days_pass(self, installment, due):
        """Move the due date in the database only, as time passing would:
        nothing is written through the ORM, so no compute is triggered."""
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE realestate_sale_installment SET date_due = %s WHERE id = %s",
            (due, installment.id))
        self.env.invalidate_all()

    def test_an_invoice_that_falls_due_becomes_overdue(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        today = fields.Date.context_today(contract)
        first = contract.installment_ids.sorted(lambda i: (i.date_due, i.sequence))[0]
        first.date_due = today + timedelta(days=5)
        first.action_generate_invoice()
        self.assertEqual(first.state, 'invoiced')

        self._let_days_pass(first, today - timedelta(days=5))
        self.assertEqual(first.state, 'invoiced',
                         "a stored compute does not notice the date passing by itself")

        refreshed = self.env['realestate.sale.installment'].cron_refresh_installment_aging()
        self.assertGreaterEqual(refreshed, 1)
        self.env.invalidate_all()
        self.assertEqual(first.state, 'overdue')
        self.assertEqual(first.days_overdue, 5)
        self.assertEqual(first.aging_bucket, 'current')
        self.assertEqual(contract.collection_state, 'overdue')

        self._let_days_pass(first, today - timedelta(days=45))
        self.env['realestate.sale.installment'].cron_refresh_installment_aging()
        self.env.invalidate_all()
        self.assertEqual(first.days_overdue, 45)
        self.assertEqual(first.aging_bucket, 'bucket_60')

    def test_paid_and_cancelled_instalments_are_left_alone(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        today = fields.Date.context_today(contract)
        first, second = contract.installment_ids.sorted(lambda i: (i.date_due, i.sequence))[:2]
        second.action_cancel_installment('test')
        self._let_days_pass(second, today - timedelta(days=30))
        self.env['realestate.sale.installment'].cron_refresh_installment_aging()
        self.env.invalidate_all()
        self.assertEqual(second.state, 'cancelled')
        self.assertEqual(second.aging_bucket, 'not_due')
