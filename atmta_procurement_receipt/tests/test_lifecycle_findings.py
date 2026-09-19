# -*- coding: utf-8 -*-
"""Regressions found by the procurement lifecycle run, at the loading bay."""
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'atmta_receipt')
class TestReceiptLifecycleFindings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.Inspection = env['realestate.procurement.receipt.inspection']
        cls.project = env['realestate.project'].create({
            'name': 'Receipt findings', 'code': 'RCF1',
            'company_id': env.company.id})
        cls.vendor = env['res.partner'].create({'name': 'Receipt Vendor',
                                                'supplier_rank': 1})
        cls.product = env['product.product'].create({
            'name': 'Receipt findings cement', 'type': 'consu',
            'is_storable': True, 'purchase_ok': True,
            'uom_id': env.ref('uom.product_uom_unit').id,
            'uom_po_id': env.ref('uom.product_uom_unit').id})
        cls.storekeeper = new_test_user(
            env, login='rcf_store', email='rcf_store@example.com',
            groups='base.group_user,stock.group_stock_user')

    def _ordered_request(self, qty=10.0):
        request = self.env['realestate.material.request'].create({
            'project_id': self.project.id,
            'requested_by_id': self.env.user.id,
            'line_ids': [(0, 0, {'product_id': self.product.id, 'qty': qty,
                                 'uom_id': self.product.uom_id.id,
                                 'estimated_unit_cost': 5.0})],
        })
        order = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'product_qty': qty,
                'price_unit': 5.0,
                're_material_request_line_id': request.line_ids.id,
            })],
        })
        order.button_confirm()
        request.with_context(re_procurement_revision=True).write(
            {'state': 'ordered'})
        return request, order.picking_ids

    # -- item 6: a storekeeper with no procurement role --------------------
    def test_plain_storekeeper_validates_a_requisition_linked_receipt(self):
        request, picking = self._ordered_request()
        picking.move_ids.quantity = 10.0
        picking.with_user(self.storekeeper).button_validate()
        self.assertEqual(picking.state, 'done')
        self.assertEqual(request.state, 'received')

    # -- item 11: the sheet inspects what arrived --------------------------
    def test_the_sheet_is_built_from_what_arrived(self):
        __, picking = self._ordered_request()
        picking.move_ids.quantity = 3.0
        inspection = self.Inspection._for_picking(picking)
        self.assertEqual(inspection.line_ids.received_qty, 3.0,
                         "Delivered shows the ordered quantity, not what "
                         "arrived")

    def test_acceptance_never_books_more_than_arrived(self):
        """The sheet was opened before the storekeeper counted the load."""
        __, picking = self._ordered_request()
        picking.move_ids.quantity = 10.0
        inspection = self.Inspection._for_picking(picking)
        inspection.line_ids.write({'accepted_qty': 8.0,
                                   'reason': 'Two bags split'})
        inspection.conclusion = 'Two bags split in transit.'
        inspection.action_record()
        # Only three turned up after all.
        picking.move_ids.quantity = 3.0
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')
        self.assertEqual(picking.move_ids.quantity, 3.0,
                         "the acceptance booked stock nobody delivered")
