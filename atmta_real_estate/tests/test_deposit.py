"""Security deposit lifecycle and accounting (Phase 14)."""

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestSecurityDeposit(LeaseCase):

    def setUp(self):
        super().setUp()
        self.lease = self.make_lease(rent=1000.0)
        self.activate(self.lease)
        self.deposit = self.env['realestate.contract.deposit'].create({
            'contract_id': self.lease.id,
            'partner_id': self.tenant.id,
            'requested_amount': 2000.0,
        })

    # ------------------------------------------------------------------
    # Receipt
    # ------------------------------------------------------------------
    def test_receipt_posts_an_inbound_payment(self):
        self.deposit.action_register_receipt()
        self.assertEqual(self.deposit.state, 'held')
        self.assertAlmostEqual(self.deposit.received_amount, 2000.0, places=2)
        self.assertAlmostEqual(self.deposit.held_amount, 2000.0, places=2)
        payment = self.deposit.payment_ids
        self.assertEqual(len(payment), 1)
        self.assertEqual(payment.payment_type, 'inbound')
        # Odoo 18 posts a payment as 'in_process' until it is matched on a
        # bank statement; either is a successfully posted receipt.
        self.assertIn(payment.state, ('in_process', 'paid'))

    def test_deposit_lands_on_a_liability_account_not_revenue(self):
        """The rule that matters: a deposit is money you owe back."""
        self.deposit.action_register_receipt()
        payment = self.deposit.payment_ids
        accounts = payment.move_id.line_ids.mapped('account_id')
        self.assertIn(self.deposit_account, accounts)
        self.assertEqual(self.deposit_account.account_type, 'liability_current')
        self.assertFalse(
            any(a.account_type.startswith('income') for a in accounts),
            "A deposit receipt must never touch a revenue account.")

    def test_partial_receipt(self):
        self.deposit.action_register_receipt(amount=1200.0)
        self.assertAlmostEqual(self.deposit.held_amount, 1200.0, places=2)

    def test_cannot_receive_twice(self):
        self.deposit.action_register_receipt()
        with self.assertRaises(UserError):
            self.deposit.action_register_receipt()

    # ------------------------------------------------------------------
    # Refund
    # ------------------------------------------------------------------
    def test_full_refund(self):
        self.deposit.action_register_receipt()
        self.deposit.action_refund()
        self.assertEqual(self.deposit.state, 'refunded')
        self.assertAlmostEqual(self.deposit.refunded_amount, 2000.0, places=2)
        self.assertAlmostEqual(self.deposit.held_amount, 0.0, places=2)
        outbound = self.deposit.payment_ids.filtered(
            lambda p: p.payment_type == 'outbound')
        self.assertEqual(len(outbound), 1)

    def test_partial_refund_requires_a_reason(self):
        self.deposit.action_register_receipt()
        with self.assertRaises(UserError):
            self.deposit.action_refund(amount=1500.0)

    def test_partial_refund_with_a_reason(self):
        self.deposit.action_register_receipt()
        self.deposit.action_refund(amount=1500.0, reason='Cleaning deducted.')
        self.assertEqual(self.deposit.state, 'partially_refunded')
        self.assertAlmostEqual(self.deposit.held_amount, 500.0, places=2)

    def test_cannot_refund_more_than_held(self):
        self.deposit.action_register_receipt()
        with self.assertRaises(UserError):
            self.deposit.action_refund(amount=5000.0, reason='Too much.')

    # ------------------------------------------------------------------
    # Forfeiture
    # ------------------------------------------------------------------
    def test_forfeiture_recognises_income(self):
        self.deposit.action_register_receipt()
        self.deposit.action_forfeit(reason='Unpaid final rent and damage.')
        self.assertEqual(self.deposit.state, 'forfeited')
        move = self.deposit.move_ids
        self.assertEqual(len(move), 1)
        self.assertEqual(move.state, 'posted')
        accounts = move.line_ids.mapped('account_id')
        self.assertIn(self.deposit_account, accounts)
        self.assertIn(self.forfeit_income_account, accounts)

    def test_forfeiture_requires_a_reason(self):
        self.deposit.action_register_receipt()
        with self.assertRaises(UserError):
            self.deposit.action_forfeit()

    def test_forfeiture_requires_rental_manager(self):
        self.deposit.action_register_receipt()
        agent = new_test_user(
            self.env, login='re_agent_deposit',
            groups='base.group_user,atmta_real_estate.group_rental_agent',
            company_id=self.company.id)
        agent.company_ids = [(4, self.company.id)]
        with self.assertRaises(AccessError):
            self.deposit.with_user(agent).action_forfeit(reason='Nope.')

    # ------------------------------------------------------------------
    # Configuration guards
    # ------------------------------------------------------------------
    def test_missing_deposit_account_fails_loudly(self):
        self.company.re_deposit_account_id = False
        deposit = self.env['realestate.contract.deposit'].create({
            'contract_id': self.lease.id,
            'partner_id': self.tenant.id,
            'requested_amount': 500.0,
            'deposit_account_id': False,
        })
        with self.assertRaises(UserError):
            deposit.action_register_receipt()

    def test_revenue_account_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.deposit.deposit_account_id = self.forfeit_income_account.id

    # ------------------------------------------------------------------
    # Legacy bridge
    # ------------------------------------------------------------------
    def test_legacy_deposit_state_is_kept_in_sync(self):
        self.deposit.action_register_receipt()
        self.assertEqual(self.lease.deposit_state, 'held')
        self.deposit.action_refund()
        self.assertEqual(self.lease.deposit_state, 'refunded')

    def test_lease_reports_the_held_total(self):
        self.deposit.action_register_receipt()
        self.lease.invalidate_recordset()
        self.assertAlmostEqual(self.lease.deposit_held_total, 2000.0, places=2)

    # ------------------------------------------------------------------
    # Settlement dialog (keep part, refund the rest)
    # ------------------------------------------------------------------
    def _settlement(self, **vals):
        action = self.deposit.action_open_settlement()
        Wizard = self.env[action['res_model']].with_context(action['context'])
        return Wizard.create(vals)

    def test_settlement_keeps_the_deductions_and_refunds_the_rest(self):
        self.deposit.action_register_receipt()
        wizard = self._settlement(deduction_amount=400.0, reason='Wall repaint')
        self.assertAlmostEqual(wizard.refund_amount, 1600.0, places=2)
        wizard.action_confirm()
        self.assertAlmostEqual(self.deposit.forfeited_amount, 400.0, places=2)
        self.assertAlmostEqual(self.deposit.refunded_amount, 1600.0, places=2)
        self.assertAlmostEqual(self.deposit.held_amount, 0.0, places=2)
        self.assertIn('Wall repaint', self.deposit.settlement_reason)

    def test_settlement_needs_a_reason_to_keep_anything(self):
        self.deposit.action_register_receipt()
        with self.assertRaises(UserError):
            self._settlement(deduction_amount=400.0).action_confirm()
        self.assertAlmostEqual(self.deposit.held_amount, 2000.0, places=2)

    def test_settlement_cannot_exceed_what_is_held(self):
        self.deposit.action_register_receipt()
        wizard = self._settlement(deduction_amount=400.0, reason='Repaint')
        wizard.refund_amount = 1700.0
        with self.assertRaises(UserError):
            wizard.action_confirm()
        self.assertAlmostEqual(self.deposit.held_amount, 2000.0, places=2)

    def test_move_out_settle_deposit_carries_the_deductions(self):
        """It opened the deposit form, and neither amount nor reason arrived."""
        self.deposit.action_register_receipt()
        move_out = self.env['realestate.move.out'].create({
            'contract_id': self.lease.id,
            'property_id': self.unit_a.id,
            'scheduled_date': self.today,
            'tenant_acknowledged': True,
        })
        self.env['realestate.move.out.deduction'].create({
            'move_out_id': move_out.id, 'name': 'Wall repaint', 'amount': 400.0})
        move_out.action_start_inspection()
        move_out.action_complete()
        action = move_out.action_settle_deposit()
        self.assertEqual(action['res_model'], 'realestate.deposit.settlement.wizard')
        wizard = self.env[action['res_model']].with_context(action['context']).create({})
        self.assertEqual(wizard.deposit_id, self.deposit)
        self.assertEqual(wizard.move_out_id, move_out)
        self.assertAlmostEqual(wizard.deduction_amount, 400.0, places=2)
        self.assertAlmostEqual(wizard.refund_amount, 1600.0, places=2)
        self.assertIn('Wall repaint', wizard.reason)
