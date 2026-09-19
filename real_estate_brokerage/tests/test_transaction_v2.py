# -*- coding: utf-8 -*-
"""M12 / M24 — the transaction, and the Developer boundary it used to cross."""

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import BrokerageCommon


@tagged('post_install', '-at_install')
class TestDeveloperAuthority(BrokerageCommon):
    """0.1 marked Developer units sold behind the reservation engine's back."""

    def test_an_internal_transaction_cannot_close_without_a_contract(self):
        txn = self._transaction(self._listing(activate=True))
        txn.action_sign_contract()

        with self.assertRaises(UserError) as caught:
            txn.action_close()

        self.assertIn('Developer', str(caught.exception))

    def test_closing_internal_inventory_leaves_the_unit_to_developer(self):
        """The regression that matters.

        0.1 wrote `state = 'sold'` and reassigned `owner_id` straight onto the
        property. Developer would still have believed the unit was available
        and could have sold it a second time.
        """
        listing = self._listing(activate=True)
        unit = listing.property_id
        txn = self._transaction(listing)
        txn.developer_contract_id = self._developer_contract(unit)
        self._commission(txn)
        txn.action_sign_contract()
        original_owner = unit.owner_id

        txn.action_close()

        self.assertEqual(txn.state, 'closed')
        self.assertNotEqual(
            unit.state, 'sold',
            "Brokerage marked a Developer unit sold on its own authority.")
        self.assertEqual(unit.owner_id, original_owner,
                         "Brokerage reassigned Developer inventory.")

    def test_the_listing_still_stops_being_marketed(self):
        """What *is* Brokerage's record still gets written."""
        listing = self._listing(activate=True)
        txn = self._transaction(listing)
        txn.developer_contract_id = self._developer_contract(
            listing.property_id)
        self._commission(txn)
        txn.action_sign_contract()

        txn.action_close()

        self.assertEqual(listing.state, 'sold')
        self.assertEqual(listing.publication_state, 'unpublished')
        self.assertEqual(listing.final_sale_price, txn.sale_price)

    def test_a_contract_for_a_different_unit_is_refused(self):
        listing = self._listing(activate=True)
        other_unit = self._unit()
        txn = self._transaction(listing)
        txn.developer_contract_id = self._developer_contract(other_unit)
        txn.action_sign_contract()

        with self.assertRaises(UserError):
            txn.action_close()

    def test_an_external_deal_still_closes_the_old_way(self):
        """External inventory is Brokerage's from end to end — unchanged."""
        listing = self._mandated_listing()
        listing.action_activate()
        unit = listing.property_id
        txn = self._transaction(listing, transaction_type='in_house')
        txn.action_sign_contract()

        txn.action_close()

        self.assertEqual(txn.state, 'closed')
        self.assertEqual(unit.state, 'sold')
        self.assertEqual(unit.owner_id, self.buyer)


@tagged('post_install', '-at_install')
class TestCancellation(BrokerageCommon):

    def test_cancelling_needs_a_reason(self):
        txn = self._transaction(self._listing(activate=True))

        with self.assertRaises(UserError):
            txn.action_cancel()

    def test_a_reason_is_recorded(self):
        txn = self._transaction(self._listing(activate=True))

        txn.action_cancel('finance_failed')

        self.assertEqual(txn.state, 'cancelled')
        self.assertEqual(txn.cancellation_reason_code, 'finance_failed')
        self.assertTrue(txn.cancelled_on)

    def test_falling_through_after_signing_is_a_different_event(self):
        """One of these costs somebody money; the other does not."""
        early = self._transaction(self._listing(activate=True))
        late = self._transaction(self._listing(activate=True))
        late.action_sign_contract()

        early.action_cancel('duplicate')
        late.action_cancel('finance_failed')

        self.assertFalse(early.cancelled_after_contract)
        self.assertTrue(late.cancelled_after_contract)

    def test_a_closed_transaction_cannot_be_cancelled(self):
        listing = self._mandated_listing()
        listing.action_activate()
        txn = self._transaction(listing, transaction_type='in_house')
        txn.action_sign_contract()
        txn.action_close()

        with self.assertRaises(UserError):
            txn.action_cancel('other')


@tagged('post_install', '-at_install')
class TestTransactionAttribution(BrokerageCommon):
    """Source-to-revenue needs the opportunity to survive the whole chain."""

    def test_the_opportunity_travels_from_offer_to_transaction(self):
        lead = self._opportunity()
        listing = self._listing(activate=True)
        offer = self._offer(listing=listing, amount=1000000.0, lead=lead)
        offer.action_accept()

        offer.action_create_transaction()

        self.assertEqual(listing.transaction_id.crm_lead_id, lead)


@tagged('post_install', '-at_install')
class TestTransactionCompany(BrokerageCommon):

    def test_a_transaction_may_not_straddle_companies(self):
        other = self.env['res.company'].create({'name': 'Other Brokerage'})
        listing = self._listing(activate=True)

        with self.assertRaises(ValidationError):
            self._transaction(listing, company_id=other.id)
