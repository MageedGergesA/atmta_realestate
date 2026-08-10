# -*- coding: utf-8 -*-
"""M3 — reservation, conversion, and the arithmetic between them.

The M3AE matrix, in the order the brief states it. Every test here is about
one number appearing in exactly one place: the failure this milestone exists
to prevent is not an amount being wrong, it is the same amount being right
twice.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import M3Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM3Reservation(M3Common):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0),
                      (self.electrical, 5_000_000.0)])

    # -- TEST A --------------------------------------------------------
    def test_a_reservation_is_not_commitment(self):
        request = self._demand(3_000.0)

        self.assertEqual(request.reserved_amount, 3_000_000.0)
        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._actual(self.project), 0.0)

    # -- TEST B --------------------------------------------------------
    def test_b_a_draft_rfq_preserves_the_reservation(self):
        """Sourcing is asking. Nothing moves between control stages."""
        request = self._demand(3_000.0)
        request.action_create_rfqs(vendors=self.vendor)

        self.assertEqual(request.purchase_order_ids.state, 'draft')
        self.assertEqual(self._reserved(self.project), 3_000_000.0)
        self.assertEqual(self._commitment(self.project), 0.0)

    # -- TEST C --------------------------------------------------------
    def test_c_confirming_converts_the_reservation(self):
        request = self._demand(3_000.0)
        request.action_create_rfqs(vendors=self.vendor)
        self._confirm(request.purchase_order_ids)

        reservation = request.reservation_ids
        self.assertEqual(reservation.amount_converted, 3_000_000.0)
        self.assertEqual(reservation.amount_active, 0.0)
        self.assertEqual(reservation.state, 'converted')
        self.assertEqual(self._commitment(self.project), 3_000_000.0)
        self.assertEqual(self._reserved(self.project), 0.0)

    # -- TEST D --------------------------------------------------------
    def test_d_partial_conversion_leaves_the_remainder_reserved(self):
        """Reserved 1M, ordered 600K → 600K committed, 400K still reserved.

        Combined controlled exposure stays 1,000,000. The alternative — the
        whole reservation converting because *an* order arrived — would leave
        400,000 of approved demand consuming nothing, which is how a project
        discovers in month six that it has already spent its contingency.
        """
        request = self._demand(100.0, unit=10_000.0)
        self.assertEqual(request.reserved_amount, 1_000_000.0)
        request.action_create_rfqs(vendors=self.vendor)

        order = request.purchase_order_ids
        order.order_line.write({'product_qty': 60.0, 'price_unit': 10_000.0})
        self._confirm(order)

        reservation = request.reservation_ids
        self.assertEqual(reservation.amount_converted, 600_000.0)
        self.assertEqual(reservation.amount_active, 400_000.0)
        self.assertEqual(reservation.state, 'reserved')
        self.assertEqual(self._commitment(self.project), 600_000.0)
        self.assertEqual(
            self._reserved(self.project) + self._commitment(self.project),
            1_000_000.0,
            "One million of demand, controlled once. Not 1.6 million.")
        self.assertEqual(request.state, 'partially_ordered')

    def test_d2_a_second_order_consumes_the_remainder(self):
        """Split award readiness — architecture, not a tender UI."""
        request = self._demand(100.0, unit=10_000.0)
        request.action_create_rfqs(vendors=self.vendor)
        first = request.purchase_order_ids
        first.order_line.write({'product_qty': 60.0, 'price_unit': 10_000.0})
        self._confirm(first)

        second = self._vendor('Second half')
        request.action_create_rfqs(vendors=second)
        follow_up = request.purchase_order_ids.filtered(
            lambda po: po.partner_id == second)
        follow_up.order_line.write({'product_qty': 40.0,
                                    'price_unit': 10_000.0})
        self._confirm(follow_up)

        reservation = request.reservation_ids
        self.assertEqual(reservation.amount_converted, 1_000_000.0)
        self.assertEqual(reservation.amount_active, 0.0)
        self.assertEqual(reservation.state, 'converted')
        self.assertEqual(len(reservation.conversion_ids), 2)
        self.assertEqual(self._commitment(self.project), 1_000_000.0)
        self.assertEqual(request.state, 'ordered')

    def test_d3_cumulative_conversion_cannot_exceed_the_reservation(self):
        """The engine refuses, whatever the caller believes."""
        request = self._demand(100.0, unit=10_000.0)
        request.action_create_rfqs(vendors=self.vendor)
        reservation = request.reservation_ids
        po_line = request.purchase_order_ids.order_line

        with self.assertRaises(UserError):
            reservation._convert(1_500_000.0, po_line)

    # -- TEST N --------------------------------------------------------
    def test_n_an_order_below_the_reservation_releases_the_difference(self):
        """Reserved 3M, ordered 2.7M → commitment 2.7M, 300K back to the
        project the moment the order is confirmed."""
        request = self._demand(3_000.0)
        request.action_create_rfqs(vendors=self.vendor)
        order = request.purchase_order_ids
        order.order_line.price_unit = 900.0
        self._confirm(order)

        reservation = request.reservation_ids
        self.assertEqual(reservation.amount_converted, 2_700_000.0)
        self.assertEqual(reservation.amount_released, 300_000.0)
        self.assertEqual(reservation.amount_active, 0.0)
        self.assertEqual(reservation.state, 'converted')
        self.assertEqual(self._commitment(self.project), 2_700_000.0)
        self.assertEqual(
            self._position(self.concrete)['available'], 7_300_000.0,
            "The unused 300,000 is available again, not held for demand that "
            "no longer exists.")
        self.assertTrue(reservation.release_reason)

    # -- TEST O --------------------------------------------------------
    def test_o_multi_code_demand_reserves_against_each_position(self):
        """Concrete 2M and electrical 1M are two positions, not one 3M."""
        request = self._request(
            self.project, [(self.product, 2_000.0), (self.product, 1_000.0)],
            line_defaults={'wbs_id': self.wbs.id,
                           'estimated_unit_cost': 1_000.0})
        request.line_ids[0].cost_code_id = self.concrete
        request.line_ids[1].cost_code_id = self.electrical
        request.action_submit()
        request.action_approve()

        self.assertEqual(len(request.reservation_ids), 2)
        self.assertEqual(self._reserved(self.project, self.concrete),
                         2_000_000.0)
        self.assertEqual(self._reserved(self.project, self.electrical),
                         1_000_000.0)
        self.assertEqual(self._position(self.concrete)['available'],
                         8_000_000.0)
        self.assertEqual(self._position(self.electrical)['available'],
                         4_000_000.0)

    # -- TEST P --------------------------------------------------------
    def test_p_the_control_basis_is_tax_exclusive(self):
        """1M of demand, 15% VAT on the order → 1M reserved, 1M committed."""
        tax = self.env['account.tax'].create({
            'name': 'M3 VAT 15',
            'amount': 15.0,
            'amount_type': 'percent',
            'type_tax_use': 'purchase',
            'company_id': self.company.id,
        })
        request = self._demand(1_000.0)
        self.assertEqual(request.reserved_amount, 1_000_000.0)

        request.action_create_rfqs(vendors=self.vendor)
        order = request.purchase_order_ids
        order.order_line.taxes_id = [(6, 0, tax.ids)]
        order.button_confirm()

        self.assertEqual(order.amount_total, 1_150_000.0)
        self.assertEqual(order.amount_untaxed, 1_000_000.0)
        self.assertEqual(self._commitment(self.project), 1_000_000.0)
        self.assertEqual(request.reservation_ids.amount_converted,
                         1_000_000.0)
        self.assertEqual(
            self._reserved(self.project), 0.0,
            "Reserving the tax-inclusive figure would overstate every "
            "position by the tax rate, invisibly.")

    # -- TEST R --------------------------------------------------------
    def test_r_reservation_creates_no_accounting(self):
        """A reservation is a control record. It is not a transaction."""
        moves_before = self.env['account.move'].search_count([])
        lines_before = self.env['account.analytic.line'].search_count([])

        request = self._demand(3_000.0)
        self.assertTrue(request.reserved_amount)

        self.assertEqual(self.env['account.move'].search_count([]),
                         moves_before)
        self.assertEqual(self.env['account.analytic.line'].search_count([]),
                         lines_before)
        self.assertEqual(self._actual(self.project), 0.0)

    # ------------------------------------------------------------------
    # Release paths — M3N
    # ------------------------------------------------------------------
    def test_cancelling_a_requisition_releases_its_reservation(self):
        request = self._demand(3_000.0)
        request.action_cancel()

        self.assertEqual(self._reserved(self.project), 0.0)
        reservation = request.reservation_ids
        self.assertEqual(reservation.state, 'released')
        self.assertEqual(reservation.amount_released, 3_000_000.0)
        self.assertTrue(reservation.release_reason)
        self.assertEqual(self._position(self.concrete)['available'],
                         10_000_000.0)

    def test_revising_a_requisition_releases_the_old_basis(self):
        """The old reservation authorised the old numbers."""
        request = self._demand(3_000.0)
        request.action_revise(reason='Quantity was wrong')

        self.assertEqual(self._reserved(self.project), 0.0)
        self.assertEqual(request.state, 'draft')
        self.assertEqual(request.reservation_ids.state, 'released')

        request.line_ids.qty = 4_000.0
        request.action_submit()
        request.action_approve()

        self.assertEqual(request.reserved_amount, 4_000_000.0)
        self.assertEqual(len(request.reservation_ids), 2,
                         "The superseded reservation is kept, not rewritten.")
        self.assertEqual(request.reservation_ids.filtered(
            lambda r: r.state == 'reserved').revision, 1)

    def test_a_reserved_line_cannot_be_quietly_deleted(self):
        request = self._demand(3_000.0)
        with self.assertRaises(UserError):
            request.line_ids.unlink()

    def test_a_reservation_cannot_be_deleted_or_hand_edited(self):
        request = self._demand(3_000.0)
        reservation = request.reservation_ids
        with self.assertRaises(UserError):
            reservation.unlink()
        with self.assertRaises(UserError):
            reservation.amount_reserved = 1.0

    def test_expiry_releases_only_where_a_company_chose_one(self):
        """Zero days means never, which is the default."""
        request = self._demand(3_000.0)
        self.assertFalse(request.reservation_ids.expiry_date)
        Reservation = self.env['realestate.procurement.reservation']
        self.assertEqual(Reservation.expire_due_reservations(), 0)

        self.company.procurement_reservation_expiry_days = 30
        later = self._demand(1_000.0, code=self.electrical)
        reservation = later.reservation_ids
        self.assertTrue(reservation.expiry_date)
        reservation.with_context(re_reservation_engine=True).write({
            'expiry_date': self.today.replace(year=self.today.year - 1)})

        self.assertEqual(Reservation.expire_due_reservations(), 1)
        self.assertEqual(reservation.state, 'expired')
        self.assertEqual(self._reserved(self.project, self.electrical), 0.0)

    def test_cancelling_a_confirmed_order_gives_the_capacity_back(self):
        """Neither reserved nor committed is not a control state.

        Construction reads commitment from confirmed orders, so cancelling one
        removes the commitment immediately. If the reservation stayed
        converted the demand would vanish from every control figure while
        still needing to be bought.
        """
        request = self._demand(3_000.0)
        request.action_create_rfqs(vendors=self.vendor)
        order = request.purchase_order_ids
        self._confirm(order)
        self.assertEqual(self._commitment(self.project), 3_000_000.0)

        order.button_cancel()

        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._reserved(self.project), 3_000_000.0)
        reservation = request.reservation_ids
        self.assertEqual(reservation.state, 'reserved')
        self.assertEqual(reservation.conversion_ids.state, 'reversed')
        self.assertEqual(reservation.amount_converted, 0.0)

    # -- M3V multi-currency --------------------------------------------
    def test_the_control_amount_is_converted_and_its_basis_recorded(self):
        """A USD request measured against an EGP budget is not a measurement.

        The reservation keeps both sides — what was asked for, in the currency
        it was asked in, and what it costs in the currency the budget is
        stated in — plus the date the rate was taken on. A control figure
        whose basis date is unknown cannot be reconciled later; it can only be
        believed.
        """
        foreign = self.env['res.currency'].search(
            [('id', '!=', self.company.currency_id.id), ('active', '=', True)],
            limit=1)
        if not foreign:
            self.skipTest('no second active currency in this database')
        self.env['res.currency.rate'].create({
            'currency_id': foreign.id,
            'company_id': self.company.id,
            'name': self.today,
            'rate': 2.0,
        })

        request = self._demand(1_000.0, currency_id=foreign.id)
        reservation = request.reservation_ids

        self.assertEqual(reservation.source_currency_id, foreign)
        self.assertEqual(reservation.source_amount, 1_000_000.0)
        self.assertEqual(reservation.currency_id, self.company.currency_id)
        self.assertEqual(
            reservation.amount_reserved, 500_000.0,
            "One million at a rate of two is five hundred thousand of "
            "company-currency capacity.")
        self.assertEqual(reservation.rate_date, self.today)
        self.assertEqual(self._reserved(self.project), 500_000.0)
