"""Lines added on the other Rental forms, as the web client adds them.

The lease form's escalation rows crashed because a compute could not handle
unsaved rows. The meter, move-in and move-out forms also let a user add rows in
place, and a meter reading's previous value is computed by searching for the
reading before it -- a search that, on the form, is handed the unsaved row's
own id and, on a new meter, the meter's unsaved id.
"""

from dateutil.relativedelta import relativedelta

from odoo.tests import Form, tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestFormLines(LeaseCase):

    def test_readings_added_on_a_new_meter(self):
        form = Form(self.env['realestate.property.meter'])
        form.name = 'EL-FORM-001'
        form.property_id = self.unit_b
        with form.reading_ids.new() as reading:
            reading.reading_date = self.today - relativedelta(days=30)
            reading.current_reading = 100.0
        meter = form.save()
        self.assertEqual(meter.reading_ids.current_reading, 100.0)

    def test_a_reading_added_on_a_saved_meter_follows_the_last_one(self):
        meter = self.env['realestate.property.meter'].create({
            'name': 'EL-FORM-002', 'property_id': self.unit_b.id,
        })
        self.env['realestate.property.meter.reading'].create({
            'meter_id': meter.id, 'reading_date': self.today - relativedelta(days=30),
            'current_reading': 100.0,
        })
        form = Form(meter)
        with form.reading_ids.new() as reading:
            reading.reading_date = self.today
            reading.current_reading = 140.0
        form.save()

        latest = meter.reading_ids.sorted('reading_date')[-1]
        self.assertEqual(latest.previous_reading, 100.0)
        self.assertEqual(latest.consumption, 40.0)

    def test_readings_and_deductions_added_on_a_move_out(self):
        lease = self.activate(self.make_lease(prop=self.unit_a))
        meter = self.env['realestate.property.meter'].create({
            'name': 'EL-FORM-003', 'property_id': self.unit_a.id,
        })
        self.env['realestate.property.meter.reading'].create({
            'meter_id': meter.id, 'reading_date': self.today - relativedelta(days=60),
            'current_reading': 50.0,
        })
        move_out = self.env['realestate.move.out'].create({
            'contract_id': lease.id, 'property_id': self.unit_a.id,
            'scheduled_date': self.today,
        })
        form = Form(move_out)
        with form.reading_ids.new() as reading:
            reading.meter_id = meter
            reading.reading_date = self.today
            reading.current_reading = 80.0
        with form.deduction_ids.new() as deduction:
            deduction.name = 'Broken window'
            deduction.amount = 150.0
        form.save()

        self.assertEqual(move_out.deduction_ids.amount, 150.0)
        reading = move_out.reading_ids
        self.assertEqual(reading.previous_reading, 50.0)
        self.assertEqual(reading.consumption, 30.0)

    def test_a_reading_added_on_a_move_in(self):
        lease = self.activate(self.make_lease(prop=self.unit_a))
        meter = self.env['realestate.property.meter'].create({
            'name': 'EL-FORM-004', 'property_id': self.unit_a.id,
        })
        move_in = self.env['realestate.move.in'].create({
            'contract_id': lease.id, 'property_id': self.unit_a.id,
            'scheduled_date': self.today,
        })
        form = Form(move_in)
        with form.reading_ids.new() as reading:
            reading.meter_id = meter
            reading.reading_date = self.today
            reading.current_reading = 12.0
        form.save()
        self.assertEqual(move_in.reading_ids.current_reading, 12.0)

    def test_a_charge_added_on_a_billing_obligation(self):
        lease = self.activate(self.make_lease(
            prop=self.unit_b, rent=1000.0, use_billing_engine=True))
        lease.action_generate_billing_schedule()
        obligation = lease.contract_payment_ids.filtered(lambda o: not o.move_id)[:1]
        self.assertTrue(obligation, "the fixture needs an uninvoiced obligation")
        base = obligation.amount_total

        form = Form(obligation)
        with form.charge_line_ids.new() as charge:
            charge.name = 'Water'
            charge.amount = 25.0
        form.save()

        self.assertAlmostEqual(obligation.amount_total, base + 25.0, places=2)
