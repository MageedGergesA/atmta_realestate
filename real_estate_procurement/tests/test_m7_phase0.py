# -*- coding: utf-8 -*-
"""M7.0 — the Phase 0 audit, written as tests before anything is built.

```
    M6 EVALUATES THE BIDS.
    M7 AWARDS THE CONTRACT, AND AWARDING IS WHAT COMMITS.
```

M7 is the milestone that crosses the money boundary, so it opens the way M2
through M6 opened: by reproducing what is actually there rather than by
trusting what the previous reports said about it.

Two of these tests are **characterisation tests of defects that exist right
now**. They are written to fail against the current code, and the fix is part
of M7 rather than a side repair — §10 of this report deferred exactly this and
called it the *M7 PO Confirmation Integration Gate*.

The rest pin the seams M5 and M3 deliberately left, so that M7 fills them
instead of rebuilding machinery that already works and is already tested.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import M6Common


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7Seams(M6Common):
    """What M5 and M3 left for M7, asserted so M7 does not reinvent them."""

    def test_the_award_authorisation_hook_exists_and_answers_no(self):
        """M5 cut this seam on purpose. M7 fills it; it does not move it."""
        Order = self.env['purchase.order']

        self.assertTrue(hasattr(Order, '_award_authorisation'),
                        "M5's award hook is gone.")
        self._receive_bids()
        invitation = self.event.invitation_ids[:1]
        order = invitation.purchase_order_id

        self.assertFalse(order._award_authorisation(),
                         "Something already grants award authorisation, and "
                         "M7 has not been written yet.")
        with self.assertRaises(UserError):
            order.button_confirm()

    def test_the_reservation_conversion_machinery_is_already_built(self):
        """M3 owns reservation → commitment. M7 must trigger, not duplicate."""
        Reservation = self.env['realestate.procurement.reservation']

        for name in ('_convert', '_reverse_conversions', '_release',
                     '_sync_state'):
            self.assertTrue(hasattr(Reservation, name),
                            "M3's %s is gone; M7 was going to rely on it."
                            % name)
        Order = self.env['purchase.order']
        self.assertTrue(hasattr(Order, '_convert_reservations'))
        self.assertTrue(hasattr(Order, '_revalidate_against_approved_basis'))

    def test_construction_still_owns_the_commitment_computation(self):
        """M7 must not compute commitment. Construction does, from the order."""
        Commitment = self.env['realestate.construction.commitment']

        for name in ('po_commitment_by_cost_code',
                     'current_commitment_by_cost_code',
                     'approved_change_by_cost_code'):
            self.assertTrue(hasattr(Commitment, name),
                            "Construction's %s is gone." % name)
        # And procurement still holds no commitment of its own.
        self.assertEqual(self._commitment(self.project), 0.0)

    def test_bid_validity_is_recorded_and_can_be_revalidated_at_award(self):
        """M7 must re-check validity at award; M5 stored the date to check."""
        Response = self.env['realestate.procurement.bid.response']

        self.assertIn('validity_date', Response._fields)
        self._receive_bids()
        self.assertTrue(self.event.bid_response_ids)

    def test_vendor_eligibility_already_understands_an_award_purpose(self):
        """M4 anticipated the award moment; M7 calls it earlier, not newly."""
        Eligibility = self.env['realestate.procurement.vendor.eligibility']
        outcome = Eligibility.check_vendor_eligibility(
            self.vendor_a, company=self.company, category=None,
            project=self.project, date=self.today, purpose='award')

        self.assertIn('eligible', outcome)
        self.assertIn('blocking_reasons', outcome)


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7ConfirmationIntegrationGate(M6Common):
    """§10, reproduced. The defect this milestone was told to resolve first.

    Confirming a project-coded purchase order routes the receipt to the
    project's stock location. `realestate.project._get_stock_location()`
    creates that location **on first use** and then writes the id back onto
    the project:

    ```
        location = self.env['stock.location'].sudo().create({...})   # sudo
        self.stock_location_id = location.id                          # NOT sudo
    ```

    Two separate problems, and the second is the one §10 named:

    1. Confirming a purchase order silently creates master data. A receipt
       route is a read; provisioning a warehouse location is not.
    2. The write back onto `realestate.project` runs as the confirming user.
       Somebody Odoo says may confirm a purchase order is not therefore
       somebody who may edit a project record — so the confirmation fails
       with an `AccessError` that has nothing to do with purchasing.

    Every existing test that confirms a project order does so as a
    procurement manager, or through `sudo()`, which is why this has never
    fired. M7 makes it fire for real, because an awarded tender order is
    confirmed by whoever holds the award authority.
    """

    def setUp(self):
        super().setUp()
        self._set_po_governance('optional', project=self.project)

    def _direct_project_order(self):
        """A project-coded order with no requisition behind it."""
        order = self.env['purchase.order'].create({
            'partner_id': self.vendor_a.id,
            'company_id': self.company.id,
            're_project_id': self.project.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 10.0,
                'price_unit': 1_000.0,
                'name': self.product.name,
                'date_planned': self._close_at(),
            })],
        })
        order.order_line.taxes_id = [(5, 0, 0)]
        return order

    def test_the_project_has_no_stock_location_until_something_makes_one(self):
        """The precondition. Without it the rest proves nothing."""
        self.assertFalse(
            self.project.stock_location_id,
            "The project already has a location, so lazy creation cannot be "
            "observed and this whole class is inert.")

    def test_confirming_a_project_order_creates_a_stock_location(self):
        """Reproduces problem 1: master data appears as a side effect."""
        order = self._direct_project_order()
        self.assertFalse(self.project.stock_location_id)

        order.button_confirm()
        self.project.invalidate_recordset()

        self.assertTrue(
            self.project.stock_location_id,
            "Nothing created a location, so §10 no longer reproduces and this "
            "test should be re-examined rather than deleted.")

    def test_a_purchase_user_cannot_confirm_a_project_order(self):
        """Reproduces problem 2 — the M7 PO Confirmation Integration Gate.

        Expected to FAIL before M7 fixes it. The failure is an `AccessError`
        on `realestate.project`, raised from inside stock-move preparation,
        during an operation the user is entitled to perform.
        """
        buyer = self._purchase_user('m7.buyer')
        order = self._direct_project_order()
        self.assertFalse(self.project.stock_location_id)

        order.with_user(buyer).button_confirm()

        self.assertEqual(order.state, 'purchase')

    def test_the_location_is_provisioned_without_touching_the_project_as_user(
            self):
        """The shape of the fix, stated as an assertion.

        Whatever M7 does, two things have to be true afterwards: a buyer can
        confirm, and the project row is not written by them. Reading the
        location is fine; provisioning it is an administrative act.
        """
        buyer = self._purchase_user('m7.buyer2')
        order = self._direct_project_order()

        order.with_user(buyer).button_confirm()
        self.project.invalidate_recordset()

        self.assertEqual(order.state, 'purchase')
        picking = order.picking_ids[:1]
        self.assertTrue(picking, "Confirmation produced no receipt.")
