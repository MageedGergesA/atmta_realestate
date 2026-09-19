# -*- coding: utf-8 -*-
"""M7 / M8 / M17 / M37 — where the money actually is.

This is the core of the module and the part 0.1 got wrong in every direction:
the payment was created at the wrong moment, the reconciliation crashed on
Odoo 18, and `cleared` meant "someone pressed a button".

The end-to-end scenario M37 demands is `test_full_lifecycle_to_cleared`, and it
is deliberately built out of real Odoo objects — a posted invoice, a real
`account.payment`, a real `account.bank.statement.line`, real reconciliation.
Nothing is mocked, and no test writes `payment.state` or an invoice's
`payment_state` by hand; a test that faked those would prove nothing about a
module whose entire claim is that it reads them.
"""

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import ChecksCommon


@tagged('post_install', '-at_install', 'atmta_checks')
class TestPaymentTiming(ChecksCommon):
    """M7 — the accounting moment is presentation, not clearance."""

    def test_registering_a_cheque_creates_no_payment(self):
        """Rule 2 — a received PDC is not cash."""
        check = self._check()
        self.assertFalse(check.payment_id)
        self.assertEqual(check.accounting_state, 'no_payment')

    def test_a_pdc_does_not_mark_its_instalment_paid(self):
        """The example from Rule 2, asserted."""
        contract = self._signed_contract()
        target = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[-1]
        invoice = self._invoice_installment(target)
        self._check(amount=target.current_amount,
                    sale_contract_id=contract.id,
                    sale_installment_id=target.id,
                    due_date=target.date_due)
        target.invalidate_recordset()
        self.assertNotEqual(target.state, 'paid')
        self.assertGreater(target.residual_amount, 0.0)
        self.assertEqual(invoice.payment_state, 'not_paid')

    def test_confirming_a_deposit_creates_one_payment_per_cheque(self):
        checks = self._check() | self._check()
        deposit = self._deposit(checks)
        self.assertEqual(len(deposit.payment_ids), 2)
        for check in checks:
            self.assertTrue(check.payment_id)
            self.assertEqual(check.payment_id.payment_type, 'inbound')
            self.assertEqual(check.payment_id.partner_type, 'customer')
            self.assertEqual(check.payment_id.amount, check.amount)
            self.assertEqual(check.payment_id.company_id, self.company)
            self.assertEqual(check.payment_id.journal_id, deposit.journal_id)

    def test_the_payment_carries_a_back_reference(self):
        check = self._check()
        deposit = self._deposit(check)
        self.assertEqual(check.payment_id.realestate_check_id, check)
        self.assertEqual(check.payment_id.realestate_deposit_id, deposit)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestReconciliationOnPresentation(ChecksCommon):
    """The bug that had never worked on Odoo 18."""

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()
        self.installments = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))

    def test_account_payment_has_no_line_ids_in_odoo_18(self):
        """The premise of the 0.1 crash, asserted so it cannot silently change.

        Odoo 17 delegated `line_ids` through `_inherits = {'account.move':
        'move_id'}`. Odoo 18 removed the delegation, so 0.1's `_try_reconcile`
        raised `AttributeError` after posting a payment and rolled the whole
        clear back.
        """
        self.assertNotIn('line_ids', self.env['account.payment']._fields)
        self.assertFalse(self.env['account.payment']._inherits)

    def test_presenting_a_cheque_reconciles_its_invoice(self):
        target = self.installments[0]
        invoice = self._invoice_installment(target)
        self.assertEqual(invoice.payment_state, 'not_paid')
        residual_before = invoice.amount_residual

        check = self._check(amount=invoice.amount_total,
                            sale_contract_id=self.contract.id,
                            sale_installment_id=target.id)
        self._deposit(check)

        invoice.invalidate_recordset()
        self.assertLess(invoice.amount_residual, residual_before)
        self.assertAlmostEqual(invoice.amount_residual, 0.0, 2)
        self.assertIn(invoice, check.payment_id.reconciled_invoice_ids)

    def test_one_cheque_reconciles_across_several_invoices(self):
        """M17 — one physical instrument, one payment, many invoices.

        Not three invented payments because it happens to cover three
        instalments.
        """
        targets = self.installments[:3]
        invoices = self.env['account.move']
        for installment in targets:
            invoices |= self._invoice_installment(installment)
        total = sum(invoices.mapped('amount_total'))

        check = self._check(amount=total, sale_contract_id=self.contract.id)
        for installment in targets:
            self.Allocation.create({
                'check_id': check.id,
                'sale_installment_id': installment.id,
                'allocated_amount': installment.current_amount,
            })
        deposit = self._deposit(check)

        self.assertEqual(len(deposit.payment_ids), 1,
                         'one cheque must not produce three payments')
        invoices.invalidate_recordset()
        for invoice in invoices:
            self.assertAlmostEqual(invoice.amount_residual, 0.0, 2,
                                   '%s was not settled' % invoice.name)

    def test_two_cheques_for_one_instalment_make_two_payments(self):
        """M17, the other direction — two pieces of paper, two payments."""
        target = self.installments[0]
        invoice = self._invoice_installment(target)
        half = invoice.amount_total / 2.0
        first = self._check(amount=half)
        second = self._check(amount=half)
        for check in (first, second):
            self.Allocation.create({
                'check_id': check.id, 'sale_installment_id': target.id,
                'allocated_amount': half,
            })
        deposit = self._deposit(first | second)
        self.assertEqual(len(deposit.payment_ids), 2)
        invoice.invalidate_recordset()
        self.assertAlmostEqual(invoice.amount_residual, 0.0, 2)

    def test_partial_coverage_leaves_the_invoice_partly_open(self):
        """M18 — the obligation stays outstanding until it is fully settled."""
        target = self.installments[0]
        invoice = self._invoice_installment(target)
        check = self._check(amount=invoice.amount_total * 0.6)
        self.Allocation.create({
            'check_id': check.id, 'sale_installment_id': target.id,
            'allocated_amount': check.amount,
        })
        self._deposit(check)
        invoice.invalidate_recordset()
        self.assertGreater(invoice.amount_residual, 0.0)
        self.assertEqual(invoice.payment_state, 'partial')

    def test_an_uninvoiced_obligation_simply_gets_no_reconciliation(self):
        """Not an error — a PDC often arrives long before the invoice."""
        target = self.installments[-1]
        check = self._check(amount=target.current_amount,
                            sale_contract_id=self.contract.id,
                            sale_installment_id=target.id)
        deposit = self._deposit(check)
        self.assertTrue(check.payment_id)
        self.assertFalse(check.payment_id.reconciled_invoice_ids)
        self.assertEqual(deposit.state, 'confirmed')

    def test_a_reversed_invoice_is_not_reconciled_against(self):
        target = self.installments[0]
        invoice = self._invoice_installment(target)
        invoice._reverse_moves(default_values_list=[{
            'invoice_date': invoice.invoice_date}], cancel=True)
        check = self._check(amount=100000.0,
                            sale_contract_id=self.contract.id,
                            sale_installment_id=target.id)
        self.assertFalse(check._invoices_to_settle())


