# -*- coding: utf-8 -*-
"""M15 — the P0 integration with Developer's commercial changes.

Every one of Developer's five post-signature workflows is exercised against
each of the three physical positions a cheque can be in: on hand, at the bank,
and cleared. The matrix these produce is the one reproduced in §10 of the
implementation report.
"""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import ChecksCommon


@tagged('post_install', '-at_install', 'atmta_checks')
class TestExposureApi(ChecksCommon):
    """M15 — Developer asks a question and gets an answer, not a search."""

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()
        self.installments = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))

    def test_the_api_exists_on_the_contract(self):
        for method in ('get_check_exposure', 'get_locked_allocations',
                       'assert_schedule_can_change'):
            self.assertTrue(hasattr(self.contract, method), method)

    def test_exposure_buckets_a_mixed_portfolio(self):
        on_hand = self._check(sale_contract_id=self.contract.id)
        at_bank = self._check(sale_contract_id=self.contract.id,
                              journal=self.journal_outstanding)
        self._deposit(at_bank, journal=self.journal_outstanding)
        cleared = self._check(sale_contract_id=self.contract.id,
                              journal=self.journal_direct)
        self._deposit(cleared, journal=self.journal_direct)
        cleared._sync_clearance_from_accounting()

        exposure = self.contract.get_check_exposure()
        self.assertEqual(exposure['on_hand']['count'], 1)
        self.assertEqual(exposure['at_bank']['count'], 1)
        self.assertEqual(exposure['cleared']['count'], 1)
        self.assertEqual(exposure['total_count'], 3)
        self.assertFalse(exposure['can_change_schedule'])
        self.assertEqual(exposure['blocking_count'], 1)

    def test_the_payload_carries_what_a_decision_needs(self):
        check = self._check(sale_contract_id=self.contract.id,
                            sale_installment_id=self.installments[0].id)
        exposure = self.contract.get_check_exposure()
        row = exposure['on_hand']['checks'][0]
        for key in ('reference', 'check_number', 'bank', 'due_date', 'amount',
                    'state', 'accounting_state', 'allocated_amount',
                    'installment_id', 'invoice_ids', 'payment_id'):
            self.assertIn(key, row)
        self.assertEqual(row['reference'], check.name)

    def test_a_contract_level_cheque_is_visible_to_the_guard(self):
        """0.1's Developer-side search looked only at `sale_installment_id`, so
        a cheque attached to the contract itself was invisible to every
        guard — and a restructuring would run underneath it."""
        check = self._check(sale_contract_id=self.contract.id,
                            journal=self.journal_outstanding)
        self.assertFalse(check.sale_installment_id)
        self._deposit(check, journal=self.journal_outstanding)
        blocking = self.contract._blocking_checks()
        self.assertIn(check, blocking)

    def test_locked_allocations_are_the_ones_that_left_the_building(self):
        free = self._check(sale_contract_id=self.contract.id,
                           sale_installment_id=self.installments[0].id)
        locked = self._check(sale_contract_id=self.contract.id,
                             sale_installment_id=self.installments[1].id,
                             journal=self.journal_outstanding)
        self._deposit(locked, journal=self.journal_outstanding)
        allocations = self.contract.get_locked_allocations()
        self.assertIn(locked.allocation_ids[0], allocations)
        self.assertNotIn(free.allocation_ids[0], allocations)

    def test_the_dead_states_are_gone(self):
        """Developer's placeholder matched `collected` and `endorsed`, which
        have never been values of this field."""
        values = dict(self.Check._fields['state'].selection)
        self.assertNotIn('collected', values)
        self.assertNotIn('endorsed', values)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestRestructuring(ChecksCommon):

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()
        self.installments = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))

    def _amendment(self, kind='schedule_change'):
        return self.env['realestate.sale.contract.amendment'].create({
            'contract_id': self.contract.id,
            'amendment_type': kind,
            'reason': 'Test',
        })

    def test_a_registered_cheque_does_not_block_restructuring(self):
        """Paper still in the safe can be cancelled or reallocated."""
        self._check(sale_contract_id=self.contract.id,
                    sale_installment_id=self.installments[-1].id)
        self.contract.assert_schedule_can_change(
            self.contract._open_installments())

    def test_a_deposited_cheque_blocks_restructuring(self):
        check = self._check(sale_contract_id=self.contract.id,
                            sale_installment_id=self.installments[-1].id,
                            journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        with self.assertRaises(UserError) as err:
            self.contract._apply_restructure(self._amendment(), self._plan('P2'))
        message = str(err.exception)
        self.assertIn('at the bank', message)
        self.assertIn(check.name, message)

    def test_an_unresolved_bounce_blocks_restructuring(self):
        """0.1's guard let a bounced cheque through. Once the payment exists,
        a bounce leaves the money position in flux."""
        check = self._check(sale_contract_id=self.contract.id,
                            sale_installment_id=self.installments[-1].id,
                            journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        self.Bounce.create({'check_id': check.id, 'reason': 'insufficient'})
        with self.assertRaises(UserError):
            self.contract.assert_schedule_can_change(
                self.contract._open_installments())

    def test_a_cleared_cheque_does_not_block(self):
        """It is history; the instalment it settled is no longer open."""
        check = self._check(sale_contract_id=self.contract.id,
                            journal=self.journal_direct)
        self._deposit(check, journal=self.journal_direct)
        check._sync_clearance_from_accounting()
        self.assertEqual(check.state, 'cleared')
        self.contract.assert_schedule_can_change(
            self.contract._open_installments())

    def test_the_message_names_the_offending_cheques(self):
        check = self._check(sale_contract_id=self.contract.id,
                            journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        with self.assertRaises(UserError) as err:
            self.contract.assert_schedule_can_change(
                self.contract._open_installments(), operation='A restructure')
        message = str(err.exception)
        self.assertIn('A restructure', message)
        self.assertIn(check.check_number, message)
        self.assertIn('Resolve them in Treasury first', message)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestSettlementAndCancellation(ChecksCommon):

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()

    def _amendment(self, kind):
        return self.env['realestate.sale.contract.amendment'].create({
            'contract_id': self.contract.id, 'amendment_type': kind,
            'reason': 'Test',
        })

    def test_the_settlement_preview_lists_the_paper(self):
        """M15 — the buyer must be told what instruments are involved."""
        self._check(sale_contract_id=self.contract.id)
        preview = self.contract._settlement_preview()
        self.assertIn('check_exposure', preview)
        self.assertEqual(preview['check_exposure']['on_hand']['count'], 1)

    def test_settlement_is_blocked_by_a_presented_cheque(self):
        check = self._check(sale_contract_id=self.contract.id,
                            journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        with self.assertRaises(UserError) as err:
            self.contract._apply_settlement(self._amendment('early_settlement'))
        self.assertIn('early settlement', str(err.exception).lower())

    def test_settlement_warns_about_paper_still_held(self):
        check = self._check(sale_contract_id=self.contract.id)
        self.contract._apply_settlement(self._amendment('early_settlement'))
        messages = self.contract.message_ids.mapped('body')
        self.assertTrue(any('post-dated cheque' in (m or '') for m in messages))
        self.assertTrue(any(check.check_number in (m or '') for m in messages))

    def test_the_cancellation_preview_lists_the_paper(self):
        self._check(sale_contract_id=self.contract.id)
        preview = self.contract._cancellation_preview()
        self.assertIn('check_exposure', preview)

    def test_cancellation_is_blocked_while_paper_is_held(self):
        """M15 — future cheques on hand must be returned or cancelled first."""
        self._check(sale_contract_id=self.contract.id)
        with self.assertRaises(UserError) as err:
            self.contract._apply_cancellation(self._amendment('cancellation'))
        message = str(err.exception)
        self.assertIn('physically held', message)
        self.assertIn('Return to Customer', message)

    def test_cancellation_is_blocked_by_a_cheque_at_the_bank(self):
        check = self._check(sale_contract_id=self.contract.id,
                            journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        with self.assertRaises(UserError) as err:
            self.contract._apply_cancellation(self._amendment('cancellation'))
        self.assertIn('at the bank', str(err.exception))

    def test_cancellation_proceeds_once_the_paper_is_resolved(self):
        check = self._check(sale_contract_id=self.contract.id)
        check.action_return_to_customer()
        self.contract._apply_cancellation(self._amendment('cancellation'))
        self.assertEqual(self.contract.state, 'cancelled')

    def test_a_cleared_cheque_does_not_block_cancellation(self):
        """It is historical payment. The money arrived."""
        check = self._check(sale_contract_id=self.contract.id,
                            journal=self.journal_direct)
        self._deposit(check, journal=self.journal_direct)
        check._sync_clearance_from_accounting()
        self.contract._apply_cancellation(self._amendment('cancellation'))
        self.assertEqual(self.contract.state, 'cancelled')
        self.assertEqual(check.state, 'cleared')


@tagged('post_install', '-at_install', 'atmta_checks')
class TestUnitSwapAndBuyerTransfer(ChecksCommon):

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()

    def _amendment(self, kind, **kwargs):
        vals = {'contract_id': self.contract.id, 'amendment_type': kind,
                'reason': 'Test'}
        vals.update(kwargs)
        return self.env['realestate.sale.contract.amendment'].create(vals)

    def test_a_swap_is_blocked_by_a_presented_cheque(self):
        target = self.units[1]
        self._release(target)
        check = self._check(sale_contract_id=self.contract.id,
                            journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        amendment = self._amendment('unit_swap', new_property_id=target.id)
        with self.assertRaises(UserError) as err:
            self.contract._apply_unit_swap(amendment)
        self.assertIn('unit swap', str(err.exception).lower())

    def test_a_cleared_cheque_stays_with_its_original_obligation(self):
        """M15 — do not remap history from the old property to the new one."""
        old_property = self.contract.property_id
        check = self._check(sale_contract_id=self.contract.id,
                            journal=self.journal_direct)
        self._deposit(check, journal=self.journal_direct)
        check._sync_clearance_from_accounting()
        self.assertEqual(check.property_id, old_property)

        target = self.units[1]
        self._release(target)
        self.contract._apply_unit_swap(
            self._amendment('unit_swap', new_property_id=target.id))

        check.invalidate_recordset()
        self.assertEqual(check.state, 'cleared')
        self.assertEqual(check.property_id, old_property,
                         'a cleared cheque was remapped to the new unit')

    def test_a_transfer_is_blocked_while_the_old_buyer_has_paper_at_the_bank(self):
        check = self._check(sale_contract_id=self.contract.id,
                            journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        amendment = self._amendment('buyer_change',
                                    new_partner_id=self.co_buyer.id)
        with self.assertRaises(UserError) as err:
            self.contract._apply_buyer_change(amendment)
        message = str(err.exception)
        self.assertIn('drawn by', message)
        self.assertIn(self.buyer.name, message)

    def test_a_transfer_never_rewrites_a_drawer(self):
        """M15 — historical instrument ownership is a fact."""
        old_buyer = self.contract.partner_id
        check = self._check(sale_contract_id=self.contract.id,
                            journal=self.journal_direct)
        self._deposit(check, journal=self.journal_direct)
        check._sync_clearance_from_accounting()
        held = self._check(sale_contract_id=self.contract.id)

        self.contract._apply_buyer_change(
            self._amendment('buyer_change', new_partner_id=self.co_buyer.id))

        check.invalidate_recordset()
        held.invalidate_recordset()
        self.assertEqual(self.contract.partner_id, self.co_buyer)
        self.assertEqual(check.partner_id, old_buyer,
                         'the drawer on a cleared cheque was rewritten')
        self.assertEqual(held.partner_id, old_buyer,
                         'the drawer on an uncashed cheque was rewritten')

    def test_a_transfer_tells_treasury_what_to_collect(self):
        held = self._check(sale_contract_id=self.contract.id)
        self.contract._apply_buyer_change(
            self._amendment('buyer_change', new_partner_id=self.co_buyer.id))
        messages = self.contract.message_ids.mapped('body')
        self.assertTrue(any('deliberately NOT been rewritten' in (m or '')
                            for m in messages))
        self.assertTrue(any(held.check_number in (m or '') for m in messages))
