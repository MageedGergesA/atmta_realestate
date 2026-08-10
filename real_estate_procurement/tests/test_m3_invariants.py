# -*- coding: utf-8 -*-
"""M3 — the five invariants, written before the models that satisfy them.

M2 stopped a requisition from becoming a confirmed purchase order in one
click. What it deliberately did **not** do was claim any budget control
existed: an approved 3,000,000 requisition consumed nothing, so ten of them
could be approved against a 10,000,000 budget and every one of them looked
fine until the orders arrived.

M3 introduces the missing control stage. The single sentence the whole
milestone has to be true to:

```
    ONE ECONOMIC OBLIGATION EXISTS IN ONE CONTROL STAGE AT A TIME.
```

Approved demand consumes purchasing capacity as a **reservation**. Confirming
the order converts that reservation into a Construction **commitment**. What
must never happen — the failure mode these tests exist to prevent — is the two
coexisting, so that 3,000,000 of demand reads as 6,000,000 of exposure.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ProcurementCommon


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM3Invariants(ProcurementCommon):

    def setUp(self):
        super().setUp()
        self._require_construction()
        self.project = self._project()
        self.wbs = self._wbs(self.project, 'A.1')
        self.concrete = self._cost_code('M3-CONC', 'Concrete', 'material')
        self._baselined_budget(self.project, 10_000_000.0, self.concrete)
        self.vendor = self._vendor()
        self.product = self._product(price=1_000.0, vendor=self.vendor)

    def _demand(self, qty, unit=1_000.0, **kwargs):
        """An approved requisition for `qty * unit`, fully coded."""
        request = self._request(
            self.project, [(self.product, qty)],
            line_defaults={'cost_code_id': self.concrete.id,
                           'wbs_id': self.wbs.id,
                           'estimated_unit_cost': unit},
            **kwargs)
        request.action_submit()
        if request.state == 'submitted':
            request.action_approve()
        return request

    # -- 1 -------------------------------------------------------------
    def test_1_reservation_is_not_commitment(self):
        """Budget 10M, approved requisition 3M → reserve 3M, commit nothing.

        The reservation consumes purchasing capacity. It is not an accounting
        encumbrance, not a journal entry and not a Construction commitment —
        nobody is owed anything, and the cost report must still say so.
        """
        request = self._demand(3_000.0)

        self.assertEqual(request.state, 'approved')
        self.assertEqual(request.reserved_amount, 3_000_000.0)
        self.assertEqual(self._reserved(self.project), 3_000_000.0)

        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._actual(self.project), 0.0)

        position = self._position(self.concrete)
        self.assertEqual(position['current_budget'], 10_000_000.0)
        self.assertEqual(position['current_commitment'], 0.0)
        self.assertEqual(position['reserved'], 3_000_000.0)
        self.assertEqual(position['available'], 7_000_000.0)

    # -- 2 -------------------------------------------------------------
    def test_2_conversion_happens_exactly_once(self):
        """Confirming the order moves the obligation. It does not duplicate it.

        Before: reservation 3M, commitment 0.
        After:  reservation no longer active, commitment 3M.
        Never:  reservation 3M **and** commitment 3M.
        """
        request = self._demand(3_000.0)
        request.action_create_rfqs(vendors=self.vendor)
        order = request.purchase_order_ids
        order.order_line.taxes_id = [(5, 0, 0)]

        self.assertEqual(self._reserved(self.project), 3_000_000.0)
        self.assertEqual(self._commitment(self.project), 0.0,
                         "A draft RFQ is a question, not an obligation.")

        order.button_confirm()

        self.assertEqual(self._commitment(self.project), 3_000_000.0)
        self.assertEqual(
            self._reserved(self.project), 0.0,
            "The reservation was replaced by the commitment, not added to it.")
        self.assertEqual(
            self._reserved(self.project) + self._commitment(self.project),
            3_000_000.0,
            "3,000,000 of demand is never 6,000,000 of exposure.")

        reservation = request.reservation_ids
        self.assertEqual(len(reservation), 1)
        self.assertEqual(reservation.state, 'converted')
        self.assertEqual(reservation.amount_converted, 3_000_000.0)
        self.assertEqual(reservation.amount_active, 0.0)
        self.assertEqual(self._position(self.concrete)['available'],
                         7_000_000.0)

    # -- 3 -------------------------------------------------------------
    def test_3_two_requests_cannot_both_take_the_same_capacity(self):
        """Budget 10M. Two 8M approvals. Under BLOCK, one of them fails.

        The failure mode is a read-then-write race: both approvals read
        10,000,000 available, both reserve 8,000,000, and the project is
        16,000,000 committed against a 10,000,000 budget before anybody
        notices. `_lock_control_scope()` is what stops it; see
        `test_m3_concurrency.py` for the lock itself.
        """
        self._set_budget_policy('block')
        first = self._demand(8_000.0)
        self.assertEqual(first.reserved_amount, 8_000_000.0)

        with self.assertRaises(UserError) as caught:
            self._demand(8_000.0)
        self.assertIn('2,000,000', str(caught.exception),
                      "The refusal must say what is actually available.")

        self.assertEqual(
            self._reserved(self.project), 8_000_000.0,
            "Only one reservation may exist against a 10,000,000 budget.")

    # -- 4 -------------------------------------------------------------
    def test_4_urgent_carries_no_authority(self):
        """Priority is operational. It has never been an approval.

        Phase 0: `action_submit()` promoted an urgent request straight to
        approved, so the requester decided whether their own request needed
        approving. Urgency may shorten a lead time; it cannot authorise money.
        """
        self._set_budget_policy('block')
        request = self._request(
            self.project, [(self.product, 10_000.0)],
            line_defaults={'cost_code_id': self.concrete.id,
                           'estimated_unit_cost': 1_000.0},
            priority='1')
        request.action_submit()

        self.assertEqual(
            request.state, 'submitted',
            "Urgent still means submitted. It never meant approved.")
        self.assertFalse(request.approved_by_id)
        self.assertEqual(request.reserved_amount, 0.0,
                         "Nothing is reserved for unapproved demand.")
        self.assertEqual(self._commitment(self.project), 0.0)

    # -- 5 -------------------------------------------------------------
    def test_5_a_native_purchase_order_cannot_bypass_procurement(self):
        """A governed project PO refuses to confirm without authorisation.

        Phase 0 proved that anybody who could confirm a purchase order could
        commit a construction budget with no requisition, no approval and no
        enquiry. The gate is server-side and sits in `button_confirm()`, so
        RPC, imports and automated code meet it as well as the button does.
        """
        self._set_po_governance('required')
        # Native Purchase rights, plus the Construction read a person coding a
        # purchase order to a cost code genuinely has — anybody without it
        # cannot see the codes to type them in the first place. Deliberately
        # no procurement authority of any kind: the whole question is whether
        # Odoo's permission to confirm an order is also ATMTA's permission to
        # commit a project's budget.
        buyer = self._purchase_user(
            'm3.direct.buyer',
            'real_estate_construction.group_construction_user',
            'real_estate_developer.group_dev_readonly')
        order = self.PO.with_user(buyer).create({
            'partner_id': self.vendor.id,
            're_project_id': self.project.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': 'Direct purchase',
                'product_qty': 3_000.0,
                'price_unit': 1_000.0,
                'taxes_id': [(5, 0, 0)],
                're_cost_code_id': self.concrete.id,
                're_wbs_id': self.wbs.id,
            })],
        })

        with self.assertRaises(UserError):
            order.with_user(buyer).button_confirm()

        order.invalidate_recordset()
        self.assertEqual(order.state, 'draft',
                         "The quotation survives. Only the commitment is "
                         "refused.")
        self.assertEqual(self._commitment(self.project), 0.0)
