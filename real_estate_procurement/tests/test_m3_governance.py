# -*- coding: utf-8 -*-
"""M3K — the purchase-order confirmation boundary.

Phase 0's third bypass was the widest: anybody who could confirm a purchase
order could commit a construction budget with no requisition, no approval, no
enquiry and no award. Procurement was, in effect, optional.

M3 does not fix that by taking the Purchase app away from purchasing people.
It adds one question immediately before confirmation, on the server, for
orders that are coded to a construction project — and leaves the office
stationery alone.

```
    OPTIONAL     direct project purchase orders confirm as they always did
    CONTROLLED   requisition, or an authorised direct-purchase exception
    REQUIRED     approved requisition only
```

The default is OPTIONAL, deliberately. Turning governance on retroactively
invalidates every project purchase order path an existing installation
depends on, and that is an operational decision with a runbook — not a side
effect of installing a new version.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import M3Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM3PurchaseGovernance(M3Common):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        self.buyer = self._purchase_user(
            'm3.gov.buyer.%d' % self._next(),
            'real_estate_construction.group_construction_user',
            'real_estate_developer.group_dev_readonly')

    def _direct_order(self, amount=3_000_000.0, code=True, project=True):
        return self.PO.create({
            'partner_id': self.vendor.id,
            're_project_id': self.project.id if project else False,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': 'Direct purchase',
                'product_qty': 1.0,
                'price_unit': amount,
                'taxes_id': [(5, 0, 0)],
                're_cost_code_id': self.concrete.id if code else False,
            })],
        })

    # -- TEST J --------------------------------------------------------
    def test_j_a_direct_order_cannot_confirm_under_required(self):
        self._set_po_governance('required', project=self.project)
        order = self._direct_order()

        with self.assertRaises(UserError):
            order.button_confirm()

        order.invalidate_recordset()
        self.assertEqual(order.state, 'draft',
                         "The quotation survives. Only the commitment is "
                         "refused.")
        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(order.re_governance_status, 'direct')

    def test_j2_the_gate_is_server_side_not_a_button(self):
        """The same refusal for anything that calls the method.

        RPC, an import, a scheduled action and another module's code all
        arrive here. Phase 0's finding was never about who could see a button.
        """
        self._set_po_governance('required', project=self.project)
        order = self._direct_order()

        with self.assertRaises(UserError):
            order.with_user(self.buyer).sudo().button_confirm()
        self.assertEqual(self._commitment(self.project), 0.0)

    def test_j3_an_unapproved_requisition_does_not_authorise_an_order(self):
        self._set_po_governance('controlled', project=self.project)
        request = self._demand(1_000.0, approve=False)
        request.with_context(re_procurement_revision=True).write(
            {'state': 'approved'})
        request.action_create_rfqs(vendors=self.vendor)
        order = request.purchase_order_ids
        order.order_line.taxes_id = [(5, 0, 0)]
        request.with_context(re_procurement_revision=True).write(
            {'state': 'draft'})

        with self.assertRaises(UserError):
            order.button_confirm()
        self.assertEqual(self._commitment(self.project), 0.0)

    # -- TEST K --------------------------------------------------------
    def test_k_optional_governance_leaves_odoo_purchasing_alone(self):
        self._set_po_governance('optional', project=self.project)
        order = self._direct_order()

        order.button_confirm()

        self.assertEqual(order.state, 'purchase')
        self.assertEqual(self._commitment(self.project), 3_000_000.0)
        self.assertFalse(
            self.env['realestate.material.request'].search(
                [('purchase_order_ids', 'in', order.ids)]),
            "Procurement does not invent a requisition to make its own "
            "records look complete.")
        self.assertFalse(order.re_exception_id)

    def test_k2_a_non_project_purchase_is_never_gated(self):
        """Office stationery is not a construction commitment."""
        self._set_po_governance('required')
        order = self.PO.create({
            'partner_id': self.vendor.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': 'Printer paper',
                'product_qty': 1.0,
                'price_unit': 500.0,
                'taxes_id': [(5, 0, 0)],
            })],
        })

        order.button_confirm()
        self.assertEqual(order.state, 'purchase')
        self.assertEqual(order.re_governance_status, 'not_project')

    # -- TEST L --------------------------------------------------------
    def test_l_an_authorised_exception_lets_a_direct_order_confirm(self):
        self._set_po_governance('controlled', project=self.project)
        order = self._direct_order()
        with self.assertRaises(UserError):
            order.button_confirm()

        wizard = self.env['realestate.procurement.purchase.exception'].create({
            'order_id': order.id,
            'reason': 'Sole distributor for the specified pump.',
        })
        wizard.action_request()
        exception = order.re_exception_id
        self.assertEqual(exception.exception_type, 'direct_purchase')
        self.assertEqual(exception.state, 'requested')
        self.assertEqual(exception.requested_amount, 3_000_000.0)

        exception.action_approve()
        order.button_confirm()

        self.assertEqual(order.state, 'purchase')
        self.assertEqual(self._commitment(self.project), 3_000_000.0)
        self.assertEqual(exception.state, 'approved')
        self.assertTrue(exception.reason)
        self.assertEqual(order.re_exception_id, exception)

    def test_l2_a_rejected_exception_authorises_nothing(self):
        self._set_po_governance('controlled', project=self.project)
        order = self._direct_order()
        exception = self.env[
            'realestate.procurement.control.exception'].create({
                'exception_type': 'direct_purchase',
                'company_id': self.company.id,
                'project_id': self.project.id,
                'purchase_order_id': order.id,
                'requested_amount': 3_000_000.0,
                'reason': 'Wanted to skip the queue.',
            })
        exception.decision_note = 'Raise a requisition like everyone else.'
        exception.action_reject()

        with self.assertRaises(UserError):
            order.button_confirm()
        self.assertEqual(self._commitment(self.project), 0.0)

    # -- Coding completeness -------------------------------------------
    def test_a_governed_order_must_say_what_kind_of_money_it_is(self):
        self._set_po_governance('controlled', project=self.project)
        order = self._direct_order(code=False)
        exception = self.env[
            'realestate.procurement.control.exception'].create({
                'exception_type': 'direct_purchase',
                'company_id': self.company.id,
                'project_id': self.project.id,
                'purchase_order_id': order.id,
                'requested_amount': 3_000_000.0,
                'reason': 'Emergency plant hire.',
            })
        exception.action_approve()

        with self.assertRaises(UserError):
            order.button_confirm()

    def test_a_requisition_backed_order_confirms_under_required(self):
        """The governed path works end to end for the people using it."""
        self._set_po_governance('required', project=self.project)
        request = self._demand(2_000.0)
        request.action_create_rfqs(vendors=self.vendor)
        order = request.purchase_order_ids
        self._confirm(order)

        self.assertEqual(order.state, 'purchase')
        self.assertEqual(order.re_governance_status, 'linked')
        self.assertEqual(self._commitment(self.project), 2_000_000.0)
        self.assertEqual(self._reserved(self.project), 0.0)
        self.assertEqual(request.state, 'ordered')

    def test_the_project_overrides_the_company_governance(self):
        Control = self.env['realestate.procurement.control']
        self._set_po_governance('required')
        relaxed = self._project()
        relaxed.procurement_po_governance = 'optional'

        self.assertEqual(Control.po_governance_for(self.project), 'required')
        self.assertEqual(Control.po_governance_for(relaxed), 'optional')
