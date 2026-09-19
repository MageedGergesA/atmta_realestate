# -*- coding: utf-8 -*-
"""M6 / M7 — brokers, agreements, lead protection and collisions."""

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import BrokerageCommon


@tagged('post_install', '-at_install')
class TestBrokerEligibility(BrokerageCommon):
    """A broker is a partner with standing — and the standing is checked."""

    def test_an_unverified_broker_cannot_be_activated(self):
        broker = self._broker(broker_kyc_state='submitted')

        with self.assertRaises(UserError):
            broker.action_activate_broker()

    def test_an_expired_licence_stops_a_broker_transacting(self):
        broker = self._broker(broker_license_expiry=fields.Date.subtract(
            fields.Date.today(), days=1))

        with self.assertRaises(UserError) as caught:
            broker._check_may_transact()

        self.assertIn('licence', str(caught.exception).lower())

    def test_a_suspended_broker_cannot_transact(self):
        broker = self._broker()
        broker.action_suspend_broker()

        with self.assertRaises(UserError):
            broker._check_may_transact()

    def test_an_unverified_broker_cannot_transact(self):
        broker = self._broker()
        broker.broker_kyc_state = 'submitted'

        with self.assertRaises(UserError):
            broker._check_may_transact()

    def test_expired_licences_are_searchable(self):
        """A list of who cannot be paid is an operational need, not a compute."""
        stale = self._broker(broker_license_expiry=fields.Date.subtract(
            fields.Date.today(), days=1))
        current = self._broker()

        found = self.env['res.partner'].search(
            [('broker_license_expired', '=', True)])

        self.assertIn(stale, found)
        self.assertNotIn(current, found)


@tagged('post_install', '-at_install')
class TestBrokerAgreement(BrokerageCommon):

    def test_terms_live_on_the_agreement_not_the_partner(self):
        broker = self._broker()

        agreement = self._agreement(broker, commission_percentage=25.0,
                                    protection_days=60)

        self.assertEqual(broker.active_broker_agreement_id, agreement)
        self.assertEqual(agreement.protection_days, 60)

    def test_two_live_agreements_are_refused(self):
        """Two sets of terms means two answers to what a broker is owed."""
        broker = self._broker()
        self._agreement(broker)

        with self.assertRaises(ValidationError):
            self._agreement(broker)

    def test_an_ineligible_broker_cannot_have_an_agreement_activated(self):
        broker = self._broker(broker_kyc_state='submitted',
                              broker_state='pending')
        agreement = self.env['realestate.broker.agreement'].create({
            'broker_partner_id': broker.id,
        })

        with self.assertRaises(UserError):
            agreement.action_activate()

    def test_an_agreement_may_be_scoped_to_projects(self):
        other = self.env['realestate.project'].create({
            'name': 'Unauthorised Project', 'code': 'UNA',
            'company_id': self.company.id})
        agreement = self._agreement(
            self._broker(),
            allowed_project_ids=[(6, 0, self.project.ids)])

        self.assertTrue(agreement._covers_project(self.project))
        self.assertFalse(agreement._covers_project(other))

    def test_an_unscoped_agreement_covers_everything(self):
        agreement = self._agreement(self._broker())

        self.assertTrue(agreement._covers_project(self.project))

    def test_an_agreement_that_ends_before_it_starts_is_refused(self):
        with self.assertRaises(ValidationError):
            self._agreement(
                self._broker(), activate=False,
                date_start=fields.Date.today(),
                date_end=fields.Date.subtract(fields.Date.today(), days=5))

    def test_the_cron_expires_a_lapsed_agreement(self):
        agreement = self._agreement(
            self._broker(),
            date_start=fields.Date.subtract(fields.Date.today(), days=400),
            date_end=fields.Date.subtract(fields.Date.today(), days=1))

        self.env['realestate.broker.agreement']._cron_expire_agreements()

        self.assertEqual(agreement.state, 'expired')


