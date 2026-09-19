# -*- coding: utf-8 -*-
"""M3 / M4 — listings that know what they sell, and the mandate that authorises them."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import BrokerageCommon


@tagged('post_install', '-at_install', 'atmta_brokerage')
class TestListingInventoryType(BrokerageCommon):

    def test_a_listing_declares_internal_or_external(self):
        self.assertIn('inventory_type', self.Listing._fields)
        self.assertIn('deal_type', self.Listing._fields)

    def test_a_listing_belongs_to_a_company(self):
        """0.1 had no company on any Brokerage model at all."""
        listing = self._listing()
        self.assertEqual(listing.company_id, self.company)

    def test_an_external_listing_needs_an_owner(self):
        standalone = self._unit(released=False, project_id=False,
                                phase_id=False)
        with self.assertRaises(ValidationError) as err:
            self._listing(unit=standalone, inventory_type='external',
                          owner_partner_id=False)
        self.assertIn('names no owner', str(err.exception))

    def test_the_inventory_type_is_read_off_the_property(self):
        """A unit in a Developer project is internal; anything else is not.

        Defaulting instead of deriving would have made every existing 0.1
        listing invalid the moment the owner constraint appeared.
        """
        in_project = self._listing()
        self.assertEqual(in_project.inventory_type, 'internal')

        standalone = self._unit(released=False, project_id=False,
                                phase_id=False, owner_id=self.seller.id)
        outside = self._listing(unit=standalone)
        self.assertEqual(outside.inventory_type, 'external')
        self.assertEqual(outside.owner_partner_id, self.seller,
                         "The owner was not taken from the property register")

    def test_an_internal_listing_needs_no_external_owner(self):
        listing = self._listing(inventory_type='internal')
        self.assertFalse(listing.owner_partner_id)

    def test_no_second_property_master_was_created(self):
        """Rule 1 — internal inventory is Developer's property, not a copy."""
        unit = self._unit()
        listing = self._listing(unit=unit, inventory_type='internal')
        self.assertEqual(listing.property_id, unit)
        self.assertNotIn('realestate.listing.property', self.env)

    def test_a_listing_cannot_straddle_companies(self):
        other = self.env['res.company'].create({'name': 'Rival Brokerage'})
        unit = self._unit()
        with self.assertRaises(ValidationError) as err:
            self._listing(unit=unit, company_id=other.id)
        self.assertIn('belongs to', str(err.exception))

    def test_the_confidential_minimum_is_manager_only(self):
        """M22 — the seller's floor is not for agents or brokers."""
        field = self.Listing._fields['minimum_price']
        self.assertIn('group_realestate_sales_manager', (field.groups or ''))

    def test_the_minimum_cannot_exceed_the_asking_price(self):
        with self.assertRaises(ValidationError):
            self._listing(price=1000000.0, minimum_price=1200000.0)


@tagged('post_install', '-at_install', 'atmta_brokerage')
class TestListingActivationAuthority(BrokerageCommon):
    """The availability defect Phase 0 found, and its fix."""

    def test_internal_activation_asks_developer_not_module_one(self):
        """0.1 called the legacy check, which cannot see releases or blocks."""
        unreleased = self._unit(released=False)
        listing = self._listing(unit=unreleased, inventory_type='internal')
        with self.assertRaises(ValidationError) as err:
            listing.action_activate()
        self.assertIn('released', str(err.exception).lower())

    def test_a_blocked_unit_cannot_be_marketed(self):
        unit = self._unit()
        self.env['realestate.unit.block'].create({
            'property_id': unit.id,
            'reason': 'management',
        })
        unit._recompute_sale_availability()
        listing = self._listing(unit=unit, inventory_type='internal')
        with self.assertRaises(ValidationError) as err:
            listing.action_activate()
        self.assertIn('blocked', str(err.exception).lower())

    def test_a_released_unit_activates(self):
        listing = self._listing(inventory_type='internal')
        listing.action_activate()
        self.assertEqual(listing.state, 'active')

    def test_an_external_listing_needs_an_active_mandate(self):
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.seller.id)
        with self.assertRaises(UserError) as err:
            listing.action_activate()
        self.assertIn('no active owner mandate', str(err.exception))

    def test_an_external_listing_activates_with_a_mandate(self):
        listing = self._mandated_listing()
        listing.action_activate()
        self.assertEqual(listing.state, 'active')

    def test_brokerage_refuses_to_act_on_developer_inventory(self):
        """M24 — availability, pricing and reservation are Developer's."""
        listing = self._listing(inventory_type='internal')
        with self.assertRaises(UserError) as err:
            listing._assert_not_internal('Reserving the unit')
        message = str(err.exception)
        self.assertIn("Developer module's authority", message)
        self.assertIn('Reserving the unit', message)


