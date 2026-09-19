# -*- coding: utf-8 -*-
"""Regressions for the second Codex review (15 Sep 2026).

* P1: upgrading a 0.4 database would have zeroed every instalment. The new
  ``original_amount`` and ``adjustment_amount`` columns start empty, and the
  ``current_amount`` compute also writes ``amount``.
* P1: a price-change amendment wrote ``sale_price`` only, so the schedule kept
  billing the old price.
* P2: cancellation, schedule-change and early-settlement amendments (and
  amendments missing their payload) could be applied while changing nothing.
* P1: a swap onto a unit cheaper than what was already invoiced clamped the
  open balance to zero, leaving the buyer owing more than the new price.
"""

import importlib.util
import os

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .test_contract import ContractCommon
from .test_contract_changes import ChangeCommon

MIGRATION = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         'migrations', '0.5', 'pre-migrate.py')


def _migration():
    spec = importlib.util.spec_from_file_location(
        'developer_pre_migrate_0_5', MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ordered(installments):
    return installments.sorted(lambda i: (i.date_due, i.sequence, i.id))


@tagged('post_install', '-at_install', 'atmta_developer')
class TestInstallmentAmountsSurviveTheUpgrade(ContractCommon):

    def _rows(self, ids):
        self.env.cr.execute("""
            SELECT id, amount, original_amount, adjustment_amount, current_amount
              FROM realestate_sale_installment
             WHERE id = ANY(%s)
        """, (list(ids),))
        # An empty monetary column reads as 0 through the ORM; compare it so.
        return {row[0]: tuple(float(v or 0) for v in row[1:])
                for row in self.env.cr.fetchall()}

    def _signed(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        return contract

    def test_a_0_4_table_keeps_every_amount(self):
        contract = self._signed()
        lines = contract.installment_ids
        expected = {line.id: line.amount for line in lines}
        self.assertEqual(sum(expected.values()), 1000000.0)
        self.env.flush_all()

        # The table as 0.4 left it: `amount` is the only amount column.
        self.env.cr.execute("""
            ALTER TABLE realestate_sale_installment
                DROP COLUMN original_amount,
                DROP COLUMN adjustment_amount,
                DROP COLUMN current_amount
        """)
        _migration().migrate(self.env.cr, '0.4')

        for line_id, (amount, original, adjustment, current) in self._rows(lines.ids).items():
            self.assertEqual((amount, original, adjustment, current),
                             (expected[line_id], expected[line_id], 0.0, expected[line_id]))

        self.env.invalidate_all()
        first = _ordered(lines)[0]
        self.assertEqual(first.current_amount, expected[first.id])
        first.action_generate_invoice()
        self.assertEqual(first.move_id.amount_untaxed, expected[first.id],
                         "an upgraded instalment is billed for its amount, not zero")

    def test_an_adjusted_database_is_left_as_it_is(self):
        contract = self._signed()
        last = _ordered(contract.installment_ids)[-1]
        self.env['realestate.sale.installment.adjustment'].create({
            'installment_id': last.id, 'amount_after': last.current_amount + 5000.0,
            'reason': 'Already adjusted before the upgrade',
        }).action_apply()
        self.env.flush_all()
        before = self._rows(contract.installment_ids.ids)

        _migration().migrate(self.env.cr, '0.4')

        self.assertEqual(self._rows(contract.installment_ids.ids), before)

    def test_a_fresh_install_is_not_touched(self):
        contract = self._signed()
        self.env.flush_all()
        before = self._rows(contract.installment_ids.ids)
        _migration().migrate(self.env.cr, None)
        self.assertEqual(self._rows(contract.installment_ids.ids), before)


class AmendmentCommon(ChangeCommon):

    def _approved(self, contract, kind, **vals):
        amendment = self.Amendment.create(dict(
            contract_id=contract.id, amendment_type=kind,
            reason='Agreed with the buyer', **vals))
        amendment.action_submit()
        amendment.action_approve()
        return amendment

    def _live(self, contract):
        contract.invalidate_recordset()
        return contract.installment_ids.filtered(lambda i: not i.is_cancelled)

    def _invoice_all(self, contract):
        for line in _ordered(contract.installment_ids):
            line.action_generate_invoice()
        self.assertFalse(contract._open_installments())


@tagged('post_install', '-at_install', 'atmta_developer')
class TestPriceChangeBillsTheNewPrice(AmendmentCommon):

    def test_an_increase_is_spread_over_the_open_instalments(self):
        contract = self._signed_contract()
        paid = self._pay_first(contract)
        amendment = self._approved(contract, 'price_change', new_price=1200000.0,
                                   financial_effect=200000.0)
        amendment.action_apply()

        live = self._live(contract)
        self.assertEqual(contract.sale_price, 1200000.0)
        self.assertEqual(amendment.old_price, 1000000.0)
        self.assertEqual(len(live), 6, "the schedule is re-cut, not replaced")
        self.assertEqual(paid.current_amount, 200000.0, "paid history is untouched")
        self.assertAlmostEqual(sum(live.mapped('current_amount')), 1200000.0, places=2)
        self.assertEqual(len(amendment.adjustment_ids), 5)
        self.assertTrue(all(amendment.adjustment_ids.mapped('is_applied')))

        # The open 800,000 now carries 1,000,000, so a 10% instalment bills 125,000.
        second = _ordered(live - paid)[0]
        self.assertAlmostEqual(second.current_amount, 125000.0, places=2)
        second.action_generate_invoice()
        self.assertAlmostEqual(second.move_id.amount_untaxed, 125000.0, places=2)

    def test_a_decrease_is_taken_off_the_open_instalments(self):
        contract = self._signed_contract()
        first = _ordered(contract.installment_ids)[0]
        first.action_generate_invoice()
        self._approved(contract, 'price_change', new_price=900000.0).action_apply()

        live = self._live(contract)
        self.assertEqual(first.current_amount, 200000.0,
                         "an invoiced instalment is not re-cut underneath")
        self.assertAlmostEqual(sum(live.mapped('current_amount')), 900000.0, places=2)
        self.assertAlmostEqual(contract.scheduled_amount, 900000.0, places=2)

    def test_an_increase_after_everything_is_invoiced_raises_one_balance(self):
        contract = self._signed_contract()
        self._invoice_all(contract)
        before = contract.installment_ids
        self._approved(contract, 'price_change', new_price=1100000.0).action_apply()

        live = self._live(contract)
        added = contract.installment_ids - before
        self.assertEqual(len(added), 1)
        self.assertAlmostEqual(added.current_amount, 100000.0, places=2)
        self.assertEqual(added.state, 'pending')
        self.assertAlmostEqual(sum(live.mapped('current_amount')), 1100000.0, places=2)

    def test_a_price_below_what_is_billed_is_refused(self):
        contract = self._signed_contract()
        self._invoice_all(contract)
        before = contract.installment_ids
        amendment = self._approved(contract, 'price_change', new_price=900000.0)
        with self.assertRaises(UserError) as err:
            amendment.action_apply()
        self.assertIn('Credit the excess', str(err.exception))
        self.env.invalidate_all()
        self.assertEqual(contract.sale_price, 1000000.0)
        self.assertEqual(amendment.state, 'approved')
        self.assertEqual(contract.installment_ids, before)

    def test_a_price_change_without_a_price_is_refused(self):
        contract = self._signed_contract()
        amendment = self._approved(contract, 'price_change')
        with self.assertRaises(UserError):
            amendment.action_apply()
        self.assertEqual(amendment.state, 'approved')


@tagged('post_install', '-at_install', 'atmta_developer')
class TestAmendmentsCannotBeAppliedWithoutEffect(AmendmentCommon):

    def test_wizard_only_types_are_refused(self):
        contract = self._signed_contract()
        amounts = contract.installment_ids.mapped('current_amount')
        for kind in ('early_settlement', 'cancellation'):
            amendment = self._approved(contract, kind)
            with self.assertRaises(UserError) as err:
                amendment.action_apply()
            self.assertIn('without changing the contract', str(err.exception))
            self.assertEqual(amendment.state, 'approved', kind)
        contract.invalidate_recordset()
        self.assertEqual(contract.state, 'signed')
        self.assertFalse(contract.installment_ids.filtered('is_cancelled'))
        self.assertEqual(contract.installment_ids.mapped('current_amount'), amounts)

    def test_the_form_does_not_offer_apply_for_wizard_only_types(self):
        arch = self.Amendment.get_view(
            self.env.ref('real_estate_developer.view_amendment_form').id, 'form')['arch']
        self.assertIn("amendment_type in ('early_settlement','cancellation')", arch)

    def test_a_schedule_change_restructures_under_the_new_plan(self):
        contract = self._signed_contract()
        paid = self._pay_first(contract)
        old_plan = contract.payment_plan_id
        superseded = contract._open_installments()
        new_plan = self._plan('Plan B')
        amendment = self._approved(contract, 'schedule_change',
                                   new_payment_plan_id=new_plan.id)
        amendment.action_apply()

        live = self._live(contract)
        self.assertEqual(amendment.state, 'applied')
        self.assertEqual(contract.payment_plan_id, new_plan)
        self.assertEqual(amendment.old_payment_plan_id, old_plan)
        self.assertTrue(all(superseded.mapped('is_cancelled')))
        self.assertIn(paid, live)
        self.assertFalse(live & superseded)
        self.assertAlmostEqual(sum(live.mapped('current_amount')), 1000000.0, places=2)

    def test_a_plan_change_without_a_plan_is_refused(self):
        contract = self._signed_contract()
        for kind in ('payment_plan_change', 'schedule_change'):
            amendment = self._approved(contract, kind)
            with self.assertRaises(UserError):
                amendment.action_apply()
            self.assertEqual(amendment.state, 'approved')

    def test_a_swap_or_transfer_without_its_target_is_refused(self):
        contract = self._signed_contract()
        for kind in ('unit_swap', 'buyer_change'):
            amendment = self._approved(contract, kind)
            with self.assertRaises(UserError):
                amendment.action_apply()
            self.assertEqual(amendment.state, 'approved', kind)
        self.assertEqual(contract.property_id, self.unit)
        self.assertEqual(contract.partner_id, self.buyer)

    def test_a_fee_change_needs_its_adjustments(self):
        contract = self._signed_contract()
        amendment = self._approved(contract, 'fee_change')
        with self.assertRaises(UserError):
            amendment.action_apply()

        last = _ordered(contract.installment_ids)[-1]
        self.env['realestate.sale.installment.adjustment'].create({
            'installment_id': last.id, 'amendment_id': amendment.id,
            'adjustment_type': 'penalty',
            'amount_after': last.current_amount + 5000.0,
            'reason': 'Administration fee',
        })
        amendment.action_apply()
        self.assertEqual(amendment.state, 'applied')
        self.assertEqual(last.current_amount, 405000.0)

    def test_documentary_types_still_apply(self):
        contract = self._signed_contract()
        for kind in ('term_change', 'other'):
            amendment = self._approved(contract, kind)
            amendment.action_apply()
            self.assertEqual(amendment.state, 'applied')


@tagged('post_install', '-at_install', 'atmta_developer')
class TestUnitSwapBelowWhatIsBilled(AmendmentCommon):

    def setUp(self):
        super().setUp()
        self.target = self.units[1]
        self._release(self.target)
        self.target.base_price = 700000.0

    def _swap(self, contract):
        return self.env['realestate.contract.unit.swap'].create({
            'contract_id': contract.id,
            'new_property_id': self.target.id,
            'new_price': 700000.0,
            'reason': 'Buyer moves to a smaller unit',
        })

    def test_a_swap_below_the_invoiced_amount_is_refused(self):
        contract = self._signed_contract()
        self._invoice_all(contract)
        wizard = self._swap(contract)
        with self.assertRaises(UserError) as err:
            wizard.action_apply()
        self.assertIn('Credit the excess', str(err.exception))
        self.env.invalidate_all()
        self.assertEqual(contract.property_id, self.unit)
        self.assertEqual(contract.sale_price, 1000000.0)
        self.assertNotEqual(self.target.commercial_status, 'contracted')

    def test_a_cheaper_swap_within_the_open_balance_still_applies(self):
        contract = self._signed_contract()
        first = _ordered(contract.installment_ids)[0]
        first.action_generate_invoice()
        self._swap(contract).action_apply()

        live = self._live(contract)
        self.assertEqual(contract.property_id, self.target)
        self.assertEqual(first.current_amount, 200000.0)
        self.assertAlmostEqual(sum(live.mapped('current_amount')), 700000.0, places=2)
