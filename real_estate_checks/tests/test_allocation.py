# -*- coding: utf-8 -*-
"""M3 / M18 — the allocation engine, and its three shapes."""

import psycopg2

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

from .common import ChecksCommon


@tagged('post_install', '-at_install', 'atmta_checks')
class TestAllocationShapes(ChecksCommon):
    """Case A, Case B and Case C from M3."""

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()
        self.installments = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))

    def test_case_a_one_cheque_one_instalment(self):
        target = self.installments[0]
        check = self._check(amount=target.current_amount,
                            sale_contract_id=self.contract.id,
                            sale_installment_id=target.id)
        live = check.allocation_ids.filtered(lambda a: a.state == 'active')
        self.assertEqual(len(live), 1)
        self.assertEqual(live.sale_installment_id, target)
        self.assertEqual(live.allocated_amount, target.current_amount)
        self.assertEqual(check.unapplied_amount, 0.0)

    def test_case_b_two_cheques_one_instalment(self):
        """1,000,000 owed; 600,000 + 400,000 of paper."""
        target = self.installments[0]
        amount = target.current_amount
        first = self._check(amount=amount * 0.6)
        second = self._check(amount=amount * 0.4)
        for check in (first, second):
            self.Allocation.create({
                'check_id': check.id,
                'sale_installment_id': target.id,
                'allocated_amount': check.amount,
            })
        target.invalidate_recordset()
        self.assertAlmostEqual(target.secured_by_checks_amount, amount, 2)
        self.assertAlmostEqual(target.unsecured_amount, 0.0, 2)

    def test_case_c_one_cheque_several_instalments(self):
        """A consolidated cheque covering the next three obligations."""
        targets = self.installments[:3]
        total = sum(targets.mapped('current_amount'))
        check = self._check(amount=total)
        for installment in targets:
            self.Allocation.create({
                'check_id': check.id,
                'sale_installment_id': installment.id,
                'allocated_amount': installment.current_amount,
            })
        check.invalidate_recordset()
        self.assertEqual(check.allocation_count, 3)
        self.assertAlmostEqual(check.allocated_amount, total, 2)
        self.assertAlmostEqual(check.unapplied_amount, 0.0, 2)

    def test_partial_allocation_shows_the_balance_rather_than_hiding_it(self):
        target = self.installments[0]
        check = self._check(amount=100000.0)
        self.Allocation.create({
            'check_id': check.id,
            'sale_installment_id': target.id,
            'allocated_amount': 60000.0,
        })
        check.invalidate_recordset()
        self.assertEqual(check.allocated_amount, 60000.0)
        self.assertEqual(check.unapplied_amount, 40000.0)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestAllocationInvariants(ChecksCommon):

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()
        self.installments = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))

    def test_a_cheque_cannot_pay_more_than_it_is_worth(self):
        target = self.installments[0]
        check = self._check(amount=100000.0)
        self.Allocation.create({
            'check_id': check.id, 'sale_installment_id': target.id,
            'allocated_amount': 100000.0,
        })
        with self.assertRaises(ValidationError) as err:
            self.Allocation.create({
                'check_id': check.id,
                'sale_installment_id': self.installments[1].id,
                'allocated_amount': 1.0,
            })
        self.assertIn('cannot pay more than it is worth', str(err.exception))

    def test_an_obligation_cannot_be_covered_twice_over(self):
        target = self.installments[0]
        amount = target.current_amount
        first = self._check(amount=amount)
        second = self._check(amount=amount)
        self.Allocation.create({
            'check_id': first.id, 'sale_installment_id': target.id,
            'allocated_amount': amount,
        })
        with self.assertRaises(ValidationError) as err:
            self.Allocation.create({
                'check_id': second.id, 'sale_installment_id': target.id,
                'allocated_amount': amount,
            })
        self.assertIn('twice over', str(err.exception))

    def test_an_allocation_names_exactly_one_obligation(self):
        check = self._check()
        with self.assertRaises(ValidationError):
            self.Allocation.create({
                'check_id': check.id, 'allocated_amount': 1.0,
            })

    def test_a_negative_allocation_is_refused(self):
        check = self._check()
        with mute_logger('odoo.sql_db'):
            with self.assertRaises(psycopg2.errors.CheckViolation):
                with self.env.cr.savepoint():
                    self.Allocation.create({
                        'check_id': check.id,
                        'sale_installment_id': self.installments[0].id,
                        'allocated_amount': -1.0,
                    })
                    self.env.cr.flush()

    def test_cancelled_allocations_free_the_capacity(self):
        target = self.installments[0]
        check = self._check(amount=100000.0)
        allocation = self.Allocation.create({
            'check_id': check.id, 'sale_installment_id': target.id,
            'allocated_amount': 100000.0,
        })
        allocation.action_cancel(reason='Restructured')
        check.invalidate_recordset()
        self.assertEqual(check.allocated_amount, 0.0)
        self.assertEqual(check.unapplied_amount, 100000.0)
        # Kept, not deleted — a cancelled allocation is evidence.
        self.assertEqual(allocation.state, 'cancelled')
        self.assertEqual(allocation.cancel_reason, 'Restructured')


