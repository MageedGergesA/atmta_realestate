"""Rows that tie on the form must not crash the form.

A form's new rows are unsaved records whose ids cannot be ordered. The lease's
current rent re-sorts its escalations by (date, sequence, id) and a meter's
reading stats re-sort its readings by (date, id), so two rows sharing a date --
two readings both defaulting to today, or two escalations typed with the same
date before one is corrected -- fell through to comparing ids:
``TypeError: '<' not supported between instances of 'NewId' and 'NewId'``.
"""

from dateutil.relativedelta import relativedelta

from odoo.tests import Form, tagged

from .common import LeaseCase

LEASE_FORM = 'atmta_real_estate.view_realestate_contract_form'


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestFormTies(LeaseCase):

    def test_two_readings_added_with_the_default_date(self):
        form = Form(self.env['realestate.property.meter'])
        form.name = 'EL-TIE-001'
        form.property_id = self.unit_b
        with form.reading_ids.new() as reading:
            reading.current_reading = 100.0
        with form.reading_ids.new() as reading:
            reading.current_reading = 140.0
        # Both defaulted to today; the user dates the first one back.
        with form.reading_ids.edit(0) as reading:
            reading.reading_date = self.today - relativedelta(days=30)
        meter = form.save()

        readings = meter.reading_ids.sorted('reading_date')
        self.assertEqual(readings.mapped('current_reading'), [100.0, 140.0])
        self.assertEqual(meter.current_reading, 140.0)
        self.assertEqual(meter.last_reading_date, self.today)

    def test_two_escalations_typed_with_the_same_date(self):
        start = self.today
        form = Form(self.env['realestate.contract'], view=LEASE_FORM)
        form.partner_id = self.tenant
        form.property_id = self.unit_a
        form.start_date = start
        form.end_date = start + relativedelta(years=3, days=-1)
        form.price = 1000.0
        first = start + relativedelta(years=1)
        for value in (5.0, 3.0):
            with form.escalation_rule_ids.new() as line:
                line.effective_date = first
                line.escalation_type = 'percentage'
                line.percentage = value
        # The user corrects the second one to the following year.
        with form.escalation_rule_ids.edit(1) as line:
            line.effective_date = start + relativedelta(years=2)
        lease = form.save()

        rules = lease.escalation_rule_ids.sorted('effective_date')
        self.assertAlmostEqual(rules[0].resulting_amount, 1050.0, places=2)
        self.assertAlmostEqual(rules[1].resulting_amount, 1081.5, places=2)
        self.assertAlmostEqual(lease._rent_on(start + relativedelta(years=2)), 1081.5, places=2)
