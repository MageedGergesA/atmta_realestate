# -*- coding: utf-8 -*-
"""M6 / M22 / M28 — the deposit batch and the workbench."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import ChecksCommon


@tagged('post_install', '-at_install', 'atmta_checks')
class TestDepositValidation(ChecksCommon):
    """0.1 checked one thing. These are the rest."""

    def test_a_valid_batch_confirms(self):
        checks = self._check() | self._check()
        deposit = self._deposit(checks)
        self.assertEqual(deposit.state, 'confirmed')
        self.assertEqual(deposit.check_count, 2)
        self.assertEqual(deposit.total_amount, 200000.0)
        for check in checks:
            self.assertEqual(check.state, 'deposited')

    def test_an_empty_batch_is_refused(self):
        deposit = self.Deposit.create({
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'currency_id': self.company.currency_id.id,
        })
        with self.assertRaises(UserError) as err:
            deposit.action_confirm()
        self.assertIn('at least one check', str(err.exception))

    def test_a_cheque_from_another_company_is_refused(self):
        """M29 — 0.1 let a Company A cheque go into Company B's journal."""
        other = self.env['res.company'].create({'name': 'Other Developer'})
        foreign = self._check()
        # Moving the company also invalidates its journal link, which the
        # cheque's own company constraint catches first — so both are cleared
        # together, leaving a genuinely foreign cheque to offer the slip.
        # `_check_company_auto` also binds `location_id`, so the custody
        # location has to move with the cheque — otherwise the company
        # constraint fires on the wrong field and hides what is being tested.
        foreign.sudo().write({'company_id': other.id, 'journal_id': False,
                              'sale_contract_id': False, 'location_id': False})
        deposit = self.Deposit.create({
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'currency_id': self.company.currency_id.id,
        })
        with self.assertRaises(ValidationError):
            deposit.write({'check_ids': [(6, 0, foreign.ids)]})
            deposit.action_confirm()

    def test_mixed_currencies_are_refused_rather_than_summed(self):
        """M30 — do not add EGP to SAR and call it a total."""
        foreign = self._check(currency_id=self._other_currency().id)
        native = self._check()
        deposit = self.Deposit.create({
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'currency_id': self.company.currency_id.id,
            'check_ids': [(6, 0, (foreign | native).ids)],
        })
        with self.assertRaises(UserError) as err:
            deposit.action_confirm()
        self.assertIn('Mixed-currency', str(err.exception))

    def test_a_cheque_already_at_a_bank_cannot_be_presented_again(self):
        check = self._check()
        self._deposit(check)
        second = self.Deposit.create({
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'currency_id': self.company.currency_id.id,
        })
        with self.assertRaises(UserError):
            second.write({'check_ids': [(4, check.id)]})
            second.action_confirm()

    def test_a_draft_cheque_cannot_be_presented(self):
        """0.1's `action_confirm` accepted `draft`, which contradicted both
        its own field domain and its own wizard."""
        check = self._check(state='draft')
        deposit = self.Deposit.create({
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'currency_id': self.company.currency_id.id,
            'check_ids': [(6, 0, check.ids)],
        })
        with self.assertRaises(UserError) as err:
            deposit.action_confirm()
        self.assertIn('only a registered cheque', str(err.exception).lower())

    def test_every_problem_is_reported_at_once(self):
        """A treasurer fixing a 50-cheque batch should not discover the
        problems one press at a time."""
        today = fields.Date.context_today(self.env['res.partner'])
        draft = self._check(state='draft')
        future = self._check(due_date=today + timedelta(days=30))
        deposit = self.Deposit.create({
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'currency_id': self.company.currency_id.id,
            'check_ids': [(6, 0, (draft | future).ids)],
        })
        with self.assertRaises(UserError) as err:
            deposit.action_confirm()
        message = str(err.exception)
        self.assertIn(draft.name, message)
        self.assertIn(future.name, message)

    def test_a_journal_from_another_company_is_refused(self):
        other = self.env['res.company'].create({'name': 'Third Developer'})
        foreign_journal = self.env['account.journal'].create({
            'name': 'Foreign Bank', 'code': 'FBK', 'type': 'bank',
            'company_id': other.id,
        })
        with self.assertRaises(ValidationError):
            self.Deposit.create({
                'company_id': self.company.id,
                'journal_id': foreign_journal.id,
                'currency_id': self.company.currency_id.id,
            })


