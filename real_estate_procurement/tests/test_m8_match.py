# -*- coding: utf-8 -*-
"""M8 — the three-way match.

```
    ORDERED  — what was authorised
    RECEIVED — what arrived and was accepted
    BILLED   — what the vendor says is owed
```

The tests here are about what gets **posted**, because a bill that is refused
in draft is a conversation and a bill that is posted is a liability. Every
assertion is made after `action_post()` has been attempted, not after a
validation helper has been called.

The case that matters most is the last one: a bill that is under on one line
and over on another. It nets to something reasonable, and it is still paying
for material that never arrived. Netting is precisely how that stops being
visible, which is why the match is per line.
"""

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_m8_inspection import M8InspectionCommon


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m8')
class TestM8ThreeWayMatch(M8InspectionCommon):

    def _received_order(self, ratio=1.0):
        """An issued, confirmed order with `ratio` of it received."""
        award, order, picking = self._delivered_order()
        if ratio < 1.0:
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty * ratio
            picking.picking_type_id.create_backorder = 'never'
        picking.button_validate()
        order.invalidate_recordset()
        return award, order, picking

    def _draft_bill(self, order, factor=1.0):
        """A draft vendor bill, optionally for a multiple of what is billable."""
        invoice = self.env['account.move'].browse(
            order.with_context(create_bill=True)
                 .action_create_invoice()['res_id'])
        invoice.invoice_date = invoice.invoice_date or \
            fields.Date.context_today(order)
        if factor != 1.0:
            for line in invoice.invoice_line_ids.filtered('purchase_line_id'):
                line.quantity = line.quantity * factor
        return invoice

    # ------------------------------------------------------------------
    def test_a_bill_for_what_arrived_posts(self):
        """The control has to let the ordinary case through, or it is a wall."""
        award, order, picking = self._received_order()

        invoice = self._draft_bill(order)
        invoice.action_post()

        self.assertEqual(invoice.state, 'posted')

    def test_a_bill_for_more_than_arrived_is_refused(self):
        award, order, picking = self._received_order()
        invoice = self._draft_bill(order, factor=2.0)

        with self.assertRaises(UserError) as caught:
            invoice.action_post()

        self.assertIn('more than this site accepted',
                      str(caught.exception).lower())
        self.assertNotEqual(invoice.state, 'posted',
                            "The over-bill posted anyway.")

    def test_a_short_bill_posts_without_complaint(self):
        """Under-billing is normal and is nobody's emergency."""
        award, order, picking = self._received_order()
        invoice = self._draft_bill(order, factor=0.5)

        invoice.action_post()

        self.assertEqual(invoice.state, 'posted')

    def test_billing_the_rejected_quantity_is_refused(self):
        """The money end of the inspection.

        Half the load is rejected, so half of it never becomes `qty_received`,
        so a bill for the whole delivery is a bill for material this site
        refused.
        """
        award, order, picking = self._delivered_order()
        self._set_inspection('required')
        sheet = self.Inspection._for_picking(picking)
        for line in sheet.line_ids:
            line.accepted_qty = line.received_qty / 2.0
            line.reason = 'Damaged in transit'
        sheet.conclusion = 'Half the load was damaged in transit.'
        sheet.action_record()
        picking.button_validate()
        order.invalidate_recordset()

        invoice = self._draft_bill(order, factor=2.0)

        with self.assertRaises(UserError):
            invoice.action_post()

    def test_a_second_bill_cannot_finish_what_the_first_started(self):
        """Already-billed quantity counts. Two whole bills are not one bill.

        The second bill is built explicitly rather than through
        `action_create_invoice()`, which refuses to produce one at all once
        everything is billed. That refusal is native Purchase protecting
        itself, and going through it would mean this test never reached the
        M8 gate — which is the thing it exists to exercise. A vendor sending a
        duplicate invoice, or a clerk keying one by hand, arrives exactly this
        way.
        """
        award, order, picking = self._received_order()
        first = self._draft_bill(order)
        first.action_post()
        order.invalidate_recordset()

        journal = self.env['account.journal'].search([
            ('type', '=', 'purchase'),
            ('company_id', '=', self.company.id)], limit=1)
        lines = order.order_line.filtered(
            lambda line: not line.display_type and line.qty_received)
        second = self.env['account.move'].with_company(self.company).create({
            'move_type': 'in_invoice',
            'partner_id': order.partner_id.id,
            'invoice_date': fields.Date.context_today(order),
            'journal_id': journal.id,
            'invoice_line_ids': [(0, 0, {
                'name': line.name,
                'product_id': line.product_id.id,
                'quantity': line.qty_received,
                'price_unit': line.price_unit,
                'purchase_line_id': line.id,
            }) for line in lines],
        })

        with self.assertRaises(UserError) as caught:
            second.action_post()

        self.assertIn('more than this site accepted',
                      str(caught.exception).lower())
        self.assertNotEqual(second.state, 'posted',
                            "The same delivery was billed twice.")

    def test_a_bill_under_on_one_line_and_over_on_another_is_refused(self):
        """Netting is how paying for absent material stops being visible."""
        award, order, picking = self._received_order()
        invoice = self._draft_bill(order)
        lines = invoice.invoice_line_ids.filtered('purchase_line_id')
        if len(lines) < 2:
            self.skipTest("This order has one line, so there is nothing to "
                          "net against.")
        lines[0].quantity = lines[0].quantity * 3.0
        lines[1].quantity = 0.0

        with self.assertRaises(UserError):
            invoice.action_post()

    def test_a_non_project_bill_is_never_gated(self):
        """The match is a project control and says nothing about other buying."""
        order = self.env['purchase.order'].create({
            'partner_id': self.vendor_a.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 4.0,
                'price_unit': 100.0,
                'name': self.product.name,
                'date_planned': self._close_at(),
            })],
        })
        order.order_line.taxes_id = [(5, 0, 0)]
        order.button_confirm()
        picking = order.picking_ids[:1]
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.button_validate()
        order.invalidate_recordset()

        invoice = self._draft_bill(order, factor=5.0)
        invoice.action_post()

        self.assertEqual(invoice.state, 'posted',
                         "A purchase with no project was gated by a project "
                         "control.")

    def test_the_tolerance_is_configuration_and_not_a_hard_coded_number(self):
        """Set the company tolerance and the same bill becomes acceptable."""
        award, order, picking = self._received_order()
        invoice = self._draft_bill(order, factor=1.05)

        with self.assertRaises(UserError):
            invoice.action_post()

        self.company.procurement_amount_tolerance_pct = 10.0
        invoice.invalidate_recordset()
        invoice.action_post()

        self.assertEqual(invoice.state, 'posted')