@tagged('post_install', '-at_install', 'atmta_checks')
class TestClearanceComesFromOdoo(ChecksCommon):
    """M8 — the whole point of the release."""

    def test_presented_is_not_cleared(self):
        """Rule 4 — 'deposit created' is not 'cash cleared'."""
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        self.assertEqual(check.state, 'deposited')
        self.assertNotEqual(check.state, 'cleared')
        self.assertFalse(check.is_bank_matched)
        self.assertIn(check.accounting_state,
                      ('payment_registered', 'in_payment'))

    def test_marking_cleared_by_hand_is_refused_without_a_bank_match(self):
        """0.1 wrote `state = 'cleared'` unconditionally right here."""
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        with self.assertRaises(UserError) as err:
            check.action_mark_cleared()
        message = str(err.exception)
        self.assertIn('not been matched against a bank transaction', message)
        self.assertIn('would record cash that has not arrived', message)
        self.assertEqual(check.state, 'deposited')

    def test_bank_reconciliation_clears_the_cheque(self):
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        self._reconcile_with_bank(check.payment_id)
        self.assertEqual(check.payment_id.state, 'paid')
        check._sync_clearance_from_accounting()
        self.assertEqual(check.state, 'cleared')
        self.assertTrue(check.cleared_date)
        self.assertEqual(check.accounting_state, 'reconciled')
        self.assertTrue(check.is_bank_matched)

    def test_the_cron_clears_it_without_anyone_pressing_anything(self):
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        self._reconcile_with_bank(check.payment_id)
        self.Check._cron_sync_accounting()
        check.invalidate_recordset()
        self.assertEqual(check.state, 'cleared')

    def test_the_sync_is_idempotent(self):
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        self._reconcile_with_bank(check.payment_id)
        check._sync_clearance_from_accounting()
        cleared_on = check.cleared_date
        self.Check._cron_sync_accounting()
        self.Check._cron_sync_accounting()
        check.invalidate_recordset()
        self.assertEqual(check.state, 'cleared')
        self.assertEqual(check.cleared_date, cleared_on)

    def test_the_manual_refresh_cannot_invent_clearance(self):
        """M8 — a manual override refreshes; it never pretends."""
        check = self._check(journal=self.journal_outstanding)
        deposit = self._deposit(check, journal=self.journal_outstanding)
        deposit.action_sync_accounting()
        self.assertEqual(check.state, 'deposited')
        deposit.action_clear_all()
        check.invalidate_recordset()
        self.assertEqual(check.state, 'deposited')

    def test_presentation_attempt_records_the_clearance(self):
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        self._reconcile_with_bank(check.payment_id)
        check._sync_clearance_from_accounting()
        attempt = check.presentation_ids
        self.assertEqual(attempt.state, 'cleared')
        self.assertTrue(attempt.cleared_date)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestDirectBankConfiguration(ChecksCommon):
    """M7's second supported configuration.

    When the payment method posts straight into the bank account there is no
    outstanding balance and no separate matching step: Odoo marks the payment
    `paid` on posting. A cheque banked this way clears immediately, and that is
    **correct for that configuration** — not a bug and not a loophole.
    """

    def test_a_direct_journal_clears_on_presentation(self):
        check = self._check(journal=self.journal_direct)
        self._deposit(check, journal=self.journal_direct)
        self.assertEqual(check.payment_id.state, 'paid')
        check._sync_clearance_from_accounting()
        self.assertEqual(check.state, 'cleared')

    def test_both_configurations_use_the_same_clearance_test(self):
        """There is exactly one definition of 'the bank honoured this'."""
        direct = self._check(journal=self.journal_direct)
        self._deposit(direct, journal=self.journal_direct)
        self.assertTrue(direct._is_cash_confirmed())

        outstanding = self._check(journal=self.journal_outstanding)
        self._deposit(outstanding, journal=self.journal_outstanding)
        self.assertFalse(outstanding._is_cash_confirmed())
        self._reconcile_with_bank(outstanding.payment_id)
        self.assertTrue(outstanding._is_cash_confirmed())