@tagged('post_install', '-at_install', 'atmta_checks')
class TestDepositLifecycle(ChecksCommon):

    def test_presentation_stamps_the_cheque_not_just_the_slip(self):
        """0.1 made `deposit_date` a related field, so cancelling a slip
        erased the record of a presentation that really happened."""
        check = self._check()
        deposit = self._deposit(check)
        self.assertEqual(check.presented_date, deposit.deposit_date)
        self.assertEqual(check.deposit_date, deposit.deposit_date)
        self.assertFalse(self.Check._fields['deposit_date'].related)

    def test_confirming_opens_a_presentation_attempt(self):
        check = self._check()
        deposit = self._deposit(check)
        self.assertEqual(check.presentation_count, 1)
        attempt = check.presentation_ids
        self.assertEqual(attempt.attempt, 1)
        self.assertEqual(attempt.deposit_id, deposit)
        self.assertEqual(attempt.state, 'presented')

    def test_cancelling_a_slip_that_never_went_to_the_bank(self):
        checks = self._check() | self._check()
        deposit = self._deposit(checks, confirm=False)
        deposit.action_cancel()
        self.assertEqual(deposit.state, 'cancelled')
        for check in checks:
            self.assertEqual(check.state, 'registered')

    def test_a_slip_with_posted_payments_cannot_be_cancelled(self):
        """Cancelling would erase a presentation that really happened."""
        check = self._check()
        deposit = self._deposit(check)
        with self.assertRaises(UserError) as err:
            deposit.action_cancel()
        self.assertIn('Accounting', str(err.exception))

    def test_a_fully_matched_slip_reaches_reconciled(self):
        """0.1 had no state stronger than 'all cleared'."""
        checks = self._check() | self._check()
        deposit = self._deposit(checks)
        for check in checks:
            self._reconcile_with_bank(check.payment_id)
        deposit.action_sync_accounting()
        self.assertEqual(deposit.state, 'reconciled')

    def test_totals_split_pending_from_cleared(self):
        checks = self._check() | self._check()
        deposit = self._deposit(checks)
        self.assertEqual(deposit.total_pending, 200000.0)
        self.assertEqual(deposit.total_cleared, 0.0)
        self._reconcile_with_bank(checks[0].payment_id)
        deposit.action_sync_accounting()
        deposit.invalidate_recordset()
        self.assertEqual(deposit.total_cleared, 100000.0)
        self.assertEqual(deposit.total_pending, 100000.0)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestMakerChecker(ChecksCommon):
    """M28 — enforced on the server, not by hiding a button."""

    def setUp(self):
        super().setUp()
        self.company.check_maker_checker = True
        self.company.check_self_approve_limit = 0.0

    def test_the_preparer_cannot_confirm_their_own_deposit(self):
        check = self._check()
        deposit = self.Deposit.create({
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'currency_id': self.company.currency_id.id,
            'check_ids': [(6, 0, check.ids)],
        })
        self.assertEqual(deposit.prepared_by_id, self.env.user)
        with self.assertRaises(UserError) as err:
            deposit.action_confirm()
        self.assertIn('maker/checker', str(err.exception))

    def test_a_small_deposit_is_exempt_when_a_limit_is_set(self):
        self.company.check_self_approve_limit = 500000.0
        check = self._check(amount=1000.0)
        deposit = self._deposit(check)
        self.assertEqual(deposit.state, 'confirmed')

    def test_a_large_deposit_is_not_exempt(self):
        self.company.check_self_approve_limit = 50000.0
        check = self._check(amount=100000.0)
        deposit = self.Deposit.create({
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'currency_id': self.company.currency_id.id,
            'check_ids': [(6, 0, check.ids)],
        })
        with self.assertRaises(UserError):
            deposit.action_confirm()

    def test_someone_else_can_confirm(self):
        check = self._check()
        deposit = self.Deposit.create({
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'currency_id': self.company.currency_id.id,
            'check_ids': [(6, 0, check.ids)],
        })
        approver = self.env['res.users'].with_context(
            no_reset_password=True, mail_create_nosubscribe=True).create({
            'name': 'Approver', 'login': 'approver@test.example',
            'email': 'approver@test.example',
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('real_estate_checks.group_checks_approver').id,
                self.env.ref('account.group_account_user').id,
            ])],
        })
        deposit.with_user(approver).with_context(
            tracking_disable=True, mail_notrack=True).action_confirm()
        self.assertEqual(deposit.state, 'confirmed')
        self.assertEqual(deposit.confirmed_by_id, approver)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestDepositWorkbench(ChecksCommon):
    """M22 — nothing offered here may be refused at confirmation."""

    def setUp(self):
        super().setUp()
        self.today = fields.Date.context_today(self.env['res.partner'])

    def _workbench(self, **kwargs):
        vals = {
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'currency_id': self.company.currency_id.id,
            'payment_method_line_id': self._method_line(self.journal).id,
        }
        vals.update(kwargs)
        return self.env['realestate.check.deposit.wizard'].create(vals)

    def test_only_matured_cheques_are_offered_by_default(self):
        due = self._check(due_date=self.today)
        future = self._check(due_date=self.today + timedelta(days=60))
        wizard = self._workbench()
        self.assertIn(due, wizard.eligible_check_ids)
        self.assertNotIn(future, wizard.eligible_check_ids)

    def test_the_horizon_widens_the_offer(self):
        future = self._check(due_date=self.today + timedelta(days=10))
        wizard = self._workbench(horizon_days=30)
        self.assertIn(future, wizard.eligible_check_ids)

    def test_a_cheque_already_on_a_slip_is_not_offered(self):
        check = self._check()
        self._deposit(check)
        wizard = self._workbench()
        self.assertNotIn(check, wizard.eligible_check_ids)

    def test_a_foreign_currency_cheque_is_not_offered(self):
        foreign = self._check(currency_id=self._other_currency().id)
        wizard = self._workbench()
        self.assertNotIn(foreign, wizard.eligible_check_ids)

    def test_select_all_then_create(self):
        self._check()
        self._check()
        wizard = self._workbench()
        wizard.action_select_all_eligible()
        self.assertEqual(len(wizard.check_ids), len(wizard.eligible_check_ids))
        wizard.action_create_deposit()
        deposit = self.Deposit.search([], order='id desc', limit=1)
        self.assertEqual(deposit.state, 'confirmed')
        self.assertEqual(len(deposit.check_ids), len(wizard.check_ids))

    def test_the_wizard_refuses_an_empty_selection(self):
        wizard = self._workbench()
        wizard.check_ids = [(5, 0, 0)]
        with self.assertRaises(UserError):
            wizard.action_create_deposit()
