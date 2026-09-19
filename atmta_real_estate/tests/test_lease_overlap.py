"""Overlapping lease protection (Phase 5).

Covers every boundary the specification calls out, plus the two cases that
were genuinely broken before the upgrade:

* two single-property leases on the same unit (previously **unguarded**)
* a single-property lease colliding with a multi-property lease line
  (previously invisible to each other)
"""

from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLeaseOverlap(LeaseCase):

    def setUp(self):
        super().setUp()
        self.start = self.today
        self.end = self.start + relativedelta(years=1) - relativedelta(days=1)
        self.first = self.make_lease(
            prop=self.unit_a, start=self.start, end=self.end, rent=1000.0)
        self.activate(self.first)

    # ------------------------------------------------------------------
    # Collisions that must be rejected
    # ------------------------------------------------------------------
    def test_identical_dates_rejected(self):
        second = self.make_lease(prop=self.unit_a, start=self.start, end=self.end)
        with self.assertRaises(ValidationError):
            self.activate(second)

    def test_partial_overlap_rejected(self):
        # The example from the specification: an existing 2026 lease and a new
        # Jun-Nov one.
        second = self.make_lease(
            prop=self.unit_a,
            start=self.start + relativedelta(months=5),
            end=self.start + relativedelta(months=10))
        with self.assertRaises(ValidationError):
            self.activate(second)

    def test_start_on_previous_end_date_rejected(self):
        """Both leases cover that day, so it is a genuine conflict."""
        second = self.make_lease(
            prop=self.unit_a, start=self.end,
            end=self.end + relativedelta(months=6))
        with self.assertRaises(ValidationError):
            self.activate(second)

    def test_enclosing_lease_rejected(self):
        second = self.make_lease(
            prop=self.unit_a,
            start=self.start - relativedelta(months=1),
            end=self.end + relativedelta(months=1))
        with self.assertRaises(ValidationError):
            self.activate(second)

    def test_future_lease_on_occupied_unit_rejected(self):
        """A future lease starting inside the current term still collides."""
        second = self.make_lease(
            prop=self.unit_a,
            start=self.start + relativedelta(months=6),
            end=self.end + relativedelta(years=1))
        with self.assertRaises(ValidationError):
            self.activate(second)

    def test_single_vs_multi_property_lease_rejected(self):
        """The gap that existed before the upgrade.

        A multi-unit lease and a single-unit lease used to put units on a lease
        through two different records, blind to each other. Both put units on a
        lease as allocations now, so one constraint sees both.
        """
        multi = self.make_lease(
            prop=False, start=self.start, end=self.end,
            is_single_property=False, is_multi_property=True,
            property_id=False)
        multi.write({'property_line_ids': [(0, 0, {
            'property_id': self.unit_a.id,
            'allocated_rent': 900.0,
            'start_date': self.start,
            'end_date': self.end,
        })]})
        with self.assertRaises(ValidationError):
            self.activate(multi)

    def test_open_ended_allocation_blocks_everything_after_its_start(self):
        rolling = self.make_lease(prop=self.unit_b, start=self.start)
        self.activate(rolling)
        # Open-endedness lives on the allocation: contract.end_date is a
        # pre-existing required field and is deliberately not relaxed.
        rolling.property_line_ids.write({'end_date': False})

        later = self.make_lease(
            prop=self.unit_b,
            start=self.start + relativedelta(years=3),
            end=self.start + relativedelta(years=4))
        with self.assertRaises(ValidationError):
            self.activate(later)

    # ------------------------------------------------------------------
    # Collisions that must be allowed
    # ------------------------------------------------------------------
    def test_start_day_after_previous_end_allowed(self):
        second = self.make_lease(
            prop=self.unit_a,
            start=self.end + relativedelta(days=1),
            end=self.end + relativedelta(years=1))
        self.activate(second)
        self.assertEqual(second.lifecycle_state, 'active')

    def test_different_property_allowed(self):
        second = self.make_lease(
            prop=self.unit_b, start=self.start, end=self.end)
        self.activate(second)
        self.assertEqual(second.lifecycle_state, 'active')

    def test_draft_lease_does_not_block(self):
        """Sales must be able to quote two options on the same unit."""
        draft = self.make_lease(prop=self.unit_b, start=self.start, end=self.end)
        other_draft = self.make_lease(prop=self.unit_b, start=self.start, end=self.end)
        self.assertEqual(draft.lifecycle_state, 'draft')
        self.assertEqual(other_draft.lifecycle_state, 'draft')
        # Only one of them can go live.
        self.activate(draft)
        with self.assertRaises(ValidationError):
            self.activate(other_draft)

    def test_cancelled_lease_frees_the_unit(self):
        """A lease cancelled before it runs releases its hold.

        Cancellation is only reachable before the lease is live -- an ACTIVE
        lease must be terminated, not cancelled, so the billing history is
        preserved. That asymmetry is deliberate and is covered below.
        """
        pending = self.make_lease(prop=self.unit_b, start=self.start, end=self.end)
        pending.action_to_proposal()
        pending.action_submit_for_approval()
        pending.action_approve_lease()
        self.assertTrue(pending.property_line_ids.is_blocking)
        pending.action_cancel_lease()
        second = self.make_lease(prop=self.unit_b, start=self.start, end=self.end)
        self.activate(second)
        self.assertEqual(second.lifecycle_state, 'active')

    def test_active_lease_cannot_be_cancelled(self):
        """Billing history must survive; terminate instead."""
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.first.action_cancel_lease()

    def test_terminated_lease_frees_the_unit_for_later_terms(self):
        self.first._do_transition('terminated')
        second = self.make_lease(prop=self.unit_a, start=self.start, end=self.end)
        self.activate(second)
        self.assertEqual(second.lifecycle_state, 'active')

    # ------------------------------------------------------------------
    # Multi-property contracts
    # ------------------------------------------------------------------
    def test_multi_property_lease_allocates_every_unit(self):
        multi = self.make_lease(
            prop=False, start=self.start, end=self.end,
            is_single_property=False, is_multi_property=True,
            property_id=False)
        multi.write({'property_line_ids': [(0, 0, {
            'property_id': prop.id,
            'allocated_rent': price,
            'start_date': self.start,
            'end_date': self.end,
        }) for prop, price in ((self.unit_b, 900.0), (self.parking, 150.0))]})
        multi._sync_property_lines()
        allocations = self.allocations_of(multi)
        self.assertEqual(len(allocations), 2)
        self.assertEqual(
            set(allocations.mapped('property_id')), {self.unit_b, self.parking})
        self.assertEqual(sum(allocations.mapped('allocated_rent')), 1050.0)

    def test_pre_flight_check_finds_the_conflict(self):
        """``_check_property_free`` lets callers validate before creating."""
        clash = self.env['realestate.contract.property.line']._check_property_free(
            self.unit_a.id, self.start, self.end)
        self.assertTrue(clash)
        self.assertEqual(clash.contract_id, self.first)

    def test_pre_flight_check_can_exclude_a_lease(self):
        clash = self.env['realestate.contract.property.line']._check_property_free(
            self.unit_a.id, self.start, self.end,
            exclude_contract=self.first.id)
        self.assertFalse(clash)
