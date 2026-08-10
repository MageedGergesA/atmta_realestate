# -*- coding: utf-8 -*-
"""M3E/M3F/M3M — the four budget policies, and what each one actually does.

The policies are not four shades of the same behaviour. They are four
different answers to one question, and a company picks the one that matches
how it runs:

```
    NONE               no reservation, no position, no control
    WARN               reserve, record the overage, continue
    APPROVAL_REQUIRED  reserve only with authority already granted
    BLOCK              refuse
```

None of them increases a Construction budget. A project that needs more money
needs a change order.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import M3Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM3BudgetPolicy(M3Common):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0),
                      (self.electrical, 5_000_000.0)])

    # -- TEST E --------------------------------------------------------
    def test_e_two_requests_cannot_both_take_the_same_capacity(self):
        self._set_budget_policy('block')
        self._demand(8_000.0)

        with self.assertRaises(UserError):
            self._demand(8_000.0)
        self.assertEqual(self._reserved(self.project, self.concrete),
                         8_000_000.0)

    def test_e2_different_cost_codes_do_not_block_each_other(self):
        """Concrete running out has nothing to do with electrical."""
        self._set_budget_policy('block')
        self._demand(9_000.0)
        electrical = self._demand(4_000.0, code=self.electrical)

        self.assertEqual(electrical.state, 'approved')
        self.assertEqual(self._reserved(self.project, self.electrical),
                         4_000_000.0)

    # -- TEST F --------------------------------------------------------
    def test_f_warn_allows_and_records(self):
        """Available 2M, demand 3M. Approved, and the overage is evidence."""
        self._set_budget_policy('warn')
        self._demand(8_000.0)
        request = self._demand(3_000.0)

        self.assertEqual(request.state, 'approved')
        self.assertEqual(request.reserved_amount, 3_000_000.0)

        exception = self.env[
            'realestate.procurement.control.exception'].search([
                ('request_id', '=', request.id)])
        self.assertEqual(len(exception), 1)
        self.assertEqual(exception.exception_type, 'over_budget')
        self.assertEqual(exception.state, 'noted',
                         "Nobody approved it, so it must not say approved.")
        self.assertEqual(exception.overage_amount, 1_000_000.0)
        self.assertEqual(
            self._position(self.concrete)['current_budget'], 10_000_000.0,
            "Warn records the overage. It never quietly raises the budget.")
        self.assertEqual(self._position(self.concrete)['status'],
                         'over_budget')

    # -- TEST G --------------------------------------------------------
    def test_g_approval_required_needs_authority_first(self):
        self._set_budget_policy('approval_required')
        self._demand(8_000.0)

        request = self._request(
            self.project, [(self.product, 3_000.0)],
            line_defaults={'cost_code_id': self.concrete.id,
                           'estimated_unit_cost': 1_000.0})
        request.action_submit()
        with self.assertRaises(UserError):
            request.action_approve()
        self.assertEqual(request.state, 'submitted')
        self.assertEqual(request.reserved_amount, 0.0)

        # The authority is a record somebody decides, not a flag on the form.
        wizard = self.env['realestate.procurement.budget.exception'].create({
            'request_id': request.id,
            'requested_amount': 3_000_000.0,
            'available_amount': 2_000_000.0,
            'reason': 'Concrete pour cannot wait for the change order.',
        })
        wizard.action_request()
        exception = self.env[
            'realestate.procurement.control.exception'].search([
                ('request_id', '=', request.id)])
        self.assertEqual(exception.state, 'requested')

        with self.assertRaises(UserError):
            request.action_approve()

        exception.action_approve()
        request.action_approve()

        self.assertEqual(request.state, 'approved')
        self.assertEqual(request.reserved_amount, 3_000_000.0)
        self.assertEqual(request.reservation_ids.exception_id, exception)

    def test_none_disables_reservation_entirely(self):
        self._set_budget_policy('none')
        request = self._demand(3_000.0)

        self.assertEqual(request.state, 'approved')
        self.assertFalse(request.reservation_ids)
        self.assertEqual(self._reserved(self.project), 0.0)

    def test_a_project_overrides_its_company(self):
        other = self._project()
        self._set_budget_policy('block')
        other.procurement_budget_policy = 'warn'

        Control = self.env['realestate.procurement.control']
        self.assertEqual(Control.budget_policy_for(self.project), 'block')
        self.assertEqual(Control.budget_policy_for(other), 'warn')

    # -- Rule 4 --------------------------------------------------------
    def test_unknown_is_not_available(self):
        """A demand with no cost code cannot be positioned, and says so."""
        self._set_budget_policy('block')
        request = self._request(
            self.project, [(self.product, 1.0)],
            line_defaults={'estimated_unit_cost': 100.0})
        request.action_submit()

        self.assertEqual(request.approval_control_status, 'insufficient_data')
        with self.assertRaises(UserError):
            request.action_approve()
        self.assertEqual(
            request.reserved_amount, 0.0,
            "Refused because the position is unknown — not because the "
            "budget was assumed to be zero, and certainly not allowed "
            "because it was assumed to be unlimited.")

    def test_a_cost_code_absent_from_the_baseline_is_not_zero_budget(self):
        missing = self._cost_code('M3-MISS%s' % self._next(), 'Not budgeted')
        position = self._position(missing)

        self.assertEqual(position['status'], 'insufficient_data')
        self.assertTrue(position['warnings'])

    def test_a_project_with_no_baseline_has_no_position(self):
        bare = self._project()
        position = self._position(self.concrete, project=bare)

        self.assertEqual(position['status'], 'insufficient_data')
        self.assertIn('baselined', position['warnings'][0])


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM3AmountDelta(M3Common):
    """M3M — an approval covers an amount, not a document number."""

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])

    def _order_for(self, request, unit):
        request.action_create_rfqs(vendors=self.vendor)
        order = request.purchase_order_ids
        order.order_line.write({'price_unit': unit, 'taxes_id': [(5, 0, 0)]})
        return order

    # -- TEST M --------------------------------------------------------
    def test_m_an_order_above_the_approved_basis_is_refused(self):
        request = self._demand(3_000.0)
        order = self._order_for(request, 1_166.67)  # ≈ 3.5M

        with self.assertRaises(UserError):
            order.button_confirm()

        order.invalidate_recordset()
        self.assertEqual(order.state, 'draft')
        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._reserved(self.project), 3_000_000.0,
                         "The reservation survives a refused confirmation.")

    def test_m2_a_configured_tolerance_covers_a_small_difference(self):
        """The tolerance is configuration. There is no hard-coded 5%."""
        self.company.procurement_amount_tolerance_pct = 20.0
        request = self._demand(3_000.0)
        order = self._order_for(request, 1_100.0)  # 3.3M, inside 20%

        order.button_confirm()

        self.assertEqual(self._commitment(self.project), 3_300_000.0)
        self.assertEqual(request.reservation_ids.amount_converted,
                         3_000_000.0)
        self.assertEqual(
            self._reserved(self.project), 0.0,
            "The 300,000 above the reservation is committed and was never "
            "reserved — which is what the tolerance authorised.")

    def test_m3_an_authorised_exception_covers_the_difference(self):
        request = self._demand(3_000.0)
        order = self._order_for(request, 1_200.0)  # 3.6M

        wizard = self.env['realestate.procurement.purchase.exception'].create({
            'order_id': order.id,
            'requested_amount': 3_600_000.0,
            'reason': 'Steel price moved between enquiry and award.',
        })
        wizard.action_request()
        exception = order.re_exception_id
        exception.exception_type = 'amount_delta'
        exception.action_approve()

        order.button_confirm()
        self.assertEqual(self._commitment(self.project), 3_600_000.0)

    def test_the_reservation_holds_while_the_order_is_only_an_enquiry(self):
        request = self._demand(3_000.0)
        self._order_for(request, 1_000.0)

        self.assertEqual(self._reserved(self.project), 3_000_000.0)
        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._position(self.concrete)['available'],
                         7_000_000.0)
