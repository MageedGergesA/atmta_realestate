"""Availability engine (Phase 4)."""

from dateutil.relativedelta import relativedelta

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestAvailability(LeaseCase):

    def test_fresh_unit_is_available(self):
        self.assertTrue(self.unit_a.is_available_for_lease)
        self.assertEqual(self.unit_a.availability_reason, 'Available')

    def test_structural_level_is_never_available(self):
        self.assertFalse(self.building.is_available_for_lease)
        self.assertIn('Not a leasable unit', self.building.availability_reason)

    def test_active_lease_blocks_and_explains(self):
        lease = self.make_lease(prop=self.unit_a)
        self.activate(lease)
        self.unit_a.invalidate_recordset()
        self.assertFalse(self.unit_a.is_available_for_lease)
        self.assertIn('Leased to', self.unit_a.availability_reason)
        self.assertEqual(self.unit_a.blocking_contract_id, lease)

    def test_next_available_date_is_the_day_after_the_lease_ends(self):
        end = self.today + relativedelta(months=6)
        lease = self.make_lease(prop=self.unit_a, end=end)
        self.activate(lease)
        self.unit_a.invalidate_recordset()
        self.assertEqual(
            self.unit_a.next_available_date, end + relativedelta(days=1))

    def test_open_ended_allocation_has_no_knowable_free_date(self):
        """We do not invent a date we cannot know.

        ``realestate.contract.end_date`` is required (pre-existing, and left
        alone), so open-endedness is expressed on the ALLOCATION, which is the
        record availability actually reads.
        """
        lease = self.make_lease(prop=self.unit_a)
        self.activate(lease)
        lease.property_line_ids.write({'end_date': False})
        self.unit_a.invalidate_recordset()
        self.assertFalse(self.unit_a.next_available_date)
        self.assertFalse(self.unit_a.is_available_for_lease)

    def test_maintenance_blocks_availability(self):
        self.unit_a.maintenance_status = 'maintenance'
        self.assertFalse(self.unit_a.is_available_for_lease)
        self.assertIn('Maintenance status', self.unit_a.availability_reason)

    def test_sold_unit_is_not_leasable(self):
        self.unit_a.commercial_status = 'sold'
        self.assertFalse(self.unit_a.is_available_for_lease)

    def test_unreleased_unit_is_not_available(self):
        self.unit_a.commercial_status = 'unreleased'
        self.assertFalse(self.unit_a.is_available_for_lease)

    def test_delivered_construction_status_does_not_block(self):
        """Regression: 'delivered' is a developer-module value meaning ready."""
        self.unit_a.construction_status = 'delivered'
        self.assertTrue(self.unit_a.is_available_for_lease)

    def test_construction_does_not_block_a_standalone_unit(self):
        """A building the company simply owns and lets has no phase.

        With real_estate_developer installed, construction_status is computed
        and falls back to 'planning' for anything with no project -- so
        blocking on it unconditionally would make every standalone rental unit
        permanently unavailable.
        """
        self.unit_a.construction_status = 'under_construction'
        self.assertTrue(self.unit_a.is_available_for_lease)

    def test_construction_blocks_a_development_unit(self):
        """When the unit IS part of a development, readiness matters."""
        if 'project_id' not in self.env['realestate.property']._fields:
            self.skipTest('real_estate_developer is not installed')
        project = self.env['realestate.project'].create({
            'name': 'Test Project', 'state': 'construction'})
        self.unit_a.project_id = project.id
        self.unit_a.invalidate_recordset()
        self.assertIn(self.unit_a.construction_status,
                      ('planning', 'under_construction', 'finishing'))
        self.assertFalse(self.unit_a.is_available_for_lease)
        self.assertIn('Construction status', self.unit_a.availability_reason)

    def test_future_release_date_blocks_until_then(self):
        self.unit_a.available_from = self.today + relativedelta(months=2)
        self.assertFalse(self.unit_a.is_available_for_lease)
        self.assertIn('Not released', self.unit_a.availability_reason)

    def test_withdrawn_unit_blocks(self):
        self.unit_a.available_until = self.today - relativedelta(days=1)
        self.assertFalse(self.unit_a.is_available_for_lease)
        self.assertIn('Withdrawn', self.unit_a.availability_reason)

    def test_availability_window_must_be_ordered(self):
        with self.assertRaises(ValidationError):
            self.unit_a.write({
                'available_from': self.today + relativedelta(months=2),
                'available_until': self.today,
            })

    # ------------------------------------------------------------------
    # Override
    # ------------------------------------------------------------------
    def test_override_forces_available_and_is_audited(self):
        self.unit_a.maintenance_status = 'maintenance'
        self.unit_a.write({
            'availability_override': 'force_available',
            'availability_override_reason': 'Tenant accepted as-is.',
        })
        self.assertTrue(self.unit_a.is_available_for_lease)
        self.assertEqual(self.unit_a.availability_override_uid, self.env.user)
        self.assertTrue(self.unit_a.availability_override_date)

    def test_override_forces_unavailable(self):
        self.unit_a.write({
            'availability_override': 'force_unavailable',
            'availability_override_reason': 'Held for a director.',
        })
        self.assertFalse(self.unit_a.is_available_for_lease)

    def test_override_requires_a_reason(self):
        with self.assertRaises(UserError):
            self.unit_a.availability_override = 'force_available'

    def test_override_requires_property_manager(self):
        agent = new_test_user(
            self.env, login='re_agent_avail',
            groups='atmta_real_estate.group_rental_agent')
        with self.assertRaises(AccessError):
            self.unit_a.with_user(agent).write({
                'availability_override': 'force_available',
                'availability_override_reason': 'Trying it on.',
            })

    def test_clearing_the_override_restores_the_derived_answer(self):
        self.unit_a.maintenance_status = 'maintenance'
        self.unit_a.write({
            'availability_override': 'force_available',
            'availability_override_reason': 'Temporary.',
        })
        self.assertTrue(self.unit_a.is_available_for_lease)
        self.unit_a.action_clear_availability_override()
        self.assertFalse(self.unit_a.is_available_for_lease)
