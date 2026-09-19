# -*- coding: utf-8 -*-
"""M2 — price books, premiums, history and versioning.

The property these tests protect is Rule 5: **a price that has been approved
never changes underneath a deal**. Everything else here is arithmetic.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import DeveloperCommon


@tagged('post_install', '-at_install', 'atmta_developer')
class TestPriceBook(DeveloperCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Book = cls.env['realestate.price.book']
        cls.Rule = cls.env['realestate.price.rule']
        cls.History = cls.env['realestate.price.history']

    def _book(self, **kwargs):
        vals = {
            'name': 'Launch Price Book',
            'project_id': self.project.id,
            'company_id': self.company.id,
        }
        vals.update(kwargs)
        return self.Book.create(vals)

    def _line(self, book, unit, **kwargs):
        vals = {
            'price_book_id': book.id,
            'property_id': unit.id,
            'base_mode': 'amount',
            'base_amount': 1000000.0,
        }
        vals.update(kwargs)
        return self.env['realestate.price.book.line'].create(vals)

    # ---- base price ----

    def test_lump_sum_base(self):
        book = self._book()
        line = self._line(book, self.units[0], base_amount=2500000.0)
        self.assertEqual(line.base_price, 2500000.0)
        self.assertEqual(line.list_price, 2500000.0)

    def test_rate_per_sqm_base(self):
        book = self._book()
        unit = self.units[0]  # area 101 m²
        line = self._line(book, unit, base_mode='per_sqm', base_rate_sqm=20000.0)
        self.assertEqual(line.base_price, 20000.0 * unit.area_sqm)
        self.assertEqual(line.price_per_sqm, 20000.0)

    def test_a_unit_can_only_be_priced_once_per_book(self):
        book = self._book()
        self._line(book, self.units[0])
        with self.assertRaises(Exception):
            self._line(book, self.units[0])
            self.env.flush_all()

    # ---- premiums ----

    def test_percent_premium(self):
        book = self._book()
        self.Rule.create({
            'name': 'Sea View', 'price_book_id': book.id,
            'premium_type': 'view', 'calculation_type': 'percent', 'value': 10.0,
        })
        line = self._line(book, self.units[0], base_amount=1000000.0)
        self.assertEqual(line.premium_total, 100000.0)
        self.assertEqual(line.list_price, 1100000.0)

    def test_fixed_and_per_sqm_premiums_stack(self):
        book = self._book()
        unit = self.units[0]
        self.Rule.create({
            'name': 'Parking', 'price_book_id': book.id,
            'premium_type': 'parking', 'calculation_type': 'fixed',
            'value': 150000.0,
        })
        self.Rule.create({
            'name': 'Finishing', 'price_book_id': book.id,
            'premium_type': 'finishing', 'calculation_type': 'per_sqm',
            'value': 1000.0,
        })
        line = self._line(book, unit, base_amount=1000000.0)
        expected = 150000.0 + 1000.0 * unit.area_sqm
        self.assertEqual(line.premium_total, expected)

    def test_premium_conditions_restrict(self):
        """A rule with an area band must not touch units outside it."""
        book = self._book()
        self.Rule.create({
            'name': 'Large Unit', 'price_book_id': book.id,
            'premium_type': 'custom', 'calculation_type': 'percent',
            'value': 5.0, 'area_from': 500.0,
        })
        line = self._line(book, self.units[0], base_amount=1000000.0)
        self.assertEqual(line.premium_total, 0.0,
                         "a 101 m² unit is not in a 500 m²+ band")

    def test_absurd_percent_premium_is_rejected(self):
        book = self._book()
        with self.assertRaises(ValidationError):
            self.Rule.create({
                'name': 'Typo', 'price_book_id': book.id,
                'premium_type': 'custom', 'calculation_type': 'percent',
                'value': 500.0,
            })

    def test_manual_adjustment_is_its_own_component(self):
        book = self._book()
        line = self._line(book, self.units[0], base_amount=1000000.0,
                          manual_adjustment=-50000.0,
                          adjustment_reason='Corner defect')
        self.assertEqual(line.list_price, 950000.0)
        components = line._component_values()
        manual = [c for c in components if c['name'] == 'Corner defect']
        self.assertEqual(len(manual), 1,
                         "a hand-made adjustment must never look like a rule "
                         "outcome")

    # ---- immutability and versioning (Rule 5, Phase 13) ----

    def test_approved_book_pricing_cannot_be_edited(self):
        book = self._book()
        self._line(book, self.units[0])
        book.action_submit()
        book.action_approve()
        with self.assertRaises(UserError):
            book.line_ids[0].unlink()
            book.write({'valid_from': False})
        with self.assertRaises(UserError):
            book.write({'project_id': self.project.id, 'valid_from': False})

    def test_active_book_cannot_be_repriced_in_place(self):
        book = self._book()
        self._line(book, self.units[0])
        book.action_submit()
        book.action_approve()
        book.action_activate()
        with self.assertRaises(UserError):
            book.write({'currency_id': self.company.currency_id.id,
                        'valid_from': False})

    def test_new_version_leaves_the_old_book_untouched(self):
        book = self._book()
        self._line(book, self.units[0], base_amount=1000000.0)
        book.action_submit()
        book.action_approve()
        book.action_activate()

        action = book.action_new_version()
        new_book = self.Book.browse(action['res_id'])
        self.assertEqual(new_book.version, 2)
        self.assertEqual(new_book.previous_version_id, book)
        self.assertEqual(new_book.state, 'draft')
        self.assertEqual(len(new_book.line_ids), 1,
                         "a new version starts from the old prices")

        new_book.line_ids[0].base_amount = 1200000.0
        self.assertEqual(
            book.line_ids[0].base_price, 1000000.0,
            "repricing v2 must not restate v1 — deals were signed against v1")

    def test_activating_expires_the_previous_book(self):
        first = self._book(name='V1')
        self._line(first, self.units[0])
        first.action_submit(); first.action_approve(); first.action_activate()

        second = self._book(name='V2', version=2)
        self._line(second, self.units[0], base_amount=1100000.0)
        second.action_submit(); second.action_approve(); second.action_activate()

        self.assertEqual(first.state, 'expired')
        self.assertEqual(second.state, 'active')

    def test_only_draft_books_can_be_deleted(self):
        book = self._book()
        self._line(book, self.units[0])
        book.action_submit()
        book.action_approve()
        with self.assertRaises(UserError):
            book.unlink()

    def test_empty_book_cannot_be_submitted(self):
        book = self._book()
        with self.assertRaises(UserError):
            book.action_submit()

    def test_validity_window_must_be_ordered(self):
        from datetime import date
        with self.assertRaises(ValidationError):
            self._book(valid_from=date(2026, 6, 1), valid_until=date(2026, 5, 1))

    # ---- application and history (Phase 7) ----

    def test_activation_writes_prices_and_history(self):
        unit = self.units[0]
        unit.base_price = 900000.0
        book = self._book()
        self._line(book, unit, base_amount=1000000.0)
        book.action_submit(); book.action_approve(); book.action_activate()

        self.assertEqual(unit.base_price, 1000000.0)
        history = self.History.search([('property_id', '=', unit.id)])
        self.assertEqual(len(history), 1)
        self.assertEqual(history.old_price, 900000.0)
        self.assertEqual(history.new_price, 1000000.0)
        self.assertEqual(history.delta, 100000.0)
        self.assertAlmostEqual(history.delta_percent, 11.11, places=1)
        self.assertEqual(history.price_book_id, book)

    def test_activation_records_the_price_build_up(self):
        unit = self.units[0]
        book = self._book()
        self.Rule.create({
            'name': 'Sea View', 'price_book_id': book.id,
            'premium_type': 'view', 'calculation_type': 'percent', 'value': 10.0,
        })
        self._line(book, unit, base_amount=1000000.0)
        book.action_submit(); book.action_approve(); book.action_activate()

        components = unit.price_component_ids
        self.assertEqual(len(components), 2)
        self.assertEqual(
            sum(components.mapped('amount')), 1100000.0)
        self.assertEqual(unit.premium_total, 100000.0)
        self.assertEqual(unit.list_price_developer, 1100000.0)

    def test_history_cannot_be_edited_or_deleted(self):
        unit = self.units[0]
        unit.base_price = 900000.0
        book = self._book()
        self._line(book, unit, base_amount=1000000.0)
        book.action_submit(); book.action_approve(); book.action_activate()
        history = self.History.search([('property_id', '=', unit.id)])

        with self.assertRaises(UserError):
            history.write({'new_price': 1.0})
        with self.assertRaises(UserError):
            history.unlink()

    def test_unpriced_unit_reports_its_own_base_price(self):
        """No book yet is not the same as a price of zero."""
        unit = self.units[0]
        unit.base_price = 750000.0
        self.assertEqual(unit.list_price_developer, 750000.0)
        self.assertEqual(unit.premium_total, 0.0)

    # ---- Phase 40: public vs internal price ----

    def test_public_price_is_withheld_for_unreleased_units(self):
        """The audit found base_price served from a public endpoint."""
        unit = self.units[0]
        unit.base_price = 1000000.0
        self.assertFalse(unit.is_available_for_sale)
        self.assertEqual(
            unit._public_price(), 0.0,
            "an unreleased unit has no public price, and guessing one leaks "
            "the pricing of inventory that has not launched")

        self._open_project_for_sales()
        self._release(unit)
        self.assertEqual(unit._public_price(), unit.list_price_developer)
