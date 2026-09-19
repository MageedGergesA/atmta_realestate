# -*- coding: utf-8 -*-
"""M3 — payment plan templates, schedule generation, versioning.

Two properties matter more than the rest:

1. **The schedule adds up.** `sum(schedule) == price` exactly, at the
   currency's precision, for every plan and every price. Silent residual drift
   on a ten-year plan is how a customer ends up owing 3 EGP forever.
2. **Months are calendar months.** The audit found the old engine treating a
   month as 30 days, so a 96-month plan drifted to 7.9 years and no instalment
   ever landed on a fixed day. These tests pin that down permanently.
"""

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import DeveloperCommon


@tagged('post_install', '-at_install', 'atmta_developer')
class TestPaymentPlan(DeveloperCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Plan = cls.env['realestate.payment.plan']
        cls.Line = cls.env['realestate.payment.plan.line']

    def _plan(self, **kwargs):
        vals = {
            'name': 'Standard 8 Years',
            'company_id': self.company.id,
            'project_id': self.project.id,
        }
        vals.update(kwargs)
        return self.Plan.create(vals)

    def _line(self, plan, **kwargs):
        vals = {'plan_id': plan.id}
        vals.update(kwargs)
        return self.Line.create(vals)

    def _standard_plan(self):
        """10% down, 32 quarterly instalments, 5% on handover."""
        plan = self._plan()
        self._line(plan, sequence=10, name='Down Payment', kind='down_payment',
                   calculation_type='percent', value=10.0,
                   date_rule='on_booking')
        self._line(plan, sequence=20, name='Instalment', kind='installment',
                   calculation_type='percent', value=2.65625, occurrences=32,
                   interval='quarterly', date_rule='months_after_booking',
                   offset_value=3)
        self._line(plan, sequence=30, name='Handover', kind='handover',
                   calculation_type='residual', date_rule='on_handover')
        return plan

    # ---- totals and validation ----

    def test_percentages_must_reach_one_hundred(self):
        plan = self._plan()
        self._line(plan, kind='down_payment', calculation_type='percent',
                   value=10.0, date_rule='on_booking')
        self._line(plan, kind='installment', calculation_type='percent',
                   value=10.0, occurrences=5,
                   date_rule='months_after_booking', offset_value=1)
        self.assertAlmostEqual(plan.total_percent, 60.0)
        with self.assertRaises(UserError) as err:
            plan.action_activate()
        self.assertIn('60.00%', str(err.exception))
        self.assertIn('Residual', str(err.exception))

    def test_a_residual_line_makes_it_valid(self):
        plan = self._plan()
        self._line(plan, kind='down_payment', calculation_type='percent',
                   value=10.0, date_rule='on_booking')
        self._line(plan, kind='handover', calculation_type='residual',
                   date_rule='months_after_booking', offset_value=48)
        plan.action_activate()
        self.assertEqual(plan.state, 'active')

    def test_only_one_residual_line_is_allowed(self):
        plan = self._plan()
        self._line(plan, kind='installment', calculation_type='residual',
                   date_rule='months_after_booking', offset_value=1)
        self._line(plan, kind='handover', calculation_type='residual',
                   date_rule='months_after_booking', offset_value=2)
        with self.assertRaises(UserError) as err:
            plan.action_activate()
        self.assertIn('ambiguous', str(err.exception))

    def test_fixed_amounts_require_a_residual(self):
        """A fixed amount cannot be checked against 100% without a price."""
        plan = self._plan()
        self._line(plan, kind='booking', calculation_type='fixed_amount',
                   value=50000.0, date_rule='on_booking')
        with self.assertRaises(UserError) as err:
            plan.action_activate()
        self.assertIn('residual', str(err.exception).lower())

    def test_over_allocation_is_refused(self):
        plan = self._plan()
        self._line(plan, kind='down_payment', calculation_type='percent',
                   value=60.0, date_rule='on_booking')
        self._line(plan, kind='installment', calculation_type='percent',
                   value=60.0, date_rule='months_after_booking', offset_value=6)
        self._line(plan, kind='handover', calculation_type='residual',
                   date_rule='months_after_booking', offset_value=12)
        with self.assertRaises(UserError) as err:
            plan.action_activate()
        self.assertIn('negative', str(err.exception))

    def test_empty_plan_cannot_activate(self):
        with self.assertRaises(UserError):
            self._plan().action_activate()

    def test_a_line_must_stand_for_at_least_one_payment(self):
        plan = self._plan()
        with self.assertRaises(ValidationError):
            self._line(plan, kind='installment', calculation_type='percent',
                       value=1.0, occurrences=0,
                       date_rule='months_after_booking')

    def test_absurd_single_payment_percentage_is_rejected(self):
        plan = self._plan()
        with self.assertRaises(ValidationError):
            self._line(plan, kind='installment', calculation_type='percent',
                       value=150.0, date_rule='on_booking')

    def test_fixed_date_rule_needs_a_date(self):
        plan = self._plan()
        with self.assertRaises(ValidationError):
            self._line(plan, kind='installment', calculation_type='percent',
                       value=10.0, date_rule='fixed_date')

    # ---- schedule generation ----

    def test_schedule_sums_exactly_to_the_price(self):
        plan = self._standard_plan()
        plan.action_activate()
        rows = plan._generate_schedule(
            total_price=1000000.0,
            booking_date=date(2026, 1, 15),
            handover_date=date(2034, 1, 15))
        plan._validate_schedule_total(rows, 1000000.0)
        self.assertEqual(sum(r['amount'] for r in rows), 1000000.0)

    def test_schedule_sums_exactly_for_an_awkward_price(self):
        """The case rounding actually breaks: a price that does not divide."""
        plan = self._standard_plan()
        plan.action_activate()
        for price in (1234567.89, 999999.99, 3333333.33, 7777777.77):
            rows = plan._generate_schedule(
                total_price=price,
                booking_date=date(2026, 3, 31),
                handover_date=date(2034, 3, 31))
            plan._validate_schedule_total(rows, price)

    def test_payment_count_is_what_the_plan_says(self):
        plan = self._standard_plan()
        plan.action_activate()
        rows = plan._generate_schedule(
            total_price=1000000.0, booking_date=date(2026, 1, 15),
            handover_date=date(2034, 1, 15))
        self.assertEqual(len(rows), 1 + 32 + 1)

    def test_months_are_calendar_months_not_thirty_days(self):
        """The regression the audit found (§4.7).

        The old engine expressed everything as `days_after` with a month
        approximated to 30 days. Over 96 months that is 2,880 days — about two
        months short of eight years — and no instalment ever falls on the same
        day of the month.
        """
        plan = self._plan()
        self._line(plan, kind='installment', calculation_type='percent',
                   value=1.0, occurrences=96, interval='monthly',
                   date_rule='months_after_booking', offset_value=1)
        self._line(plan, kind='handover', calculation_type='residual',
                   date_rule='months_after_booking', offset_value=96)
        plan.action_activate()

        booking = date(2026, 1, 31)
        rows = plan._generate_schedule(
            total_price=1000000.0, booking_date=booking)
        instalments = [r for r in rows if r['kind'] == 'installment']

        # 96 monthly instalments starting one month after booking end in
        # January 2034 — exactly eight years, not 7.9.
        self.assertEqual(instalments[-1]['date_due'].year, 2034)
        self.assertEqual(instalments[-1]['date_due'].month, 1)

        # And every instalment lands on the same day of the month, clamped by
        # relativedelta for short months.
        days = {r['date_due'].day for r in instalments}
        self.assertLessEqual(
            days, {28, 29, 30, 31},
            "instalments should land at month-end, not drift through the month")

    def test_quarterly_spacing_is_three_calendar_months(self):
        plan = self._plan()
        self._line(plan, kind='installment', calculation_type='percent',
                   value=25.0, occurrences=4, interval='quarterly',
                   date_rule='months_after_booking', offset_value=3)
        plan.action_activate()
        rows = plan._generate_schedule(
            total_price=1000000.0, booking_date=date(2026, 1, 15))
        due = [r['date_due'] for r in rows]
        self.assertEqual(due, [date(2026, 4, 15), date(2026, 7, 15),
                               date(2026, 10, 15), date(2027, 1, 15)])

    def test_handover_line_without_a_handover_date_is_refused(self):
        """Better a clear error than a due date invented years too early."""
        plan = self._standard_plan()
        plan.action_activate()
        with self.assertRaises(UserError) as err:
            plan._generate_schedule(
                total_price=1000000.0, booking_date=date(2026, 1, 15))
        self.assertIn('handover', str(err.exception).lower())

    def test_schedule_is_ordered_by_due_date(self):
        plan = self._standard_plan()
        plan.action_activate()
        rows = plan._generate_schedule(
            total_price=1000000.0, booking_date=date(2026, 1, 15),
            handover_date=date(2034, 1, 15))
        due = [r['date_due'] for r in rows]
        self.assertEqual(due, sorted(due))

    def test_cumulative_figures_are_consistent(self):
        plan = self._standard_plan()
        plan.action_activate()
        rows = plan._generate_schedule(
            total_price=1000000.0, booking_date=date(2026, 1, 15),
            handover_date=date(2034, 1, 15))
        self.assertEqual(rows[-1]['cumulative_amount'], 1000000.0)
        self.assertEqual(rows[-1]['remaining_amount'], 0.0)
        self.assertAlmostEqual(rows[-1]['cumulative_percent'], 100.0, places=6)

    def test_fixed_amount_line_is_honoured(self):
        plan = self._plan()
        self._line(plan, kind='booking', calculation_type='fixed_amount',
                   value=50000.0, date_rule='on_booking')
        self._line(plan, kind='handover', calculation_type='residual',
                   date_rule='months_after_booking', offset_value=12)
        plan.action_activate()
        rows = plan._generate_schedule(
            total_price=1000000.0, booking_date=date(2026, 1, 15))
        self.assertEqual(rows[0]['amount'], 50000.0)
        self.assertEqual(rows[1]['amount'], 950000.0)

    # ---- versioning (Phase 13) ----

    def test_active_plan_schedule_cannot_be_edited(self):
        plan = self._standard_plan()
        plan.action_activate()
        with self.assertRaises(UserError):
            plan.write({'booking_handling': 'separate'})

    def test_new_version_leaves_existing_deals_alone(self):
        """'10% down over 8 years' → '15% down over 7' must not touch v1."""
        plan = self._standard_plan()
        plan.action_activate()
        action = plan.action_new_version()
        v2 = self.Plan.browse(action['res_id'])
        self.assertEqual(v2.version, 2)
        self.assertEqual(v2.state, 'draft')
        self.assertEqual(v2.previous_version_id, plan)

        v2.line_ids.filtered(lambda l: l.kind == 'down_payment').value = 15.0
        self.assertEqual(
            plan.line_ids.filtered(lambda l: l.kind == 'down_payment').value,
            10.0, "v1 must be exactly as it was when deals were signed under it")

    def test_only_draft_plans_can_be_deleted(self):
        plan = self._standard_plan()
        plan.action_activate()
        with self.assertRaises(UserError):
            plan.unlink()

    # ---- negotiated plans (Phase 14) ----

    def test_custom_plan_forks_rather_than_edits_the_template(self):
        template = self._standard_plan()
        template.action_activate()
        custom = template.action_create_custom_copy()

        self.assertTrue(custom.is_custom)
        self.assertEqual(custom.source_plan_id, template)
        self.assertEqual(custom.state, 'draft')
        self.assertEqual(len(custom.line_ids), len(template.line_ids))

        custom.line_ids.filtered(lambda l: l.kind == 'down_payment').value = 5.0
        self.assertEqual(
            template.line_ids.filtered(lambda l: l.kind == 'down_payment').value,
            10.0, "the master template is never edited for one buyer")

    def test_custom_divergence_is_measurable(self):
        template = self._standard_plan()
        template.action_activate()
        custom = template.action_create_custom_copy()
        custom.line_ids.filtered(lambda l: l.kind == 'down_payment').value = 5.0

        divergence = custom._custom_divergence()
        self.assertAlmostEqual(divergence['down_payment_delta'], -5.0)

    def test_custom_plans_are_not_offered_as_templates(self):
        template = self._standard_plan()
        template.action_activate()
        custom = template.action_create_custom_copy()
        custom.action_activate()

        self._open_project_for_sales()
        available = self.Plan._available_for(self.units[0])
        self.assertIn(template, available)
        self.assertNotIn(custom, available,
                         "a plan negotiated for one buyer is not on the menu")


@tagged('post_install', '-at_install', 'atmta_developer')
class TestPaymentPlanPreview(DeveloperCommon):
    """Phase 12 — the preview must run the real generator, not an illustration."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Plan = cls.env['realestate.payment.plan']
        cls.Preview = cls.env['realestate.payment.plan.preview']

    def _plan(self):
        plan = self.Plan.create({
            'name': 'Preview Plan', 'company_id': self.company.id,
            'project_id': self.project.id,
        })
        self.env['realestate.payment.plan.line'].create([
            {'plan_id': plan.id, 'sequence': 10, 'kind': 'down_payment',
             'calculation_type': 'percent', 'value': 10.0,
             'date_rule': 'on_booking'},
            {'plan_id': plan.id, 'sequence': 20, 'kind': 'installment',
             'calculation_type': 'percent', 'value': 2.0, 'occurrences': 40,
             'interval': 'quarterly', 'date_rule': 'months_after_booking',
             'offset_value': 3},
            {'plan_id': plan.id, 'sequence': 30, 'kind': 'handover',
             'calculation_type': 'residual',
             'date_rule': 'months_after_booking', 'offset_value': 120},
        ])
        plan.action_activate()
        return plan

    def test_preview_balances(self):
        plan = self._plan()
        wiz = self.Preview.create({
            'plan_id': plan.id, 'unit_price': 10000000.0,
            'booking_date': date(2026, 1, 15),
        })
        wiz.action_generate()
        self.assertEqual(len(wiz.line_ids), 42)
        self.assertTrue(wiz.balances, "the preview must add up to the price")
        self.assertEqual(wiz.total_amount, 10000000.0)
        self.assertAlmostEqual(wiz.total_percent, 100.0, places=6)

    def test_preview_shows_running_balance(self):
        plan = self._plan()
        wiz = self.Preview.create({
            'plan_id': plan.id, 'unit_price': 10000000.0,
            'booking_date': date(2026, 1, 15),
        })
        wiz.action_generate()
        first = wiz.line_ids[0]
        last = wiz.line_ids[-1]
        self.assertEqual(first.amount, 1000000.0)
        self.assertEqual(first.remaining_amount, 9000000.0)
        self.assertEqual(last.remaining_amount, 0.0)

    def test_preview_refuses_a_schedule_that_does_not_balance(self):
        """The preview runs the same guard the deal path will."""
        plan = self.Plan.create({
            'name': 'Broken', 'company_id': self.company.id,
            'project_id': self.project.id,
        })
        self.env['realestate.payment.plan.line'].create({
            'plan_id': plan.id, 'kind': 'down_payment',
            'calculation_type': 'percent', 'value': 10.0,
            'date_rule': 'on_booking',
        })
        # Forced active without the completeness check, to prove the generator
        # itself refuses rather than relying on activation having been careful.
        plan.state = 'active'
        wiz = self.Preview.create({
            'plan_id': plan.id, 'unit_price': 1000000.0,
            'booking_date': date(2026, 1, 15),
        })
        with self.assertRaises(UserError):
            wiz.action_generate()