@tagged('post_install', '-at_install')
class TestLeadRegistration(BrokerageCommon):
    """"This customer is mine", and the fight that follows."""

    def test_an_eligible_broker_gets_the_protection(self):
        reg = self._registration(self._agreement(self._broker(),
                                                 protection_days=90))

        reg.action_submit()

        self.assertEqual(reg.state, 'approved')
        self.assertTrue(reg.is_protected)
        # `context_today`, not `today`: `_grant_protection` dates the window
        # from the user's timezone (lead_registration.py), and asserting
        # against UTC makes this test fail for users ahead of UTC between
        # local midnight and UTC midnight — a broker's protection is measured
        # from the day they registered where they are, not in Greenwich.
        self.assertEqual(
            reg.protected_until,
            fields.Date.add(fields.Date.context_today(reg), days=90))

    def test_a_registration_needs_a_way_to_identify_the_customer(self):
        """A name alone matches half a city, so it protects nobody."""
        agreement = self._agreement(self._broker())

        with self.assertRaises(ValidationError):
            self.env['realestate.lead.registration'].create({
                'broker_partner_id': agreement.broker_partner_id.id,
                'agreement_id': agreement.id,
                'customer_name': 'Nameless Buyer',
            })

    def test_a_broker_with_no_agreement_is_refused(self):
        broker = self._broker()
        reg = self.env['realestate.lead.registration'].create({
            'broker_partner_id': broker.id,
            'customer_name': 'Buyer',
            'customer_phone': '+201001234567',
        })

        reg.action_submit()

        self.assertEqual(reg.state, 'rejected')
        self.assertEqual(reg.rejection_reason, 'broker_ineligible')

    def test_an_unauthorised_project_is_refused(self):
        other = self.env['realestate.project'].create({
            'name': 'Not Theirs', 'code': 'NOT',
            'company_id': self.company.id})
        agreement = self._agreement(
            self._broker(), allowed_project_ids=[(6, 0, self.project.ids)])
        reg = self._registration(agreement, project_id=other.id)

        reg.action_submit()

        self.assertEqual(reg.state, 'rejected')
        self.assertEqual(reg.rejection_reason, 'not_authorised')

    def test_a_lapsed_licence_at_registration_time_is_refused(self):
        agreement = self._agreement(self._broker())
        agreement.broker_partner_id.broker_license_expiry = \
            fields.Date.subtract(fields.Date.today(), days=1)
        reg = self._registration(agreement)

        reg.action_submit()

        self.assertEqual(reg.state, 'rejected')
        self.assertEqual(reg.rejection_reason, 'broker_ineligible')


