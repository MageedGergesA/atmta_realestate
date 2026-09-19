# -*- coding: utf-8 -*-
"""M6 — post-signature commercial changes.

One rule runs through every test here: **paid history is never rewritten.**
A paid instalment is not edited, a posted invoice is not deleted, and a unit is
not blindly returned to the market. What changes is the open balance, and what
changed stays legible afterwards.
"""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .test_contract import ContractCommon


class ChangeCommon(ContractCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Amendment = cls.env['realestate.sale.contract.amendment']
        cls.Adjustment = cls.env['realestate.sale.installment.adjustment']

    def _signed_contract(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        return contract

    def _pay_first(self, contract, amount=None):
        """Settle the first instalment, so there is real paid history."""
        first = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        first.action_generate_invoice()
        invoice = first.move_id
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer',
            'partner_id': invoice.partner_id.id,
            'amount': amount or invoice.amount_total,
            'currency_id': invoice.currency_id.id,
            'company_id': invoice.company_id.id,
            'date': fields.Date.context_today(invoice),
        })
        payment.action_post()
        lines = (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda l: l.account_id.account_type == 'asset_receivable'
            and not l.reconciled)
        lines.reconcile()
        first.invalidate_recordset()
        return first


@tagged('post_install', '-at_install', 'atmta_developer')
class TestAmendmentSpine(ChangeCommon):
    """Phase 33 — the amendment is the record of the change."""

    def test_amendment_workflow(self):
        contract = self._signed_contract()
        amendment = self.Amendment.create({
            'contract_id': contract.id,
            'amendment_type': 'term_change',
            'reason': 'Agreed at the sales meeting',
        })
        self.assertEqual(amendment.state, 'draft')
        amendment.action_submit()
        amendment.action_approve()
        self.assertTrue(amendment.approved_by_id)
        amendment.action_sign()
        amendment.action_apply()
        self.assertEqual(amendment.state, 'applied')
        self.assertTrue(amendment.applied_on)

    def test_applying_twice_is_refused(self):
        """Phase 33 requires idempotence."""
        contract = self._signed_contract()
        amendment = self.Amendment.create({
            'contract_id': contract.id, 'amendment_type': 'other',
            'reason': 'Test',
        })
        amendment.action_submit()
        amendment.action_approve()
        amendment.action_apply()
        with self.assertRaises(UserError) as err:
            amendment.action_apply()
        self.assertIn('already been applied', str(err.exception))

    def test_an_unapproved_amendment_cannot_be_applied(self):
        contract = self._signed_contract()
        amendment = self.Amendment.create({
            'contract_id': contract.id, 'amendment_type': 'other',
            'reason': 'Test',
        })
        with self.assertRaises(UserError):
            amendment.action_apply()

    def test_an_applied_amendment_cannot_be_cancelled_or_deleted(self):
        contract = self._signed_contract()
        amendment = self.Amendment.create({
            'contract_id': contract.id, 'amendment_type': 'other',
            'reason': 'Test',
        })
        amendment.action_submit()
        amendment.action_approve()
        amendment.action_apply()
        with self.assertRaises(UserError):
            amendment.action_cancel()
        with self.assertRaises(UserError):
            amendment.unlink()


