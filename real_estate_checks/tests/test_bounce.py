# -*- coding: utf-8 -*-
"""M10 / M11 / M37 — the bounce, and putting the receivable back.

The assertion that matters in every one of these: **accounting history is
preserved**. Nothing is deleted, no residual is written by hand, and no
`payment_state` is assigned. The invoice becomes outstanding again because the
reconciliation was undone — which is the only honest way to make it true.
"""

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import ChecksCommon


@tagged('post_install', '-at_install', 'atmta_checks')
class TestBounceAccounting(ChecksCommon):
    """M37's second scenario, in full."""

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()
        self.target = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]

    def _presented(self):
        invoice = self._invoice_installment(self.target)
        check = self._check(amount=invoice.amount_total,
                            sale_contract_id=self.contract.id,
                            sale_installment_id=self.target.id,
                            journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        return check, invoice

    def _bounce(self, check, **kwargs):
        vals = {'check_id': check.id, 'reason': 'insufficient'}
        vals.update(kwargs)
        return self.Bounce.create(vals)

    def test_before_bounce_the_invoice_is_settled(self):
        check, invoice = self._presented()
        invoice.invalidate_recordset()
        self.assertAlmostEqual(invoice.amount_residual, 0.0, 2)

    def test_a_bounce_restores_the_receivable(self):
        """INVOICE → CHECK → DEPOSIT → PAYMENT → BOUNCE → OUTSTANDING."""
        check, invoice = self._presented()
        total = invoice.amount_total
        payment = check.payment_id

        bounce = self._bounce(check)

        invoice.invalidate_recordset()
        self.target.invalidate_recordset()
        check.invalidate_recordset()

        self.assertTrue(bounce.accounting_handled)
        self.assertFalse(bounce.requires_manual_accounting)
        # The customer owes it again.
        self.assertAlmostEqual(invoice.amount_residual, total, 2)
        self.assertNotEqual(invoice.payment_state, 'paid')
        # The commercial obligation is still due.
        self.assertNotEqual(self.target.state, 'paid')
        self.assertEqual(self.target.paid_amount, 0.0)
        # And the instrument says what happened.
        self.assertEqual(check.state, 'bounced')
        self.assertEqual(check.bounce_id, bounce)
        self.assertEqual(check.bounce_date, bounce.bounce_date)

    def test_accounting_history_is_preserved(self):
        """Nothing is deleted. That is the whole rule."""
        check, invoice = self._presented()
        payment = check.payment_id
        payment_move = payment.move_id
        self._bounce(check)

        # The invoice still exists and is still posted.
        self.assertTrue(invoice.exists())
        self.assertEqual(invoice.state, 'posted')
        # The payment still exists — cancelled, not deleted.
        self.assertTrue(payment.exists())
        self.assertEqual(payment.state, 'canceled')
        # Its journal entry still exists too.
        self.assertTrue(payment_move.exists())

    def test_the_bounce_records_what_it_did_to_the_ledger(self):
        check, invoice = self._presented()
        bounce = self._bounce(check)
        self.assertTrue(bounce.accounting_note)
        self.assertIn('Unreconciled', bounce.accounting_note)
        self.assertEqual(bounce.payment_id, check.payment_id)

    def test_a_bounce_on_an_uninvoiced_obligation_still_unwinds_the_payment(self):
        """There is no invoice to restore, but there IS a payment.

        Presenting always registers one (M7), so even a cheque against an
        uninvoiced obligation has money on the ledger that a bounce must take
        back off it.
        """
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        payment = check.payment_id
        bounce = self._bounce(check)
        self.assertTrue(bounce.accounting_handled)
        self.assertIn('cancelled', bounce.accounting_note)
        self.assertEqual(payment.state, 'canceled')

    def test_the_paper_comes_back_from_the_bank(self):
        check, _invoice = self._presented()
        self._bounce(check)
        check.invalidate_recordset()
        self.assertTrue(check.custody_ids.filtered(
            lambda c: c.reason == 'return_from_bank'))
        self.assertEqual(check.location_id, self.safe)

    def test_the_presentation_attempt_records_the_failure(self):
        check, _invoice = self._presented()
        bounce = self._bounce(check)
        attempt = check.presentation_ids
        self.assertEqual(attempt.state, 'bounced')
        self.assertEqual(attempt.bounce_id, bounce)
        self.assertEqual(bounce.presentation_id, attempt)

    def test_a_presentation_can_only_bounce_once(self):
        check, _invoice = self._presented()
        self._bounce(check)
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self._bounce(check)
                self.env.cr.flush()


@tagged('post_install', '-at_install', 'atmta_checks')
class TestBounceGuards(ChecksCommon):
    """0.1's `create()` flipped a cheque to `bounced` from any state at all."""

    def test_only_a_cheque_at_the_bank_can_bounce(self):
        check = self._check()
        with self.assertRaises(UserError) as err:
            self.Bounce.create({'check_id': check.id, 'reason': 'insufficient'})
        self.assertIn('at the bank', str(err.exception))

    def test_a_cleared_cheque_cannot_be_bounced_by_creating_a_row(self):
        check = self._check(journal=self.journal_direct)
        self._deposit(check, journal=self.journal_direct)
        check._sync_clearance_from_accounting()
        self.assertEqual(check.state, 'cleared')
        with self.assertRaises(UserError):
            self.Bounce.create({'check_id': check.id, 'reason': 'insufficient'})

    def test_the_wizard_enforces_the_same_rule(self):
        check = self._check()
        wizard = self.env['realestate.check.bounce.wizard'].create({
            'check_id': check.id, 'reason': 'insufficient',
        })
        with self.assertRaises(UserError):
            wizard.action_register_bounce()


@tagged('post_install', '-at_install', 'atmta_checks')
class TestAlreadyBankMatchedBounce(ChecksCommon):
    """The case M10 says must be handled separately.

    If the bank statement was already matched and the bank *later* posts a
    returned-cheque debit, the original receipt really happened. Silently
    unwinding it would leave the bank account unreconcilable against a
    statement the accountant has already agreed. So the module refuses to
    automate it and issues instructions instead.
    """

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()
        self.target = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]

    def test_a_matched_receipt_is_not_silently_unwound(self):
        invoice = self._invoice_installment(self.target)
        check = self._check(amount=invoice.amount_total,
                            sale_contract_id=self.contract.id,
                            sale_installment_id=self.target.id,
                            journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        self._reconcile_with_bank(check.payment_id)
        check._sync_clearance_from_accounting()
        self.assertEqual(check.state, 'cleared')

        # The bank returns it after clearance. Re-presenting the cheque is how
        # it gets back to a bank-facing state; the point of this test is what
        # happens to the ledger when the payment is already matched.
        check.write({'state': 'deposited'})
        bounce = self.Bounce.create({
            'check_id': check.id, 'reason': 'technical',
        })

        self.assertFalse(bounce.accounting_handled)
        self.assertTrue(bounce.requires_manual_accounting)
        self.assertIn('already been matched', bounce.accounting_note)
        self.assertIn('returned-cheque debit', bounce.accounting_note)
        # The instrument is marked; the ledger is untouched.
        self.assertEqual(check.state, 'bounced')
        invoice.invalidate_recordset()
        self.assertAlmostEqual(invoice.amount_residual, 0.0, 2)

    def test_re_presentation_is_blocked_until_it_is_resolved(self):
        """Otherwise the same money would be registered twice."""
        check = self._check(journal=self.journal_direct)
        self._deposit(check, journal=self.journal_direct)
        check._sync_clearance_from_accounting()
        check.write({'state': 'deposited'})
        bounce = self.Bounce.create({'check_id': check.id, 'reason': 'technical'})
        self.assertTrue(bounce.requires_manual_accounting)
        with self.assertRaises(UserError) as err:
            bounce.action_authorize_representation()
        self.assertIn('awaiting manual accounting', str(err.exception))


@tagged('post_install', '-at_install', 'atmta_checks')
class TestBounceFees(ChecksCommon):
    """M11 — a bank charge and a customer penalty are different things."""

    def setUp(self):
        super().setUp()
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        self.check = check

    def test_they_are_separate_fields(self):
        """0.1 had one generic `penalty_amount` doing both jobs."""
        fields_ = self.Bounce._fields
        self.assertIn('bank_charge_amount', fields_)
        self.assertIn('penalty_amount', fields_)
        self.assertNotEqual(fields_['bank_charge_amount'].string,
                            fields_['penalty_amount'].string)

    def test_recording_a_bounce_does_not_invoice_anyone(self):
        """0.1's wizard raised an invoice as a side effect of pressing Bounce."""
        bounce = self.Bounce.create({
            'check_id': self.check.id, 'reason': 'insufficient',
            'penalty_amount': 500.0, 'bank_charge_amount': 75.0,
        })
        self.assertFalse(bounce.penalty_invoice_id)

    def test_the_penalty_is_invoiced_deliberately(self):
        bounce = self.Bounce.create({
            'check_id': self.check.id, 'reason': 'insufficient',
            'penalty_amount': 500.0,
        })
        bounce.action_issue_penalty_invoice()
        invoice = bounce.penalty_invoice_id
        self.assertTrue(invoice)
        self.assertEqual(invoice.move_type, 'out_invoice')
        self.assertEqual(invoice.partner_id, self.check.partner_id)
        self.assertEqual(invoice.company_id, self.company)
        self.assertAlmostEqual(invoice.amount_untaxed, 500.0, 2)
        # Left in draft: posting is an accounting act.
        self.assertEqual(invoice.state, 'draft')

    def test_the_penalty_uses_a_product_variant_not_a_template(self):
        """0.1 wrote a `product.template` id into `account.move.line.product_id`."""
        field = self.env['res.company']._fields[
            'check_bounce_penalty_product_id']
        self.assertEqual(field.comodel_name, 'product.product')

    def test_no_account_is_hard_coded(self):
        bounce = self.Bounce.create({
            'check_id': self.check.id, 'reason': 'insufficient',
            'penalty_amount': 500.0,
        })
        bounce.action_issue_penalty_invoice()
        line = bounce.penalty_invoice_id.invoice_line_ids
        self.assertTrue(line.account_id,
                        'the account must come from the product, not from us')

    def test_an_unconfigured_penalty_product_says_so(self):
        self.company.check_bounce_penalty_product_id = False
        bounce = self.Bounce.create({
            'check_id': self.check.id, 'reason': 'insufficient',
            'penalty_amount': 500.0,
        })
        with self.assertRaises(UserError) as err:
            bounce.action_issue_penalty_invoice()
        self.assertIn('No bounce penalty product is configured',
                      str(err.exception))

    def test_a_zero_penalty_cannot_be_invoiced(self):
        bounce = self.Bounce.create({
            'check_id': self.check.id, 'reason': 'insufficient',
        })
        with self.assertRaises(UserError):
            bounce.action_issue_penalty_invoice()

    def test_the_penalty_is_invoiced_only_once(self):
        bounce = self.Bounce.create({
            'check_id': self.check.id, 'reason': 'insufficient',
            'penalty_amount': 500.0,
        })
        bounce.action_issue_penalty_invoice()
        with self.assertRaises(UserError):
            bounce.action_issue_penalty_invoice()


@tagged('post_install', '-at_install', 'atmta_checks')
class TestBounceResolution(ChecksCommon):

    def setUp(self):
        super().setUp()
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        self.check = check
        self.bounce = self.Bounce.create({
            'check_id': check.id, 'reason': 'insufficient'})

    def test_every_0_1_reason_survives(self):
        values = dict(self.Bounce._fields['reason'].selection)
        for legacy in ('insufficient', 'stop_payment', 'signature', 'closed',
                       'technical', 'other'):
            self.assertIn(legacy, values)

    def test_resolved_is_derived_from_the_resolution(self):
        """0.1's boolean could disagree with reality; now it cannot."""
        self.assertEqual(self.bounce.resolution, 'pending')
        self.assertFalse(self.bounce.resolved)
        self.bounce.action_mark_resolved('settled_cash')
        self.assertTrue(self.bounce.resolved)
        self.assertEqual(self.bounce.resolution, 'settled_cash')
        self.assertTrue(self.bounce.resolved_date)

    def test_the_followup_cron_creates_one_activity_and_no_more(self):
        self.Bounce._cron_bounce_followup()
        model_id = self.env['ir.model']._get_id('realestate.check.bounce')
        activities = self.env['mail.activity'].search([
            ('res_model_id', '=', model_id), ('res_id', '=', self.bounce.id)])
        self.assertEqual(len(activities), 1)
        self.Bounce._cron_bounce_followup()
        activities = self.env['mail.activity'].search([
            ('res_model_id', '=', model_id), ('res_id', '=', self.bounce.id)])
        self.assertEqual(len(activities), 1, 'the cron spammed activities')
