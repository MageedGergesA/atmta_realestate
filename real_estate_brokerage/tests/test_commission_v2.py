# -*- coding: utf-8 -*-
"""M13 / M14 — one gross, splits of it, approval, payout, clawback."""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import BrokerageCommon


@tagged('post_install', '-at_install')
class TestAuthoritativeGross(BrokerageCommon):
    """There is exactly one figure for what the company earned."""

    def test_the_gross_comes_from_the_mandate(self):
        listing = self._mandated_listing(commission_basis='percentage',
                                         commission_percentage=2.0)
        listing.action_activate()
        txn = self._transaction(listing, sale_price=1000000.0)

        self.assertEqual(txn.commission_gross_amount, 20000.0)
        self.assertEqual(txn.commission_gross_source, 'mandate')

    def test_a_fixed_mandate_fee_is_not_a_percentage(self):
        listing = self._mandated_listing(commission_basis='fixed',
                                         commission_fixed=35000.0)
        listing.action_activate()
        txn = self._transaction(listing, sale_price=1000000.0)

        self.assertEqual(txn.commission_gross_amount, 35000.0)

    def test_the_mandate_outranks_the_listings_own_note(self):
        """One is signed by the owner; the other is our own memo."""
        listing = self._mandated_listing(commission_basis='percentage',
                                         commission_percentage=2.0)
        listing.write({'commission_basis': 'percentage',
                       'commission_percentage': 5.0})
        listing.action_activate()
        txn = self._transaction(listing, sale_price=1000000.0)

        self.assertEqual(txn.commission_gross_amount, 20000.0)

    def test_the_listing_terms_apply_when_there_is_no_mandate(self):
        listing = self._listing(activate=True)
        listing.write({'commission_basis': 'percentage',
                       'commission_percentage': 3.0})
        txn = self._transaction(listing, sale_price=1000000.0)

        self.assertEqual(txn.commission_gross_amount, 30000.0)
        self.assertEqual(txn.commission_gross_source, 'listing')

    def test_a_manual_gross_needs_a_reason(self):
        txn = self._transaction(self._listing(activate=True))

        with self.assertRaises(UserError):
            txn.action_set_gross_manually(50000.0, '')

    def test_a_manual_gross_is_not_walked_over_by_a_recompute(self):
        listing = self._listing(activate=True)
        listing.write({'commission_basis': 'percentage',
                       'commission_percentage': 3.0})
        txn = self._transaction(listing, sale_price=1000000.0)

        txn.action_set_gross_manually(50000.0, 'Owner negotiated the fee down')
        txn.sale_price = 2000000.0

        self.assertEqual(txn.commission_gross_amount, 50000.0)


@tagged('post_install', '-at_install')
class TestSplitsOfTheGross(BrokerageCommon):
    """The defect: 0.1 measured every line against the sale price."""

    def test_a_share_is_a_share_of_the_gross(self):
        txn = self._closed_deal_setup(gross_percentage=2.0)  # 20,000 on 1M

        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)

        self.assertEqual(line.amount, 10000.0)

    def test_splits_cannot_exceed_what_was_earned(self):
        """Three agents at 2% each billed 6% of the sale in 0.1."""
        txn = self._closed_deal_setup(gross_percentage=2.0)
        self._commission(txn, calculation_method='share',
                         share_percentage=60.0)

        with self.assertRaises(ValidationError):
            self._commission(txn, calculation_method='share',
                             share_percentage=60.0,
                             partner_id=self.seller.id)

    def test_the_company_keeps_what_is_left(self):
        txn = self._closed_deal_setup(gross_percentage=2.0)

        self._commission(txn, calculation_method='share',
                         share_percentage=40.0)

        self.assertEqual(txn.commission_allocated, 8000.0)
        self.assertEqual(txn.commission_unallocated, 12000.0)
        self.assertFalse(txn.commission_over_allocated)

    def test_a_share_over_a_hundred_percent_is_refused(self):
        txn = self._closed_deal_setup(gross_percentage=2.0)

        with self.assertRaises(ValidationError):
            self._commission(txn, calculation_method='share',
                             share_percentage=120.0)

    def test_a_transaction_with_no_gross_is_not_constrained(self):
        """Pre-0.2 rows must survive the upgrade.

        Their listings carried no commission terms, so there is no gross to
        measure against. The enforcement moves to billing, where it matters.
        """
        txn = self._transaction(self._listing(activate=True))

        line = self._commission(txn, calculation_method='percentage',
                                percentage=2.0)

        self.assertEqual(txn.commission_gross_amount, 0.0)
        self.assertEqual(line.amount, 20000.0)

    def test_seeded_defaults_are_shares_not_slices_of_the_sale(self):
        txn = self._closed_deal_setup(gross_percentage=2.0)
        self.agent.commission_share_default = 50.0

        txn.action_add_default_commissions()

        self.assertTrue(txn.commission_ids)
        self.assertEqual(txn.commission_ids.mapped('calculation_method'),
                         ['share'] * len(txn.commission_ids))
        self.assertFalse(txn.commission_over_allocated)