@tagged('post_install', '-at_install', 'atmta_checks')
class TestFullLifecycle(ChecksCommon):
    """M37 — the end-to-end scenario, with nothing mocked."""

    def test_full_lifecycle_to_cleared(self):
        """INVOICE → CHECK → DEPOSIT → PAYMENT → BANK → RECONCILE → CLEARED."""
        contract = self._signed_contract()
        target = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]

        # 1. INVOICE
        invoice = self._invoice_installment(target)
        self.assertEqual(invoice.state, 'posted')
        self.assertEqual(invoice.payment_state, 'not_paid')

        # 2. CHEQUE — paper arrives. No money has moved.
        check = self._check(amount=invoice.amount_total,
                            sale_contract_id=contract.id,
                            sale_installment_id=target.id,
                            journal=self.journal_outstanding)
        invoice.invalidate_recordset()
        target.invalidate_recordset()
        self.assertEqual(invoice.payment_state, 'not_paid')
        self.assertEqual(target.paid_amount, 0.0)
        self.assertAlmostEqual(target.secured_by_checks_amount,
                               target.current_amount, 2)

        # 3. DEPOSIT → 4. ACCOUNT.PAYMENT
        deposit = self._deposit(check, journal=self.journal_outstanding)
        self.assertEqual(len(deposit.payment_ids), 1)
        payment = check.payment_id
        invoice.invalidate_recordset()
        self.assertAlmostEqual(invoice.amount_residual, 0.0, 2)
        # Money on its way, but the bank has NOT confirmed it.
        #
        # Note what `payment.state` says here: Odoo 18 has already promoted it
        # to 'paid', because `_compute_state` promotes any payment whose
        # reconciled invoices all read 'paid' — and on Community
        # `_get_invoice_in_payment_state()` returns 'paid'. So `state` is not a
        # statement about the bank at this point, and keying clearance off it
        # would report cash that has not arrived. `is_matched` is.
        self.assertFalse(payment.is_matched)
        self.assertFalse(check.is_bank_matched)
        self.assertEqual(check.state, 'deposited')
        self.assertNotEqual(check.state, 'cleared')

        # 5. BANK TRANSACTION → 6. RECONCILE
        self._reconcile_with_bank(payment)
        self.assertEqual(payment.state, 'paid')

        # 7. INVOICE PAID → 8. CHECK CLEARED
        self.Check._cron_sync_accounting()
        check.invalidate_recordset()
        invoice.invalidate_recordset()
        target.invalidate_recordset()
        self.assertEqual(check.state, 'cleared')
        self.assertEqual(check.accounting_state, 'reconciled')
        self.assertEqual(invoice.payment_state, 'paid')
        self.assertEqual(target.state, 'paid')
        self.assertAlmostEqual(target.paid_amount, invoice.amount_total, 2)
        # And the cheque stops being counted as security, so the same money is
        # never both paper and cash.
        self.assertEqual(target.secured_by_checks_amount, 0.0)
        self.assertEqual(deposit.state, 'reconciled')


