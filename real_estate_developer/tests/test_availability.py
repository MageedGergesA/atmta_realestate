# -*- coding: utf-8 -*-
"""Phases 3 & 4 — the sale-availability engine.

The point of these tests is that "can I sell this unit?" has exactly one
answer, and that the answer is *reasoned*: every refusal names a cause the user
can act on. Before 0.3 the question was answered by
``commercial_status == 'available'``, which cannot tell "we have not released
it" from "someone already reserved it".
"""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import DeveloperCommon


@tagged('post_install', '-at_install', 'atmta_developer')
class TestSaleAvailability(DeveloperCommon):

    def test_unreleased_unit_is_not_for_sale(self):
        """A unit that physically exists is not automatically on the market."""
        self._open_project_for_sales()
        unit = self.units[0]
        self.assertEqual(unit.commercial_status, 'available',
                         "nobody has committed to the unit")
        self.assertFalse(unit.is_available_for_sale)
        self.assertEqual(unit.sale_unavailable_reason, 'not_released')

    def test_release_makes_the_unit_sellable(self):
        self._open_project_for_sales()
        unit = self.units[0]
        self._release(unit)
        self.assertTrue(unit.is_released_for_sale)
        self.assertTrue(unit.is_available_for_sale)
        self.assertFalse(unit.sale_unavailable_reason)

    def test_release_must_be_approved_before_it_goes_live(self):
        self._open_project_for_sales()
        unit = self.units[0]
        batch = self.Release.create({
            'project_id': self.project.id,
            'property_ids': [(6, 0, unit.ids)],
        })
        with self.assertRaises(Exception):
            batch.action_release()
        self.assertFalse(unit.is_available_for_sale)
        batch.action_approve()
        self.assertFalse(unit.is_available_for_sale,
                         "approval alone must not put units on the market")
        batch.action_release()
        self.assertTrue(unit.is_available_for_sale)

    def test_release_window_is_respected(self):
        """A future window does not sell today; a past one no longer sells.

        Each window gets its own unit and its own batch, because a released
        batch's window start is deliberately frozen — see
        ``test_released_batch_scope_is_frozen``. Re-dating a live release would
        change what is on the market with no record of the decision.
        """
        self._open_project_for_sales()
        today = fields.Date.context_today(self.env['realestate.property'])
        future_unit, current_unit, past_unit = self.units[0], self.units[1], self.units[2]

        self._release(future_unit, valid_from=today + timedelta(days=7))
        self.assertFalse(future_unit.is_available_for_sale)
        self.assertEqual(future_unit.sale_unavailable_reason, 'release_window')

        self._release(current_unit, valid_from=today,
                      valid_until=today + timedelta(days=1))
        self.assertTrue(current_unit.is_available_for_sale)

        self._release(past_unit, valid_from=today - timedelta(days=10),
                      valid_until=today - timedelta(days=1))
        self.assertFalse(past_unit.is_available_for_sale)
        self.assertEqual(past_unit.sale_unavailable_reason, 'release_window')

    def test_valid_until_can_still_be_shortened_on_a_live_release(self):
        """Withdrawing early is allowed; re-dating the start is not.

        Bringing the end date forward is the documented way to pull inventory
        back, and it leaves the batch itself intact as the record.
        """
        self._open_project_for_sales()
        today = fields.Date.context_today(self.env['realestate.property'])
        unit = self.units[0]
        batch = self._release(unit)
        self.assertTrue(unit.is_available_for_sale)

        batch.valid_until = today - timedelta(days=1)
        self.assertFalse(unit.is_available_for_sale)
        self.assertEqual(unit.sale_unavailable_reason, 'release_window')

    def test_closing_a_release_withdraws_the_units(self):
        self._open_project_for_sales()
        unit = self.units[0]
        batch = self._release(unit)
        self.assertTrue(unit.is_available_for_sale)
        batch.action_close()
        self.assertFalse(unit.is_available_for_sale)
        self.assertFalse(unit.is_released_for_sale)

    def test_project_not_selling_blocks_everything_under_it(self):
        unit = self.units[0]
        self.project.commercial_state = 'selling'
        self.phase.commercial_state = 'selling'
        self._release(unit)
        self.assertTrue(unit.is_available_for_sale)

        self.project.commercial_state = 'closed'
        self.assertFalse(unit.is_available_for_sale)
        self.assertEqual(unit.sale_unavailable_reason, 'project_not_selling')

    def test_phase_cannot_outrank_its_project(self):
        """A phase marked selling under a closed project still cannot sell."""
        unit = self.units[0]
        self._open_project_for_sales()
        self._release(unit)
        self.project.commercial_state = 'planning'
        self.phase.commercial_state = 'selling'
        self.assertFalse(unit.is_available_for_sale)
        self.assertFalse(self.phase._is_commercially_open())

    def test_committed_unit_is_not_available(self):
        self._open_project_for_sales()
        unit = self.units[0]
        self._release(unit)
        for status in ('held', 'reserved', 'contracted', 'sold'):
            unit.commercial_status = status
            self.assertFalse(unit.is_available_for_sale,
                             "%s must not be sellable" % status)
            self.assertEqual(unit.sale_unavailable_reason, 'committed')

    def test_maintenance_blocks_sale(self):
        self._open_project_for_sales()
        unit = self.units[0]
        self._release(unit)
        unit.maintenance_status = 'maintenance'
        self.assertFalse(unit.is_available_for_sale)
        self.assertEqual(unit.sale_unavailable_reason, 'maintenance')

    def test_only_leaf_units_are_sellable(self):
        """Selling a whole building is a different transaction."""
        self._open_project_for_sales()
        building = self.Property.create({
            'name': 'Block A',
            'property_code': 'PH-B-A',
            'hierarchy_level': 'building',
            'usage_category': 'apartment',
            'area_sqm': 5000.0,
            'company_id': self.company.id,
            'project_id': self.project.id,
        })
        self._release(building, phase=None)
        self.assertFalse(building.is_available_for_sale)
        self.assertEqual(building.sale_unavailable_reason, 'not_a_unit')

    def test_reason_order_is_most_actionable_first(self):
        """When several things are wrong, report the structural one."""
        unit = self.units[0]
        # Project not selling AND not released AND blocked, all at once.
        self.Block.create({
            'property_id': unit.id,
            'reason': 'legal',
            'company_id': self.company.id,
        })
        self.assertEqual(unit.sale_unavailable_reason, 'project_not_selling',
                         "no point telling a user about a block when the whole "
                         "project is shut")

    def test_check_available_for_sale_raises_a_specific_error(self):
        self._open_project_for_sales()
        unit = self.units[0]
        with self.assertRaises(ValidationError) as err:
            unit._check_available_for_sale()
        self.assertIn('released', str(err.exception).lower())

        self._release(unit)
        unit._check_available_for_sale()  # must not raise


