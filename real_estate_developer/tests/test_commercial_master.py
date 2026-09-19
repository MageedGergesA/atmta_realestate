# -*- coding: utf-8 -*-
"""Phases 1 & 2 — project and phase as commercial masters.

Two things are being pinned here:

* the commercial lifecycle is **not** the construction lifecycle, and adding it
  did not disturb ``state``, which five downstream modules read;
* phase settings inherit from the project through exactly one code path, so the
  two layers cannot drift.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import DeveloperCommon


@tagged('post_install', '-at_install', 'atmta_developer')
class TestProjectCommercial(DeveloperCommon):

    def test_commercial_state_is_independent_of_construction_state(self):
        """An off-plan project sells while it is still being built.

        The old model could not express this: `state` held both meanings in one
        field, so `marketing` and `construction` were mutually exclusive.
        """
        self.project.state = 'construction'
        self.project.commercial_state = 'selling'
        self.assertEqual(self.project.state, 'construction')
        self.assertEqual(self.project.commercial_state, 'selling')

    def test_existing_state_field_is_untouched(self):
        """Downstream modules read `state`; its values must not have moved."""
        values = dict(self.project._fields['state'].selection)
        for expected in ('planning', 'construction', 'marketing', 'handover',
                         'completed', 'cancelled'):
            self.assertIn(expected, values,
                          "removing %r would break construction/api/maquette"
                          % expected)

    def test_cannot_sell_a_project_with_no_inventory(self):
        empty = self.Project.create({
            'name': 'Empty Project', 'code': 'EMPTY',
            'company_id': self.company.id,
        })
        with self.assertRaises(UserError):
            empty.action_commercial_start_selling()

    @mute_logger('odoo.sql_db')
    def test_project_code_is_unique_per_company(self):
        """The module has never had any uniqueness on project codes.

        The logger is muted because the violation is the expected outcome, and
        an unexplained ERROR line in a green build teaches people to ignore
        ERROR lines.
        """
        from psycopg2 import IntegrityError
        with self.assertRaises(IntegrityError):
            self.Project.create({
                'name': 'Duplicate', 'code': self.project.code,
                'company_id': self.company.id,
            })
            self.env.flush_all()

    def test_booking_window_must_be_ordered(self):
        from datetime import date
        with self.assertRaises(ValidationError):
            self.project.write({
                'booking_start_date': date(2026, 6, 1),
                'booking_end_date': date(2026, 5, 1),
            })

    def test_hold_duration_must_be_positive(self):
        with self.assertRaises(ValidationError):
            self.project.default_hold_duration_hours = 0

    def test_counters_use_grouped_queries_not_python_filters(self):
        """Behavioural check on the counters, whatever the implementation."""
        self._open_project_for_sales()
        self._release(self.units[:2])
        self.project.invalidate_recordset()
        self.assertEqual(self.project.released_unit_count, 2)
        self.assertEqual(self.project.for_sale_unit_count, 2)

        self.units[0].commercial_status = 'reserved'
        self.project.invalidate_recordset()
        self.assertEqual(self.project.reserved_commercial_unit_count, 1)
        self.assertEqual(self.project.for_sale_unit_count, 1,
                         "a reserved unit is released but not for sale")


@tagged('post_install', '-at_install', 'atmta_developer')
class TestPhaseInheritance(DeveloperCommon):

    def test_phase_inherits_hold_duration_from_project(self):
        self.project.default_hold_duration_hours = 48.0
        self.assertFalse(self.phase.hold_duration_hours)
        self.assertEqual(self.phase._resolve_hold_duration_hours(), 48.0)

    def test_phase_override_wins(self):
        self.project.default_hold_duration_hours = 48.0
        self.phase.hold_duration_hours = 24.0
        self.assertEqual(self.phase._resolve_hold_duration_hours(), 24.0)

    def test_phase_inherits_booking_fee(self):
        self.project.default_booking_fee = 50000.0
        self.assertEqual(self.phase._resolve_booking_fee(), 50000.0)
        self.phase.booking_fee = 75000.0
        self.assertEqual(self.phase._resolve_booking_fee(), 75000.0)

    def test_zero_project_booking_fee_is_a_real_answer(self):
        """'No booking fee' must not be silently replaced by a default."""
        self.project.default_booking_fee = 0.0
        self.assertEqual(self.phase._resolve_booking_fee(), 0.0)

    def test_negative_override_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.phase.hold_duration_hours = -1

    def test_phase_company_follows_project(self):
        self.assertEqual(self.phase.company_id, self.project.company_id)


@tagged('post_install', '-at_install', 'atmta_developer')
class TestReleaseBatchIntegrity(DeveloperCommon):

    def test_release_scope_is_enforced_as_a_constraint(self):
        """A view domain does not protect the API or 2D/3D."""
        other_project = self.Project.create({
            'name': 'Other', 'code': 'OTH', 'company_id': self.company.id})
        foreign = self._make_units(count=1, prefix='OTH-U',
                                   project=other_project, phase=None)
        with self.assertRaises(ValidationError):
            self.Release.create({
                'project_id': self.project.id,
                'property_ids': [(6, 0, foreign.ids)],
            })

    def test_released_batch_scope_is_frozen(self):
        self._open_project_for_sales()
        batch = self._release(self.units[:1])
        with self.assertRaises(UserError):
            batch.property_ids = [(6, 0, self.units.ids)]

    def test_live_batch_cannot_be_cancelled(self):
        self._open_project_for_sales()
        batch = self._release(self.units[:1])
        with self.assertRaises(UserError):
            batch.action_cancel()

    def test_empty_batch_cannot_be_approved(self):
        batch = self.Release.create({'project_id': self.project.id})
        with self.assertRaises(UserError):
            batch.action_approve()

    def test_window_must_be_ordered(self):
        from datetime import date
        with self.assertRaises(ValidationError):
            self.Release.create({
                'project_id': self.project.id,
                'property_ids': [(6, 0, self.units[:1].ids)],
                'valid_from': date(2026, 6, 1),
                'valid_until': date(2026, 5, 1),
            })


@tagged('post_install', '-at_install', 'atmta_developer')
class TestSaleStatusRegressions(DeveloperCommon):
    """The dead-state bugs found in the Phase 0 audit."""

    def test_for_sale_status_is_reachable(self):
        """`for_sale` was unreachable: the branch did not exist.

        A unit actively on the market reported `not_listed`, which is what the
        public API and the portal displayed.
        """
        self._open_project_for_sales()
        unit = self.units[0]
        self.assertEqual(unit.sale_status, 'not_listed')
        self._release(unit)
        self.assertEqual(unit.sale_status, 'for_sale')

    def test_is_sold_still_means_handed_over(self):
        """Deliberately unchanged: `is_sold` feeds historical figures."""
        unit = self.units[0]
        self.assertFalse(unit.is_sold)
        self.assertFalse(unit.is_contracted)

    def test_contracted_is_separate_from_sold(self):
        self.assertIn('is_contracted', self.Property._fields,
                      "contracted-but-not-handed-over needs its own field "
                      "rather than being folded into is_sold")