@tagged('post_install', '-at_install', 'atmta_checks')
class TestBatchPaymentIntegration(ChecksCommon):
    """M6 — Odoo's accounting batch, when Odoo can take it."""

    def test_the_link_is_late_bound(self):
        """`account_batch_payment` is Enterprise; this module is LGPL-3, so it
        can never be a hard dependency."""
        manifest_depends = self.env['ir.module.module'].search(
            [('name', '=', 'real_estate_checks')]).dependencies_id.mapped('name')
        self.assertNotIn('account_batch_payment', manifest_depends)

    def test_payments_are_grouped_when_the_module_is_present(self):
        if 'account.batch.payment' not in self.env:
            self.skipTest('account_batch_payment is not installed')
        checks = self._check() | self._check()
        deposit = self._deposit(checks, journal=self.journal_outstanding)
        self.assertTrue(deposit.batch_payment_id)
        self.assertEqual(set(deposit.batch_payment_id.payment_ids.ids),
                         set(deposit.payment_ids.ids))
        self.assertEqual(deposit.batch_payment_id.batch_type, 'inbound')

    def test_the_deposit_works_without_the_batch(self):
        """The treasury workflow must not depend on Enterprise being present.

        Simulated by presenting into a cash journal, which the batch
        integration declines — the same code path a Community install takes.
        """
        cash_journal = self.env['account.journal'].create({
            'name': 'Cash Desk', 'code': 'CSHD', 'type': 'cash',
            'company_id': self.company.id,
        })
        check = self._check(journal=cash_journal)
        deposit = self._deposit(check, journal=cash_journal)
        self.assertEqual(deposit.state, 'confirmed')
        self.assertTrue(check.payment_id)
        self.assertFalse(deposit.batch_payment_id)
