# -*- coding: utf-8 -*-
"""Phase 0 — what `real_estate_procurement` actually does today.

Written before any redesign, and deliberately asserting the **current**
behaviour rather than the desired behaviour. Tests named `test_defect_…` pin a
problem so that fixing it later has to break a test and replace it with a
positive one; the rest record behaviour worth keeping.

The five architectural questions the milestone brief asks are answered here
from running code, not from the module's own documentation:

1. Does Procurement duplicate anything Odoo Purchase already owns?
2. At exactly which event does Construction Commitment appear?
3. Can one economic obligation be counted more than once?
4. Does every PO line carry Project + WBS + Cost Code?
5. Can governance be bypassed?
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import ProcurementCommon


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestQ1PurchaseOwnership(ProcurementCommon):
    """Q1 — what does Procurement own, and what does Odoo Purchase own?"""

    def test_the_purchase_order_is_odoos_own_model(self):
        """The good news first: no competing PO model was ever built."""
        self.assertNotIn('realestate.purchase.order', self.env.registry)
        self.assertIn('purchase.order', self.env.registry)

        request = self.Request
        self.assertEqual(
            request._fields['purchase_order_ids'].comodel_name,
            'purchase.order',
            "Requests point at Odoo's purchase orders, which is correct.")

    def test_sourcing_produces_a_quotation_not_a_confirmed_order(self):
        """FIXED IN M2 — was `test_defect_creating_purchase_orders_skips_the
        _quotation_stage`.

        `action_create_purchase_orders()` used to call `button_confirm()`
        itself, so an approved requisition became a confirmed purchase order —
        and a Construction commitment — in a single click. Sourcing now
        prepares a quotation and stops.
        """
        project = self._project()
        vendor = self._vendor()
        product = self._product(price=1_000.0, vendor=vendor)
        request = self._request(project, [(product, 5.0)])
        request.action_submit()
        request.action_approve()

        request.action_create_rfqs(vendors=vendor)
        orders = request.purchase_order_ids

        self.assertEqual(len(orders), 1)
        self.assertEqual(orders.state, 'draft')
        self.assertEqual(request.state, 'sourcing')

        # And the old entry point refuses rather than doing something
        # different from what its name says.
        with self.assertRaises(UserError):
            request.action_create_purchase_orders()

    def test_catalogue_suppliers_are_a_suggestion_not_a_decision(self):
        """FIXED IN M2 — was `test_defect_the_vendor_is_chosen_by_list_order
        _not_by_competition`.

        `_get_preferred_supplier()` returned `seller_ids[0]` and that vendor
        got the order. It is replaced by `_suggested_suppliers()`, which
        returns every candidate and decides nothing.
        """
        project = self._project()
        cheap = self._vendor('Cheap Vendor')
        expensive = self._vendor('Expensive Vendor')
        product = self._product(price=500.0, vendor=expensive)
        self.env['product.supplierinfo'].create({
            'partner_id': cheap.id,
            'product_tmpl_id': product.product_tmpl_id.id,
            'price': 100.0,
            'sequence': 99,
        })

        self.assertFalse(hasattr(self.RequestLine, '_get_preferred_supplier'))

        request = self._request(project, [(product, 1.0)])
        suggestions = request.line_ids._suggested_suppliers()
        self.assertIn(cheap, suggestions)
        self.assertIn(expensive, suggestions)

        request.action_submit()
        request.action_approve()
        with self.assertRaises(UserError):
            request.action_create_rfqs()


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestQ2WhereCommitmentAppears(ProcurementCommon):
    """Q2 — the exact event at which Construction Commitment changes."""

    def setUp(self):
        super().setUp()
        self.project = self._project()
        self.code = self._cost_code('Q2-CIV', 'Civil', 'subcontract')
        self._baselined_budget(self.project, 10_000_000.0, self.code)
        self.vendor = self._vendor()

    def test_a_draft_purchase_order_is_not_a_commitment(self):
        self._confirmed_po(self.project, self.vendor,
                           [(self.code, 3_000_000.0)], confirm=False)
        self.assertEqual(self._commitment(self.project), 0.0,
                         "A quotation is an enquiry, not an obligation.")

    def test_a_confirmed_purchase_order_is_the_commitment(self):
        self._confirmed_po(self.project, self.vendor,
                           [(self.code, 3_000_000.0)])
        self.assertEqual(self._commitment(self.project), 3_000_000.0)

    def test_a_material_request_alone_is_not_a_commitment(self):
        product = self._product(price=1_000.0, vendor=self.vendor)
        request = self._request(self.project, [(product, 100.0)])
        request.action_submit()
        request.action_approve()

        self.assertEqual(request.state, 'approved')
        self.assertEqual(
            self._commitment(self.project), 0.0,
            "Correct today, and must stay true: an approved requisition is "
            "authorised demand, not an obligation to a vendor.")

    def test_sourcing_an_approved_request_creates_no_commitment(self):
        """FIXED IN M2 — was `test_defect_approving_and_ordering_commits_in
        _one_step`.

        There is now room between 'somebody approved the need' and 'the
        company is committed', which is where the enquiry, the comparison and
        the award belong.
        """
        product = self._product(price=1_000.0, vendor=self.vendor)
        request = self._request(self.project, [(product, 500.0)])
        request.action_submit()
        request.action_approve()

        request.action_create_rfqs(vendors=self.vendor)

        self.assertEqual(request.state, 'sourcing')
        self.assertEqual(request.purchase_order_ids.state, 'draft')
        self.assertEqual(self._commitment(self.project), 0.0)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestQ3DoubleCounting(ProcurementCommon):
    """Q3 — can one obligation be counted twice?"""

    def setUp(self):
        super().setUp()
        self.project = self._project()
        self.code = self._cost_code('Q3-CIV', 'Civil', 'subcontract')
        self._baselined_budget(self.project, 10_000_000.0, self.code)
        self.vendor = self._vendor()

    def test_a_package_and_its_order_are_one_commitment(self):
        """Construction already owns this precedence. Pinned from Procurement."""
        contractor = self.env['realestate.contractor'].create({
            'name': 'Q3 Contractor', 'partner_id': self.vendor.id})
        package = self.env[
            'realestate.construction.contract.package'].create({
                'title': 'Q3 package',
                'project_id': self.project.id,
                'company_id': self.company.id,
                'contractor_id': contractor.id,
                'tender_value': 8_000_000.0,
            })
        package.action_award()
        self.assertEqual(self._commitment(self.project), 8_000_000.0)

        self._confirmed_po(self.project, self.vendor,
                           [(self.code, 8_000_000.0)], package=package)

        self.assertEqual(
            self._commitment(self.project), 8_000_000.0,
            "The order speaks for the package. 16,000,000 would be the same "
            "obligation counted twice.")

    def test_the_reservation_layer_never_double_counts_with_commitment(self):
        """Converted at M3, which is when the reservation layer arrived.

        Phase 0 recorded the absence: there was no reservation, so nothing
        could double-count with commitment — and nothing stopped two approved
        requests from spending the same remaining budget either. Now that the
        layer exists, the test that recorded its absence becomes the test that
        pins its central invariant.
        """
        self.assertIn('realestate.procurement.reservation', self.env.registry)
        product = self._product(price=1_000.0, vendor=self.vendor)
        request = self._request(
            self.project, [(product, 3_000.0)],
            line_defaults={'cost_code_id': self.code.id})
        request.action_submit()
        request.action_approve()

        self.assertEqual(request.reserved_amount, 3_000_000.0)
        self.assertEqual(self._commitment(self.project), 0.0)

        request.action_create_rfqs(vendors=self.vendor)
        order = request.purchase_order_ids
        order.order_line.taxes_id = [(5, 0, 0)]
        order.button_confirm()

        self.assertEqual(self._commitment(self.project), 3_000_000.0)
        self.assertEqual(
            self._reserved(self.project), 0.0,
            "One obligation, one control stage. 3,000,000 of demand is never "
            "3,000,000 reserved plus 3,000,000 committed.")

    def test_two_requests_can_no_longer_quietly_plan_the_same_budget(self):
        """Converted at M3E.

        Phase 0: two 8,000,000 requests were both approved against a
        10,000,000 budget, and nothing was checked, reserved or warned —
        because there was nothing to check against. The exposure existed and
        no field in the system held it.

        Now the demand consumes purchasing capacity as it is approved, so the
        16,000,000 is visible while it is still demand; and a company that
        wants the second one refused says so with one policy setting.
        """
        product = self._product(price=1_000.0, vendor=self.vendor)
        first = self._request(
            self.project, [(product, 8_000.0)],
            line_defaults={'cost_code_id': self.code.id,
                           'estimated_unit_cost': 1_000.0})
        first.action_submit()
        first.action_approve()

        self.assertEqual(first.reserved_amount, 8_000_000.0)
        self.assertEqual(self._commitment(self.project), 0.0,
                         "Reserved is not committed. Nobody is owed this.")
        self.assertEqual(self._position(self.code)['available'], 2_000_000.0)

        self.company.procurement_budget_policy = 'block'
        second = self._request(
            self.project, [(product, 8_000.0)],
            line_defaults={'cost_code_id': self.code.id,
                           'estimated_unit_cost': 1_000.0})
        second.action_submit()
        with self.assertRaises(UserError):
            second.action_approve()

        self.assertEqual(second.state, 'submitted')
        self.assertEqual(self._reserved(self.project), 8_000_000.0)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestQ4CostCodePropagation(ProcurementCommon):
    """Q4 — does every PO line reach the Construction cost dimension?"""

    def setUp(self):
        super().setUp()
        self.project = self._project()
        self.code = self._cost_code('Q4-CIV', 'Civil', 'material')
        self._baselined_budget(self.project, 5_000_000.0, self.code)
        self.vendor = self._vendor()

    def test_a_hand_coded_order_line_reaches_its_cost_code(self):
        """Construction's own coding works, when somebody sets it."""
        self._confirmed_po(self.project, self.vendor,
                           [(self.code, 250_000.0)])
        by_code = self.Commitment.current_commitment_by_cost_code(self.project)
        self.assertEqual(by_code.get(self.code.id), 250_000.0)

    def test_generated_lines_carry_the_requisition_coding(self):
        """FIXED IN M2 — was `test_defect_requisition_generated_lines_carry
        _no_cost_code`.

        The generated lines used to receive `{project_analytic: 100}` and
        nothing else, so every one of them landed under Unassigned.
        """
        wbs = self._wbs(self.project, 'Q4.1')
        product = self._product(price=1_000.0, vendor=self.vendor)
        request = self._request(
            self.project, [(product, 300.0)],
            line_defaults={'cost_code_id': self.code.id, 'wbs_id': wbs.id})
        request.action_submit()
        request.action_approve()
        request.action_create_rfqs(vendors=self.vendor)

        po_lines = request.purchase_order_ids.order_line
        self.assertTrue(po_lines)
        self.assertEqual(po_lines.re_cost_code_id, self.code)
        self.assertEqual(po_lines.re_wbs_id, wbs)

    def test_the_request_line_carries_its_own_coding(self):
        """FIXED IN M2 — the line is where the coding is authoritative."""
        line_fields = set(self.RequestLine._fields)
        self.assertIn('cost_code_id', line_fields)
        self.assertIn('wbs_id', line_fields)
        self.assertIn('required_on_site_date', line_fields)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestQ5GovernanceBypass(ProcurementCommon):
    """Q5 — can the approval architecture be walked around?"""

    def setUp(self):
        super().setUp()
        self.project = self._project()
        self.vendor = self._vendor()
        self.product = self._product(price=1_000.0, vendor=self.vendor)

    def _plain_user(self, login, *groups):
        return self.env['res.users'].create({
            'name': login, 'login': login,
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id] + [
                self.env.ref(g).id for g in groups])],
        })

    def test_urgency_no_longer_decides_whether_approval_applies(self):
        """Converted at M3J. The bypass is gone.

        Phase 0: `action_submit()` promoted an urgent request straight to
        approved, so the requester — who owns the priority field — decided
        whether their own request needed approving. Priority is now a
        matching dimension in the approval matrix, which can require *more*
        authority for urgent demand and has no setting that requires less.
        """
        request = self._request(self.project, [(self.product, 10_000.0)],
                                priority='1')
        request.action_submit()

        self.assertEqual(request.state, 'submitted')
        self.assertFalse(request.approved_by_id)
        self.assertEqual(request.reserved_amount, 0.0,
                         "Unapproved demand reserves nothing.")

    def test_a_requester_can_no_longer_approve_their_own_request(self):
        """Converted at M3I.

        Phase 0's check asked whether the user was in an approver group,
        which is a question about capability. Separation of duties is a
        question about identity — and in a company small enough that
        everybody is in the approver group, that is exactly where
        self-approval is most likely and least visible.

        The company fixture allows self-approval so ordinary tests can build
        approved records; this one turns it off, which is the default a fresh
        company gets.
        """
        self.assertFalse(
            self.env['res.company'].default_get(
                ['procurement_allow_self_approval']
            ).get('procurement_allow_self_approval'),
            "Self-approval must be off unless a company chooses it.")
        self.company.procurement_allow_self_approval = False
        approver = self._plain_user(
            'proc.selfapprover',
            'real_estate_procurement.group_procurement_approver',
            'real_estate_developer.group_dev_readonly')
        request = self._request(self.project, [(self.product, 5.0)],
                                requested_by_id=approver.id)
        request.with_user(approver).action_submit()
        with self.assertRaises(UserError):
            request.with_user(approver).action_approve()

        self.assertEqual(request.state, 'submitted')
        self.assertFalse(request.approved_by_id)

    def test_defect_a_purchase_user_can_commit_without_any_requisition(self):
        """The whole Procurement workflow is optional.

        Anyone who may confirm a purchase order can create commitment against
        a construction project without a requisition, an approval, an enquiry
        or an award. The commitment is real and lands under Unassigned.
        """
        code = self._cost_code('Q5-CIV', 'Civil', 'subcontract')
        self._baselined_budget(self.project, 1_000_000.0, code)

        # Purchase rights plus project rights. Neither group says anything
        # about procurement governance — no requisition, no approval, no
        # enquiry, no award is required of this user, and none is recorded.
        # (Read-only project access is not enough, because confirming a
        # project order writes back to the project to resolve its stock
        # location — noted in the audit report.)
        buyer = self._plain_user('proc.buyer', 'purchase.group_purchase_user',
                                 'real_estate_developer.group_dev_manager')
        product = self._product(price=1.0, vendor=self.vendor)
        po = self.PO.with_user(buyer).create({
            'partner_id': self.vendor.id,
            're_project_id': self.project.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'name': 'Direct purchase',
                'product_qty': 1.0,
                'price_unit': 400_000.0,
                'taxes_id': [(5, 0, 0)],
            })],
        })
        po.with_user(buyer).button_confirm()

        self.assertEqual(po.state, 'purchase')
        self.assertEqual(
            self._commitment(self.project), 400_000.0,
            "DEFECT: 400,000 of commitment created with no procurement "
            "governance whatsoever.")
        self.assertFalse(po.order_line.re_material_request_line_id)

    def test_defect_a_buyer_coding_a_line_hits_an_access_error(self):
        """And the buyer who tries to do the right thing is punished for it.

        Construction stamps the cost-code analytic in its `create()` override,
        which reads `realestate.construction.cost.code` — a model no purchase
        user may read. So an uncoded direct order succeeds and a *coded* one
        raises an AccessError from inside another module.

        The bypass is therefore not merely open; the coded path is the one
        that breaks.
        """
        code = self._cost_code('Q5-ACC', 'Civil', 'subcontract')
        buyer = self._plain_user('proc.buyer2', 'purchase.group_purchase_user',
                                 'real_estate_developer.group_dev_manager')
        product = self._product(price=1.0, vendor=self.vendor)

        with self.assertRaises(AccessError):
            self.PO.with_user(buyer).create({
                'partner_id': self.vendor.id,
                're_project_id': self.project.id,
                'order_line': [(0, 0, {
                    'product_id': product.id,
                    'name': 'Coded direct purchase',
                    'product_qty': 1.0,
                    'price_unit': 100_000.0,
                    'taxes_id': [(5, 0, 0)],
                    're_cost_code_id': code.id,
                })],
            })

    def test_approval_rules_are_scoped_to_the_requisition_company(self):
        """Converted at M3W.

        Phase 0 matched rules against `self.env.company` — whichever company
        the approver happened to have active, rather than the one that owns
        the requisition. The rule set is now the requisition's, which is the
        only one that can be said to govern it.
        """
        import inspect
        source = inspect.getsource(type(self.Request)._generate_approval_steps)
        self.assertIn('self.company_id.id', source)
        self.assertNotIn('self.env.company', source)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestPhase0StructuralGaps(ProcurementCommon):
    """The gaps that are not behaviour but absence."""

    PROCUREMENT_MODELS = (
        'realestate.material.request',
        'realestate.material.request.line',
        'realestate.procurement.approval.rule',
        'realestate.procurement.approval.step',
    )

    def test_every_procurement_model_carries_a_company(self):
        """FIXED IN M2 — was `test_defect_no_procurement_model_carries_a
        _company`."""
        without = [name for name in self.PROCUREMENT_MODELS
                   if 'company_id' not in self.env[name]._fields]
        self.assertFalse(without, "No company on: %s" % without)

    def test_every_procurement_model_is_scoped_to_a_company(self):
        """FIXED IN M2 — was `test_defect_there_are_no_record_rules_at_all`.

        Phase 0 found not one record rule across the whole module: every user
        with the group saw every company's requisitions. Each model now
        carries a global company rule, and the requester/buyer split is layered
        on top of it.
        """
        rules = self.env['ir.rule'].sudo().search([
            ('model_id.model', 'in', list(self.PROCUREMENT_MODELS))])
        self.assertTrue(rules, "Nothing scopes a requisition to anything.")
        by_model = {rule.model_id.model for rule in rules
                    if not rule.groups}
        for model in ('realestate.material.request',
                      'realestate.material.request.line'):
            self.assertIn(model, by_model,
                          "%s has no global company rule." % model)

    def test_a_request_states_what_kind_of_procurement_it_is(self):
        """FIXED IN M2 — a service is not a pallet of cement."""
        self.assertIn('procurement_type', self.Request._fields)
        values = dict(self.Request._fields['procurement_type'].selection)
        for key in ('material', 'service', 'subcontract', 'equipment'):
            self.assertIn(key, values)

    def test_an_unknown_estimate_says_so_rather_than_reading_as_zero(self):
        """FIXED IN M2 — was `test_defect_estimated_cost_is_a_price_hint_not
        _an_estimate`.

        The amount is still zero when nothing is known, but the line now says
        that it is unknown, so M3's approval matrix can refuse to treat it as
        an authorised amount.
        """
        product = self._product(price=0.0)
        request = self._request(self._project(), [(product, 1_000.0)])
        line = request.line_ids

        self.assertEqual(request.estimated_total, 0.0)
        self.assertFalse(line.estimate_is_known,
                         "Unknown, and it says so.")

        line.estimated_unit_cost = 25.0
        self.assertTrue(line.estimate_is_known)
        self.assertEqual(request.estimated_total, 25_000.0)

    def test_the_receipt_rollup_no_longer_writes_from_a_compute(self):
        """FIXED IN M2 — the compute reads Inventory and writes nothing."""
        import inspect
        source = inspect.getsource(type(self.RequestLine)._compute_received)
        self.assertNotIn('ln.request_id._refresh_state_from_lines()', source)
        self.assertIn('qty_received', source)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestPhase0WorthKeeping(ProcurementCommon):
    """Behaviour the redesign must not lose."""

    def test_a_request_records_where_the_need_came_from(self):
        project = self._project()
        milestone = self.env['realestate.construction.milestone'].create({
            'name': 'Structure', 'project_id': project.id,
            'budget_amount': 100_000.0, 'weight': 10.0,
        })
        request = self._request(
            project, [], source_ref='%s,%s' % (milestone._name, milestone.id))

        self.assertEqual(request.source_model, milestone._name)
        self.assertEqual(request.project_id, project,
                         "The project is derived from the source, not typed.")

    def test_a_cancelled_request_cannot_abandon_a_confirmed_order(self):
        project = self._project()
        vendor = self._vendor()
        product = self._product(price=100.0, vendor=vendor)
        request = self._request(project, [(product, 10.0)])
        request.action_submit()
        request.action_approve()
        request.action_create_rfqs(vendors=vendor)
        request.purchase_order_ids.button_confirm()

        with self.assertRaises(UserError):
            request.action_cancel()

    def test_purchase_orders_route_receipts_to_the_project_location(self):
        """Worth keeping: `_prepare_stock_moves` sets the destination."""
        import inspect
        source = inspect.getsource(type(self.POLine)._prepare_stock_moves)
        self.assertIn('_get_re_project_location', source)
        self.assertIn('location_dest_id', source)