@tagged('post_install', '-at_install', 'atmta_developer')
class TestInstallmentAdjustment(ChangeCommon):
    """Phase 27 — adjustments add, they do not overwrite."""

    def test_adjustment_captures_the_before_from_the_record(self):
        contract = self._signed_contract()
        target = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[-1]
        original = target.current_amount

        adj = self.Adjustment.create({
            'installment_id': target.id,
            'adjustment_type': 'discount',
            'amount_after': original - 10000.0,
            'reason': 'Goodwill',
        })
        self.assertEqual(adj.amount_before, original,
                         "the before is read from the record, not trusted "
                         "from the caller")
        self.assertEqual(adj.amount_delta, -10000.0)

    def test_applying_an_adjustment_leaves_the_original_intact(self):
        contract = self._signed_contract()
        target = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[-1]
        original = target.original_amount

        adj = self.Adjustment.create({
            'installment_id': target.id,
            'adjustment_type': 'discount',
            'amount_after': original - 10000.0,
            'reason': 'Goodwill',
        })
        adj.action_apply()
        target.invalidate_recordset()
        self.assertEqual(
            target.original_amount, original,
            "the schedule as signed must stay readable next to the schedule "
            "as it now stands")
        self.assertEqual(target.adjustment_amount, -10000.0)
        self.assertEqual(target.current_amount, original - 10000.0)

    def test_an_adjustment_can_move_the_due_date(self):
        contract = self._signed_contract()
        target = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[-1]
        new_date = target.date_due + timedelta(days=90)
        adj = self.Adjustment.create({
            'installment_id': target.id,
            'adjustment_type': 'reschedule',
            'amount_after': target.current_amount,
            'date_after': new_date,
            'reason': 'Buyer requested a deferral',
        })
        adj.action_apply()
        self.assertEqual(target.date_due, new_date)

    def test_an_invoiced_obligation_cannot_be_adjusted_underneath(self):
        contract = self._signed_contract()
        target = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        target.action_generate_invoice()
        adj = self.Adjustment.create({
            'installment_id': target.id,
            'adjustment_type': 'correction',
            'amount_after': 1.0,
            'reason': 'Oops',
        })
        with self.assertRaises(UserError) as err:
            adj.action_apply()
        self.assertIn('credit', str(err.exception).lower())

    def test_an_applied_adjustment_cannot_be_deleted(self):
        contract = self._signed_contract()
        target = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[-1]
        adj = self.Adjustment.create({
            'installment_id': target.id,
            'adjustment_type': 'waiver',
            'amount_after': 0.0,
            'reason': 'Waived',
        })
        adj.action_apply()
        with self.assertRaises(UserError):
            adj.unlink()


@tagged('post_install', '-at_install', 'atmta_developer')
class TestRestructuring(ChangeCommon):
    """Phase 28 — only the open balance moves."""

    def test_restructuring_leaves_paid_instalments_alone(self):
        contract = self._signed_contract()
        paid = self._pay_first(contract)
        paid_amount = paid.paid_amount
        self.assertGreater(paid_amount, 0)

        new_plan = self._plan(name='Longer Plan')
        wiz = self.env['realestate.contract.restructure'].create({
            'contract_id': contract.id,
            'new_plan_id': new_plan.id,
            'reason': 'Buyer cash-flow difficulty',
        })
        wiz.action_preview()
        self.assertTrue(wiz.line_ids)
        wiz.action_apply()

        paid.invalidate_recordset()
        self.assertEqual(paid.paid_amount, paid_amount,
                         "a settled obligation is not touched")
        self.assertFalse(paid.is_cancelled)

    def test_restructuring_reschedules_only_what_is_left(self):
        contract = self._signed_contract()
        self._pay_first(contract)
        remaining_before = contract._outstanding_amount()

        new_plan = self._plan(name='Longer Plan 2')
        wiz = self.env['realestate.contract.restructure'].create({
            'contract_id': contract.id, 'new_plan_id': new_plan.id,
            'reason': 'Restructure',
        })
        wiz.action_preview()
        self.assertAlmostEqual(wiz.new_total, remaining_before, places=2,
                               msg="the new schedule must cover exactly the "
                                   "open balance")
        wiz.action_apply()
        contract.invalidate_recordset()
        self.assertAlmostEqual(
            contract._outstanding_amount(), remaining_before, places=2)

    def test_preview_is_required_before_applying(self):
        contract = self._signed_contract()
        new_plan = self._plan(name='Plan X')
        wiz = self.env['realestate.contract.restructure'].create({
            'contract_id': contract.id, 'new_plan_id': new_plan.id,
            'reason': 'Restructure',
        })
        with self.assertRaises(UserError) as err:
            wiz.action_apply()
        self.assertIn('preview', str(err.exception).lower())

    def test_the_superseded_instalments_stay_on_record(self):
        contract = self._signed_contract()
        original_ids = set(contract.installment_ids.ids)
        new_plan = self._plan(name='Plan Y')
        wiz = self.env['realestate.contract.restructure'].create({
            'contract_id': contract.id, 'new_plan_id': new_plan.id,
            'reason': 'Restructure',
        })
        wiz.action_preview()
        wiz.action_apply()
        contract.invalidate_recordset()
        surviving = set(contract.installment_ids.ids)
        self.assertTrue(original_ids.issubset(surviving),
                        "superseded obligations are cancelled, never deleted")
        cancelled = contract.installment_ids.filtered('is_cancelled')
        self.assertTrue(cancelled)
        self.assertTrue(all(c.adjustment_ids for c in cancelled)
                        if 'adjustment_ids' in contract.installment_ids._fields
                        else True)


