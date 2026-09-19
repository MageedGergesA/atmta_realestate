# -*- coding: utf-8 -*-
"""The status dimensions and the legacy `state` bridge, owned by Property Core.

This model used to live in `atmta_real_estate`. Developer and Brokerage depend on
it without Rental: their older code writes `state`, their newer code writes
`commercial_status`, and the stock lifecycle reads `state`. These tests pin the
bridge in both directions using Property Core fields only, so they run with no
leasing installed. Rental's own suite covers the occupancy rule it adds.
"""

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_property_status')
class TestPropertyStatusBridge(TransactionCase):

    def _unit(self, code, **vals):
        return self.env['realestate.property'].create({
            'name': 'Status unit %s' % code,
            'property_code': 'STATUS-%s' % code,
            'hierarchy_level': 'unit',
            'company_id': self.env.company.id,
            **vals,
        })

    def test_a_new_unit_is_commercially_available(self):
        unit = self._unit('NEW')
        self.assertEqual(unit.commercial_status, 'available')
        self.assertEqual(unit.maintenance_status, 'normal')
        self.assertEqual(unit.state, 'available')

    def test_a_legacy_state_write_lands_on_the_dimension_its_writer_owns(self):
        unit = self._unit('LEGACY')
        unit.state = 'reserved'
        self.assertEqual(unit.commercial_status, 'reserved')
        self.assertEqual(unit.state, 'reserved')
        unit.write({'state': 'sold'})
        self.assertEqual(unit.commercial_status, 'sold')
        self.assertEqual(unit.state, 'sold')

    def test_a_dimension_write_is_reflected_in_state(self):
        unit = self._unit('DIMENSION')
        unit.commercial_status = 'contracted'
        self.assertEqual(unit.state, 'reserved')
        unit.commercial_status = 'sold'
        self.assertEqual(unit.state, 'sold')

    def test_an_explicit_dimension_beats_a_legacy_write_in_the_same_call(self):
        unit = self._unit('BOTH')
        unit.write({'state': 'reserved', 'commercial_status': 'held'})
        self.assertEqual(unit.commercial_status, 'held')

    def test_maintenance_outranks_the_sales_pipeline(self):
        unit = self._unit('MAINT', commercial_status='sold')
        unit.maintenance_status = 'maintenance'
        self.assertEqual(unit.state, 'maintenance')
        self.assertEqual(unit.commercial_status, 'sold', 'maintenance must not clear the sale')

    def test_writing_rented_is_accepted_and_ignored(self):
        unit = self._unit('RENTED')
        unit.state = 'rented'
        self.assertEqual(unit.commercial_status, 'available')
        self.assertEqual(unit.state, 'available')

    def test_an_unreleased_unit_reads_as_inactive(self):
        unit = self._unit('UNRELEASED', commercial_status='unreleased')
        self.assertEqual(unit.state, 'inactive')

    def test_archiving_blocks_commercially_and_can_be_undone(self):
        unit = self._unit('ARCHIVE')
        unit.set_property_inactive()
        self.assertEqual(unit.commercial_status, 'blocked')
        self.assertEqual(unit.state, 'inactive')
        unit.set_property_available()
        self.assertEqual(unit.commercial_status, 'available')
        self.assertEqual(unit.state, 'available')

    def test_a_reserved_unit_is_not_archived(self):
        unit = self._unit('KEEP', commercial_status='reserved')
        unit.set_property_inactive()
        self.assertEqual(unit.commercial_status, 'reserved')

    def test_usage_is_seeded_from_the_property_type(self):
        ptype = self.env['property.type'].create({
            'name': 'Status villa type', 'usage_category': 'villa'})
        unit = self._unit('USAGE', property_type_id=ptype.id)
        self.assertEqual(unit.usage_category, 'villa')
