# -*- coding: utf-8 -*-
"""Payout dry run — commission calculated end to end, no external money moved.

Eight representative shapes, run through the whole chain the brief specifies:

```
    TRANSACTION VALUE
      → GROSS BROKERAGE COMMISSION
        → COMMISSION SPLITS
          → INDIVIDUAL ENTITLEMENTS
            → APPROVAL
              → PAYABLE
                → ACCOUNTING
```

The chain stops at the vendor bill in every case. Odoo owns payment truth; this
suite asserts the *commercial entitlement* and the state of the payable, never
that money reached anybody's bank.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import BrokerageCommon


class PayoutDryRunCommon(BrokerageCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Runner = cls.env[
            'realestate.commission.share.migration.runner']

    _dry_agent_seq = 0

    def _payee(self, name=None, share=0.0):
        type(self)._dry_agent_seq += 1
        seq = type(self)._dry_agent_seq
        return self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': name or 'Payee %d' % seq,
                'login': 'payee.%d@test.example' % seq,
                'company_id': self.company.id,
                'company_ids': [(6, 0, [self.company.id])],
                'groups_id': [(6, 0, [
                    self.env.ref('base.group_user').id,
                    self.env.ref(
                        'real_estate_brokerage.group_realestate_sales_agent'
                    ).id,
                    self.env.ref('sales_team.group_sale_salesman').id,
                ])],
                'is_realestate_agent': True,
                'commission_share_default': share,
            })

    def _deal(self, basis='percentage', rate=2.5, fixed=0.0,
              price=1000000.0, close=True, agent=None):
        listing = self._mandated_listing(
            commission_basis=basis,
            commission_percentage=rate if basis == 'percentage' else 0.0,
            commission_fixed=fixed if basis == 'fixed' else 0.0)
        listing.action_activate()
        txn = self._transaction(
            listing, sale_price=price, transaction_type='in_house',
            selling_agent_id=(agent or self.agent).id)
        if close:
            txn.action_sign_contract()
            txn.action_close()
        return txn

    def _zero_gross_deal(self, close=True):
        """A pre-0.2 shape: external listing, no commission terms anywhere.

        Deliberately not a Developer unit — internal inventory cannot close
        without a Developer contract (M24), and that boundary is not what this
        case is about.
        """
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.seller.id)
        txn = self._transaction(listing, sale_price=1000000.0,
                                transaction_type='in_house')
        if close:
            txn.action_sign_contract()
            txn.action_close()
        return txn

    def _payable(self, line):
        """Approve and raise the payable. No payment is registered."""
        line.action_approve()
        line.action_create_vendor_bill()
        return line.bill_id


@tagged('post_install', '-at_install')
class TestCaseAFixedGross(PayoutDryRunCommon):
    """Case A — fixed gross commission, one agent split."""

    def test_a_fixed_fee_splits_correctly_through_to_a_payable(self):
        txn = self._deal(basis='fixed', fixed=40000.0)
        payee = self._payee('Case A Agent')

        line = self._commission(
            txn, calculation_method='share', share_percentage=25.0,
            partner_id=payee.partner_id.id)

        self.assertEqual(txn.commission_gross_amount, 40000.0)
        self.assertEqual(line.amount, 10000.0)          # 25% of 40,000

        bill = self._payable(line)

        self.assertEqual(line.state, 'billed')
        self.assertEqual(bill.amount_total, 10000.0)
        self.assertEqual(bill.partner_id, payee.partner_id)
        self.assertEqual(txn.commission_unallocated, 30000.0)

    def test_the_payable_is_a_vendor_bill_and_nothing_more(self):
        """The accounting boundary: no direct-payment shortcut exists."""
        txn = self._deal(basis='fixed', fixed=40000.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=25.0)

        bill = self._payable(line)

        self.assertEqual(bill.move_type, 'in_invoice')
        self.assertEqual(bill.state, 'posted')
        self.assertNotEqual(bill.payment_state, 'paid')
        self.assertFalse(line.paid)


@tagged('post_install', '-at_install')
class TestCaseBPercentageMultiParty(PayoutDryRunCommon):
    """Case B — percentage gross, several parties sharing it."""

    def test_three_parties_split_one_gross_without_exceeding_it(self):
        txn = self._deal(basis='percentage', rate=2.5)   # 25,000 on 1M
        lister = self._payee('Case B Lister')
        seller = self._payee('Case B Seller')
        referrer = self._payee('Case B Referrer')

        lines = [
            self._commission(txn, calculation_method='share',
                             share_percentage=40.0, role='lister',
                             partner_id=lister.partner_id.id),
            self._commission(txn, calculation_method='share',
                             share_percentage=30.0, role='selling',
                             partner_id=seller.partner_id.id),
            self._commission(txn, calculation_method='share',
                             share_percentage=10.0, role='referrer',
                             partner_id=referrer.partner_id.id),
        ]

        self.assertEqual(txn.commission_gross_amount, 25000.0)
        self.assertEqual([l.amount for l in lines],
                         [10000.0, 7500.0, 2500.0])
        self.assertEqual(txn.commission_allocated, 20000.0)
        self.assertEqual(txn.commission_unallocated, 5000.0)
        self.assertFalse(txn.commission_over_allocated)

    def test_every_party_gets_their_own_payable(self):
        txn = self._deal(basis='percentage', rate=2.5)
        first = self._commission(
            txn, calculation_method='share', share_percentage=40.0,
            partner_id=self._payee('B1').partner_id.id)
        second = self._commission(
            txn, calculation_method='share', share_percentage=30.0,
            partner_id=self._payee('B2').partner_id.id)

        bills = self._payable(first) | self._payable(second)

        self.assertEqual(len(bills), 2)
        self.assertEqual(sum(bills.mapped('amount_total')), 17500.0)

    def test_the_splits_cannot_be_pushed_past_the_gross(self):
        from odoo.exceptions import ValidationError
        txn = self._deal(basis='percentage', rate=2.5)
        self._commission(txn, calculation_method='share',
                         share_percentage=70.0,
                         partner_id=self._payee('B3').partner_id.id)

        with self.assertRaises(ValidationError):
            self._commission(txn, calculation_method='share',
                             share_percentage=40.0,
                             partner_id=self._payee('B4').partner_id.id)


@tagged('post_install', '-at_install')
class TestCaseCConvertedLegacyValue(PayoutDryRunCommon):
    """Case C — a deterministically converted legacy default.

    The point of the whole migration: the agent must end up with exactly the
    money the old configuration would have paid them.
    """

    def test_old_money_equals_new_money_through_the_full_chain(self):
        agent = self._payee('Case C Agent', share=1.0)   # 1% of the sale, 0.1
        self._deal(basis='percentage', rate=2.5, agent=agent)   # history
        old_money = 1000000.0 * 1.0 / 100.0              # what 0.1 would pay

        self.Runner.run(agent)
        self.assertEqual(agent.commission_share_default, 40.0)

        txn = self._deal(basis='percentage', rate=2.5, agent=agent)
        txn.action_add_default_commissions()
        line = txn.commission_ids.filtered(
            lambda c: c.partner_id == agent.partner_id)

        self.assertEqual(line.amount, old_money)

        bill = self._payable(line)
        self.assertEqual(bill.amount_total, old_money)

    def test_the_conversion_is_evidenced_on_the_paid_line(self):
        """A payout dispute is settled from the record, not from memory."""
        agent = self._payee('Case C Evidence', share=1.0)
        self._deal(basis='percentage', rate=2.5, agent=agent)
        self.Runner.run(agent)

        txn = self._deal(basis='percentage', rate=2.5, agent=agent)
        txn.action_add_default_commissions()
        line = txn.commission_ids.filtered(
            lambda c: c.partner_id == agent.partner_id)
        self._payable(line)

        self.assertEqual(line.share_source, 'user_default')
        self.assertEqual(line.snapshot_share_percentage, 40.0)
        self.assertEqual(line.snapshot_gross_amount, 25000.0)
        self.assertEqual(line.snapshot_amount, 10000.0)
        log = agent.commission_share_migration_id
        self.assertEqual(log.original_value, 1.0)
        self.assertEqual(log.gross_rate_used, 2.5)


@tagged('post_install', '-at_install')
class TestCaseDAmbiguousConfiguration(PayoutDryRunCommon):
    """Case D — an ambiguous legacy default must block, not guess."""

    def setUp(self):
        super().setUp()
        self.agent_d = self._payee('Case D Agent', share=1.0)
        self._deal(basis='percentage', rate=2.0, agent=self.agent_d)
        self._deal(basis='percentage', rate=2.5, agent=self.agent_d)
        self.Runner.run(self.agent_d)

    def test_the_system_refuses_to_allocate_automatically(self):
        txn = self._deal(basis='percentage', rate=2.5, agent=self.agent_d)

        with self.assertRaises(UserError) as caught:
            txn.action_add_default_commissions()

        self.assertIn(self.agent_d.name, str(caught.exception))
        self.assertFalse(txn.commission_ids)

    def test_a_line_that_leans_on_the_default_cannot_reach_a_payable(self):
        txn = self._deal(basis='percentage', rate=2.5, agent=self.agent_d)
        line = self._commission(
            txn, calculation_method='share', share_percentage=40.0,
            partner_id=self.agent_d.partner_id.id, share_source='user_default')

        with self.assertRaises(UserError):
            line.action_approve()
        with self.assertRaises(UserError):
            line.action_create_vendor_bill()
        self.assertEqual(line.state, 'draft')
        self.assertFalse(line.bill_id)

    def test_after_a_managers_review_the_payout_proceeds(self):
        self.agent_d.action_review_commission_share(
            40.0, 'Reviewed against the 2.5% book of business')
        txn = self._deal(basis='percentage', rate=2.5, agent=self.agent_d)

        txn.action_add_default_commissions()
        line = txn.commission_ids.filtered(
            lambda c: c.partner_id == self.agent_d.partner_id)
        bill = self._payable(line)

        self.assertEqual(line.amount, 10000.0)
        self.assertEqual(bill.amount_total, 10000.0)


@tagged('post_install', '-at_install')
class TestCaseECancelledBeforeEarned(PayoutDryRunCommon):
    """Case E — the deal dies before the commission is earned. Nothing pays."""

    def test_a_cancelled_deal_produces_no_payable(self):
        txn = self._deal(close=False)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=40.0)

        txn.action_cancel('buyer_withdrew')

        self.assertEqual(txn.state, 'cancelled')
        with self.assertRaises(UserError):
            line.action_create_vendor_bill()
        self.assertFalse(line.bill_id)
        self.assertFalse(line.paid)

    def test_the_commission_can_be_cancelled_outright(self):
        txn = self._deal(close=False)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=40.0)
        txn.action_cancel('buyer_withdrew')

        line.action_cancel()

        self.assertEqual(line.state, 'cancelled')
        self.assertEqual(txn.commission_allocated, 0.0)


@tagged('post_install', '-at_install')
class TestCaseFCancelledAfterEarnedUnpaid(PayoutDryRunCommon):
    """Case F — earned and billed, then the deal collapses."""

    def test_a_billed_commission_claws_back_rather_than_cancelling(self):
        txn = self._deal()
        line = self._commission(txn, calculation_method='share',
                                share_percentage=40.0)
        self._payable(line)

        with self.assertRaises(UserError):
            line.action_cancel()

        mirror = line.action_clawback('deal_collapsed')

        self.assertEqual(line.state, 'clawed_back')
        self.assertEqual(mirror.amount, -10000.0)
        self.assertEqual(line.reversal_move_id.move_type, 'in_refund')
        self.assertEqual(txn.commission_allocated, 0.0)

    def test_the_reversal_is_posted_and_not_reconciled(self):
        """The accounting boundary, stated as a test rather than a hope."""
        txn = self._deal()
        line = self._commission(txn, calculation_method='share',
                                share_percentage=40.0)
        self._payable(line)

        line.action_clawback('deal_collapsed')
        reversal = line.reversal_move_id

        self.assertEqual(reversal.state, 'posted')
        self.assertEqual(reversal.amount_total, 10000.0)
        self.assertFalse(line.paid)


@tagged('post_install', '-at_install')
class TestCaseGCancelledAfterPaid(PayoutDryRunCommon):
    """Case G — the money already went out. The exposure must be visible."""

    def test_a_paid_commission_claws_back_and_the_exposure_is_recorded(self):
        txn = self._deal()
        line = self._commission(txn, calculation_method='share',
                                share_percentage=40.0)
        line.action_approve()
        line.action_mark_paid()
        self.assertEqual(line.state, 'paid')
        self.assertTrue(line.paid)

        mirror = line.action_clawback('rescinded')

        self.assertEqual(line.state, 'clawed_back')
        self.assertEqual(mirror.amount, -10000.0)
        self.assertTrue(line.reversal_move_id)

    def test_the_recovery_is_left_to_accounting_and_said_so(self):
        """`paid` stops reading true, and nothing pretends the money is back."""
        txn = self._deal()
        line = self._commission(txn, calculation_method='share',
                                share_percentage=40.0)
        line.action_approve()
        line.action_mark_paid()

        line.action_clawback('rescinded')

        # The reversal exists and is posted; it is deliberately not reconciled
        # against the original payment. That is bank work.
        self.assertEqual(line.reversal_move_id.state, 'posted')
        self.assertNotEqual(line.reversal_move_id.payment_state, 'paid')
        self.assertTrue(any('clawed back' in (m.body or '').lower()
                            for m in txn.message_ids))


@tagged('post_install', '-at_install')
class TestCaseHZeroGrossLegacyRow(PayoutDryRunCommon):
    """Case H — a pre-0.2 row with no gross. It may exist; it may not pay."""

    def test_the_row_survives_the_upgrade(self):
        txn = self._zero_gross_deal(close=False)

        line = self._commission(txn, calculation_method='percentage',
                                percentage=2.0)

        self.assertEqual(txn.commission_gross_amount, 0.0)
        self.assertTrue(line.exists())

    def test_it_cannot_be_approved(self):
        txn = self._zero_gross_deal(close=False)
        line = self._commission(txn, calculation_method='percentage',
                                percentage=2.0)

        with self.assertRaises(UserError):
            line.action_approve()

        self.assertEqual(line.state, 'draft')

    def test_it_cannot_be_billed_or_paid(self):
        txn = self._zero_gross_deal(close=False)
        line = self._commission(txn, calculation_method='percentage',
                                percentage=2.0)

        with self.assertRaises(UserError):
            line.action_create_vendor_bill()
        with self.assertRaises(UserError):
            line.action_mark_paid()

        self.assertFalse(line.bill_id)
        self.assertFalse(line.paid)

    def test_correcting_the_basis_unblocks_it(self):
        txn = self._zero_gross_deal(close=False)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=40.0)
        txn.action_sign_contract()
        txn.action_close()

        txn.action_set_gross_manually(25000.0, 'Fee agreed retrospectively')
        bill = self._payable(line)

        self.assertEqual(line.amount, 10000.0)
        self.assertEqual(bill.amount_total, 10000.0)

    def test_an_explicit_fixed_entitlement_is_payable_without_a_gross(self):
        """A fixed amount is a complete entitlement on its own."""
        txn = self._zero_gross_deal(close=False)
        line = self._commission(txn, calculation_method='fixed',
                                fixed_amount=5000.0)
        txn.action_sign_contract()
        txn.action_close()

        bill = self._payable(line)

        self.assertEqual(line.amount, 5000.0)
        self.assertEqual(bill.amount_total, 5000.0)

    def test_a_fixed_entitlement_of_nothing_is_not_an_entitlement(self):
        txn = self._zero_gross_deal()
        line = self._commission(txn, calculation_method='fixed',
                                fixed_amount=0.0)

        with self.assertRaises(UserError):
            line.action_approve()


@tagged('post_install', '-at_install')
class TestOwnerMandateAuthority(PayoutDryRunCommon):
    """Retained by decision: the owner's reservation is not overridable.

    Documented as intentional business behaviour. A Brokerage Manager's
    approval is an internal control; a mandate reserving acceptance to the
    owner is a contractual limit on the agency's authority, and one cannot
    grant the other.
    """

    def test_a_manager_cannot_accept_where_the_owner_reserved_the_right(self):
        listing = self._mandated_listing(negotiation_authority='none')
        listing.action_activate()
        offer = self._offer(listing=listing, amount=1200000.0)

        with self.assertRaises(UserError) as caught:
            offer.action_accept()

        self.assertIn('owner', str(caught.exception).lower())
        self.assertNotEqual(offer.state, 'accepted')

    def test_nor_by_approving_it_first(self):
        listing = self._mandated_listing(negotiation_authority='none',
                                         minimum_price=950000.0)
        listing.action_activate()
        offer = self._offer(listing=listing, amount=900000.0)

        with self.assertRaises(UserError):
            offer.action_approve()
        with self.assertRaises(UserError):
            offer.action_accept()
