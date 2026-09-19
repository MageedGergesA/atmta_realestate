"""Legacy price rules carry over to rent escalations and incentives.

Only the retired legacy schedule generator applied
``realestate.contract.increment.rule``. These tests pin the conversion: a lease
keeps the rent it was billed, rules the engines cannot represent are flagged
rather than guessed, and running the conversion twice changes nothing.
"""

from dateutil.relativedelta import relativedelta

from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLegacyPriceRuleConversion(LeaseCase):

    def setUp(self):
        super().setUp()
        self.start = self.today.replace(day=1)
        self.lease = self.make_lease(
            start=self.start,
            end=self.start + relativedelta(years=3, days=-1),
            rent=1000.0)

    def _rule(self, start_month, value, kind='percent', priority=10, duration=0, discount=False):
        return self.env['realestate.contract.increment.rule'].create({
            'start_month': start_month,
            'increase_type': kind,
            'increase_value': value,
            'priority': priority,
            'duration_months': duration,
            'discount': discount,
        })

    def _convert(self, lease=None):
        return (lease or self.lease)._convert_legacy_price_rules()

    # ------------------------------------------------------------------
    # Increases that convert
    # ------------------------------------------------------------------
    def test_a_permanent_percentage_increase_becomes_an_escalation(self):
        rule = self._rule(12, 5.0)
        self.lease.increment_rule_ids = rule
        report = self._convert()
        self.assertEqual(report['escalations'], 1)
        escalation = self.lease.escalation_rule_ids
        self.assertEqual(escalation.effective_date, self.start + relativedelta(months=12))
        self.assertEqual(escalation.escalation_type, 'percentage')
        self.assertAlmostEqual(escalation.percentage, 5.0)
        month_13 = self.start + relativedelta(months=13)
        self.assertAlmostEqual(self.lease._rent_on(month_13),
                               self.lease._legacy_rent_at_month(13, rule), places=2)
        self.assertAlmostEqual(self.lease._rent_on(month_13), 1050.0, places=2)

    def test_a_permanent_fixed_increase_becomes_an_escalation(self):
        self.lease.increment_rule_ids = self._rule(24, 200.0, kind='fixed')
        self._convert()
        escalation = self.lease.escalation_rule_ids
        self.assertEqual(escalation.escalation_type, 'fixed')
        self.assertAlmostEqual(escalation.fixed_amount, 200.0)
        self.assertAlmostEqual(self.lease._rent_on(self.start + relativedelta(months=25)), 1200.0, places=2)

    def test_chained_increases_keep_the_billed_rent(self):
        rules = self._rule(12, 5.0) | self._rule(24, 7.0)
        self.lease.increment_rule_ids = rules
        self._convert()
        self.assertEqual(len(self.lease.escalation_rule_ids), 2)
        month_25 = self.start + relativedelta(months=25)
        self.assertAlmostEqual(self.lease._rent_on(month_25),
                               self.lease._legacy_rent_at_month(25, rules), places=2)

    # ------------------------------------------------------------------
    # Increases that are flagged or skipped
    # ------------------------------------------------------------------
    def test_an_increase_from_the_lease_start_is_flagged(self):
        self.lease.increment_rule_ids = self._rule(0, 5.0)
        report = self._convert()
        self.assertFalse(self.lease.escalation_rule_ids)
        self.assertIn(self.lease.display_name, report['flagged'])

    def test_a_temporary_increase_is_flagged(self):
        self.lease.increment_rule_ids = self._rule(6, 200.0, kind='fixed', duration=6)
        report = self._convert()
        self.assertFalse(self.lease.escalation_rule_ids)
        self.assertIn(self.lease.display_name, report['flagged'])

    def test_increases_whose_order_changes_the_rent_are_flagged(self):
        """Fixed first by priority, percentage first by date: different rent."""
        self.lease.increment_rule_ids = (
            self._rule(12, 10.0, kind='percent', priority=10)
            | self._rule(24, 300.0, kind='fixed', priority=5))
        report = self._convert()
        self.assertFalse(self.lease.escalation_rule_ids,
                         "nothing is converted when the result would differ")
        self.assertIn(self.lease.display_name, report['flagged'])

    def test_one_unrepresentable_increase_blocks_the_whole_set(self):
        """A valid increase is not converted beside one that cannot be.

        Converting only part of a lease's increases would misstate the rent it
        was billed, so the set converts all-or-nothing.
        """
        self.lease.increment_rule_ids = self._rule(0, 5.0) | self._rule(12, 5.0)
        report = self._convert()
        self.assertFalse(self.lease.escalation_rule_ids)
        self.assertIn(self.lease.display_name, report['flagged'])

    def test_an_increase_after_the_lease_ends_is_skipped(self):
        short = self.make_lease(
            prop=self.unit_b, start=self.start,
            end=self.start + relativedelta(years=1, days=-1), rent=800.0)
        short.increment_rule_ids = self._rule(12, 5.0)
        report = self._convert(short)
        self.assertFalse(short.escalation_rule_ids)
        self.assertIn(short.display_name, report['skipped'])
        self.assertNotIn(short.display_name, report['flagged'])

    # ------------------------------------------------------------------
    # Discounts
    # ------------------------------------------------------------------
    def test_a_limited_discount_becomes_an_incentive_for_its_window(self):
        self.lease.discount_rule_ids = self._rule(0, 10.0, duration=3, discount=True)
        report = self._convert()
        self.assertEqual(report['incentives'], 1)
        incentive = self.lease.incentive_ids
        self.assertEqual(incentive.incentive_type, 'percent_discount')
        self.assertAlmostEqual(incentive.percentage, 10.0)
        self.assertEqual(incentive.start_date, self.start)
        self.assertEqual(incentive.end_date, self.start + relativedelta(months=3, days=-1))

    def test_an_open_ended_discount_runs_to_the_lease_end(self):
        self.lease.discount_rule_ids = self._rule(6, 150.0, kind='fixed', discount=True)
        self._convert()
        incentive = self.lease.incentive_ids
        self.assertEqual(incentive.incentive_type, 'fixed_discount')
        self.assertAlmostEqual(incentive.amount, 150.0)
        self.assertEqual(incentive.start_date, self.start + relativedelta(months=6))
        self.assertEqual(incentive.end_date, self.lease.end_date)

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------
    def test_running_the_conversion_twice_creates_nothing_new(self):
        self.lease.increment_rule_ids = self._rule(12, 5.0)
        self.lease.discount_rule_ids = self._rule(0, 10.0, duration=3, discount=True)
        self._convert()
        again = self._convert()
        self.assertEqual((again['escalations'], again['incentives']), (0, 0))
        self.assertEqual(len(self.lease.escalation_rule_ids), 1)
        self.assertEqual(len(self.lease.incentive_ids), 1)

    def test_a_closed_lease_is_left_alone(self):
        self.lease.increment_rule_ids = self._rule(12, 5.0)
        self.lease.action_cancel_lease()
        self._convert()
        self.assertFalse(self.lease.escalation_rule_ids)
