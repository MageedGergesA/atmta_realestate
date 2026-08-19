# -*- coding: utf-8 -*-
"""M8 — material inspection at the point of receipt.

```
    A RECEIPT SAYS THE LORRY ARRIVED.
    AN INSPECTION SAYS WHAT ON IT IS FIT TO USE.
```

The tests that matter here are the ones about **quantity**, not about state.
An inspection that records "half of this was cracked" while the receipt books
the whole load into stock is worse than no inspection at all: it produces a
record that looks like a control and changes nothing, and somebody downstream
pays for material nobody can use.

So the assertions are made against stock, against `qty_received`, against the
requisition rollup and against what is billable — not against the sheet's own
state field, which is the one thing that cannot be wrong.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .test_m8_phase0 import M8Common


class M8InspectionCommon(M8Common):

    def setUp(self):
        super().setUp()
        self.Inspection = self.env[
            'realestate.procurement.receipt.inspection']

    def _delivered_order(self, governance='optional'):
        """An issued award with its receipt waiting at the gate."""
        award, order = self._approved_award(governance, login='m8.ins.appr')
        order.order_line.taxes_id = [(5, 0, 0)]
        award.action_issue()
        order.invalidate_recordset()
        picking = order.picking_ids.filtered(
            lambda p: p.state not in ('done', 'cancel'))[:1]
        self.assertTrue(picking, "Confirmation produced no receipt.")
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.picking_type_id.create_backorder = 'never'
        return award, order, picking

    def _inspector(self, login='m8.inspector'):
        return self.env['res.users'].create({
            'name': login, 'login': login, 'email': '%s@example.com' % login,
            'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('stock.group_stock_user').id,
                self.env.ref(
                    'real_estate_procurement.group_procurement_inspector').id,
            ])],
        })

    def _set_inspection(self, policy, project=None):
        target = project if project is not None else self.project
        target.procurement_receipt_inspection = policy


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m8')
class TestM8InspectionPolicy(M8InspectionCommon):

    def test_inspection_is_off_by_default(self):
        """An upgrade must not stop the loading bay on the morning it lands."""
        self.assertEqual(
            self.env['realestate.procurement.control'].receipt_inspection_for(
                self.project, self.company), 'off',
            "Receipt inspection is on out of the box. Every receipt in every "
            "warehouse now needs an inspector who has not been appointed.")

    def test_a_required_project_refuses_an_uninspected_receipt(self):
        award, order, picking = self._delivered_order()
        self._set_inspection('required')

        with self.assertRaises(UserError) as caught:
            picking.button_validate()

        self.assertIn('inspection', str(caught.exception).lower())
        self.assertNotEqual(picking.state, 'done')

    def test_a_warn_project_records_the_gap_and_continues(self):
        """A site that wants the record without the refusal is a real position."""
        award, order, picking = self._delivered_order()
        self._set_inspection('warn')
        before = len(picking.message_ids)

        picking.button_validate()

        self.assertEqual(picking.state, 'done')
        self.assertGreater(len(picking.message_ids), before,
                           "The missing inspection was not recorded anywhere.")

    def test_an_off_project_is_not_asked_about_inspection_at_all(self):
        award, order, picking = self._delivered_order()
        self._set_inspection('off')

        picking.button_validate()

        self.assertEqual(picking.state, 'done')
        self.assertFalse(picking.re_inspection_id)

    def test_a_receipt_with_no_project_is_never_gated(self):
        """Inspection is a project control; a non-project receipt has none."""
        self.company.procurement_receipt_inspection = 'required'
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

        self.assertEqual(picking.state, 'done')


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m8')
class TestM8InspectionOutcome(M8InspectionCommon):
    """The quantity tests. This is where an inspection either means something
    or is a signature somebody collected."""

    def _inspection_sheet(self, picking):
        """Named for what it is, not `_sheet`.

        The evaluation fixtures already own `_sheet(round_, candidate, ...)`,
        and shadowing it here broke every test in this class through a helper
        three inheritance levels away — an error that pointed at
        `_advance_to_commercial` and had nothing to do with it.
        """
        return self.Inspection._for_picking(picking)

    def test_nothing_is_accepted_until_somebody_accepts_it(self):
        """A sheet that arrived pre-accepted would be signed without being read."""
        award, order, picking = self._delivered_order()

        sheet = self._inspection_sheet(picking)

        self.assertTrue(sheet.line_ids)
        self.assertEqual(sheet.accepted_qty, 0.0)
        self.assertEqual(sheet.received_qty,
                         sum(picking.move_ids.mapped('product_uom_qty')))

    def test_a_full_acceptance_receives_everything(self):
        award, order, picking = self._delivered_order()
        self._set_inspection('required')
        sheet = self._inspection_sheet(picking)
        for line in sheet.line_ids:
            line.accepted_qty = line.received_qty
        sheet.action_record()

        self.assertEqual(sheet.state, 'passed')

        picking.button_validate()

        self.assertEqual(picking.state, 'done')
        self.assertEqual(sum(order.order_line.mapped('qty_received')),
                         sum(sheet.line_ids.mapped('received_qty')))

    def test_a_partial_rejection_receives_only_what_was_accepted(self):
        """The test that decides whether any of this is worth having."""
        award, order, picking = self._delivered_order()
        self._set_inspection('required')
        sheet = self._inspection_sheet(picking)
        delivered = sum(sheet.line_ids.mapped('received_qty'))
        for line in sheet.line_ids:
            line.accepted_qty = line.received_qty / 4.0
            line.reason = 'Cracked on arrival'
        sheet.conclusion = 'Three quarters of the load arrived cracked.'
        sheet.action_record()

        self.assertEqual(sheet.state, 'partial')

        picking.button_validate()
        order.invalidate_recordset()

        received = sum(order.order_line.mapped('qty_received'))
        self.assertAlmostEqual(
            received, delivered / 4.0, places=2,
            msg="Rejected material was booked in as received. The project "
                "now owns, and will be billed for, material nobody can use.")

    def test_rejected_material_never_reaches_the_requisition(self):
        award, order, picking = self._delivered_order()
        self._set_inspection('required')
        sheet = self._inspection_sheet(picking)
        for line in sheet.line_ids:
            line.accepted_qty = line.received_qty / 2.0
            line.reason = 'Wrong grade'
        sheet.conclusion = 'Half the load is the wrong grade.'
        sheet.action_record()
        picking.button_validate()

        request_lines = order.order_line.re_material_request_line_id
        if not request_lines:
            self.skipTest("This order carries no requisition link to roll up.")
        request_lines.invalidate_recordset()

        self.assertAlmostEqual(
            sum(request_lines.mapped('received_qty')),
            sum(sheet.line_ids.mapped('accepted_qty')), places=2,
            msg="The requisition counted rejected material as delivered "
                "demand.")

    def test_a_wholly_rejected_load_is_not_validated_silently(self):
        """Somebody has to decide what happens to it. Not this screen."""
        award, order, picking = self._delivered_order()
        self._set_inspection('required')
        sheet = self._inspection_sheet(picking)
        for line in sheet.line_ids:
            line.accepted_qty = 0.0
            line.reason = 'Contaminated'
        sheet.conclusion = 'The whole load is contaminated.'
        sheet.action_record()

        self.assertEqual(sheet.state, 'failed')
        with self.assertRaises(UserError) as caught:
            picking.button_validate()

        self.assertIn('nothing on it was accepted',
                      str(caught.exception).lower())
        self.assertNotEqual(picking.state, 'done')

    def test_rejected_material_is_not_billable(self):
        """The money end of the same fact."""
        award, order, picking = self._delivered_order()
        self._set_inspection('required')
        sheet = self._inspection_sheet(picking)
        for line in sheet.line_ids:
            line.accepted_qty = line.received_qty / 2.0
            line.reason = 'Damaged'
        sheet.conclusion = 'Half damaged.'
        sheet.action_record()
        picking.button_validate()
        order.invalidate_recordset()

        billable = sum(order.order_line.mapped('qty_to_invoice'))
        accepted = sum(sheet.line_ids.mapped('accepted_qty'))

        self.assertAlmostEqual(
            billable, accepted, places=2,
            msg="The vendor can bill for material this site rejected.")


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m8')
class TestM8InspectionIntegrity(M8InspectionCommon):

    def test_accepting_more_than_arrived_is_refused(self):
        award, order, picking = self._delivered_order()
        sheet = self.Inspection._for_picking(picking)
        line = sheet.line_ids[:1]

        with self.assertRaises(ValidationError):
            line.accepted_qty = line.received_qty + 1.0

    def test_a_rejection_without_a_finding_is_refused_at_record(self):
        """At record time, not at create time.

        A sheet opens with nothing accepted, so every line is fully rejected
        the moment it exists. Making the finding a constraint refused to open
        the sheet at all — which the first version of this model did, and the
        tests caught.
        """
        award, order, picking = self._delivered_order()
        sheet = self.Inspection._for_picking(picking)
        sheet.conclusion = 'Load refused.'
        for line in sheet.line_ids:
            line.accepted_qty = 0.0

        with self.assertRaises(UserError) as caught:
            sheet.action_record()

        self.assertIn('no finding recorded', str(caught.exception).lower())

    def test_a_draft_sheet_opens_with_nothing_accepted_and_no_complaint(self):
        """The other half of the same decision, so it cannot regress quietly."""
        award, order, picking = self._delivered_order()

        sheet = self.Inspection._for_picking(picking)

        self.assertTrue(sheet.line_ids)
        self.assertEqual(sheet.accepted_qty, 0.0)
        self.assertGreater(sheet.rejected_qty, 0.0)

    def test_a_rejection_without_a_conclusion_is_refused(self):
        award, order, picking = self._delivered_order()
        sheet = self.Inspection._for_picking(picking)
        for line in sheet.line_ids:
            line.accepted_qty = line.received_qty / 2.0
            line.reason = 'Damaged'

        with self.assertRaises(UserError) as caught:
            sheet.action_record()

        self.assertIn('why', str(caught.exception).lower())

    def test_one_receipt_has_one_inspection(self):
        award, order, picking = self._delivered_order()
        first = self.Inspection._for_picking(picking)

        second = self.Inspection._for_picking(picking)

        self.assertEqual(first, second,
                         "A second sheet was opened on the same delivery.")

    def test_a_recorded_result_is_not_edited_away(self):
        award, order, picking = self._delivered_order()
        sheet = self.Inspection._for_picking(picking)
        for line in sheet.line_ids:
            line.accepted_qty = line.received_qty
        sheet.action_record()

        with self.assertRaises(UserError):
            sheet.action_record()
        with self.assertRaises(UserError):
            sheet.action_cancel(reason='Changed my mind')

    def test_an_empty_sheet_is_not_a_pass(self):
        award, order, picking = self._delivered_order()
        sheet = self.Inspection._for_picking(picking)
        sheet.line_ids.unlink()

        with self.assertRaises(UserError):
            sheet.action_record()


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m8')
class TestM8InspectionAuthority(M8InspectionCommon):
    """The person who chose the vendor is not the person who certifies the
    vendor's material. That separation is the point of an inspection."""

    def test_a_buyer_cannot_record_an_inspection_result(self):
        award, order, picking = self._delivered_order()
        sheet = self.Inspection._for_picking(picking)
        for line in sheet.line_ids:
            line.accepted_qty = line.received_qty
        buyer = self._purchase_user('m8.buyer.ins')

        with self.assertRaises(UserError):
            sheet.with_user(buyer).action_record()

    def test_an_inspector_may_record_one(self):
        award, order, picking = self._delivered_order()
        sheet = self.Inspection._for_picking(picking)
        for line in sheet.line_ids:
            line.accepted_qty = line.received_qty
        inspector = self._inspector()

        sheet.with_user(inspector).action_record()

        self.assertEqual(sheet.state, 'passed')
        self.assertEqual(sheet.inspector_id, inspector,
                         "The sheet does not name who inspected it.")

    def test_an_inspector_gets_no_buying_authority_from_it(self):
        """Accepting material is not approving a purchase."""
        inspector = self._inspector('m8.ins.only')

        for group in ('real_estate_procurement.group_procurement_user',
                      'real_estate_procurement.group_procurement_approver',
                      'real_estate_procurement.group_procurement_manager'):
            self.assertFalse(
                inspector.has_group(group),
                "The inspector group carries %s, so inspecting and buying are "
                "the same person again." % group)