@tagged('post_install', '-at_install')
class TestCommissionApproval(BrokerageCommon):
    """0.1 let any agent with write access bill themselves."""

    def test_a_draft_commission_cannot_be_billed(self):
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)

        with self.assertRaises(UserError):
            line.action_create_vendor_bill()

    def test_an_agent_cannot_approve(self):
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)

        with self.assertRaises(UserError):
            line.with_user(self.agent).action_approve()

    def test_nobody_approves_their_own(self):
        """The one control that stops payout being a single signature."""
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0,
                                partner_id=self.env.user.partner_id.id)

        with self.assertRaises(UserError):
            line.action_approve()

    def test_an_approved_commission_can_be_billed(self):
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)

        line.action_approve()
        line.action_create_vendor_bill()

        self.assertEqual(line.state, 'billed')
        self.assertTrue(line.bill_id)
        self.assertEqual(line.approved_by_id, self.env.user)

    def test_a_commission_is_not_payable_before_the_deal_closes(self):
        """Earned on completion, not on expectation."""
        txn = self._open_deal(gross_percentage=2.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)
        line.action_approve()

        with self.assertRaises(UserError):
            line.action_create_vendor_bill()

    def test_nothing_is_approvable_without_a_gross(self):
        """The block moved earlier: undefined entitlements never get approved.

        0.3.0 caught this at billing. An approved commission is a promise
        already made, so the gate belongs before the promise, not before the
        payment.
        """
        txn = self._closed_deal(gross_percentage=0.0)
        line = self._commission(txn, calculation_method='percentage',
                                percentage=2.0)

        with self.assertRaises(UserError) as caught:
            line.action_approve()

        self.assertIn('gross', str(caught.exception))


@tagged('post_install', '-at_install')
class TestClawback(BrokerageCommon):
    """M14 — a deal that collapses after the agent was paid."""

    def test_an_unbilled_commission_is_cancelled_not_clawed_back(self):
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)

        with self.assertRaises(UserError):
            line.action_clawback('deal_collapsed')

    def test_a_billed_commission_is_reversed(self):
        line = self._billed_commission()

        line.action_clawback('deal_collapsed')

        self.assertEqual(line.state, 'clawed_back')
        self.assertTrue(line.reversal_move_id)
        self.assertEqual(line.reversal_move_id.move_type, 'in_refund')

    def test_a_clawback_leaves_a_mirrored_negative_line(self):
        line = self._billed_commission()

        mirror = line.action_clawback('deal_collapsed')

        self.assertEqual(mirror.clawback_of_id, line)
        self.assertEqual(line.clawback_id, mirror)
        self.assertEqual(mirror.amount, -line.amount)

    def test_a_clawback_frees_the_allocation_back_up(self):
        line = self._billed_commission()
        txn = line.transaction_id
        allocated_before = txn.commission_allocated

        line.action_clawback('deal_collapsed')

        self.assertEqual(allocated_before, 10000.0)
        self.assertEqual(txn.commission_allocated, 0.0)

    def test_a_clawback_needs_a_reason(self):
        line = self._billed_commission()

        with self.assertRaises(UserError):
            line.action_clawback('')

    def test_a_clawback_is_a_managers_decision(self):
        line = self._billed_commission()

        with self.assertRaises(UserError):
            line.with_user(self.agent).action_clawback('deal_collapsed')

    def test_a_commission_is_only_clawed_back_once(self):
        line = self._billed_commission()
        line.action_clawback('deal_collapsed')

        with self.assertRaises(UserError):
            line.action_clawback('overpaid')

    def test_a_billed_commission_cannot_be_quietly_cancelled(self):
        line = self._billed_commission()

        with self.assertRaises(UserError):
            line.action_cancel()

    def test_a_reversed_bill_does_not_read_as_paid(self):
        """0.1 counted `reversed` as paid, which is exactly backwards."""
        line = self._billed_commission()

        line.action_clawback('deal_collapsed')

        self.assertFalse(line.paid)

    # ------------------------------------------------------------------
    def _billed_commission(self):
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)
        line.action_approve()
        line.action_create_vendor_bill()
        return line
