"""Proration arithmetic (Phase 13).

Pure functions, so these are fast unit tests with no ORM at all. They exist
because proration is where rent disputes come from: every boundary condition
here corresponds to a real argument with a tenant.
"""

from datetime import date

from odoo.tests.common import BaseCase, tagged

from odoo.addons.atmta_real_estate.models import proration


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestProration(BaseCase):

    # ---------------- Day counting ----------------
    def test_days_inclusive_counts_both_ends(self):
        # 1 Jan to 31 Jan is 31 days of occupancy, not 30.
        self.assertEqual(
            proration.days_inclusive(date(2026, 1, 1), date(2026, 1, 31)), 31)

    def test_days_inclusive_single_day(self):
        self.assertEqual(
            proration.days_inclusive(date(2026, 1, 5), date(2026, 1, 5)), 1)

    def test_days_inclusive_reversed_is_zero(self):
        self.assertEqual(
            proration.days_inclusive(date(2026, 1, 31), date(2026, 1, 1)), 0)

    def test_thirty_day_february_bills_as_thirty(self):
        # The point of the 30/360 convention: February and July cost the same.
        feb = proration.thirty_day_count(date(2026, 2, 1), date(2026, 2, 28))
        jul = proration.thirty_day_count(date(2026, 7, 1), date(2026, 7, 31))
        self.assertEqual(feb, 30)
        self.assertEqual(jul, 30)

    # ---------------- Factors ----------------
    def test_full_period_is_always_one(self):
        for method in ('actual', 'thirty_day', 'none', 'full'):
            self.assertEqual(
                proration.proration_factor(
                    method, date(2026, 1, 1), date(2026, 1, 31),
                    date(2026, 1, 1), date(2026, 1, 31)),
                1.0, "%s should charge a whole period in full" % method)

    def test_no_overlap_is_zero(self):
        for method in ('actual', 'thirty_day', 'full'):
            self.assertEqual(
                proration.proration_factor(
                    method, date(2026, 1, 1), date(2026, 1, 31),
                    date(2026, 3, 1), date(2026, 3, 31)),
                0.0, "%s should charge nothing outside the liable window" % method)

    def test_actual_half_month(self):
        # Moving in on the 16th of a 31-day month: 16 days of 31.
        factor = proration.proration_factor(
            'actual', date(2026, 1, 1), date(2026, 1, 31),
            date(2026, 1, 16), date(2026, 1, 31))
        self.assertAlmostEqual(factor, 16 / 31, places=6)

    def test_thirty_day_half_month(self):
        factor = proration.proration_factor(
            'thirty_day', date(2026, 1, 1), date(2026, 1, 31),
            date(2026, 1, 16), date(2026, 1, 31))
        # 30-day convention: day 16 to day 30 inclusive = 15 of 30.
        self.assertAlmostEqual(factor, 0.5, places=6)

    def test_none_charges_full_period_for_any_overlap(self):
        factor = proration.proration_factor(
            'none', date(2026, 1, 1), date(2026, 1, 31),
            date(2026, 1, 30), date(2026, 1, 31))
        self.assertEqual(factor, 1.0)

    def test_full_charges_nothing_for_a_partial_period(self):
        factor = proration.proration_factor(
            'full', date(2026, 1, 1), date(2026, 1, 31),
            date(2026, 1, 16), date(2026, 1, 31))
        self.assertEqual(factor, 0.0)

    def test_mid_month_termination(self):
        # Leaving on the 10th of a 31-day month: 10 days of 31.
        factor = proration.proration_factor(
            'actual', date(2026, 1, 1), date(2026, 1, 31),
            date(2026, 1, 1), date(2026, 1, 10))
        self.assertAlmostEqual(factor, 10 / 31, places=6)

    def test_factor_never_exceeds_one(self):
        # A liable window wider than the period must not over-charge.
        factor = proration.proration_factor(
            'actual', date(2026, 1, 1), date(2026, 1, 31),
            date(2025, 1, 1), date(2027, 1, 1))
        self.assertEqual(factor, 1.0)

    def test_invalid_period_is_zero(self):
        self.assertEqual(
            proration.proration_factor(
                'actual', date(2026, 1, 31), date(2026, 1, 1),
                date(2026, 1, 1), date(2026, 1, 31)),
            0.0)

    # ---------------- Money ----------------
    def test_prorate_rounds_to_currency_precision(self):
        amount = proration.prorate(
            1000.0, 'actual', date(2026, 1, 1), date(2026, 1, 31),
            date(2026, 1, 16), date(2026, 1, 31))
        self.assertEqual(amount, round(1000.0 * 16 / 31, 2))

    def test_describe_is_empty_for_a_full_period(self):
        self.assertEqual(
            proration.describe(
                'actual', date(2026, 1, 1), date(2026, 1, 31),
                date(2026, 1, 1), date(2026, 1, 31)),
            '')

    def test_describe_explains_a_partial_period(self):
        note = proration.describe(
            'actual', date(2026, 1, 1), date(2026, 1, 31),
            date(2026, 1, 16), date(2026, 1, 31))
        self.assertIn('16/31', note)
        self.assertIn('2026-01-16', note)
