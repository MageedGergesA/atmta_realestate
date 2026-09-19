"""Units are allocations (rental re-architecture, batch 3)."""

from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLeaseUnits(LeaseCase):

    def setUp(self):
        super().setUp()
        self.start = self.today.replace(day=1)
        self.end = self.start + relativedelta(years=1) - relativedelta(days=1)

    def _multi_unit_lease(self, *units):
        """A multi-unit lease whose units are entered on the Properties tab."""
        lease = self.make_lease(
            prop=False, start=self.start, end=self.end, rent=1050.0,
            is_single_property=False, is_multi_property=True, property_id=False)
        lease.write({'property_line_ids': [(0, 0, {
            'property_id': unit.id,
            'allocated_rent': rent,
            'start_date': self.start,
            'end_date': self.end,
        }) for unit, rent in units]})
        return lease

    def test_a_multi_unit_lease_entered_as_allocations_goes_live(self):
        lease = self._multi_unit_lease((self.unit_b, 900.0), (self.parking, 150.0))

        self.activate(lease)

        self.assertEqual(lease.lifecycle_state, 'active')
        self.assertEqual(set(self.allocations_of(lease).mapped('origin')), {'manual'})
        self.assertEqual(self.unit_b.rental_status, 'rented')
        self.assertEqual(self.parking.rental_status, 'rented')

    def test_its_allocations_block_other_leases_of_the_same_unit(self):
        self.activate(self._multi_unit_lease((self.unit_b, 900.0)))
        other = self.make_lease(prop=self.unit_b, start=self.start, end=self.end)
        with self.assertRaises(ValidationError):
            self.activate(other)

    def test_rental_status_follows_the_lease_lifecycle(self):
        lease = self.make_lease(prop=self.unit_a, start=self.start, end=self.end)
        self.assertEqual(self.unit_a.rental_status, 'not_rented')

        self.activate(lease)
        self.assertEqual(self.unit_a.rental_status, 'rented')

        lease._do_transition('terminated')
        self.assertEqual(self.unit_a.rental_status, 'not_rented')

    def test_renewal_carries_over_the_units_the_lease_still_holds(self):
        lease = self._multi_unit_lease((self.unit_b, 900.0), (self.parking, 150.0))
        self.activate(lease)
        # As an amendment removing the parking space would.
        self.allocations_of(lease).filtered(
            lambda a: a.property_id == self.parking
        ).end_date = self.start + relativedelta(months=6)
        renewal = self.env['realestate.contract.renewal'].create({
            'contract_id': lease.id,
            'proposed_start_date': self.end + relativedelta(days=1),
            'proposed_end_date': self.end + relativedelta(years=1),
            'proposed_rent': 1100.0,
        })
        renewal.action_propose()
        renewal.action_approve()
        renewal.action_accept()
        renewal.action_create_renewal_lease()

        new_lease = renewal.new_contract_id
        allocations = self.allocations_of(new_lease)
        self.assertEqual(allocations.property_id, self.unit_b)
        self.assertEqual(allocations.start_date, self.end + relativedelta(days=1))
        self.assertEqual(allocations.end_date, self.end + relativedelta(years=1))
        self.assertAlmostEqual(allocations.allocated_rent, 900.0, places=2)
        self.activate(new_lease)
        self.assertEqual(new_lease.lifecycle_state, 'active')

    def test_rental_history_of_a_multi_unit_lease_comes_from_its_allocations(self):
        lease = self._multi_unit_lease((self.unit_b, 900.0), (self.parking, 150.0))
        history = self.env['realestate.property.rental.history'].search(
            [('contract_id', '=', lease.id)])
        self.assertEqual(set(history.mapped('property_id')), {self.unit_b, self.parking})

    def test_unit_report_lists_leases_from_allocations(self):
        lease = self._multi_unit_lease((self.unit_b, 900.0))
        self.activate(lease)
        html, _format = self.env['ir.actions.report']._render_qweb_html(
            'atmta_real_estate.action_unit_status_report', self.unit_b.ids)
        self.assertIn(lease.name, html.decode())

    def test_legacy_line_status_jobs_are_retired(self):
        for xmlid in ('atmta_real_estate.ir_cron_update_contract_line_status',
                      'atmta_real_estate.ir_cron_contract_line_expiry'):
            self.assertFalse(self.env.ref(xmlid, raise_if_not_found=False), xmlid)

    # ------------------------------------------------------------------
    # The legacy unit line model is gone (0.10)
    # ------------------------------------------------------------------
    def test_the_legacy_unit_line_model_and_pivot_reports_are_gone(self):
        removed = ('realestate.contract.line', 'realestate.report.contract',
                   'realestate.report.contract.line')
        for model in removed:
            self.assertNotIn(model, self.env)
        self.assertFalse(self.env['ir.model'].search([('model', 'in', removed)]))
        self.assertFalse(self.env['ir.model.fields'].search([('relation', 'in', removed)]))
        self.assertNotIn('line_ids', self.env['realestate.contract']._fields)
        self.assertNotIn('contract_line_id', self.env['realestate.contract.payment']._fields)
        self.assertNotIn('legacy_line_id', self.env['realestate.contract.property.line']._fields)
        origins = dict(self.env['realestate.contract.property.line']._fields['origin'].selection)
        self.assertNotIn('legacy_line', origins)

    # ------------------------------------------------------------------
    # Carrying legacy unit lines over (0.10 upgrade)
    # ------------------------------------------------------------------
    def _legacy_row(self, lease, unit, line_id, **values):
        row = {
            'id': line_id, 'contract_id': lease.id, 'property_id': unit.id,
            'price': 700.0, 'start_date': self.start, 'end_date': self.end,
            'notes': 'Corner unit', 'allocation_id': None,
        }
        row.update(values)
        return row

    def _empty_multi_unit_lease(self):
        return self.make_lease(
            prop=False, start=self.start, end=self.end, rent=1050.0,
            is_single_property=False, is_multi_property=True, property_id=False)

    def test_an_unmirrored_legacy_line_becomes_an_allocation(self):
        lease = self._empty_multi_unit_lease()

        report = self.env['realestate.contract']._restore_legacy_unit_lines(
            [self._legacy_row(lease, self.unit_b, 9001)])

        allocation = self.allocations_of(lease)
        self.assertEqual(len(report['created']), 1)
        self.assertEqual(allocation.property_id, self.unit_b)
        self.assertEqual((allocation.start_date, allocation.end_date), (self.start, self.end))
        self.assertAlmostEqual(allocation.allocated_rent, 700.0, places=2)
        self.assertEqual(allocation.notes, 'Corner unit')
        self.assertEqual(allocation.origin, 'manual')

    def test_carrying_over_never_duplicates_an_allocation(self):
        lease = self._multi_unit_lease((self.unit_b, 900.0))
        mirrored = self.allocations_of(lease)
        rows = [
            # Already mirrored by an allocation.
            self._legacy_row(lease, self.unit_b, 9002, allocation_id=mirrored.id),
            # Same unit and dates as an existing allocation.
            self._legacy_row(lease, self.unit_b, 9003),
            # Two legacy lines for one new unit.
            self._legacy_row(lease, self.parking, 9004),
            self._legacy_row(lease, self.parking, 9005),
        ]
        Contract = self.env['realestate.contract']

        first = Contract._restore_legacy_unit_lines(rows)
        second = Contract._restore_legacy_unit_lines(rows)

        self.assertEqual(len(first['created']), 1)
        self.assertEqual(len(first['already_allocated']), 3)
        self.assertFalse(second['created'])
        self.assertEqual(len(self.allocations_of(lease)), 2)

    def test_a_legacy_line_on_a_single_unit_lease_is_reported_not_added(self):
        lease = self.make_lease(prop=self.unit_a, start=self.start, end=self.end)

        report = self.env['realestate.contract']._restore_legacy_unit_lines(
            [self._legacy_row(lease, self.unit_b, 9006)])

        self.assertEqual(len(report['skipped']), 1)
        self.assertEqual(self.allocations_of(lease).property_id, self.unit_a)

    def test_a_legacy_line_that_would_double_book_is_reported_not_forced(self):
        self.activate(self.make_lease(prop=self.parking, start=self.start, end=self.end))
        lease = self._multi_unit_lease((self.unit_b, 900.0))
        self.activate(lease)

        report = self.env['realestate.contract']._restore_legacy_unit_lines([
            self._legacy_row(lease, self.parking, 9007),
            self._legacy_row(lease, self.unit_a, 9008),
        ])

        self.assertEqual(len(report['failed']), 1)
        self.assertEqual(len(report['created']), 1)
        self.assertEqual(set(self.allocations_of(lease).mapped('property_id')),
                         {self.unit_b, self.unit_a})
