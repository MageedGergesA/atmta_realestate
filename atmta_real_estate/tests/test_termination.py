"""Lease termination and settlement (Phase 19)."""

from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLeaseTermination(LeaseCase):

    def setUp(self):
        super().setUp()
        self.start = self.today.replace(day=1) - relativedelta(months=3)
        self.end = self.start + relativedelta(years=1) - relativedelta(days=1)
        self.lease = self.make_lease(
            start=self.start, end=self.end, rent=1000.0,
            use_billing_engine=True)
        self.activate(self.lease)
        self.lease.action_generate_billing_schedule()
        self.cutoff = self.today.replace(day=1) + relativedelta(months=1)

    def _termination(self, **vals):
        base = {
            'contract_id': self.lease.id,
            'requested_end_date': self.cutoff,
            'effective_date': self.cutoff,
            'reason': 'tenant_notice',
            'requested_by': 'tenant',
        }
        base.update(vals)
        return self.env['realestate.contract.termination'].create(base)

    # ------------------------------------------------------------------
    # Notice
    # ------------------------------------------------------------------
    def test_notice_moves_the_lease_to_notice(self):
        termination = self._termination()
        termination.action_give_notice()
        self.assertEqual(termination.state, 'notice_given')
        self.assertEqual(self.lease.lifecycle_state, 'notice')

    def test_early_termination_is_flagged(self):
        termination = self._termination()
        self.assertTrue(termination.is_early)

    def test_notice_shortfall_drives_a_suggested_penalty(self):
        termination = self._termination(
            request_date=self.today,
            effective_date=self.today + relativedelta(days=20))
        termination.action_give_notice()
        self.assertGreater(termination.notice_shortfall_days, 0)
        self.assertGreater(termination.penalty_amount, 0.0)

    def test_full_notice_suggests_no_penalty(self):
        termination = self._termination(
            request_date=self.today,
            effective_date=self.today + relativedelta(days=90))
        termination.action_give_notice()
        self.assertEqual(termination.notice_shortfall_days, 0)
        self.assertAlmostEqual(termination.penalty_amount, 0.0, places=2)

    def test_cancelling_notice_reactivates_the_lease(self):
        termination = self._termination()
        termination.action_give_notice()
        termination.action_cancel()
        self.assertEqual(self.lease.lifecycle_state, 'active')

    # ------------------------------------------------------------------
    # Approval
    # ------------------------------------------------------------------
    def test_waiver_requires_a_reason(self):
        termination = self._termination()
        termination.action_give_notice()
        termination.penalty_waived = termination.penalty_amount
        with self.assertRaises(UserError):
            termination.action_approve()

    def test_waiver_records_the_approver(self):
        termination = self._termination()
        termination.action_give_notice()
        termination.write({
            'penalty_waived': termination.penalty_amount,
            'waiver_reason': 'Goodwill; tenant is taking another unit.',
        })
        termination.action_approve()
        self.assertEqual(termination.waiver_approver_id, self.env.user)

    # ------------------------------------------------------------------
    # Settlement
    # ------------------------------------------------------------------
    def test_settlement_cancels_uninvoiced_future_obligations(self):
        termination = self._termination()
        termination.action_give_notice()
        termination.action_approve()
        before = len(self.lease.contract_payment_ids)
        termination.action_settle()
        self.lease.invalidate_recordset()
        self.assertLess(len(self.lease.contract_payment_ids), before)
        remaining = self.lease.contract_payment_ids.filtered(
            lambda o: o.period_start and o.period_start > self.cutoff)
        self.assertFalse(remaining)

    def test_settlement_credits_rather_than_deletes_invoiced_periods(self):
        """Posted invoices must never be unlinked."""
        future = self.lease.contract_payment_ids.filtered(
            lambda o: o.period_start and o.period_start > self.cutoff
        ).sorted('period_start')[0]
        future._create_invoices()
        invoice = future.move_id
        self.assertEqual(invoice.state, 'posted')

        termination = self._termination()
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()

        self.assertTrue(invoice.exists(), "The invoice must still exist.")
        self.assertEqual(invoice.state, 'posted')
        self.assertTrue(termination.credit_note_ids)
        self.assertTrue(all(
            m.move_type == 'out_refund' for m in termination.credit_note_ids))

    def test_terminal_period_is_reprorated_not_deleted(self):
        termination = self._termination()
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()
        straddling = self.lease.contract_payment_ids.filtered(
            lambda o: o.period_start and o.period_end
            and o.period_start <= self.cutoff <= o.period_end)
        self.assertTrue(straddling, "The final part-period must survive.")
        self.assertLess(straddling[0].amount, 1000.0)

    def _taxed_straddling_invoice(self, effective_date):
        """Invoice the period the termination falls in, with a sales tax.

        The tax goes on whatever product the billing engine will put on the
        rent line, so the invoice carries tax the way a real one does.
        """
        straddling = self.lease.contract_payment_ids.filtered(
            lambda o: o.period_start and o.period_end
            and o.period_start <= effective_date <= o.period_end)[0]
        tax = self.env['account.tax'].search([
            ('type_tax_use', '=', 'sale'), ('amount_type', '=', 'percent'),
            ('amount', '>', 0), ('company_id', '=', self.company.id),
        ], limit=1)
        if not tax:
            tax = self.env['account.tax'].create({
                'name': 'W26 Sales Tax', 'amount': 10.0,
                'amount_type': 'percent', 'type_tax_use': 'sale',
                'company_id': self.company.id,
            })
        product = straddling._rent_product()
        if not product:
            product = self.env['product.product'].create({
                'name': 'W26 Rent', 'type': 'service'})
            self.company.re_rent_product_id = product.id
        product.taxes_id = [(6, 0, tax.ids)]
        straddling._create_invoices()
        invoice = straddling.move_id
        self.assertEqual(invoice.state, 'posted')
        self.assertTrue(invoice.amount_tax, "the rent invoice must carry tax")
        return straddling, invoice

    def test_terminal_period_credit_mirrors_the_rent_tax(self):
        """A mid-period credit reverses the tax on the part it reduces."""
        straddling, invoice = self._taxed_straddling_invoice(self.cutoff)
        rent_line = invoice.invoice_line_ids.filtered(
            lambda l: self.currency.compare_amounts(
                l.price_unit, straddling.gross_rent) == 0)[:1]
        self.assertTrue(rent_line.tax_ids)

        termination = self._termination()
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()

        credits = termination.credit_note_ids.filtered(
            lambda m: m.reversed_entry_id == invoice)
        self.assertTrue(credits, "the straddling invoice must be credited")
        credit_lines = credits.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product')
        self.assertEqual(credit_lines.tax_ids, rent_line.tax_ids)
        self.assertTrue(credits.amount_tax, "the credit reverses tax")
        self.assertLess(sum(credits.mapped('amount_untaxed')),
                        rent_line.price_subtotal,
                        "only the unoccupied part is credited")

    def test_ending_on_the_last_day_of_the_period_credits_nothing(self):
        """No rent is reduced, so no tax may be credited either."""
        period_end = self.lease.contract_payment_ids.filtered(
            lambda o: o.period_start and o.period_end
            and o.period_start <= self.cutoff <= o.period_end)[0].period_end
        straddling, invoice = self._taxed_straddling_invoice(period_end)

        termination = self._termination(
            requested_end_date=period_end, effective_date=period_end)
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()

        self.assertFalse(
            termination.credit_note_ids.filtered(
                lambda m: m.reversed_entry_id == invoice),
            "a full period was occupied; nothing is owed back on it")

    def test_final_invoice_carries_the_penalty(self):
        termination = self._termination(
            request_date=self.today,
            effective_date=self.today + relativedelta(days=10))
        termination.action_give_notice()
        termination.write({'utility_settlement': 300.0})
        termination.action_approve()
        termination.action_settle()
        self.assertTrue(termination.final_invoice_id)
        self.assertEqual(termination.final_invoice_id.move_type, 'out_invoice')
        self.assertEqual(termination.final_invoice_id.state, 'posted')

    def test_completion_closes_the_lease_and_opens_a_unit_turn(self):
        termination = self._termination()
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()
        termination.action_complete()
        self.assertEqual(termination.state, 'completed')
        self.assertEqual(self.lease.lifecycle_state, 'terminated')
        self.assertEqual(self.lease.end_date, self.cutoff)
        turns = self.env['realestate.unit.turn'].search(
            [('contract_id', '=', self.lease.id)])
        self.assertTrue(turns)

    def test_completion_does_not_make_the_unit_available(self):
        """A vacated unit is not a lettable unit until the turn says so."""
        termination = self._termination()
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()
        termination.action_complete()
        turn = self.env['realestate.unit.turn'].search(
            [('contract_id', '=', self.lease.id)], limit=1)
        turn.write({'cleaning_required': True, 'repair_required': True})
        turn.action_start()
        self.unit_a.invalidate_recordset()
        self.assertFalse(self.unit_a.is_available_for_lease)

    def test_normal_expiry_ends_rather_than_terminates(self):
        termination = self._termination(
            effective_date=self.end, requested_end_date=self.end,
            reason='expiry')
        self.assertFalse(termination.is_early)
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()
        termination.action_complete()
        self.assertEqual(self.lease.lifecycle_state, 'ended')

    def test_billing_history_survives_termination(self):
        paid = self.lease.contract_payment_ids.sorted('period_start')[0]
        paid._create_invoices()
        move_id = paid.move_id.id
        termination = self._termination()
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()
        termination.action_complete()
        self.assertTrue(self.env['account.move'].browse(move_id).exists())
        self.assertTrue(paid.exists())
