"""Property status dimensions and the legacy bridge (Phase 2).

The regression these lock down is the one that motivated the whole phase:
before the split, completing a maintenance request on a rented unit reset it to
"available", quietly evicting a sitting tenant from the property record.
"""

from dateutil.relativedelta import relativedelta

from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestPropertyStatus(LeaseCase):

    # ------------------------------------------------------------------
    # Legacy bridge
    # ------------------------------------------------------------------
    def test_legacy_state_derives_from_dimensions(self):
        self.assertEqual(self.unit_a.state, 'available')
        self.unit_a.commercial_status = 'sold'
        self.assertEqual(self.unit_a.state, 'sold')

    def test_legacy_write_is_translated_to_the_owning_dimension(self):
        """Downstream modules still write `state`; it must land correctly."""
        self.unit_a.state = 'reserved'
        self.assertEqual(self.unit_a.commercial_status, 'reserved')
        self.assertEqual(self.unit_a.state, 'reserved')

    def test_legacy_maintenance_write_touches_only_maintenance(self):
        self.unit_a.commercial_status = 'reserved'
        self.unit_a.state = 'maintenance'
        self.assertEqual(self.unit_a.maintenance_status, 'maintenance')
        # The commercial hold survives.
        self.assertEqual(self.unit_a.commercial_status, 'reserved')

    def test_legacy_available_write_does_not_clear_maintenance(self):
        """A sales module releasing its own hold must not un-block a unit."""
        self.unit_a.maintenance_status = 'maintenance'
        self.unit_a.state = 'available'
        self.assertEqual(self.unit_a.commercial_status, 'available')
        self.assertEqual(self.unit_a.maintenance_status, 'maintenance')
        self.assertEqual(self.unit_a.state, 'maintenance')

    def test_legacy_rented_write_is_a_noop(self):
        """Occupancy is computed from leases; writing it must not fake it."""
        self.unit_a.state = 'rented'
        self.assertEqual(self.unit_a.occupancy_status, 'vacant')

    # ------------------------------------------------------------------
    # The maintenance regression
    # ------------------------------------------------------------------
    def test_maintenance_completion_does_not_evict_a_tenant(self):
        lease = self.make_lease(prop=self.unit_a, rent=1000.0)
        self.activate(lease)
        self.assertEqual(self.unit_a.occupancy_status, 'occupied')

        request = self.env['realestate.maintenance.request'].create({
            'property_id': self.unit_a.id,
        })
        request.action_schedule()
        request.action_start_progress()
        self.assertEqual(self.unit_a.maintenance_status, 'maintenance')
        self.assertEqual(self.unit_a.state, 'maintenance')

        request.action_complete()
        self.assertEqual(self.unit_a.maintenance_status, 'normal')
        # The tenant is still there.
        self.assertEqual(self.unit_a.occupancy_status, 'occupied')
        self.assertEqual(self.unit_a.state, 'rented')

    def test_second_open_request_keeps_the_block(self):
        first = self.env['realestate.maintenance.request'].create(
            {'property_id': self.unit_a.id})
        second = self.env['realestate.maintenance.request'].create(
            {'property_id': self.unit_a.id})
        first.action_schedule()
        first.action_start_progress()
        second.action_schedule()
        second.action_start_progress()
        first.action_complete()
        self.assertEqual(self.unit_a.maintenance_status, 'maintenance')
        second.action_complete()
        self.assertEqual(self.unit_a.maintenance_status, 'normal')

    # ------------------------------------------------------------------
    # Occupancy rollup
    # ------------------------------------------------------------------
    def test_occupancy_rolls_up_to_the_building(self):
        self.assertEqual(self.building.occupancy_status, 'vacant')
        lease = self.make_lease(prop=self.unit_a)
        self.activate(lease)
        self.building.invalidate_recordset()
        self.assertEqual(self.building.occupancy_status, 'partially_occupied')

    def test_fully_let_building_reads_occupied(self):
        for prop in (self.unit_a, self.unit_b, self.parking):
            self.activate(self.make_lease(prop=prop))
        self.building.invalidate_recordset()
        self.assertEqual(self.building.occupancy_status, 'occupied')

    def test_empty_building_is_vacant_not_partial(self):
        """Regression: an unset child status used to read as 'mixed'."""
        self.building.invalidate_recordset()
        self.assertEqual(self.building.occupancy_status, 'vacant')
        self.assertEqual(self.compound.occupancy_status, 'vacant')

    # ------------------------------------------------------------------
    # Hierarchy vs usage (Phase 1)
    # ------------------------------------------------------------------
    def test_usage_is_independent_of_hierarchy(self):
        self.unit_a.usage_category = 'retail'
        self.assertEqual(self.unit_a.hierarchy_level, 'unit')
        self.assertEqual(self.unit_a.usage_category, 'retail')
        self.assertTrue(self.unit_a.is_leasable)

    def test_structural_levels_are_not_leasable(self):
        self.assertFalse(self.compound.is_leasable)
        self.assertFalse(self.building.is_leasable)
        self.assertTrue(self.unit_a.is_leasable)

    def test_common_area_is_not_leasable(self):
        self.unit_b.usage_category = 'common_area'
        self.assertFalse(self.unit_b.is_leasable)

    def test_archived_property_reads_inactive(self):
        self.unit_b.active = False
        self.assertEqual(self.unit_b.state, 'inactive')
