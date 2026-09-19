# -*- coding: utf-8 -*-
"""The unit inventory lifecycle, and which module owns the locations it uses.

`models/property_stock.py` moves a unit's single quant between four locations as
the unit's state changes. Both the locations and that code used to live in
`atmta_real_estate`, and three of the lookups that find the locations are
unguarded `env.ref` calls, so a wrong identifier raises rather than degrades.
Before these tests nothing in the suite exercised any of it: a sweep for
`stock.quant` or `stock_location_re_` across every test file found nothing.

They build units with Property Core fields only, so they run without Rental.

Every expected location is resolved through `atmta_property_stock`, so these
fail if the ownership moves again without the code following.
"""

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestUnitStockLocations(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Quant = cls.env['stock.quant'].sudo()
        cls.root = cls.env.ref('atmta_property_stock.stock_location_re_root')
        cls.unassigned = cls.env.ref('atmta_property_stock.stock_location_re_unassigned')
        cls.reserved = cls.env.ref('atmta_property_stock.stock_location_re_reserved')
        cls.sold = cls.env.ref('atmta_property_stock.stock_location_re_sold')
        cls.unit = cls._property('STK-LIFE-U', 'unit')

    @classmethod
    def _property(cls, code, level):
        return cls.env['realestate.property'].create({
            'name': 'Stock lifecycle %s' % code,
            'property_code': code,
            'hierarchy_level': level,
            'area_sqm': 60.0,
            'company_id': cls.env.company.id,
        })

    def _where(self, record):
        """Locations under the real-estate root holding this record's product,
        with the quantity at each -- read the way the lifecycle writes."""
        quants = self.Quant.search([
            ('product_id', '=', record.product_variant_id.id),
            ('location_id', 'child_of', self.root.id),
        ])
        return {q.location_id: q.quantity for q in quants if q.quantity}

    def test_the_four_locations_belong_to_the_property_stock_module(self):
        for location in (self.root, self.unassigned, self.reserved, self.sold):
            data = self.env['ir.model.data'].search([
                ('model', '=', 'stock.location'),
                ('res_id', '=', location.id),
            ])
            self.assertEqual(
                data.module, 'atmta_property_stock',
                '%s is owned by %s' % (location.display_name, data.module))
        for child in (self.unassigned, self.reserved, self.sold):
            self.assertEqual(child.location_id, self.root)

    def test_a_new_unit_starts_with_one_quant_at_home(self):
        self.assertTrue(self.unit.is_storable)
        self.assertEqual(self._where(self.unit), {self.unassigned: 1.0})

    def test_reserving_moves_the_quant_and_leaves_nothing_behind(self):
        self.unit.state = 'reserved'
        self.assertEqual(self._where(self.unit), {self.reserved: 1.0})

    def test_selling_moves_the_quant_to_the_sold_location(self):
        self.unit.state = 'sold'
        self.assertEqual(self._where(self.unit), {self.sold: 1.0})

    def test_coming_back_to_available_returns_the_quant_home(self):
        self.unit.state = 'reserved'
        self.unit.state = 'available'
        self.assertEqual(self._where(self.unit), {self.unassigned: 1.0})

    def test_a_building_is_not_stock_tracked(self):
        building = self._property('STK-LIFE-B', 'building')
        self.assertEqual(self._where(building), {})
