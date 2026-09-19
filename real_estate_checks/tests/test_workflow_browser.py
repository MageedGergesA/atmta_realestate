# -*- coding: utf-8 -*-
"""Real-browser verification of the treasury workflows.

The server suite proves the engine is correct. These prove a treasurer can
*see* what state a cheque is in, and that the buttons do what the badges claim.
Each fixture plants exactly one cheque so the tours' selectors are unambiguous.

Three flows, each one an item from the release brief:

* :class:`TestDepositFlowBrowser` — obligation → cheque → allocation → deposit
  → `account.payment` → clearing → bank reconciliation → cleared.
* :class:`TestBounceBeforeBankMatchBrowser` — the safe unwind, with the
  invoice returning to outstanding and the presentation history intact.
* :class:`TestBounceAfterBankMatchBrowser` — the accounting-control boundary:
  the module refuses, explains, and blocks re-presentation.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import HttpCase, tagged

from odoo.addons.real_estate_developer.tests.test_contract import ContractCommon


class WorkflowBrowserCommon(HttpCase, ContractCommon):
    """Developer's commercial fixture, plus a bank and a browser."""

    browser_size = '1600x900'

    def setUp(self):
        super().setUp()
        self.browser_user = self.env.ref('base.user_admin')
        treasury = (self.env.ref('real_estate_checks.group_checks_treasurer')
                    | self.env.ref('account.group_account_user'))
        self.browser_user.groups_id |= treasury
        # `self.env.user` is OdooBot, who holds no Checks group; the
        # server-side gates are real (M31) and would refuse the fixtures.
        self.env.user.groups_id |= treasury
        self.today = fields.Date.context_today(self.env['res.partner'])
        self.bank = self.env['res.bank'].create({'name': 'Workflow Bank'})
        self.location = self.env['realestate.check.location'].create({
            'name': 'Workflow Safe', 'code': 'WSAFE', 'kind': 'safe',
            'company_id': self.company.id,
        })
        self.company.check_default_location_id = self.location.id
        self.journal = self._outstanding_journal()

        # Every scenario starts from a signed contract with a real schedule,
        # so the cheque settles a genuine obligation rather than nothing.
        self.contract = self._contract(payment_plan_id=self._plan().id)
        self.contract.action_sign()
        self.installment = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        self.invoice = self._invoice()

    def _outstanding_journal(self):
        """A journal whose inbound method posts to Outstanding Receipts.

        This is the configuration in which "presented" and "cleared" are
        genuinely different states, which is what these flows are about.
        """
        journal = self.env['account.journal'].create({
            'name': 'Workflow Bank Journal', 'code': 'WFB', 'type': 'bank',
            'company_id': self.company.id,
        })
        line = journal.inbound_payment_method_line_ids.filtered(
            lambda l: l.payment_method_id.code == 'manual')[:1]
        line = line or journal.inbound_payment_method_line_ids[:1]
        if line:
            account = self.env['account.account'].create({
                'name': 'Outstanding Receipts WFB', 'code': 'ORWFB',
                'account_type': 'asset_current', 'reconcile': True,
                'company_ids': [(6, 0, [self.company.id])],
            })
            line.payment_account_id = account.id
            self.method_line = line
        return journal

    def _invoice(self):
        self.installment.action_generate_invoice()
        return self.installment.move_id

    def _cheque(self):
        """The one cheque the tours will find."""
        return self.env['realestate.check'].create({
            'partner_id': self.buyer.id,
            'company_id': self.company.id,
            'currency_id': self.company.currency_id.id,
            'bank_id': self.bank.id,
            'account_number': 'WF-ACC-1',
            'check_number': '000012345',
            'amount': self.invoice.amount_total,
            'issue_date': self.today - timedelta(days=60),
            'due_date': self.today,
            'journal_id': self.journal.id,
            'sale_contract_id': self.contract.id,
            'sale_installment_id': self.installment.id,
            'state': 'registered',
        })

    def _present(self, check):
        deposit = self.env['realestate.check.deposit'].create({
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'currency_id': self.company.currency_id.id,
            'deposit_date': self.today,
            'payment_method_line_id': self.method_line.id,
            'check_ids': [(6, 0, check.ids)],
        })
        deposit.action_confirm()
        return deposit

    def _match_with_bank(self, payment):
        """A real statement line, reconciled through Odoo's own machinery."""
        move = payment.move_id
        liquidity = move.line_ids.filtered(
            lambda l: l.account_id == payment.outstanding_account_id)
        statement_line = self.env['account.bank.statement.line'].create({
            'journal_id': payment.journal_id.id,
            'date': payment.date,
            'payment_ref': 'Clearing %s' % payment.name,
            'partner_id': payment.partner_id.id,
            'amount': payment.amount,
        })
        counterpart = statement_line.move_id.line_ids.filtered(
            lambda l: l.account_id != statement_line.journal_id.default_account_id)
        counterpart.account_id = payment.outstanding_account_id
        (liquidity | counterpart).reconcile()
        payment.invalidate_recordset()
        return statement_line