@tagged('post_install', '-at_install', 'atmta_brokerage')
class TestMandate(BrokerageCommon):

    def test_the_lifecycle(self):
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.seller.id)
        mandate = self._mandate(listing)
        self.assertEqual(mandate.state, 'draft')

        mandate.action_submit_for_signature()
        self.assertEqual(mandate.state, 'pending_signature')

        mandate.action_activate()
        self.assertEqual(mandate.state, 'active')
        self.assertTrue(mandate.signed_on)
        self.assertEqual(mandate.signed_by_id, self.env.user)

        mandate.action_terminate(reason='Owner withdrew')
        self.assertEqual(mandate.state, 'terminated')
        self.assertEqual(mandate.termination_reason, 'Owner withdrew')

    def test_signed_terms_cannot_be_edited(self):
        """M4 — do not silently overwrite signed mandate terms."""
        mandate = self._active_mandate()
        with self.assertRaises(UserError) as err:
            mandate.commission_percentage = 5.0
        message = str(err.exception)
        self.assertIn('terms are fixed', message)
        self.assertIn('commission_percentage', message)

    def test_non_commercial_fields_stay_editable(self):
        mandate = self._active_mandate()
        mandate.notes = '<p>Owner prefers weekend viewings.</p>'
        self.assertIn('weekend', mandate.notes)

    def test_a_signed_mandate_cannot_be_cancelled_only_terminated(self):
        mandate = self._active_mandate()
        with self.assertRaises(UserError) as err:
            mandate.action_cancel()
        self.assertIn('terminate it rather', str(err.exception))

    def test_two_exclusives_on_one_listing_are_refused(self):
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.seller.id)
        first = self._mandate(listing, mandate_type='exclusive')
        first.action_activate()
        second = self._mandate(listing, mandate_type='exclusive')
        with self.assertRaises(ValidationError) as err:
            second.action_activate()
        self.assertIn('exclusively instructed to two parties',
                      str(err.exception))

    def test_open_mandates_may_coexist(self):
        """Several agencies instructed openly is normal."""
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.seller.id)
        for _i in range(2):
            self._mandate(listing, mandate_type='open').action_activate()
        self.assertEqual(len(listing.mandate_ids.filtered(
            lambda m: m.state == 'active')), 2)

    def test_exclusivity_is_derived_from_the_mandate(self):
        """Not a checkbox somebody can tick."""
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.seller.id)
        self.assertFalse(listing.is_exclusive)
        self._mandate(listing, mandate_type='exclusive').action_activate()
        listing.invalidate_recordset()
        self.assertTrue(listing.is_exclusive)

    def _expired_term_listing(self):
        """A listing whose mandate ran out yesterday.

        Created with past dates rather than edited into the past: signed terms
        are frozen, which is the point of the model.
        """
        today = fields.Date.context_today(self.env.user)
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.seller.id)
        mandate = self._mandate(listing,
                                date_start=today - timedelta(days=200),
                                date_end=today - timedelta(days=1))
        mandate.action_activate()
        listing.invalidate_recordset()
        return listing, mandate

    def test_an_expired_mandate_stops_authorising(self):
        _listing, mandate = self._expired_term_listing()
        with self.assertRaises(UserError) as err:
            mandate._check_still_valid()
        self.assertIn('expired', str(err.exception))

    def test_the_expiry_cron_pauses_publication(self):
        listing, mandate = self._expired_term_listing()
        # Written directly: the listing was activated while the mandate was
        # still valid, which is the situation the cron exists to clean up.
        # Going through `action_activate` now would (correctly) refuse.
        listing.write({'state': 'active', 'publication_state': 'published'})

        self.env['realestate.listing.mandate']._cron_expire_mandates()
        mandate.invalidate_recordset()
        listing.invalidate_recordset()
        self.assertEqual(mandate.state, 'expired')
        self.assertEqual(listing.publication_state, 'paused')

    def test_unauthorised_marketing_is_refused(self):
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.seller.id)
        mandate = self._mandate(listing, authorized_marketing=False)
        mandate.action_activate()
        with self.assertRaises(UserError) as err:
            listing.action_activate()
        self.assertIn('does not authorise marketing', str(err.exception))

    def test_negotiation_authority_gates_acceptance(self):
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.seller.id)
        refer = self._mandate(listing, negotiation_authority='none')
        self.assertFalse(refer._may_accept(1000000.0))

        listing2 = self._listing(inventory_type='external',
                                 owner_partner_id=self.seller.id)
        above = self._mandate(listing2, negotiation_authority='above_minimum',
                              minimum_price=900000.0)
        self.assertFalse(above._may_accept(850000.0))
        self.assertTrue(above._may_accept(950000.0))

        listing3 = self._listing(inventory_type='external',
                                 owner_partner_id=self.seller.id)
        full = self._mandate(listing3, negotiation_authority='full')
        self.assertTrue(full._may_accept(1.0))

    def test_expiring_soon_is_searchable(self):
        listing = self._mandated_listing(
            date_end=fields.Date.context_today(self.env.user) + timedelta(days=10))
        mandate = listing.mandate_ids
        self.assertTrue(mandate.is_expiring_soon)
        found = self.env['realestate.listing.mandate'].search(
            [('is_expiring_soon', '=', True)])
        self.assertIn(mandate, found)
