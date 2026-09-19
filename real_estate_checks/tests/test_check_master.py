# -*- coding: utf-8 -*-
"""M1 / M36 — the instrument itself."""

from datetime import timedelta

import psycopg2

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import ChecksCommon
from ..wizard.bulk_check_wizard import next_check_number


@tagged('post_install', '-at_install', 'atmta_checks')
class TestCheckNumbering(ChecksCommon):
    """Leading zeros are meaningful. 0.1 destroyed them."""

    def test_check_number_is_char(self):
        self.assertEqual(self.Check._fields['check_number'].type, 'char')

    def test_leading_zeros_survive_a_round_trip(self):
        check = self._check(number='000012345')
        check.invalidate_recordset()
        self.assertEqual(check.check_number, '000012345')

    def test_bulk_generation_preserves_the_format(self):
        """0.1 did `int('000012345')` then `str(...)`, giving '12345'."""
        self.assertEqual(next_check_number('000012345', 0), '000012345')
        self.assertEqual(next_check_number('000012345', 1), '000012346')
        self.assertEqual(next_check_number('000012345', 10), '000012355')

    def test_non_numeric_prefixes_work(self):
        """0.1 raised UserError on anything `int()` could not parse."""
        self.assertEqual(next_check_number('AB-0012', 1), 'AB-0013')
        self.assertEqual(next_check_number('7', 1), '8')

    def test_rollover_widens_but_never_narrows(self):
        self.assertEqual(next_check_number('999', 1), '1000')
        self.assertEqual(next_check_number('0999', 1), '1000')

    def test_a_number_with_no_digits_is_refused_clearly(self):
        with self.assertRaises(UserError) as err:
            next_check_number('ABC', 1)
        self.assertIn('does not end in digits', str(err.exception))

    def test_the_bulk_wizard_generates_padded_numbers_end_to_end(self):
        contract = self._signed_contract()
        wizard = self.env['realestate.check.bulk.wizard'].create({
            'sale_contract_id': contract.id,
            'bank_id': self.bank.id,
            'first_check_number': '000000501',
        })
        wizard.action_generate()
        checks = self.Check.search([('sale_contract_id', '=', contract.id)],
                                   order='check_number')
        self.assertTrue(checks)
        self.assertEqual(checks[0].check_number, '000000501')
        for check in checks:
            self.assertEqual(len(check.check_number), 9,
                             'padding was lost on %s' % check.check_number)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestCheckIdentity(ChecksCommon):
    """M1 — the uniqueness key, and how it was widened safely."""

    def test_the_identity_index_exists(self):
        self.env.cr.execute("""
            SELECT indexdef FROM pg_indexes
             WHERE indexname = 'realestate_check_identity_uniq'
        """)
        row = self.env.cr.fetchone()
        self.assertTrue(row, 'the partial identity index was not created')
        definition = row[0]
        for column in ('company_id', 'partner_id', 'bank_id',
                       'account_number', 'check_number'):
            self.assertIn(column, definition)

    @mute_logger('odoo.sql_db')
    def test_the_same_number_twice_on_one_account_is_refused(self):
        self._check(number='000000777')
        with self.assertRaises(psycopg2.errors.UniqueViolation):
            with self.env.cr.savepoint():
                self._check(number='000000777')
                self.env.cr.flush()

    def test_the_same_number_on_a_different_account_is_allowed(self):
        """0.1's key omitted the account, so a customer's second chequebook —
        which also starts at 000001 — collided with the first."""
        self._check(number='000000001', account_number='ACC-A')
        second = self._check(number='000000001', account_number='ACC-B')
        self.assertTrue(second.id)

    def test_a_cancelled_number_can_be_reissued(self):
        """0.1's constraint was unconditional, so a number was burned for ever
        once its cheque was cancelled. Real developers reissue."""
        first = self._check(number='000000042', state='draft')
        first.action_cancel()
        # The identity index is partial on `state`, so the cancellation has to
        # reach the database before the reissue is attempted. A user pressing
        # two buttons gets that for free; a single test transaction does not.
        self.env.flush_all()
        second = self._check(number='000000042')
        self.assertTrue(second.id)
        self.assertEqual(first.state, 'cancelled')


