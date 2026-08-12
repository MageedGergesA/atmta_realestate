# -*- coding: utf-8 -*-
"""M5 migration — classify the history, manufacture none of it."""

from odoo.tests import tagged

from .common import M5Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5Migration(M5Common):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        self.request = self._demand(1_000, code=self.concrete, unit=3_000.0)
        self.PO = self.env['purchase.order']

    def _legacy_rfq(self, vendor, confirm=False):
        product = self._product(price=100.0, vendor=vendor)
        order = self.PO.create({
            'partner_id': vendor.id,
            're_project_id': self.project.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'name': 'Legacy line',
                'product_qty': 1.0,
                'price_unit': 1_000.0,
                'taxes_id': [(5, 0, 0)],
            })],
        })
        if confirm:
            self._set_po_governance('optional')
            order.button_confirm()
        return order

    def test_a_legacy_rfq_is_described_not_adopted(self):
        rfq = self._legacy_rfq(self.vendor_a)
        counts = self.PO._classify_sourcing_history(rfq)

        self.assertEqual(counts, {'standalone_rfq': 1})
        self.assertEqual(rfq.re_sourcing_class, 'standalone_rfq')
        self.assertFalse(rfq.re_sourcing_event_id,
                         "A legacy RFQ was adopted into a tender.")
        self.assertFalse(
            self.env['realestate.procurement.sourcing.event'].search(
                [('company_id', '=', self.company.id)]),
            "The classification created a sourcing event.")

    def test_a_native_alternative_group_does_not_become_a_tender(self):
        first = self._legacy_rfq(self.vendor_a)
        second = self.PO.with_context(origin_po_id=first.id).create({
            'partner_id': self.vendor_b.id,
            're_project_id': self.project.id,
            'order_line': [(0, 0, {
                'product_id': self._product(price=100.0,
                                            vendor=self.vendor_b).id,
                'name': 'Legacy alternative',
                'product_qty': 1.0,
                'price_unit': 900.0,
                'taxes_id': [(5, 0, 0)],
            })],
        })
        self.assertTrue(first.purchase_group_id,
                        "The native alternative group was not created, so "
                        "this test is not exercising what it claims.")

        counts = self.PO._classify_sourcing_history(first | second)

        self.assertEqual(counts, {'native_alternative_group': 2})
        self.assertFalse(
            self.env['realestate.procurement.bid.response'].search([]),
            "Classification fabricated bid evidence from RFQ lines.")
        self.assertFalse(
            self.env['realestate.procurement.sourcing.invitation'].search([]))

    def test_a_confirmed_legacy_po_is_left_exactly_as_it_was(self):
        order = self._legacy_rfq(self.vendor_a, confirm=True)
        before = (order.state, order.amount_total, len(order.order_line))

        self.PO._classify_sourcing_history(order)

        self.assertEqual(order.re_sourcing_class, 'legacy_direct_po')
        self.assertEqual((order.state, order.amount_total,
                          len(order.order_line)), before)

    def test_an_open_rfq_against_approved_demand_is_a_sourcing_candidate(self):
        product = self.request.line_ids[:1].product_id
        order = self.PO.create({
            'partner_id': self.vendor_a.id,
            're_project_id': self.project.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'name': 'From approved demand',
                'product_qty': 10.0,
                'price_unit': 3_000.0,
                'taxes_id': [(5, 0, 0)],
                're_material_request_line_id': self.request.line_ids[:1].id,
            })],
        })
        counts = self.PO._classify_sourcing_history(order)

        self.assertEqual(counts, {'open_approved_demand_with_rfq': 1})
        self.assertEqual(order.re_sourcing_class,
                         'open_approved_demand_with_rfq')

    def test_an_atmta_tender_rfq_is_labelled_as_one(self):
        event = self._event(self.request)
        self._publish(event, [self.vendor_a])
        order = event.invitation_ids.purchase_order_id

        counts = self.PO._classify_sourcing_history(order)

        self.assertEqual(counts, {'atmta_tender': 1})

    def test_classification_is_deterministic_and_idempotent(self):
        orders = (self._legacy_rfq(self.vendor_a)
                  | self._legacy_rfq(self.vendor_b, confirm=True))
        first = self.PO._classify_sourcing_history(orders)
        labels_first = orders.mapped('re_sourcing_class')

        second = self.PO._classify_sourcing_history(orders)

        self.assertEqual(first, second)
        self.assertEqual(labels_first, orders.mapped('re_sourcing_class'))
        self.assertFalse(
            self.env['realestate.procurement.sourcing.event'].search([]))
        self.assertFalse(
            self.env['realestate.procurement.bid.response'].search([]))
