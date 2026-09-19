# -*- coding: utf-8 -*-
"""M5 — Contract V2 and the installment engine.

The headline is that instalments exist again. Since 0.2 nothing created them,
so `real_estate_checks` could not issue post-dated cheques against a sale
contract, the buyer portal showed an empty schedule, and the dashboard's
revenue KPI was permanently zero.

Phase 26's worked example is tested literally: a 10,000 instalment, paid 4,000,
must read *partially paid* with 6,000 residual — not paid.
"""

from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import DeveloperCommon


class ContractCommon(DeveloperCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Contract = cls.env['realestate.sale.contract']
        cls.Installment = cls.env['realestate.sale.installment']
        cls.Reservation = cls.env['realestate.unit.reservation']
        cls.Plan = cls.env['realestate.payment.plan']
        cls.buyer = cls.env['res.partner'].create({'name': 'Contract Buyer'})
        cls.co_buyer = cls.env['res.partner'].create({'name': 'Co Buyer'})

    def setUp(self):
        super().setUp()
        self._open_project_for_sales()
        self.unit = self.units[0]
        self._release(self.unit)
        self.unit.base_price = 1000000.0
        # No sales tax on the fixture unit, so the worked examples below are the
        # plain arithmetic Phase 26 describes. Tax behaviour is asserted
        # separately in `test_invoiced_amount_is_untaxed`.
        self.unit.product_variant_id.taxes_id = [(5, 0, 0)]

    def _plan(self, name='Contract Plan'):
        plan = self.Plan.create({
            'name': name, 'company_id': self.company.id,
            'project_id': self.project.id,
        })
        self.env['realestate.payment.plan.line'].create([
            {'plan_id': plan.id, 'sequence': 10, 'kind': 'down_payment',
             'calculation_type': 'percent', 'value': 20.0,
             'date_rule': 'on_booking'},
            {'plan_id': plan.id, 'sequence': 20, 'kind': 'installment',
             'calculation_type': 'percent', 'value': 10.0, 'occurrences': 4,
             'interval': 'quarterly', 'date_rule': 'months_after_booking',
             'offset_value': 3},
            {'plan_id': plan.id, 'sequence': 30, 'kind': 'handover',
             'calculation_type': 'residual',
             'date_rule': 'months_after_booking', 'offset_value': 24},
        ])
        plan.action_activate()
        return plan

    def _contract(self, **kwargs):
        vals = {
            'partner_id': self.buyer.id,
            'property_id': self.unit.id,
            'sale_price': 1000000.0,
            # Dated today on purpose: the down payment falls due on the
            # booking date, so a past contract date would make it legitimately
            # overdue and mask what these tests are actually about.
            'contract_date': fields.Date.context_today(self.env['res.partner']),
        }
        vals.update(kwargs)
        return self.Contract.create(vals)


@tagged('post_install', '-at_install', 'atmta_developer')
class TestContractLifecycle(ContractCommon):
    """Phase 21 — the lifecycle is extended, never redefined."""

    def test_original_state_values_are_intact(self):
        """Five downstream modules read these; removing one breaks them."""
        values = dict(self.Contract._fields['state'].selection)
        for expected in ('draft', 'signed', 'handed_over', 'cancelled'):
            self.assertIn(expected, values)

    def test_new_stages_were_added_around_them(self):
        values = dict(self.Contract._fields['state'].selection)
        for added in ('pending_approval', 'pending_signature', 'active',
                      'financially_cleared', 'terminated', 'transferred'):
            self.assertIn(added, values)

    def test_signing_raises_the_schedule(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        self.assertEqual(contract.state, 'signed')
        self.assertEqual(contract.signature_state, 'signed')
        self.assertEqual(len(contract.installment_ids), 6)
        self.assertEqual(
            sum(contract.installment_ids.mapped('current_amount')), 1000000.0)

    def test_signing_without_a_plan_is_refused(self):
        contract = self._contract()
        with self.assertRaises(UserError) as err:
            contract.action_sign()
        self.assertIn('payment plan', str(err.exception).lower())

    def test_signing_marks_the_unit_contracted(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        self.assertEqual(self.unit.commercial_status, 'contracted')

    def test_handover_still_works_for_the_handover_module(self):
        """`real_estate_handover` filters on 'signed' then calls this."""
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        self.assertEqual(contract.state, 'signed')
        contract.action_handover()
        self.assertEqual(contract.state, 'handed_over')
        self.assertEqual(self.unit.owner_id, self.buyer)
        self.assertEqual(self.unit.commercial_status, 'sold')

    def test_handover_works_from_the_new_intermediate_stages_too(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        contract.action_activate()
        contract.action_handover()
        self.assertEqual(contract.state, 'handed_over')

    def test_financial_clearance_requires_settlement(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        with self.assertRaises(UserError) as err:
            contract.action_mark_financially_cleared()
        self.assertIn('outstanding', str(err.exception).lower())

    def test_the_second_invoicing_path_is_neutralised(self):
        """The whole-price invoice and the per-instalment invoice both posted."""
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        self.assertFalse(
            contract._create_sale_and_invoice(),
            "V1's whole-price invoicing must no longer raise anything")
        self.assertFalse(contract.invoice_id)

    def test_generation_is_idempotent(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        before = len(contract.installment_ids)
        contract._generate_installments()
        self.assertEqual(len(contract.installment_ids), before,
                         "re-running must not double the schedule")


@tagged('post_install', '-at_install', 'atmta_developer')
class TestContractSnapshot(ContractCommon):
    """Phase 24 — the signed terms are immutable."""

    def _reserved_contract(self):
        plan = self._plan()
        res = self.Reservation.create({
            'property_id': self.unit.id,
            'partner_id': self.buyer.id,
            'payment_plan_id': plan.id,
            'discount_amount': 50000.0,
            'booking_amount_required': 25000.0,
            'booking_amount_received': 25000.0,
        })
        res.action_confirm_booking()
        contract = self._contract(reservation_id=res.id,
                                  sale_price=res.net_price)
        return res, contract

    def test_snapshot_comes_from_the_reservation(self):
        res, contract = self._reserved_contract()
        self.assertEqual(contract.snapshot_list_price, res.snapshot_list_price)
        self.assertEqual(contract.discount_amount, 50000.0)
        self.assertEqual(contract.payment_plan_version, res.payment_plan_version)

    def test_booking_credit_is_carried_so_money_is_not_charged_twice(self):
        res, contract = self._reserved_contract()
        self.assertEqual(contract.booking_credit, 25000.0)

    def test_repricing_the_unit_does_not_restate_the_contract(self):
        _res, contract = self._reserved_contract()
        signed_at = contract.snapshot_list_price
        self.unit.base_price = 2000000.0
        contract.invalidate_recordset()
        self.assertEqual(contract.snapshot_list_price, signed_at)

    def test_contract_uses_the_reservations_frozen_schedule(self):
        """Not the plan as it stands today — the plan may have been versioned."""
        res, contract = self._reserved_contract()
        contract.action_sign()
        quoted = res.schedule_line_ids.sorted(lambda l: (l.date_due, l.sequence))
        raised = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))
        self.assertEqual(len(raised), len(quoted))
        self.assertEqual(
            [i.current_amount for i in raised],
            [l.amount for l in quoted])


@tagged('post_install', '-at_install', 'atmta_developer')
class TestContractParties(ContractCommon):
    """Phase 22 — co-buyers without duplicating res.partner."""

    def test_primary_party_mirrors_partner_id(self):
        contract = self._contract()
        primary = contract.party_ids.filtered(
            lambda p: p.role == 'primary_buyer')
        self.assertEqual(len(primary), 1)
        self.assertEqual(primary.partner_id, self.buyer)

    def test_changing_the_buyer_updates_the_primary_party(self):
        contract = self._contract()
        contract.partner_id = self.co_buyer
        primary = contract.party_ids.filtered(
            lambda p: p.role == 'primary_buyer')
        self.assertEqual(primary.partner_id, self.co_buyer)

    def test_only_one_primary_buyer(self):
        contract = self._contract()
        with self.assertRaises(ValidationError):
            self.env['realestate.sale.contract.party'].create({
                'contract_id': contract.id,
                'partner_id': self.co_buyer.id,
                'role': 'primary_buyer',
            })

    def test_co_buyers_can_be_added(self):
        contract = self._contract()
        self.env['realestate.sale.contract.party'].create({
            'contract_id': contract.id,
            'partner_id': self.co_buyer.id,
            'role': 'co_buyer', 'share': 50.0,
        })
        self.assertEqual(contract.party_count, 2)

    def test_share_must_be_a_percentage(self):
        contract = self._contract()
        with self.assertRaises(ValidationError):
            self.env['realestate.sale.contract.party'].create({
                'contract_id': contract.id,
                'partner_id': self.co_buyer.id,
                'role': 'co_buyer', 'share': 150.0,
            })


@tagged('post_install', '-at_install', 'atmta_developer')
class TestContractMultiAsset(ContractCommon):
    """Phase 23 — apartment plus parking, without breaking anything."""

    def test_property_id_is_still_the_primary_unit(self):
        """Checks, Handover, the API and the Portal all read it."""
        field = self.Contract._fields['property_id']
        self.assertTrue(field.required,
                        "downstream modules depend on every contract having a "
                        "primary unit")

    def test_accessory_units_are_additive(self):
        contract = self._contract()
        parking = self._make_units(count=1, prefix='PK')
        self.env['realestate.sale.contract.property.line'].create([
            {'contract_id': contract.id, 'property_id': self.unit.id,
             'is_primary': True, 'amount': 900000.0},
            {'contract_id': contract.id, 'property_id': parking.id,
             'amount': 100000.0},
        ])
        self.assertEqual(contract.property_line_count, 2)
        self.assertEqual(contract.property_id, self.unit,
                         "the primary unit reference must not move")

    def test_a_unit_cannot_be_on_the_same_contract_twice(self):
        contract = self._contract()
        self.env['realestate.sale.contract.property.line'].create({
            'contract_id': contract.id, 'property_id': self.unit.id})
        with self.assertRaises(Exception):
            self.env['realestate.sale.contract.property.line'].create({
                'contract_id': contract.id, 'property_id': self.unit.id})
            self.env.flush_all()


@tagged('post_install', '-at_install', 'atmta_developer')
class TestInstallmentPaymentState(ContractCommon):
    """Phase 26 — 'paid' means reconciled, not 'a payment exists'."""

    def setUp(self):
        super().setUp()
        self.contract = self._contract(payment_plan_id=self._plan().id)
        self.contract.action_sign()
        self.first = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]

    def test_unposted_instalment_is_pending_and_fully_residual(self):
        self.assertEqual(self.first.state, 'pending')
        self.assertEqual(self.first.residual_amount, self.first.current_amount)
        self.assertEqual(self.first.paid_amount, 0.0)

    def test_partial_payment_reads_as_partial(self):
        """Phase 26's worked example, at the plan's scale.

        Down payment is 200,000. Pay 80,000 and 120,000 must remain owed —
        the obligation is NOT settled.
        """
        self.first.action_generate_invoice()
        invoice = self.first.move_id
        self.assertEqual(self.first.state, 'invoiced')
        self.assertEqual(self.first.invoiced_amount, 200000.0)

        self._pay(invoice, 80000.0)
        self.first.invalidate_recordset()
        self.assertEqual(self.first.paid_amount, 80000.0)
        self.assertEqual(self.first.invoiced_amount, 200000.0)
        self.assertEqual(self.first.residual_amount, 120000.0)
        self.assertEqual(
            self.first.state, 'partially_paid',
            "an instalment with money still owed on it is not paid")

        self._pay(invoice, 120000.0)
        self.first.invalidate_recordset()
        self.assertEqual(self.first.residual_amount, 0.0)
        self.assertEqual(self.first.state, 'paid')

    def _pay(self, invoice, amount):
        """Register a partial payment through Odoo's own machinery."""
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': invoice.partner_id.id,
            'amount': amount,
            'currency_id': invoice.currency_id.id,
            'company_id': invoice.company_id.id,
            'date': fields.Date.context_today(self.env['account.payment']),
        })
        payment.action_post()
        lines = (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda l: l.account_id.account_type == 'asset_receivable'
            and not l.reconciled)
        lines.reconcile()

    def test_invoiced_amount_is_untaxed_so_it_matches_the_schedule(self):
        """A 1,000,000 plan does not become 1,150,000 because VAT applies."""
        tax = self.env['account.tax'].create({
            'name': 'Test VAT 15%', 'amount': 15.0,
            'amount_type': 'percent', 'type_tax_use': 'sale',
            'company_id': self.company.id,
        })
        self.unit.product_variant_id.taxes_id = [(6, 0, tax.ids)]
        second = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[1]
        second.action_generate_invoice()
        self.assertEqual(
            second.invoiced_amount, second.current_amount,
            "the obligation is billed in full even though the invoice total "
            "carries tax on top")
        self.assertGreater(second.move_id.amount_total, second.invoiced_amount)

    def test_a_credit_note_does_not_count_as_payment(self):
        """`reversed` was treated as paid; it means the invoice was cancelled."""
        self.first.action_generate_invoice()
        invoice = self.first.move_id
        reversal = invoice._reverse_moves([{
            'date': fields.Date.context_today(invoice),
            'invoice_date': fields.Date.context_today(invoice),
        }], cancel=True)
        self.assertTrue(reversal)
        self.first.invalidate_recordset()
        self.assertNotEqual(
            self.first.state, 'paid',
            "a credit note cancels the invoice — it does not settle the "
            "obligation")
        self.assertEqual(
            self.first.state, 'pending',
            "the obligation returns to un-invoiced and still has to be billed")
        self.assertEqual(self.first.residual_amount, self.first.current_amount)
        self.assertEqual(self.first.paid_amount, 0.0)

    def test_overdue_is_derived_from_the_due_date(self):
        self.first.action_generate_invoice()
        self.first.date_due = fields.Date.context_today(self.first) - timedelta(days=45)
        self.first.invalidate_recordset()
        self.assertEqual(self.first.state, 'overdue')
        self.assertEqual(self.first.days_overdue, 45)
        self.assertEqual(self.first.aging_bucket, 'bucket_60')

    def test_aging_buckets(self):
        today = fields.Date.context_today(self.first)
        self.first.action_generate_invoice()
        for days, bucket in ((-5, 'not_due'), (10, 'current'), (45, 'bucket_60'),
                             (75, 'bucket_90'), (100, 'bucket_120'),
                             (200, 'bucket_over')):
            self.first.date_due = today - timedelta(days=days)
            self.first.invalidate_recordset()
            self.assertEqual(self.first.aging_bucket, bucket,
                             "%s days overdue should be %s" % (days, bucket))

    def test_invoice_is_dated_today_not_on_the_due_date(self):
        """V1 dated the invoice in the future, distorting every period between."""
        self.first.date_due = fields.Date.context_today(self.first) + timedelta(days=200)
        self.first.action_generate_invoice()
        self.assertEqual(
            self.first.move_id.invoice_date,
            fields.Date.context_today(self.first))
        self.assertEqual(self.first.move_id.invoice_date_due, self.first.date_due)

    def test_an_instalment_cannot_be_invoiced_twice(self):
        self.first.action_generate_invoice()
        with self.assertRaises(UserError):
            self.first.action_generate_invoice()

    def test_a_paid_instalment_cannot_be_cancelled(self):
        self.first.action_generate_invoice()
        self._pay(self.first.move_id, 200000.0)
        self.first.invalidate_recordset()
        with self.assertRaises(UserError) as err:
            self.first.action_cancel_installment()
        self.assertIn('must stay accounted for', str(err.exception))

    def test_an_unpaid_instalment_can_be_cancelled(self):
        last = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[-1]
        last.action_cancel_installment(reason='Restructured')
        self.assertEqual(last.state, 'cancelled')

    def test_v1_amount_field_still_mirrors_the_obligation(self):
        """`real_estate_checks` and the API read `amount`."""
        self.assertEqual(self.first.amount, self.first.current_amount)


@tagged('post_install', '-at_install', 'atmta_developer')
class TestContractFinancialRollup(ContractCommon):
    """The contract's own view of billing and collection."""

    def setUp(self):
        super().setUp()
        self.contract = self._contract(payment_plan_id=self._plan().id)
        self.contract.action_sign()

    def test_billing_state_tracks_invoicing(self):
        self.assertEqual(self.contract.billing_state, 'not_invoiced')
        self.contract.installment_ids[0].action_generate_invoice()
        self.contract.invalidate_recordset()
        self.assertEqual(self.contract.billing_state, 'partially_invoiced')

    def test_collection_state_tracks_arrears(self):
        self.assertEqual(self.contract.collection_state, 'current')
        overdue = self.contract.installment_ids[0]
        overdue.date_due = fields.Date.context_today(self.contract) - timedelta(days=10)
        self.contract.invalidate_recordset()
        self.assertEqual(self.contract.collection_state, 'overdue')
        self.assertEqual(self.contract.overdue_amount, overdue.current_amount)

    def test_scheduled_amount_equals_the_price(self):
        self.assertEqual(self.contract.scheduled_amount, 1000000.0)

    def test_next_due_date_is_the_earliest_outstanding(self):
        due = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0].date_due
        self.assertEqual(self.contract.next_due_date, due)

    def test_cancelled_instalments_leave_the_rollup(self):
        target = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[-1]
        amount = target.current_amount
        target.action_cancel_installment()
        self.contract.invalidate_recordset()
        self.assertEqual(self.contract.scheduled_amount, 1000000.0 - amount)


@tagged('post_install', '-at_install', 'atmta_developer')
class TestChecksCompatibility(ContractCommon):
    """Phase 36 — the contract shape `real_estate_checks` reads.

    Checks is currently unusable for developer sales because nothing creates
    instalments. These assertions pin the fields its wizard and its check model
    actually read, so restoring generation does not accidentally rename them.
    """

    def test_the_fields_checks_reads_all_exist(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()

        self.assertTrue(contract.installment_ids,
                        "the bulk PDC wizard reads contract.installment_ids — "
                        "empty since 0.2, which is why cheques could not be "
                        "issued against a sale contract")
        for field in ('date_due', 'amount', 'move_id', 'state', 'kind'):
            self.assertIn(field, self.Installment._fields,
                          "real_estate_checks reads installment.%s" % field)
        for installment in contract.installment_ids:
            self.assertTrue(installment.date_due)
            self.assertGreater(installment.amount, 0)

    def test_installments_sort_the_way_the_wizard_expects(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        ordered = contract.installment_ids.sorted(
            key=lambda i: (i.date_due or date.today(), i.id))
        self.assertEqual(
            [i.date_due for i in ordered],
            sorted(i.date_due for i in contract.installment_ids))
