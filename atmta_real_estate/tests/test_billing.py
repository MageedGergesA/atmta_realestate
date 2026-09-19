"""Billing schedule, invoicing and payment behaviour (Phases 9, 13, 15, 16)."""

from dateutil.relativedelta import relativedelta

from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestBilling(LeaseCase):

    def setUp(self):
        super().setUp()
        self.start = self.today.replace(day=1)
        self.end = self.start + relativedelta(months=12) - relativedelta(days=1)
        self.lease = self.make_lease(
            prop=self.unit_a, start=self.start, end=self.end, rent=1000.0,
            use_billing_engine=True, billing_frequency='monthly')
        self.activate(self.lease)

    # ------------------------------------------------------------------
    # Schedule generation
    # ------------------------------------------------------------------
    def test_monthly_schedule_has_one_obligation_per_month(self):
        self.lease.action_generate_billing_schedule()
        self.assertEqual(len(self.lease.contract_payment_ids), 12)

    def test_obligations_carry_their_period(self):
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('date_due')[0]
        self.assertEqual(first.period_start, self.start)
        self.assertEqual(
            first.period_end,
            self.start + relativedelta(months=1) - relativedelta(days=1))

    def test_regeneration_is_idempotent(self):
        self.lease.action_generate_billing_schedule()
        self.lease.action_generate_billing_schedule()
        self.assertEqual(len(self.lease.contract_payment_ids), 12)

    def test_quarterly_frequency(self):
        self.lease.billing_frequency = 'quarterly'
        self.lease.action_generate_billing_schedule()
        self.assertEqual(len(self.lease.contract_payment_ids), 4)

    def test_due_date_in_arrears(self):
        self.lease.billing_due_rule = 'period_end'
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('period_start')[0]
        self.assertEqual(first.date_due, first.period_end)

    def test_due_offset_is_applied(self):
        self.lease.billing_due_offset_days = 5
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('period_start')[0]
        self.assertEqual(
            first.date_due, first.period_start + relativedelta(days=5))

    # ------------------------------------------------------------------
    # Charges
    # ------------------------------------------------------------------
    def test_fixed_charge_appears_on_every_obligation(self):
        self.env['realestate.contract.charge.rule'].create({
            'contract_id': self.lease.id,
            'name': 'Service Charge',
            'charge_category': 'service_charge',
            'calculation_type': 'fixed',
            'amount': 150.0,
            'frequency': 'monthly',
        })
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('period_start')[0]
        self.assertEqual(first.charge_total, 150.0)
        self.assertEqual(first.amount_total, 1150.0)

    def test_percentage_charge_is_based_on_rent(self):
        self.env['realestate.contract.charge.rule'].create({
            'contract_id': self.lease.id,
            'name': 'Management Fee',
            'charge_category': 'management_fee',
            'calculation_type': 'percentage',
            'percentage': 5.0,
            'frequency': 'monthly',
        })
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('period_start')[0]
        self.assertAlmostEqual(first.charge_total, 50.0, places=2)

    def test_per_sqm_charge_uses_the_leased_area(self):
        self.env['realestate.contract.charge.rule'].create({
            'contract_id': self.lease.id,
            'name': 'Chilled Water',
            'charge_category': 'utilities',
            'calculation_type': 'per_sqm',
            'rate_per_sqm': 2.0,
            'frequency': 'monthly',
        })
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('period_start')[0]
        # 100 m2 * 2.0
        self.assertAlmostEqual(first.charge_total, 200.0, places=2)

    def test_annual_charge_lands_once_a_year(self):
        self.env['realestate.contract.charge.rule'].create({
            'contract_id': self.lease.id,
            'name': 'Insurance',
            'charge_category': 'insurance',
            'calculation_type': 'fixed',
            'amount': 1200.0,
            'frequency': 'annual',
        })
        self.lease.action_generate_billing_schedule()
        charged = self.lease.contract_payment_ids.filtered(
            lambda o: o.charge_total > 0)
        self.assertEqual(len(charged), 1)

    # ------------------------------------------------------------------
    # Proration
    # ------------------------------------------------------------------
    def test_partial_final_period_is_prorated(self):
        mid = self.start + relativedelta(months=2) + relativedelta(days=14)
        lease = self.make_lease(
            prop=self.unit_b, start=self.start, end=mid, rent=3100.0,
            use_billing_engine=True)
        self.activate(lease)
        lease.action_generate_billing_schedule()
        last = lease.contract_payment_ids.sorted('period_start')[-1]
        self.assertLess(last.amount, 3100.0)
        self.assertTrue(last.proration_note)

    # ------------------------------------------------------------------
    # Invoicing and Odoo accounting
    # ------------------------------------------------------------------
    def test_invoice_is_an_outbound_customer_invoice(self):
        """Tenant rent must be a customer invoice, never a vendor bill."""
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        self.assertTrue(first.move_id)
        self.assertEqual(first.move_id.move_type, 'out_invoice')
        self.assertEqual(first.move_id.state, 'posted')

    def test_obligation_reads_its_money_from_the_invoice(self):
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        self.assertEqual(first.amount_invoiced, first.move_id.amount_total)
        self.assertEqual(first.amount_residual, first.move_id.amount_residual)
        self.assertEqual(first.state, 'invoiced')
        self.assertFalse(first.is_settled)

    def test_full_payment_settles_the_obligation(self):
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        self.env['realestate.account.tools'].register_payment(first.move_id)
        first.invalidate_recordset()
        self.assertEqual(first.move_id.payment_state, 'paid')
        self.assertTrue(first.is_settled)
        self.assertEqual(first.state, 'paid')
        self.assertAlmostEqual(first.amount_residual, 0.0, places=2)

    def test_partial_payment_leaves_the_obligation_outstanding(self):
        """The rule the specification is explicit about."""
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        move = first.move_id
        half = move.amount_total / 2.0
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=move.ids,
        ).create({'amount': half})
        wizard._create_payments()
        first.invalidate_recordset()
        self.assertFalse(first.is_settled)
        self.assertEqual(first.state, 'invoiced')
        self.assertAlmostEqual(first.amount_paid, half, places=2)
        self.assertAlmostEqual(first.amount_residual, half, places=2)

    def test_payment_is_inbound(self):
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        payments = self.env['realestate.account.tools'].register_payment(
            first.move_id)
        self.assertTrue(payments)
        self.assertEqual(payments[0].payment_type, 'inbound')
        self.assertEqual(payments[0].partner_type, 'customer')

    def test_generation_never_rewrites_an_invoiced_obligation(self):
        self.lease.action_generate_billing_schedule()
        first = self.lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        original = first.amount
        self.lease.price = 5000.0
        self.lease.action_generate_billing_schedule()
        first.invalidate_recordset()
        self.assertEqual(first.amount, original)

    # ------------------------------------------------------------------
    # Arrears (Phase 16)
    # ------------------------------------------------------------------
    def test_overdue_days_and_bucket(self):
        past = self.today - relativedelta(days=45)
        lease = self.make_lease(
            prop=self.parking, start=past, end=past + relativedelta(years=1),
            rent=500.0, use_billing_engine=True)
        self.activate(lease)
        lease.action_generate_billing_schedule()
        first = lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        first.invalidate_recordset()
        self.assertGreater(first.days_overdue, 30)
        self.assertEqual(first.overdue_bucket, '31_60')
        self.assertEqual(lease.payment_status, 'overdue')

    def test_not_yet_due_is_current(self):
        self.lease.action_generate_billing_schedule()
        future = self.lease.contract_payment_ids.sorted('date_due')[-1]
        self.assertEqual(future.days_overdue, 0)
        self.assertEqual(future.overdue_bucket, 'current')

    def test_billing_status_tracks_progress(self):
        self.assertEqual(self.lease.billing_status, 'not_started')
        self.lease.action_generate_billing_schedule()
        self.lease.invalidate_recordset()
        self.assertEqual(self.lease.billing_status, 'scheduled')
        first = self.lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        self.lease.invalidate_recordset()
        self.assertEqual(self.lease.billing_status, 'partially_invoiced')