# ---------------------------------------------------------------------------
# 1. The deposit flow, end to end
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestDepositFlowBrowser(WorkflowBrowserCommon):

    def test_the_whole_flow_in_the_browser(self):
        """INVOICE → CHECK → ALLOCATION → DEPOSIT → PAYMENT → CLEARING →
        BANK RECONCILIATION → CLEARED, each step read off the screen."""
        check = self._cheque()

        # --- 1. paper received, no accounting ---
        self.assertEqual(check.state, 'registered')
        self.assertFalse(check.payment_id)
        self.start_tour("/odoo/action-real_estate_checks.action_realestate_check",
                        "atmta_treasury_before_deposit_tour",
                        login="admin", timeout=200)

        # --- 2. presentation, driven through the UI ---
        self.start_tour("/odoo/action-real_estate_checks.action_realestate_check",
                        "atmta_treasury_deposit_flow_tour",
                        login="admin", timeout=200)
        # The cheque side of the same presentation.
        self.start_tour("/odoo/action-real_estate_checks.action_realestate_check",
                        "atmta_treasury_presented_check_tour",
                        login="admin", timeout=200)

        check.invalidate_recordset()
        self.assertEqual(check.state, 'deposited',
                         "The UI flow did not present the cheque")
        self.assertTrue(check.payment_id, "No payment was raised by the UI flow")
        self.assertFalse(check.is_bank_matched,
                         "The cheque is bank-matched with no statement line")
        self.invoice.invalidate_recordset()
        self.assertAlmostEqual(self.invoice.amount_residual, 0.0, 2,
                               "The payment did not reconcile with the invoice")

        # --- 3. the bank transaction arrives, and is matched ---
        self._match_with_bank(check.payment_id)
        self.env['realestate.check']._cron_sync_accounting()
        check.invalidate_recordset()
        self.assertEqual(check.state, 'cleared')

        # --- 4. the browser shows clearance ---
        self.start_tour("/odoo/action-real_estate_checks.action_realestate_check",
                        "atmta_treasury_cleared_flow_tour",
                        login="admin", timeout=200)


# ---------------------------------------------------------------------------
# 2. Bounce before any bank match
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestBounceBeforeBankMatchBrowser(WorkflowBrowserCommon):

    def test_bounce_restores_the_receivable_and_shows_it(self):
        check = self._cheque()
        self._present(check)
        payment = check.payment_id
        payment_move = payment.move_id
        total = self.invoice.amount_total

        self.invoice.invalidate_recordset()
        self.assertAlmostEqual(self.invoice.amount_residual, 0.0, 2)
        self.assertFalse(payment.is_matched, "Precondition: no bank match yet")

        self.start_tour("/odoo/action-real_estate_checks.action_realestate_check",
                        "atmta_treasury_bounce_flow_tour",
                        login="admin", timeout=200)
        # The cheque side, opened the way a treasurer would rather than by
        # clicking through a relational field.
        self.start_tour("/odoo/action-real_estate_checks.action_realestate_check",
                        "atmta_treasury_bounced_check_tour",
                        login="admin", timeout=200)

        check.invalidate_recordset()
        self.invoice.invalidate_recordset()
        self.installment.invalidate_recordset()

        # The instrument.
        self.assertEqual(check.state, 'bounced')

        # No duplicate payment: exactly the one, and it is cancelled.
        payments = self.env['account.payment'].search(
            [('realestate_check_id', '=', check.id)])
        self.assertEqual(len(payments), 1,
                         "The bounce created a second payment")
        self.assertEqual(payments.state, 'canceled')

        # No deleted accounting history.
        self.assertTrue(self.invoice.exists())
        self.assertEqual(self.invoice.state, 'posted')
        self.assertTrue(payment.exists())
        self.assertTrue(payment_move.exists())

        # The obligation is outstanding again.
        self.assertAlmostEqual(self.invoice.amount_residual, total, 2)
        self.assertNotEqual(self.invoice.payment_state, 'paid')
        self.assertEqual(self.installment.paid_amount, 0.0)
        self.assertNotEqual(self.installment.state, 'paid')

        # The presentation history survives.
        self.assertEqual(len(check.presentation_ids), 1)
        self.assertEqual(check.presentation_ids.state, 'bounced')
        self.assertTrue(check.presentation_ids.payment_id)

        bounce = check.bounce_id
        self.assertTrue(bounce.accounting_handled)
        self.assertFalse(bounce.requires_manual_accounting)


# ---------------------------------------------------------------------------
# 3. Bounce after the bank has already matched — the control boundary
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestBounceAfterBankMatchBrowser(WorkflowBrowserCommon):
    """M10's separate case, and an intentional accounting-control boundary.

    Module 3 does **not** automate this, by design. What it must do — and what
    this proves in a browser — is refuse, explain, and block re-presentation.
    """

    def test_the_guard_refuses_explains_and_blocks(self):
        check = self._cheque()
        self._present(check)
        self._match_with_bank(check.payment_id)
        check._sync_clearance_from_accounting()
        self.assertEqual(check.state, 'cleared')

        # The bank later returns it. Getting the instrument back to a
        # bank-facing state is the precondition; the point of the test is what
        # happens to the ledger.
        check.write({'state': 'deposited'})
        bounce = self.env['realestate.check.bounce'].create({
            'check_id': check.id, 'reason': 'technical'})

        # Refused: nothing was unwound.
        self.assertFalse(bounce.accounting_handled)
        self.assertTrue(bounce.requires_manual_accounting)
        self.invoice.invalidate_recordset()
        self.assertAlmostEqual(
            self.invoice.amount_residual, 0.0, 2,
            "The ledger was modified despite the bank match")
        self.assertNotEqual(check.payment_id.state, 'canceled',
                            "A bank-matched payment was cancelled automatically")

        # Explained, and blocking.
        self.start_tour("/odoo/action-real_estate_checks.action_check_bounce",
                        "atmta_treasury_matched_bounce_tour",
                        login="admin", timeout=200)

        bounce.invalidate_recordset()
        self.assertEqual(
            bounce.resolution, 'pending',
            "Re-presentation was allowed while the accounting is unresolved")
