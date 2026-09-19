# -*- coding: utf-8 -*-
"""M16 — Rental parity.

The brief is explicit: do not build one robust flow for Developer and leave
Rental on V1 behaviour. Every capability tested here has a Developer twin
elsewhere in this suite, and both go through the same allocation engine, the
same deposit, the same clearance test and the same bounce.
"""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import ChecksCommon


@tagged('post_install', '-at_install', 'atmta_checks')
class TestRentalChecks(ChecksCommon):

    def setUp(self):
        super().setUp()
        self.tenant = self.env['res.partner'].create({'name': 'Tenant One'})
        self.rental_unit = self.units[2]
        self.rental_contract = self._rental_contract(
            self.tenant, self.rental_unit)
        self.rent_payment = self.env['realestate.contract.payment'].create({
            'contract_id': self.rental_contract.id,
            'property_id': self.rental_unit.id,
            'date_due': fields.Date.context_today(self.env['res.partner']),
            'amount': 50000.0,
        })

    def test_the_legacy_rental_field_still_exists(self):
        """`atmta_real_estate.tests.test_compatibility` asserts this."""
        self.assertIn('rental_payment_id', self.Check._fields)

    def test_a_rental_cheque_creates_an_allocation(self):
        check = self._check(amount=50000.0, partner=self.tenant,
                            rental_contract_id=self.rental_contract.id,
                            rental_payment_id=self.rent_payment.id)
        live = check.allocation_ids.filtered(lambda a: a.state == 'active')
        self.assertEqual(len(live), 1)
        self.assertEqual(live.rental_payment_id, self.rent_payment)
        self.assertEqual(live.obligation_kind, 'rental')

    def test_a_rental_cheque_inherits_the_property(self):
        check = self._check(partner=self.tenant,
                            rental_contract_id=self.rental_contract.id)
        self.assertEqual(check.property_id, self.rental_unit)

    def test_rental_coverage_is_computed(self):
        self._check(amount=30000.0, partner=self.tenant,
                    rental_contract_id=self.rental_contract.id,
                    rental_payment_id=self.rent_payment.id)
        self.rent_payment.invalidate_recordset()
        self.assertAlmostEqual(
            self.rent_payment.secured_by_checks_amount, 30000.0, 2)
        self.assertEqual(self.rent_payment.check_count, 1)

    def test_two_cheques_for_one_rent_payment(self):
        for amount in (30000.0, 20000.0):
            check = self._check(amount=amount, partner=self.tenant)
            self.Allocation.create({
                'check_id': check.id,
                'rental_payment_id': self.rent_payment.id,
                'allocated_amount': amount,
            })
        self.rent_payment.invalidate_recordset()
        self.assertAlmostEqual(
            self.rent_payment.secured_by_checks_amount, 50000.0, 2)

    def test_over_covering_a_rent_payment_is_refused(self):
        check = self._check(amount=60000.0, partner=self.tenant)
        with self.assertRaises(ValidationError):
            self.Allocation.create({
                'check_id': check.id,
                'rental_payment_id': self.rent_payment.id,
                'allocated_amount': 60000.0,
            })

    def test_a_rental_cheque_clears_through_the_same_engine(self):
        check = self._check(amount=50000.0, partner=self.tenant,
                            rental_contract_id=self.rental_contract.id,
                            rental_payment_id=self.rent_payment.id,
                            journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        self.assertEqual(check.state, 'deposited')
        self.assertFalse(check._is_cash_confirmed())
        self._reconcile_with_bank(check.payment_id)
        check._sync_clearance_from_accounting()
        self.assertEqual(check.state, 'cleared')

    def test_a_rental_cheque_bounces_through_the_same_engine(self):
        check = self._check(amount=50000.0, partner=self.tenant,
                            rental_contract_id=self.rental_contract.id,
                            rental_payment_id=self.rent_payment.id,
                            journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        bounce = self.Bounce.create({
            'check_id': check.id, 'reason': 'insufficient'})
        check.invalidate_recordset()
        self.assertEqual(check.state, 'bounced')
        self.assertTrue(bounce.accounting_handled)

    def test_the_rental_contract_carries_the_same_statistics(self):
        self._check(amount=50000.0, partner=self.tenant,
                    rental_contract_id=self.rental_contract.id)
        self.rental_contract.invalidate_recordset()
        self.assertEqual(self.rental_contract.check_count, 1)
        self.assertAlmostEqual(
            self.rental_contract.check_amount_total, 50000.0, 2)
        self.assertEqual(self.rental_contract.check_amount_cleared, 0.0)

    def test_a_rental_obligation_is_offered_by_the_allocation_wizard(self):
        check = self._check(amount=50000.0, partner=self.tenant,
                            rental_contract_id=self.rental_contract.id)
        candidates = check._candidate_obligations()
        self.assertTrue(candidates)
        self.assertIn(self.rent_payment, [record for record, _amount in candidates])