@tagged('post_install', '-at_install', 'atmta_developer')
class TestCommercialBlocks(DeveloperCommon):

    def setUp(self):
        super().setUp()
        self._open_project_for_sales()
        self.unit = self.units[0]
        self._release(self.unit)

    def test_block_removes_from_market_without_touching_status(self):
        before = self.unit.commercial_status
        self.Block.create({
            'property_id': self.unit.id,
            'reason': 'model_unit',
            'company_id': self.company.id,
        })
        self.assertFalse(self.unit.is_available_for_sale)
        self.assertEqual(self.unit.sale_unavailable_reason, 'blocked')
        self.assertEqual(
            self.unit.commercial_status, before,
            "a block must not overwrite the commercial status — that is what "
            "makes it reversible")

    def test_lifting_a_block_restores_availability(self):
        block = self.Block.create({
            'property_id': self.unit.id,
            'reason': 'pricing_review',
            'company_id': self.company.id,
        })
        self.assertFalse(self.unit.is_available_for_sale)
        block.action_lift(reason='Pricing approved')
        self.assertTrue(self.unit.is_available_for_sale)
        self.assertFalse(block.active)
        self.assertTrue(block.lifted_by_id)
        self.assertEqual(block.lift_reason, 'Pricing approved')

    def test_lifted_block_stays_auditable(self):
        block = self.Block.create({
            'property_id': self.unit.id,
            'reason': 'vip',
            'company_id': self.company.id,
        })
        block.action_lift()
        found = self.Block.with_context(active_test=False).search([
            ('property_id', '=', self.unit.id)])
        self.assertIn(block, found, "history must survive the lift")

    def test_blocks_cannot_be_deleted(self):
        block = self.Block.create({
            'property_id': self.unit.id,
            'reason': 'legal',
            'company_id': self.company.id,
        })
        with self.assertRaises(Exception):
            block.unlink()

    def test_future_block_does_not_apply_yet(self):
        today = fields.Date.context_today(self.Block)
        self.Block.create({
            'property_id': self.unit.id,
            'reason': 'management',
            'company_id': self.company.id,
            'date_start': today + timedelta(days=5),
        })
        self.assertTrue(self.unit.is_available_for_sale)

    def test_expired_block_stops_applying(self):
        today = fields.Date.context_today(self.Block)
        self.Block.create({
            'property_id': self.unit.id,
            'reason': 'management',
            'company_id': self.company.id,
            'date_start': today - timedelta(days=10),
            'date_end': today - timedelta(days=1),
        })
        self.assertTrue(self.unit.is_available_for_sale)

    def test_block_end_before_start_is_rejected(self):
        today = fields.Date.context_today(self.Block)
        with self.assertRaises(ValidationError):
            self.Block.create({
                'property_id': self.unit.id,
                'reason': 'other',
                'company_id': self.company.id,
                'date_start': today,
                'date_end': today - timedelta(days=1),
            })
