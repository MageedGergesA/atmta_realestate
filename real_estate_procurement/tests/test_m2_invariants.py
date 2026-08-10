# -*- coding: utf-8 -*-
"""M2 — the four invariants, written before the models that satisfy them.

Phase 0 proved that an approved requisition became a confirmed purchase order
in one click, that the winning vendor was whichever supplier row sorted first,
and that every generated line lost its cost code. These four tests state what
must be true instead, and they are the specification the rest of M2 is built
against.
"""

from odoo.tests import tagged

from .common import ProcurementCommon


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM2Invariants(ProcurementCommon):

    def setUp(self):
        super().setUp()
        self._require_construction()
        self.project = self._project()
        self.wbs = self._wbs(self.project, 'A.1')
        self.concrete = self._cost_code('M2-CONC', 'Concrete', 'material')
        self._baselined_budget(self.project, 10_000_000.0, self.concrete)
        self.vendor = self._vendor()

    # -- A ------------------------------------------------------------------
    def test_a_an_approved_requisition_creates_no_commitment(self):
        """Authorised demand is not an obligation to anybody."""
        product = self._product(price=1_000.0, vendor=self.vendor)
        request = self._request(
            self.project, [(product, 3_000.0)],
            line_defaults={'cost_code_id': self.concrete.id,
                           'wbs_id': self.wbs.id})
        request.action_submit()
        request.action_approve()

        self.assertEqual(request.state, 'approved')
        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._actual(self.project), 0.0)

    # -- B ------------------------------------------------------------------
    def test_b_creating_sourcing_produces_a_draft_rfq_and_no_commitment(self):
        """The change M2 exists to make.

        Beginning to source is not agreeing to buy. The purchase order stays a
        quotation until somebody confirms it deliberately, which M7 will
        govern.
        """
        product = self._product(price=1_000.0, vendor=self.vendor)
        request = self._request(
            self.project, [(product, 3_000.0)],
            line_defaults={'cost_code_id': self.concrete.id,
                           'wbs_id': self.wbs.id})
        request.action_submit()
        request.action_approve()

        request.action_create_rfqs(vendors=self.vendor)

        orders = request.purchase_order_ids
        self.assertEqual(len(orders), 1)
        self.assertEqual(
            orders.state, 'draft',
            "An RFQ is an enquiry. Confirming it is a separate decision.")
        self.assertEqual(request.state, 'sourcing')
        self.assertEqual(
            self._commitment(self.project), 0.0,
            "3,000,000 of enquiry is still nothing owed.")

    # -- C ------------------------------------------------------------------
    def test_c_the_cost_code_survives_into_the_rfq_line(self):
        """The Phase 0 defect that put real money under Unassigned."""
        product = self._product(price=1_000.0, vendor=self.vendor)
        request = self._request(
            self.project, [(product, 2_000.0)],
            line_defaults={'cost_code_id': self.concrete.id,
                           'wbs_id': self.wbs.id})
        request.action_submit()
        request.action_approve()
        request.action_create_rfqs(vendors=self.vendor)

        line = request.purchase_order_ids.order_line
        self.assertEqual(len(line), 1)
        self.assertEqual(line.order_id.re_project_id, self.project)
        self.assertEqual(line.re_wbs_id, self.wbs)
        self.assertEqual(line.re_cost_code_id, self.concrete)

        distribution = line.analytic_distribution or {}
        code_account = self.concrete._get_or_create_analytic_account()
        project_account = self.project.analytic_account_id
        self.assertTrue(
            any(str(code_account.id) in key for key in distribution),
            "The cost-code plan must be represented, or the cost sheet files "
            "this under Unassigned: %s" % distribution)
        self.assertTrue(
            any(str(project_account.id) in key for key in distribution),
            "And the project plan alongside it: %s" % distribution)

    # -- D ------------------------------------------------------------------
    def test_d_the_first_supplier_row_does_not_win_by_default(self):
        """Vendor choice is a sourcing decision, and M2 does not make it."""
        first = self._vendor('First Listed')
        second = self._vendor('Second Listed')
        product = self._product(price=900.0, vendor=first)
        self.env['product.supplierinfo'].create({
            'partner_id': second.id,
            'product_tmpl_id': product.product_tmpl_id.id,
            'price': 500.0,
            'sequence': 99,
        })
        request = self._request(
            self.project, [(product, 100.0)],
            line_defaults={'cost_code_id': self.concrete.id})
        request.action_submit()
        request.action_approve()

        # Asking for sourcing without naming anybody must refuse, rather than
        # quietly buying from whoever happens to be listed first.
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            request.action_create_rfqs()

        # Naming vendors produces one enquiry each — and no award.
        request.action_create_rfqs(vendors=first | second)
        orders = request.purchase_order_ids
        self.assertEqual(len(orders), 2)
        self.assertEqual(set(orders.mapped('state')), {'draft'})
        self.assertEqual(set(orders.mapped('partner_id')), {first, second})
        self.assertEqual(self._commitment(self.project), 0.0)
