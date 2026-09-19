"""Utility meters and readings (Phase 23)."""

from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestPropertyMeters(LeaseCase):

    def setUp(self):
        super().setUp()
        self.meter = self.env['realestate.property.meter'].create({
            'name': 'ELEC-A101',
            'property_id': self.unit_a.id,
            'meter_type': 'electricity',
            'opening_reading': 1000.0,
        })

    def test_current_reading_starts_at_the_opening_value(self):
        self.assertAlmostEqual(self.meter.current_reading, 1000.0, places=2)

    def test_consumption_is_computed_from_the_previous_reading(self):
        reading = self.env['realestate.property.meter.reading'].create({
            'meter_id': self.meter.id,
            'reading_date': self.today,
            'current_reading': 1250.0,
        })
        self.assertAlmostEqual(reading.previous_reading, 1000.0, places=2)
        self.assertAlmostEqual(reading.consumption, 250.0, places=2)

    def test_successive_readings_chain(self):
        Reading = self.env['realestate.property.meter.reading']
        Reading.create({
            'meter_id': self.meter.id,
            'reading_date': self.today - relativedelta(months=2),
            'current_reading': 1200.0,
        })
        second = Reading.create({
            'meter_id': self.meter.id,
            'reading_date': self.today - relativedelta(months=1),
            'current_reading': 1500.0,
        })
        self.assertAlmostEqual(second.previous_reading, 1200.0, places=2)
        self.assertAlmostEqual(second.consumption, 300.0, places=2)

    def test_history_is_preserved(self):
        """The whole reason meters were normalised."""
        Reading = self.env['realestate.property.meter.reading']
        for offset, value in ((3, 1100.0), (2, 1200.0), (1, 1400.0)):
            Reading.create({
                'meter_id': self.meter.id,
                'reading_date': self.today - relativedelta(months=offset),
                'current_reading': value,
            })
        self.assertEqual(self.meter.reading_count, 3)
        self.assertAlmostEqual(self.meter.current_reading, 1400.0, places=2)

    def test_backwards_reading_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.env['realestate.property.meter.reading'].create({
                'meter_id': self.meter.id,
                'reading_date': self.today,
                'current_reading': 500.0,
            })

    def test_backwards_reading_allowed_when_flagged_estimated(self):
        """A replaced or rolled-over meter is a real case; it just has to be
        declared rather than slipping through as an actual reading."""
        reading = self.env['realestate.property.meter.reading'].create({
            'meter_id': self.meter.id,
            'reading_date': self.today,
            'current_reading': 50.0,
            'reading_kind': 'estimated',
            'notes': 'Meter replaced; counter reset.',
        })
        self.assertEqual(reading.reading_kind, 'estimated')

    def test_one_reading_per_meter_per_day(self):
        from psycopg2 import IntegrityError

        from odoo.tools import mute_logger
        self.env['realestate.property.meter.reading'].create({
            'meter_id': self.meter.id,
            'reading_date': self.today,
            'current_reading': 1100.0,
        })
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.cr.savepoint():
                self.env['realestate.property.meter.reading'].create({
                    'meter_id': self.meter.id,
                    'reading_date': self.today,
                    'current_reading': 1200.0,
                })
                self.env.flush_all()

    def test_legacy_columns_stay_in_sync(self):
        """Pre-upgrade views and reports keep working untouched."""
        self.unit_a.invalidate_recordset()
        self.assertEqual(self.unit_a.electricity_meter_number, 'ELEC-A101')
        self.env['realestate.property.meter.reading'].create({
            'meter_id': self.meter.id,
            'reading_date': self.today,
            'current_reading': 1750.0,
        })
        self.unit_a.invalidate_recordset()
        self.assertAlmostEqual(
            self.unit_a.electricity_current_reading, 1750.0, places=2)

    def test_multiple_meters_of_the_same_type_are_possible(self):
        """The flat columns could not express this at all."""
        second = self.env['realestate.property.meter'].create({
            'name': 'ELEC-A101-B',
            'property_id': self.unit_a.id,
            'meter_type': 'electricity',
        })
        self.assertEqual(len(self.unit_a.meter_ids), 2)
        self.assertNotEqual(second, self.meter)

    def test_legacy_normalisation_is_idempotent(self):
        self.unit_b.write({
            'water_meter_number': 'WTR-A102',
            'water_current_reading': 42.0,
        })
        first = self.unit_b._normalise_legacy_meters()
        second = self.unit_b._normalise_legacy_meters()
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)
        self.assertAlmostEqual(first.opening_reading, 42.0, places=2)

    def test_metered_charge_reads_consumption(self):
        lease = self.make_lease(rent=1000.0, use_billing_engine=True)
        self.activate(lease)
        self.env['realestate.contract.charge.rule'].create({
            'contract_id': lease.id,
            'name': 'Electricity',
            'charge_category': 'utilities',
            'calculation_type': 'meter_consumption',
            'meter_type': 'electricity',
            'unit_rate': 0.5,
            'frequency': 'monthly',
        })
        self.env['realestate.property.meter.reading'].create({
            'meter_id': self.meter.id,
            'reading_date': lease.start_date + relativedelta(days=5),
            'current_reading': 1200.0,
        })
        lease.action_generate_billing_schedule()
        first = lease.contract_payment_ids.sorted('period_start')[0]
        # 200 kWh * 0.5
        self.assertAlmostEqual(first.charge_total, 100.0, places=2)

    def test_metered_charge_with_no_reading_bills_nothing(self):
        """We do not estimate a tenant's consumption."""
        lease = self.make_lease(
            prop=self.unit_b, rent=800.0, use_billing_engine=True)
        self.activate(lease)
        self.env['realestate.contract.charge.rule'].create({
            'contract_id': lease.id,
            'name': 'Water',
            'charge_category': 'utilities',
            'calculation_type': 'meter_consumption',
            'meter_type': 'water',
            'unit_rate': 2.0,
            'frequency': 'monthly',
        })
        lease.action_generate_billing_schedule()
        first = lease.contract_payment_ids.sorted('period_start')[0]
        self.assertAlmostEqual(first.charge_total, 0.0, places=2)
