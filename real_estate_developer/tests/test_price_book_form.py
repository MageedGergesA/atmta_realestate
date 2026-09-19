# -*- coding: utf-8 -*-
"""Price books edited through their form, as the web client edits them.

A line's list price is computed from the book's premium rules. On the form the
rules being added are unsaved records, and sorting them by id raised
``TypeError: '<' not supported between instances of 'NewId' and 'NewId'`` --
something a test that creates rules with ``create()`` never reaches.
"""

from odoo.tests import Form, tagged

from .common import DeveloperCommon


@tagged('post_install', '-at_install', 'atmta_developer')
class TestPriceBookForm(DeveloperCommon):

    def _add_rule(self, form, name, calculation, value):
        with form.rule_ids.new() as rule:
            rule.name = name
            rule.calculation_type = calculation
            rule.value = value

    def test_premium_rules_added_on_a_new_book(self):
        form = Form(self.env['realestate.price.book'])
        form.name = 'Form Price Book'
        form.project_id = self.project
        with form.line_ids.new() as line:
            line.property_id = self.units[0]
            line.base_mode = 'amount'
            line.base_amount = 1000000.0
        self._add_rule(form, 'Launch premium', 'percent', 5.0)
        self._add_rule(form, 'Corner premium', 'fixed', 20000.0)
        book = form.save()

        self.assertAlmostEqual(book.line_ids.premium_total, 70000.0, places=2)
        self.assertAlmostEqual(book.line_ids.list_price, 1070000.0, places=2)

    def test_a_rule_added_to_a_saved_book_reprices_its_lines(self):
        book = self.env['realestate.price.book'].create({
            'name': 'Saved Price Book',
            'project_id': self.project.id,
            'company_id': self.company.id,
        })
        self.env['realestate.price.book.line'].create({
            'price_book_id': book.id,
            'property_id': self.units[0].id,
            'base_mode': 'amount',
            'base_amount': 1000000.0,
        })
        self.env['realestate.price.rule'].create({
            'price_book_id': book.id, 'name': 'Launch premium',
            'calculation_type': 'percent', 'value': 5.0,
        })

        form = Form(book)
        self._add_rule(form, 'Corner premium', 'fixed', 20000.0)
        form.save()

        self.assertAlmostEqual(book.line_ids.list_price, 1070000.0, places=2)