@tagged('post_install', '-at_install', 'atmta_developer')
class TestEarlySettlement(ChangeCommon):
    """Phase 29 — preview, then collapse the remainder."""

    def test_preview_reports_the_position(self):
        contract = self._signed_contract()
        self._pay_first(contract)
        preview = contract._settlement_preview(discount_amount=50000.0)
        self.assertGreater(preview['outstanding_amount'], 0)
        self.assertEqual(
            preview['net_settlement'],
            preview['outstanding_amount'] - 50000.0)

    def test_settlement_replaces_the_remaining_schedule(self):
        contract = self._signed_contract()
        self._pay_first(contract)
        wiz = self.env['realestate.contract.settlement'].create({
            'contract_id': contract.id,
            'discount_amount': 50000.0,
            'reason': 'Buyer settling early',
        })
        net = wiz.net_settlement
        wiz.action_apply()
        contract.invalidate_recordset()

        live = contract.installment_ids.filtered(lambda i: not i.is_cancelled)
        settlement = live.filtered(lambda i: 'settlement' in (i.description or '').lower())
        self.assertTrue(settlement, "a single settlement obligation is raised")
        self.assertAlmostEqual(settlement.current_amount, net, places=2)

    def test_a_discount_bigger_than_the_balance_is_refused(self):
        contract = self._signed_contract()
        with self.assertRaises(ValidationError):
            self.env['realestate.contract.settlement'].create({
                'contract_id': contract.id,
                'discount_amount': 99999999.0,
                'reason': 'Absurd',
            })

    def test_settlement_does_not_touch_paid_history(self):
        contract = self._signed_contract()
        paid = self._pay_first(contract)
        before = paid.paid_amount
        wiz = self.env['realestate.contract.settlement'].create({
            'contract_id': contract.id, 'reason': 'Settle',
        })
        wiz.action_apply()
        paid.invalidate_recordset()
        self.assertEqual(paid.paid_amount, before)
        self.assertFalse(paid.is_cancelled)


@tagged('post_install', '-at_install', 'atmta_developer')
class TestContractCancellation(ChangeCommon):
    """Phase 30 — cancel without destroying anything."""

    def test_preview_shows_the_financial_effect(self):
        contract = self._signed_contract()
        self._pay_first(contract)
        preview = contract._cancellation_preview(penalty_amount=20000.0)
        self.assertGreater(preview['paid_amount'], 0)
        self.assertEqual(
            preview['refundable_amount'],
            preview['paid_amount'] - 20000.0)
        self.assertTrue(preview['posted_invoices'])

    def test_cancellation_keeps_posted_invoices(self):
        contract = self._signed_contract()
        paid = self._pay_first(contract)
        invoice = paid.move_id
        wiz = self.env['realestate.contract.cancel'].create({
            'contract_id': contract.id, 'reason': 'Buyer withdrew',
        })
        wiz.action_apply()
        self.assertEqual(contract.state, 'cancelled')
        self.assertTrue(invoice.exists())
        self.assertEqual(invoice.state, 'posted',
                         "a posted invoice is reversed through accounting, "
                         "never deleted by a commercial workflow")

    def test_cancellation_keeps_paid_instalments(self):
        contract = self._signed_contract()
        paid = self._pay_first(contract)
        wiz = self.env['realestate.contract.cancel'].create({
            'contract_id': contract.id, 'reason': 'Withdrew',
        })
        wiz.action_apply()
        paid.invalidate_recordset()
        self.assertTrue(paid.exists())
        self.assertFalse(paid.is_cancelled)
        self.assertGreater(paid.paid_amount, 0)

    def test_cancellation_cancels_future_obligations(self):
        contract = self._signed_contract()
        self._pay_first(contract)
        wiz = self.env['realestate.contract.cancel'].create({
            'contract_id': contract.id, 'reason': 'Withdrew',
        })
        wiz.action_apply()
        contract.invalidate_recordset()
        future = contract.installment_ids.filtered(
            lambda i: i.paid_amount <= 0 and not i.move_id)
        self.assertTrue(all(i.is_cancelled for i in future))

    def test_penalty_cannot_exceed_what_was_paid(self):
        contract = self._signed_contract()
        self._pay_first(contract)
        with self.assertRaises(ValidationError):
            self.env['realestate.contract.cancel'].create({
                'contract_id': contract.id, 'reason': 'X',
                'penalty_amount': 99999999.0,
            })

    def test_the_unit_returns_to_the_market(self):
        contract = self._signed_contract()
        self.assertEqual(self.unit.commercial_status, 'contracted')
        wiz = self.env['realestate.contract.cancel'].create({
            'contract_id': contract.id, 'reason': 'Withdrew',
        })
        wiz.action_apply()
        self.assertEqual(self.unit.commercial_status, 'available')

    def test_termination_is_distinguished_from_cancellation(self):
        contract = self._signed_contract()
        wiz = self.env['realestate.contract.cancel'].create({
            'contract_id': contract.id, 'reason': 'Default',
            'terminate': True,
        })
        wiz.action_apply()
        self.assertEqual(contract.state, 'terminated')