@tagged('post_install', '-at_install', 'atmta_checks')
class TestCheckValidation(ChecksCommon):

    def test_amount_must_be_positive(self):
        with self.assertRaises(ValidationError):
            self._check(amount=0.0)
        with self.assertRaises(ValidationError):
            self._check(amount=-5.0)

    def test_the_positive_amount_message_is_readable(self):
        """A table CHECK would fire first and surface a raw psycopg2 error."""
        with self.assertRaises(ValidationError) as err:
            self._check(amount=-1.0)
        self.assertIn('must be positive', str(err.exception))

    def test_due_date_cannot_precede_issue_date(self):
        today = fields.Date.context_today(self.env['res.partner'])
        with self.assertRaises(ValidationError) as err:
            self._check(issue_date=today, due_date=today - timedelta(days=1))
        self.assertIn('Due date', str(err.exception))

    def test_a_standalone_cheque_can_be_created(self):
        """0.1's `_compute_project_from_source` had no `else` branch, so both
        stored computed fields were left unassigned for a cheque with neither
        a sale nor a rental contract."""
        check = self._check()
        self.assertFalse(check.project_id)
        self.assertFalse(check.property_id)
        self.assertEqual(check.state, 'registered')

    def test_a_contract_cheque_inherits_project_and_property(self):
        contract = self._signed_contract()
        check = self._check(sale_contract_id=contract.id)
        self.assertEqual(check.project_id, contract.project_id)
        self.assertEqual(check.property_id, contract.property_id)

    def test_currency_and_company_default_together(self):
        check = self._check()
        self.assertEqual(check.company_id, self.company)
        self.assertEqual(check.currency_id, self.company.currency_id)

    def test_the_reference_sequence_runs(self):
        check = self._check()
        self.assertTrue(check.name.startswith('CHK-'))
        self.assertNotEqual(check.name, 'New')


@tagged('post_install', '-at_install', 'atmta_checks')
class TestCheckStateMachine(ChecksCommon):
    """M2 — states were added, never renamed or removed."""

    def test_every_0_1_state_survives(self):
        """Developer matches on these strings and production rows carry them."""
        values = dict(self.Check._fields['state'].selection)
        for legacy in ('draft', 'registered', 'deposited', 'cleared',
                       'bounced', 'replaced', 'cancelled'):
            self.assertIn(legacy, values,
                          '0.1 state %r was removed' % legacy)

    def test_the_new_states_were_added_around_them(self):
        values = dict(self.Check._fields['state'].selection)
        for added in ('in_clearing', 'returned'):
            self.assertIn(added, values)

    def test_registering_moves_draft_to_registered(self):
        check = self._check(state='draft')
        check.action_register()
        self.assertEqual(check.state, 'registered')

    def test_only_draft_can_be_registered(self):
        check = self._check()
        with self.assertRaises(UserError):
            check.action_register()

    def test_a_cleared_cheque_cannot_be_cancelled(self):
        """M14 — the money arrived. That is a fact, not a state to undo."""
        check = self._check()
        deposit = self._deposit(check)
        self._reconcile_with_bank(check.payment_id)
        check._sync_clearance_from_accounting()
        self.assertEqual(check.state, 'cleared')
        with self.assertRaises(UserError) as err:
            check.action_cancel()
        self.assertIn('cleared', str(err.exception).lower())

    def test_a_cheque_at_the_bank_cannot_be_cancelled(self):
        check = self._check()
        self._deposit(check)
        with self.assertRaises(UserError) as err:
            check.action_cancel()
        self.assertIn('at the bank', str(err.exception))

    def test_cancelling_releases_its_allocations(self):
        contract = self._signed_contract()
        installment = contract.installment_ids[0]
        check = self._check(state='draft', sale_contract_id=contract.id,
                            sale_installment_id=installment.id)
        self.assertTrue(check.allocation_ids.filtered(
            lambda a: a.state == 'active'))
        check.action_cancel()
        self.assertFalse(check.allocation_ids.filtered(
            lambda a: a.state == 'active'))
        # Cancelled, not deleted: the trail survives.
        self.assertTrue(check.allocation_ids)

    def test_returning_to_the_customer_is_distinct_from_cancelling(self):
        """M15 — handing paper back and voiding it are different acts."""
        check = self._check()
        check.action_return_to_customer()
        self.assertEqual(check.state, 'returned')
        self.assertTrue(check.custody_ids.filtered(
            lambda c: c.reason == 'return_to_customer'))
