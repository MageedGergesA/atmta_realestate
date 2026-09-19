# -*- coding: utf-8 -*-
"""M12 / M13 — the second attempt, and the replacement chain."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import ChecksCommon


@tagged('post_install', '-at_install', 'atmta_checks')
class TestRepresentation(ChecksCommon):
    """M12 — the same cheque goes back to the bank, and history survives."""

    def _bounced_check(self):
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        bounce = self.Bounce.create({
            'check_id': check.id, 'reason': 'insufficient'})
        return check, bounce

    def test_a_bounced_cheque_can_be_authorised_again(self):
        check, bounce = self._bounced_check()
        bounce.action_authorize_representation()
        check.invalidate_recordset()
        self.assertEqual(check.state, 'registered')
        self.assertFalse(check.deposit_id)
        self.assertFalse(check.payment_id)
        self.assertEqual(bounce.resolution, 're_presented')

    def test_the_second_attempt_is_attempt_two(self):
        check, bounce = self._bounced_check()
        bounce.action_authorize_representation()
        self._deposit(check, journal=self.journal_outstanding)
        check.invalidate_recordset()
        self.assertEqual(check.presentation_count, 2)
        attempts = check.presentation_ids.sorted('attempt')
        self.assertEqual(attempts.mapped('attempt'), [1, 2])
        self.assertEqual(attempts[0].state, 'bounced')
        self.assertEqual(attempts[1].state, 'presented')

    def test_attempt_one_bounced_attempt_two_cleared(self):
        """The scenario M9 exists for."""
        check, bounce = self._bounced_check()
        bounce.action_authorize_representation()
        self._deposit(check, journal=self.journal_outstanding)
        self._reconcile_with_bank(check.payment_id)
        check._sync_clearance_from_accounting()

        check.invalidate_recordset()
        self.assertEqual(check.state, 'cleared')
        attempts = check.presentation_ids.sorted('attempt')
        self.assertEqual(attempts[0].state, 'bounced')
        self.assertEqual(attempts[1].state, 'cleared')
        # The first bounce was not destroyed.
        self.assertEqual(len(check.bounce_ids), 1)
        self.assertTrue(bounce.exists())

    def test_the_second_attempt_gets_its_own_payment(self):
        """The first one was cancelled when the cheque bounced."""
        check, bounce = self._bounced_check()
        first_payment = check.presentation_ids.payment_id
        bounce.action_authorize_representation()
        self._deposit(check, journal=self.journal_outstanding)
        attempts = check.presentation_ids.sorted('attempt')
        self.assertNotEqual(attempts[1].payment_id, first_payment)
        self.assertEqual(first_payment.state, 'canceled')
        self.assertEqual(attempts[1].payment_id.state, 'in_process')

    def test_a_cheque_cannot_be_at_two_banks_at_once(self):
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        with self.assertRaises(ValidationError) as err:
            self.Presentation.open_attempt(check, journal=self.journal_direct)
        self.assertIn('one bank at a time', str(err.exception))

    def test_a_settled_attempt_cannot_be_deleted(self):
        check, _bounce = self._bounced_check()
        with self.assertRaises(UserError) as err:
            check.presentation_ids.unlink()
        self.assertIn('cannot be deleted', str(err.exception))


@tagged('post_install', '-at_install', 'atmta_checks')
class TestReplacement(ChecksCommon):
    """M13 — the lineage 0.1 declared and never wrote."""

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()
        self.target = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        self.today = fields.Date.context_today(self.env['res.partner'])

    def _replace(self, original, bounce=None, **kwargs):
        vals = {
            'original_check_id': original.id,
            'new_partner_id': original.partner_id.id,
            'check_number': '%s-R' % original.check_number,
            'bank_id': original.bank_id.id,
            'amount': original.amount,
            'issue_date': self.today,
            # Due today, so the replacement can itself be presented without
            # tripping the early-presentation policy, which is not what these
            # tests are about.
            'due_date': self.today,
        }
        if bounce:
            vals['bounce_id'] = bounce.id
        vals.update(kwargs)
        wizard = self.env['realestate.check.replace.wizard'].create(vals)
        wizard.action_replace()
        original.invalidate_recordset()
        return original.replacement_check_id

    def test_replacing_a_bounced_cheque(self):
        original = self._check(journal=self.journal_outstanding)
        self._deposit(original, journal=self.journal_outstanding)
        bounce = self.Bounce.create({
            'check_id': original.id, 'reason': 'insufficient'})
        replacement = self._replace(original, bounce)

        self.assertEqual(original.state, 'replaced')
        self.assertEqual(original.replacement_check_id, replacement)
        self.assertEqual(replacement.replaces_check_id, original)
        self.assertEqual(bounce.resolution, 'replaced')
        self.assertEqual(bounce.replacement_check_id, replacement)

    def test_a_three_link_chain_is_reconstructible(self):
        """CHK-A bounced → CHK-B bounced → CHK-C cleared."""
        a = self._check(journal=self.journal_outstanding)
        self._deposit(a, journal=self.journal_outstanding)
        bounce_a = self.Bounce.create({'check_id': a.id, 'reason': 'insufficient'})
        b = self._replace(a, bounce_a)

        self._deposit(b, journal=self.journal_outstanding,
                      date=b.due_date)
        bounce_b = self.Bounce.create({'check_id': b.id, 'reason': 'closed'})
        c = self._replace(b, bounce_b, check_number='%s-R2' % a.check_number)

        for node in (a, b, c):
            node.invalidate_recordset()
        self.assertEqual(a.replacement_generation, 0)
        self.assertEqual(b.replacement_generation, 1)
        self.assertEqual(c.replacement_generation, 2)
        self.assertEqual(b.root_check_id, a)
        self.assertEqual(c.root_check_id, a)

        # The whole chain in one indexed query, which is what the action does.
        chain = self.Check.search(['|', ('id', '=', a.id),
                                        ('root_check_id', '=', a.id)])
        self.assertEqual(set(chain.ids), {a.id, b.id, c.id})

    def test_allocations_move_rather_than_duplicate(self):
        """M13 — do not automatically duplicate already-covered amounts."""
        original = self._check(amount=self.target.current_amount,
                               sale_contract_id=self.contract.id,
                               sale_installment_id=self.target.id)
        self.target.invalidate_recordset()
        secured_before = self.target.secured_by_checks_amount

        replacement = self._replace(original)

        self.target.invalidate_recordset()
        original.invalidate_recordset()
        replacement.invalidate_recordset()
        # Covered exactly once, still.
        self.assertAlmostEqual(
            self.target.secured_by_checks_amount, secured_before, 2)
        self.assertEqual(replacement.allocation_count, 1)
        self.assertFalse(original.allocation_ids.filtered(
            lambda a: a.state == 'active'))

    def test_a_cheque_cannot_replace_itself(self):
        check = self._check()
        with self.assertRaises(ValidationError) as err:
            check.write({'replaces_check_id': check.id})
        self.assertIn('cannot replace itself', str(err.exception))

    def test_a_cycle_is_refused(self):
        a = self._check()
        b = self._check()
        b.write({'replaces_check_id': a.id})
        with self.assertRaises(ValidationError) as err:
            a.write({'replaces_check_id': b.id})
        self.assertIn('cycle', str(err.exception).lower())

    def test_a_cleared_cheque_cannot_be_replaced(self):
        check = self._check(journal=self.journal_direct)
        self._deposit(check, journal=self.journal_direct)
        check._sync_clearance_from_accounting()
        self.assertEqual(check.state, 'cleared')
        with self.assertRaises(UserError) as err:
            self._replace(check)
        self.assertIn('money arrived', str(err.exception))

    def test_a_cancelled_cheque_cannot_be_replaced(self):
        check = self._check(state='draft')
        check.action_cancel()
        with self.assertRaises(UserError) as err:
            self._replace(check)
        self.assertIn('cancelled', str(err.exception))

    def test_a_cheque_at_the_bank_cannot_be_replaced(self):
        """Otherwise the same obligation would be covered twice."""
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        with self.assertRaises(UserError) as err:
            self._replace(check)
        self.assertIn('outcome is unknown', str(err.exception))

    def test_replacing_twice_is_refused(self):
        original = self._check()
        self._replace(original)
        with self.assertRaises(UserError) as err:
            self._replace(original, check_number='SECOND')
        self.assertIn('already been replaced', str(err.exception))

    def test_a_replacement_smaller_than_its_allocations_is_refused(self):
        original = self._check(amount=self.target.current_amount,
                               sale_contract_id=self.contract.id,
                               sale_installment_id=self.target.id)
        with self.assertRaises(UserError) as err:
            self._replace(original, amount=1.0)
        self.assertIn('would be carried onto it', str(err.exception))

    def test_the_original_can_be_handed_back(self):
        original = self._check()
        self._replace(original, return_original=True)
        original.invalidate_recordset()
        self.assertTrue(original.custody_ids.filtered(
            lambda c: c.reason == 'return_to_customer'))