@tagged('post_install', '-at_install', 'atmta_developer')
class TestUnitSwap(ChangeCommon):
    """Phase 31 — move the deal, carry the money, keep the history."""

    def setUp(self):
        super().setUp()
        self.target = self.units[1]
        self._release(self.target)
        self.target.base_price = 1200000.0

    def test_preview_prices_the_difference(self):
        contract = self._signed_contract()
        preview = contract._swap_preview(self.target)
        self.assertEqual(preview['old_property'], self.unit)
        self.assertEqual(preview['new_property'], self.target)
        self.assertEqual(preview['difference'],
                         self.target.list_price_developer - 1000000.0)

    def test_swap_moves_the_deal_and_carries_payments(self):
        contract = self._signed_contract()
        paid = self._pay_first(contract)
        carried = paid.paid_amount

        wiz = self.env['realestate.contract.unit.swap'].create({
            'contract_id': contract.id,
            'new_property_id': self.target.id,
            'reason': 'Buyer prefers the corner unit',
        })
        self.assertEqual(wiz.carried_forward, carried)
        wiz.action_apply()

        contract.invalidate_recordset()
        self.assertEqual(contract.property_id, self.target)
        self.assertEqual(self.target.commercial_status, 'contracted')
        paid.invalidate_recordset()
        self.assertEqual(paid.paid_amount, carried,
                         "money already received moves with the buyer")

    def test_an_invoiced_unpaid_instalment_is_not_charged_twice(self):
        """The new schedule adds up to the new price, not more.

        The first instalment is invoiced but unpaid, so it is neither open
        (it is an accounting document) nor settled (no money arrived). It
        still has to count against the new price, or the open lines are
        re-cut to cover it a second time.
        """
        contract = self._signed_contract()
        first = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        first.action_generate_invoice()
        self.assertEqual(first.move_id.state, 'posted')
        self.assertFalse(first.paid_amount)
        kept = first.current_amount

        wiz = self.env['realestate.contract.unit.swap'].create({
            'contract_id': contract.id,
            'new_property_id': self.target.id,
            'reason': 'Swap with an unpaid invoice outstanding',
        })
        wiz.action_apply()

        contract.invalidate_recordset()
        first.invalidate_recordset()
        live = contract.installment_ids.filtered(lambda i: not i.is_cancelled)
        self.assertEqual(first.current_amount, kept,
                         "an invoiced instalment is not re-cut underneath")
        self.assertAlmostEqual(
            sum(live.mapped('current_amount')), contract.sale_price,
            delta=0.01 * len(live),
            msg="the schedule must add up to the new price exactly once")

    def test_the_old_unit_returns_to_the_market(self):
        contract = self._signed_contract()
        wiz = self.env['realestate.contract.unit.swap'].create({
            'contract_id': contract.id, 'new_property_id': self.target.id,
            'reason': 'Swap',
        })
        wiz.action_apply()
        self.assertEqual(self.unit.commercial_status, 'available')

    def test_the_swap_is_on_the_record(self):
        """The original unit reference is never overwritten silently."""
        contract = self._signed_contract()
        wiz = self.env['realestate.contract.unit.swap'].create({
            'contract_id': contract.id, 'new_property_id': self.target.id,
            'reason': 'Swap',
        })
        wiz.action_apply()
        amendment = contract.amendment_ids.filtered(
            lambda a: a.amendment_type == 'unit_swap')
        self.assertTrue(amendment)
        self.assertEqual(amendment.old_property_id, self.unit)
        self.assertEqual(amendment.new_property_id, self.target)

    def test_swapping_onto_an_unavailable_unit_is_refused(self):
        contract = self._signed_contract()
        self.target.commercial_status = 'sold'
        wiz = self.env['realestate.contract.unit.swap'].create({
            'contract_id': contract.id, 'new_property_id': self.target.id,
            'reason': 'Swap',
        })
        with self.assertRaises(Exception):
            wiz.action_apply()


