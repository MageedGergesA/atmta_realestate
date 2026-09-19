"""Rent escalation (Phase 11)."""

from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestRentEscalation(LeaseCase):

    def setUp(self):
        super().setUp()
        self.start = self.today.replace(day=1)
        self.lease = self.make_lease(
            start=self.start,
            end=self.start + relativedelta(years=3) - relativedelta(days=1),
            rent=500000.0)

    def _escalation(self, months, **vals):
        base = {
            'contract_id': self.lease.id,
            'effective_date': self.start + relativedelta(months=months),
            'escalation_type': 'percentage',
            'percentage': 10.0,
        }
        base.update(vals)
        return self.env['realestate.rent.escalation.rule'].create(base)

    # ------------------------------------------------------------------
    # Types
    # ------------------------------------------------------------------
    def test_percentage_escalation(self):
        rule = self._escalation(12, percentage=7.0)
        self.assertAlmostEqual(rule.base_amount, 500000.0, places=2)
        self.assertAlmostEqual(rule.resulting_amount, 535000.0, places=2)
        self.assertAlmostEqual(rule.increase_pct, 7.0, places=4)

    def test_fixed_escalation(self):
        rule = self._escalation(
            12, escalation_type='fixed', percentage=0.0, fixed_amount=50000.0)
        self.assertAlmostEqual(rule.resulting_amount, 550000.0, places=2)

    def test_scheduled_amounts(self):
        """Year 1 = 500k, Year 2 = 550k, Year 3 = 605k."""
        y2 = self._escalation(
            12, escalation_type='scheduled_amount', percentage=0.0,
            scheduled_amount=550000.0)
        y3 = self._escalation(
            24, escalation_type='scheduled_amount', percentage=0.0,
            scheduled_amount=605000.0)
        self.assertAlmostEqual(y2.resulting_amount, 550000.0, places=2)
        self.assertAlmostEqual(y3.base_amount, 550000.0, places=2)
        self.assertAlmostEqual(y3.resulting_amount, 605000.0, places=2)

    def test_index_linked_stores_the_basis(self):
        rule = self._escalation(
            12, escalation_type='index', percentage=0.0,
            scheduled_amount=530000.0, index_basis='CPI Egypt Jan 2027')
        self.assertEqual(rule.index_basis, 'CPI Egypt Jan 2027')
        self.assertAlmostEqual(rule.resulting_amount, 530000.0, places=2)

    def test_escalations_chain(self):
        self._escalation(12, percentage=10.0)
        self._escalation(24, percentage=10.0)
        rules = self.lease.escalation_rule_ids.sorted('effective_date')
        self.assertAlmostEqual(rules[0].resulting_amount, 550000.0, places=2)
        self.assertAlmostEqual(rules[1].base_amount, 550000.0, places=2)
        self.assertAlmostEqual(rules[1].resulting_amount, 605000.0, places=2)

    # ------------------------------------------------------------------
    # Date boundaries -- what rent applies when
    # ------------------------------------------------------------------
    def test_rent_on_before_first_escalation(self):
        self._escalation(12, percentage=10.0)
        self.assertAlmostEqual(self.lease._rent_on(self.start), 500000.0, places=2)

    def test_rent_on_the_effective_date_itself(self):
        """The new rent applies FROM the effective date, inclusive."""
        rule = self._escalation(12, percentage=10.0)
        self.assertAlmostEqual(
            self.lease._rent_on(rule.effective_date), 550000.0, places=2)

    def test_rent_on_the_day_before(self):
        rule = self._escalation(12, percentage=10.0)
        day_before = rule.effective_date - relativedelta(days=1)
        self.assertAlmostEqual(self.lease._rent_on(day_before), 500000.0, places=2)

    def test_rent_on_is_deterministic(self):
        """Called twice, same answer -- this is what makes regeneration safe."""
        self._escalation(12, percentage=10.0)
        first = self.lease._rent_on(self.start + relativedelta(months=18))
        second = self.lease._rent_on(self.start + relativedelta(months=18))
        self.assertEqual(first, second)

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    def test_escalation_before_lease_start_rejected(self):
        with self.assertRaises(ValidationError):
            self._escalation(0)

    def test_escalation_after_lease_end_rejected(self):
        with self.assertRaises(ValidationError):
            self._escalation(48)

    def test_two_escalations_on_the_same_date_rejected(self):
        from psycopg2 import IntegrityError

        from odoo.tools import mute_logger
        self._escalation(12)
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.cr.savepoint():
                self._escalation(12)
                self.env.flush_all()

    def test_percentage_type_needs_a_percentage(self):
        with self.assertRaises(ValidationError):
            self._escalation(12, percentage=0.0)

    # ------------------------------------------------------------------
    # Generator
    # ------------------------------------------------------------------
    def test_generator_creates_dated_rules(self):
        created = self.lease.action_generate_escalations(
            percentage=7.0, every_months=12)
        self.assertEqual(len(created), 2)  # 3-year lease -> years 2 and 3
        self.assertAlmostEqual(created[0].percentage, 7.0, places=4)

    def test_generator_never_duplicates(self):
        self.lease.action_generate_escalations(percentage=7.0, every_months=12)
        self.lease.action_generate_escalations(percentage=7.0, every_months=12)
        self.assertEqual(len(self.lease.escalation_rule_ids), 2)

    def test_next_escalation_summary(self):
        self.lease.action_generate_escalations(percentage=7.0, every_months=12)
        self.lease.invalidate_recordset()
        self.assertTrue(self.lease.next_escalation_date)
        self.assertAlmostEqual(self.lease.next_escalation_pct, 7.0, places=2)

    # ------------------------------------------------------------------
    # Interaction with billing
    # ------------------------------------------------------------------
    def test_schedule_uses_the_escalated_rent(self):
        self.lease.use_billing_engine = True
        self.lease.billing_frequency = 'annual'
        self._escalation(12, percentage=10.0)
        self.activate(self.lease)
        self.lease.action_generate_billing_schedule()
        obligations = self.lease.contract_payment_ids.sorted('period_start')
        self.assertAlmostEqual(obligations[0].gross_rent, 500000.0, places=2)
        self.assertAlmostEqual(obligations[1].gross_rent, 550000.0, places=2)
