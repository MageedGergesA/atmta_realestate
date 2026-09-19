"""Move-in, move-out and unit turn (Phases 20-22)."""

from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestMoveInOut(LeaseCase):

    def setUp(self):
        super().setUp()
        self.lease = self.make_lease(rent=1000.0)
        self.activate(self.lease)
        self.meter = self.env['realestate.property.meter'].create({
            'name': 'ELEC-001',
            'property_id': self.unit_a.id,
            'meter_type': 'electricity',
            'opening_reading': 1000.0,
        })

    def _move_in(self, **vals):
        base = {
            'contract_id': self.lease.id,
            'property_id': self.unit_a.id,
            'scheduled_date': self.today,
            'keys_delivered': 3,
            'access_cards_delivered': 2,
            'parking_remotes_delivered': 1,
            'tenant_acknowledged': True,
        }
        base.update(vals)
        return self.env['realestate.move.in'].create(base)

    # ------------------------------------------------------------------
    # Move-in
    # ------------------------------------------------------------------
    def test_move_in_workflow(self):
        move_in = self._move_in()
        self.assertEqual(move_in.state, 'schedule')
        move_in.action_start_inspection()
        self.assertEqual(move_in.state, 'inspection')
        move_in.action_complete()
        self.assertEqual(move_in.state, 'completed')
        self.assertTrue(move_in.actual_date)

    def test_move_in_requires_tenant_acknowledgement(self):
        move_in = self._move_in(tenant_acknowledged=False)
        with self.assertRaises(UserError):
            move_in.action_complete()

    def test_move_in_requires_an_active_lease(self):
        draft = self.make_lease(prop=self.unit_b)
        move_in = self._move_in(
            contract_id=draft.id, property_id=self.unit_b.id)
        with self.assertRaises(UserError):
            move_in.action_complete()

    def test_move_in_stamps_occupancy(self):
        move_in = self._move_in()
        move_in.action_complete()
        allocation = self.allocations_of(self.lease)
        self.assertTrue(allocation.move_in_date)

    def test_move_in_captures_meter_readings(self):
        move_in = self._move_in()
        move_in.action_start_inspection()
        created = move_in.action_capture_meter_readings()
        self.assertTrue(created)
        self.assertEqual(created.meter_id, self.meter)
        self.assertEqual(created.move_in_id, move_in)

    def test_a_reading_already_taken_today_becomes_the_handover_reading(self):
        """The capture skipped it silently and the move-in had no reading."""
        manual = self.env['realestate.property.meter.reading'].create({
            'meter_id': self.meter.id,
            'reading_date': self.today,
            'current_reading': (self.meter.current_reading or 0.0) + 10.0,
        })
        move_in = self._move_in()
        move_in.action_start_inspection()
        captured = move_in.action_capture_meter_readings()
        self.assertEqual(captured, manual)
        self.assertEqual(manual.move_in_id, move_in)
        self.assertEqual(self.env['realestate.property.meter.reading'].search_count(
            [('meter_id', '=', self.meter.id), ('reading_date', '=', self.today)]), 1)

    # ------------------------------------------------------------------
    # Move-out
    # ------------------------------------------------------------------
    def _completed_move_in(self):
        move_in = self._move_in()
        move_in.action_complete()
        return move_in

    def _move_out(self, **vals):
        base = {
            'contract_id': self.lease.id,
            'property_id': self.unit_a.id,
            'scheduled_date': self.today,
            'keys_returned': 3,
            'access_cards_returned': 2,
            'parking_remotes_returned': 1,
            'tenant_acknowledged': True,
        }
        base.update(vals)
        return self.env['realestate.move.out'].create(base)

    def test_move_out_workflow(self):
        self._completed_move_in()
        move_out = self._move_out()
        move_out.action_start_inspection()
        move_out.action_start_assessment()
        move_out.action_complete()
        self.assertEqual(move_out.state, 'completed')

    def test_move_out_links_back_to_the_move_in(self):
        move_in = self._completed_move_in()
        move_out = self._move_out()
        self.assertEqual(move_out.move_in_id, move_in)

    def test_missing_items_are_counted_against_the_move_in(self):
        self._completed_move_in()
        move_out = self._move_out(keys_returned=1, access_cards_returned=0)
        # 2 keys short + 2 cards short
        self.assertEqual(move_out.keys_missing, 4)

    def test_deductions_need_acknowledgement_or_a_dispute(self):
        self._completed_move_in()
        move_out = self._move_out(tenant_acknowledged=False)
        self.env['realestate.move.out.deduction'].create({
            'move_out_id': move_out.id,
            'name': 'Wall repair',
            'category': 'repair',
            'amount': 400.0,
        })
        move_out.action_start_inspection()
        with self.assertRaises(UserError):
            move_out.action_complete()

    def test_a_recorded_dispute_lets_the_move_out_complete(self):
        self._completed_move_in()
        move_out = self._move_out(
            tenant_acknowledged=False,
            tenant_dispute='Tenant says the crack was pre-existing.')
        self.env['realestate.move.out.deduction'].create({
            'move_out_id': move_out.id,
            'name': 'Wall repair',
            'category': 'repair',
            'amount': 400.0,
        })
        move_out.action_start_inspection()
        move_out.action_complete()
        self.assertEqual(move_out.state, 'completed')

    def test_wear_and_tear_is_recorded_not_hidden(self):
        self._completed_move_in()
        move_out = self._move_out()
        self.env['realestate.move.out.deduction'].create({
            'move_out_id': move_out.id,
            'name': 'Carpet wear',
            'category': 'repair',
            'amount': 200.0,
            'is_wear_and_tear': True,
        })
        self.assertAlmostEqual(move_out.deduction_total, 200.0, places=2)

    def test_move_out_vacates_the_allocation(self):
        self._completed_move_in()
        move_out = self._move_out()
        move_out.action_start_inspection()
        move_out.action_complete()
        allocation = self.allocations_of(self.lease)
        self.assertTrue(allocation.move_out_date)
        self.assertEqual(allocation.occupancy_status, 'vacated')

    # ------------------------------------------------------------------
    # Unit turn -- the availability gate
    # ------------------------------------------------------------------
    def test_move_out_opens_a_unit_turn(self):
        self._completed_move_in()
        move_out = self._move_out(cleaning_required=True)
        move_out.action_start_inspection()
        move_out.action_complete()
        self.assertTrue(move_out.unit_turn_id)
        self.assertTrue(move_out.unit_turn_id.cleaning_required)

    def test_move_out_does_not_make_the_unit_available(self):
        """The single most important rule in this phase."""
        self._completed_move_in()
        move_out = self._move_out(cleaning_required=True, repair_required=True)
        move_out.action_start_inspection()
        move_out.action_complete()
        move_out.unit_turn_id.action_start()
        self.unit_a.invalidate_recordset()
        self.assertFalse(self.unit_a.is_available_for_lease)

    def test_turn_cannot_complete_with_work_outstanding(self):
        self._completed_move_in()
        move_out = self._move_out(cleaning_required=True, repair_required=True)
        move_out.action_start_inspection()
        move_out.action_complete()
        turn = move_out.unit_turn_id
        turn.action_start()
        with self.assertRaises(UserError):
            turn.action_mark_ready()

    def test_completing_the_turn_returns_the_unit_to_the_market(self):
        self._completed_move_in()
        move_out = self._move_out(cleaning_required=True, repair_required=True)
        move_out.action_start_inspection()
        move_out.action_complete()
        turn = move_out.unit_turn_id
        turn.action_start()
        turn.action_cleaning_done()
        turn.action_repair_done()
        turn.action_pass_inspection()
        turn.action_mark_ready()
        self.assertEqual(turn.state, 'ready')
        self.assertTrue(turn.actual_ready_date)
        self.unit_a.invalidate_recordset()
        self.assertEqual(self.unit_a.maintenance_status, 'normal')

    def test_turn_records_vacant_days(self):
        turn = self.env['realestate.unit.turn'].create({
            'property_id': self.unit_b.id,
            'start_date': self.today - relativedelta(days=10),
        })
        turn.action_start()
        turn.action_pass_inspection()
        turn.action_mark_ready()
        self.assertEqual(turn.vacant_days, 10)

    def test_open_maintenance_blocks_the_turn(self):
        turn = self.env['realestate.unit.turn'].create({
            'property_id': self.unit_b.id,
            'start_date': self.today,
        })
        turn.action_start()
        turn.action_pass_inspection()
        self.env['realestate.maintenance.request'].create({
            'property_id': self.unit_b.id,
            'unit_turn_id': turn.id,
        })
        with self.assertRaises(UserError):
            turn.action_mark_ready()