@tagged('post_install', '-at_install')
class TestRegistrationCollision(BrokerageCommon):
    """The second broker must be refused, and must learn nothing."""

    def test_the_second_broker_is_refused(self):
        first = self._registration(self._agreement(self._broker('Broker One')))
        first.action_submit()

        second = self._registration(
            self._agreement(self._broker('Broker Two')))
        second.action_submit()

        self.assertEqual(second.state, 'rejected')
        self.assertEqual(second.rejection_reason, 'duplicate')

    def test_the_refusal_names_nobody(self):
        """A competitor's client list, leaked one name at a time."""
        first = self._registration(self._agreement(self._broker('Broker One')))
        first.action_submit()
        second = self._registration(
            self._agreement(self._broker('Broker Two')))

        second.action_submit()

        self.assertNotIn('Broker One', second.rejection_note or '')
        self.assertNotIn(str(first.name), second.rejection_note or '')

    def test_the_company_can_still_settle_the_dispute(self):
        """Silent to the broker, fully auditable to the company."""
        first = self._registration(self._agreement(self._broker('Broker One')))
        first.action_submit()
        second = self._registration(
            self._agreement(self._broker('Broker Two')))
        second.action_submit()

        self.assertEqual(second.colliding_registration_id, first)

    def test_an_agent_cannot_read_who_holds_the_lead(self):
        first = self._registration(self._agreement(self._broker('Broker One')))
        first.action_submit()
        second = self._registration(
            self._agreement(self._broker('Broker Two')))
        second.action_submit()

        fields_read = second.with_user(self.agent).read(['rejection_reason'])[0]

        self.assertNotIn('colliding_registration_id', fields_read)

    def test_a_differently_formatted_phone_is_the_same_buyer(self):
        """+20 100 123 4567 and 01001234567 are one person.

        Distinct emails deliberately: with the fixture's shared default the
        collision would be caught on email and this would pass while phone
        matching was broken.
        """
        first = self._registration(self._agreement(self._broker('Broker One')),
                                   customer_phone='+201001234567',
                                   customer_email='ali.first@example.com')
        first.action_submit()
        second = self._registration(self._agreement(self._broker('Broker Two')),
                                    customer_phone='0100 123 4567',
                                    customer_email='ali.second@example.com')

        second.action_submit()

        self.assertEqual(second.state, 'rejected')
        self.assertEqual(second.rejection_reason, 'duplicate')

    def test_a_manager_can_override_a_duplicate_rejection(self):
        """Tail matching is generous; a broker who was first needs a route."""
        first = self._registration(self._agreement(self._broker('Broker One')))
        first.action_submit()
        second = self._registration(self._agreement(self._broker('Broker Two')))
        second.action_submit()

        second.action_override_duplicate('Broker Two produced a signed '
                                         'introduction dated earlier')

        self.assertEqual(second.state, 'approved')
        self.assertTrue(second.is_protected)

    def test_an_override_needs_a_reason(self):
        first = self._registration(self._agreement(self._broker('Broker One')))
        first.action_submit()
        second = self._registration(self._agreement(self._broker('Broker Two')))
        second.action_submit()

        with self.assertRaises(UserError):
            second.action_override_duplicate('')

    def test_an_agent_cannot_override_a_duplicate(self):
        first = self._registration(self._agreement(self._broker('Broker One')))
        first.action_submit()
        second = self._registration(self._agreement(self._broker('Broker Two')))
        second.action_submit()

        with self.assertRaises(UserError):
            second.with_user(self.agent).action_override_duplicate('because')

    def test_a_different_buyer_is_not_a_collision(self):
        first = self._registration(self._agreement(self._broker('Broker One')),
                                   customer_phone='+201001234567')
        first.action_submit()
        second = self._registration(self._agreement(self._broker('Broker Two')),
                                    customer_phone='+201119876543',
                                    customer_email='someone.else@example.com')

        second.action_submit()

        self.assertEqual(second.state, 'approved')

    def test_two_people_sharing_a_name_are_not_a_collision(self):
        """Matching on names takes money off brokers who did nothing wrong."""
        first = self._registration(self._agreement(self._broker('Broker One')),
                                   customer_name='Mohamed Ali',
                                   customer_phone='+201001234567')
        first.action_submit()
        second = self._registration(self._agreement(self._broker('Broker Two')),
                                    customer_name='Mohamed Ali',
                                    customer_phone='+201119876543',
                                    customer_email='other.ali@example.com')

        second.action_submit()

        self.assertEqual(second.state, 'approved')

    def test_a_customer_the_company_already_has_cannot_be_claimed(self):
        """Not an introduction — the company found them itself."""
        self.Lead.create({
            'name': 'Direct walk-in', 'type': 'opportunity',
            'partner_id': self.buyer.id, 'phone': '+201001234567',
        })
        reg = self._registration(self._agreement(self._broker()),
                                 customer_phone='+201001234567')

        reg.action_submit()

        self.assertEqual(reg.state, 'rejected')
        self.assertEqual(reg.rejection_reason, 'existing_customer')

    def test_protection_lapses_and_the_customer_is_free_again(self):
        first = self._registration(
            self._agreement(self._broker('Broker One'), protection_days=30))
        first.action_submit()
        first.write({'protected_until': fields.Date.subtract(
            fields.Date.today(), days=1)})

        self.env['realestate.lead.registration']._cron_expire_protection()
        second = self._registration(self._agreement(self._broker('Broker Two')))
        second.action_submit()

        self.assertEqual(first.state, 'expired')
        self.assertEqual(second.state, 'approved')

    def test_a_lapsed_protection_no_longer_blocks_even_before_the_cron(self):
        """The cron tidies up; it is not what makes the rule true."""
        first = self._registration(self._agreement(self._broker('Broker One')))
        first.action_submit()
        first.write({'protected_until': fields.Date.subtract(
            fields.Date.today(), days=1)})

        second = self._registration(self._agreement(self._broker('Broker Two')))
        second.action_submit()

        self.assertEqual(second.state, 'approved')


@tagged('post_install', '-at_install')
class TestRegistrationConversion(BrokerageCommon):

    def test_an_approved_registration_becomes_an_opportunity(self):
        reg = self._registration(self._agreement(self._broker()))
        reg.action_submit()

        reg.action_convert_to_opportunity()

        lead = reg.crm_lead_id
        self.assertTrue(lead)
        self.assertEqual(reg.state, 'converted')
        self.assertEqual(lead.re_broker_partner_id, reg.broker_partner_id)
        self.assertEqual(lead.re_registration_id, reg)
        self.assertTrue(lead.re_broker_protected)

    def test_a_rejected_registration_does_not_convert(self):
        first = self._registration(self._agreement(self._broker('Broker One')))
        first.action_submit()
        second = self._registration(self._agreement(self._broker('Broker Two')))
        second.action_submit()

        with self.assertRaises(UserError):
            second.action_convert_to_opportunity()

    def test_converting_twice_returns_the_same_opportunity(self):
        reg = self._registration(self._agreement(self._broker()))
        reg.action_submit()
        reg.action_convert_to_opportunity()
        first_lead = reg.crm_lead_id

        reg.action_convert_to_opportunity()

        self.assertEqual(reg.crm_lead_id, first_lead)