@tagged('post_install', '-at_install', 'atmta_checks')
class TestLegacyFieldMirror(ChecksCommon):
    """M3's compatibility clause — one engine, but the shortcut still works."""

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()
        self.installments = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))

    def test_setting_the_legacy_field_creates_an_allocation(self):
        target = self.installments[0]
        check = self._check(sale_installment_id=target.id)
        live = check.allocation_ids.filtered(lambda a: a.state == 'active')
        self.assertEqual(len(live), 1)
        self.assertEqual(live.sale_installment_id, target)

    def test_a_single_allocation_mirrors_back_to_the_legacy_field(self):
        target = self.installments[0]
        check = self._check(amount=target.current_amount)
        self.Allocation.create({
            'check_id': check.id, 'sale_installment_id': target.id,
            'allocated_amount': target.current_amount,
        })
        check.invalidate_recordset()
        self.assertEqual(check.sale_installment_id, target)

    def test_a_split_cheque_has_no_single_instalment(self):
        """The field must go blank rather than lie.

        Pointing it at the first of three obligations would be a falsehood
        Developer's guards would then act on.
        """
        targets = self.installments[:2]
        check = self._check(amount=sum(targets.mapped('current_amount')))
        for installment in targets:
            self.Allocation.create({
                'check_id': check.id,
                'sale_installment_id': installment.id,
                'allocated_amount': installment.current_amount,
            })
        check.invalidate_recordset()
        self.assertFalse(check.sale_installment_id)
        self.assertEqual(check.allocation_count, 2)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestCoverageLabels(ChecksCommon):
    """M18 / M19 — securing is not collecting, and the names say so."""

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()
        self.installments = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))

    def test_a_pdc_secures_but_does_not_pay(self):
        target = self.installments[0]
        self._check(amount=target.current_amount,
                    sale_contract_id=self.contract.id,
                    sale_installment_id=target.id)
        target.invalidate_recordset()
        self.assertAlmostEqual(
            target.secured_by_checks_amount, target.current_amount, 2)
        # Rule 2: receiving a cheque is not receiving money.
        self.assertEqual(target.paid_amount, 0.0)
        self.assertNotEqual(target.state, 'paid')

    def test_a_cleared_cheque_stops_counting_as_security(self):
        """Otherwise the same money is counted as both paper and cash."""
        target = self.installments[0]
        check = self._check(amount=target.current_amount,
                            sale_contract_id=self.contract.id,
                            sale_installment_id=target.id)
        self._deposit(check)
        self._reconcile_with_bank(check.payment_id)
        check._sync_clearance_from_accounting()
        target.invalidate_recordset()
        self.assertEqual(check.state, 'cleared')
        self.assertEqual(target.secured_by_checks_amount, 0.0)

    def test_contract_coverage_percentage(self):
        first, second = self.installments[0], self.installments[1]
        self._check(amount=first.current_amount,
                    sale_contract_id=self.contract.id,
                    sale_installment_id=first.id)
        self.contract.invalidate_recordset()
        future = self.contract.future_obligation_amount
        secured = self.contract.pdc_secured_amount
        self.assertGreater(future, 0)
        self.assertAlmostEqual(secured, first.current_amount, 2)
        self.assertAlmostEqual(
            self.contract.pdc_coverage_percent, secured / future * 100.0, 2)
        self.assertAlmostEqual(
            self.contract.pdc_unsecured_amount, future - secured, 2)

    def test_the_field_is_not_called_paid(self):
        """The naming is load-bearing, so it is asserted."""
        fields_ = self.env['realestate.sale.installment']._fields
        self.assertIn('secured_by_checks_amount', fields_)
        self.assertNotIn('checks_paid_amount', fields_)
        self.assertNotIn('collected_by_checks_amount', fields_)


@tagged('post_install', '-at_install')
class TestNewInstalmentForm(TransactionCase):
    """Clicking New on an instalment list crashed: the coverage compute rounded
    with the currency of a contract the new record does not have yet."""

    def test_a_new_instalment_form_opens(self):
        Instalment = self.env['realestate.sale.installment']
        spec = {name: {} for name in ('unsecured_amount', 'secured_by_checks_amount', 'check_count')}
        values = Instalment.onchange({}, [], spec)['value']
        self.assertEqual(values.get('check_count', 0), 0)
        self.assertEqual(values.get('unsecured_amount', 0.0), 0.0)