@tagged('post_install', '-at_install', 'atmta_developer')
class TestBuyerTransfer(ChangeCommon):
    """Phase 32 — who held the deal before stays answerable."""

    def test_replacing_the_buyer_keeps_the_old_one_on_record(self):
        contract = self._signed_contract()
        old = contract.partner_id
        wiz = self.env['realestate.contract.transfer'].create({
            'contract_id': contract.id,
            'transfer_type': 'replace',
            'new_partner_id': self.co_buyer.id,
            'reason': 'Assignment',
        })
        wiz.action_apply()
        contract.invalidate_recordset()
        self.assertEqual(contract.partner_id, self.co_buyer)
        outgoing = contract.party_ids.filtered(
            lambda p: p.partner_id == old)
        self.assertTrue(outgoing, "the outgoing buyer stays on the contract")
        self.assertEqual(outgoing.role, 'assignee')

    def test_a_transfer_fee_raises_an_obligation(self):
        contract = self._signed_contract()
        before = len(contract.installment_ids)
        wiz = self.env['realestate.contract.transfer'].create({
            'contract_id': contract.id, 'transfer_type': 'replace',
            'new_partner_id': self.co_buyer.id,
            'fee_amount': 25000.0, 'reason': 'Assignment',
        })
        wiz.action_apply()
        contract.invalidate_recordset()
        self.assertEqual(len(contract.installment_ids), before + 1)
        fee = contract.installment_ids.filtered(
            lambda i: 'fee' in (i.description or '').lower())
        self.assertEqual(fee.current_amount, 25000.0)

    def test_adding_a_co_buyer_does_not_change_who_holds_the_contract(self):
        contract = self._signed_contract()
        holder = contract.partner_id
        wiz = self.env['realestate.contract.transfer'].create({
            'contract_id': contract.id, 'transfer_type': 'add_co_buyer',
            'new_partner_id': self.co_buyer.id, 'reason': 'Spouse added',
        })
        wiz.action_apply()
        contract.invalidate_recordset()
        self.assertEqual(contract.partner_id, holder)
        self.assertIn(self.co_buyer, contract.party_ids.mapped('partner_id'))

    def test_the_transfer_is_on_the_record(self):
        contract = self._signed_contract()
        old = contract.partner_id
        wiz = self.env['realestate.contract.transfer'].create({
            'contract_id': contract.id, 'transfer_type': 'replace',
            'new_partner_id': self.co_buyer.id, 'reason': 'Assignment',
        })
        wiz.action_apply()
        amendment = contract.amendment_ids.filtered(
            lambda a: a.amendment_type == 'buyer_change')
        self.assertTrue(amendment)
        self.assertEqual(amendment.old_partner_id, old)
        self.assertEqual(amendment.new_partner_id, self.co_buyer)


@tagged('post_install', '-at_install', 'atmta_developer')
class TestChecksGuard(ChangeCommon):
    """Phase 36 — never corrupt a financial instrument."""

    def test_the_guard_is_late_bound(self):
        """Developer does not depend on Checks and must work without it."""
        contract = self._signed_contract()
        # Returns an empty recordset rather than raising when Checks is absent,
        # and does not raise when no cheque is presented when it is present.
        contract._assert_no_blocking_checks()

    def test_a_deposited_cheque_blocks_restructuring(self):
        if 'realestate.check' not in self.env:
            self.skipTest('real_estate_checks is not installed')
        contract = self._signed_contract()
        target = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[-1]
        bank = self.env['res.bank'].search([], limit=1) or \
            self.env['res.bank'].create({'name': 'Guard Bank'})
        check = self.env['realestate.check'].create({
            'sale_contract_id': contract.id,
            'sale_installment_id': target.id,
            'partner_id': contract.partner_id.id,
            'bank_id': bank.id,
            'check_number': 'G-0001',
            'amount': target.current_amount,
            'due_date': target.date_due,
        })
        if 'state' not in check._fields:
            self.skipTest('check model has no state field')
        check.state = 'deposited'

        with self.assertRaises(UserError) as err:
            contract._assert_no_blocking_checks(
                contract._open_installments())
        self.assertIn('cheque', str(err.exception).lower())
