# -*- coding: utf-8 -*-
"""M2 — the numbered test matrix.

Tests 2 to 5 are the four invariants in `test_m2_invariants.py`; they are the
specification M2 was built against and are not repeated here. What follows is
everything else the milestone promised: that a plan is a forecast rather than
money, that coding survives all the way into the Construction cost sheet, that
dates which mean different things stay in different fields, that an unknown
lead time stays unknown, and that an approved basis cannot be quietly rewritten
once sourcing has begun.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import ProcurementCommon


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM2Requisition(ProcurementCommon):

    def setUp(self):
        super().setUp()
        self._require_construction()
        self.project = self._project()
        self.wbs = self._wbs(self.project, 'A.1')
        self.concrete = self._cost_code('M2R-CONC', 'Concrete', 'material')
        self.electrical = self._cost_code('M2R-ELEC', 'Electrical', 'material')
        self._baselined_budget(self.project, 10_000_000.0, self.concrete)
        self.vendor = self._vendor()
        self.CostSheet = self.env['realestate.construction.cost.sheet']

    # ------------------------------------------------------------------
    def _plan(self, **kwargs):
        vals = {
            'title': 'Plan %d' % self._next(),
            'project_id': self.project.id,
            'company_id': self.company.id,
        }
        vals.update(kwargs)
        return self.env['realestate.procurement.plan'].create(vals)

    def _plan_line(self, plan, amount=3_000_000.0, **kwargs):
        vals = {
            'plan_id': plan.id,
            'description': 'Structural concrete',
            'quantity': 1.0,
            'estimated_unit_cost': amount,
            'wbs_id': self.wbs.id,
            'cost_code_id': self.concrete.id,
        }
        vals.update(kwargs)
        return self.env['realestate.procurement.plan.line'].create(vals)

    def _approved_request(self, lines=None, **kwargs):
        product = self._product(price=1_000.0, vendor=self.vendor)
        request = self._request(
            self.project, lines or [(product, 3_000.0)],
            line_defaults={'cost_code_id': self.concrete.id,
                           'wbs_id': self.wbs.id},
            **kwargs)
        request.action_submit()
        request.action_approve()
        return request

    # -- TEST 1 ---------------------------------------------------------
    def test_01_a_plan_is_demand_and_never_money(self):
        """3M planned, nothing committed and nothing spent."""
        plan = self._plan()
        self._plan_line(plan, 3_000_000.0)
        plan.action_review()
        plan.action_approve()
        plan.action_activate()

        self.assertEqual(plan.estimated_total, 3_000_000.0)
        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._actual(self.project), 0.0)

    def test_01b_an_approved_plan_is_revised_not_rewritten(self):
        """Rev 0 is what the schedule was built from."""
        plan = self._plan()
        line = self._plan_line(plan, 3_000_000.0)
        plan.action_review()
        plan.action_approve()

        with self.assertRaises(UserError):
            plan.write({'date_from': self.today})

        revision = plan.action_create_revision()
        self.assertEqual(revision.revision, 1)
        self.assertEqual(plan.state, 'superseded')
        self.assertEqual(revision.supersedes_id, plan)
        self.assertEqual(plan.estimated_total, 3_000_000.0,
                         "Rev 0 keeps the amount it was approved with.")
        self.assertEqual(line.estimated_amount, 3_000_000.0)

    def test_01c_a_plan_line_becomes_a_requisition_carrying_its_coding(self):
        plan = self._plan()
        line = self._plan_line(plan, 500_000.0,
                               required_on_site_date=self.today)
        plan.action_review()
        plan.action_approve()
        plan.action_activate()

        request = line.action_create_requisition()
        self.assertEqual(request.source_type, 'procurement_plan')
        self.assertEqual(request.plan_line_id, line)
        self.assertEqual(request.plan_id, plan)
        self.assertEqual(line.state, 'requested')
        self.assertEqual(request.line_ids.cost_code_id, self.concrete)
        self.assertEqual(request.line_ids.wbs_id, self.wbs)
        self.assertEqual(self._commitment(self.project), 0.0)

    # -- TEST 6 ---------------------------------------------------------
    def test_06_the_cost_sheet_does_not_file_the_order_under_unassigned(self):
        """End to end: requisition coding → RFQ → confirmed order → report.

        The order is confirmed here deliberately. M2's own workflow stops at
        the enquiry, but the point of propagating a cost code is what the cost
        sheet says once somebody does commit — and before M2 it said
        Unassigned.
        """
        request = self._approved_request()
        request.action_create_rfqs(vendors=self.vendor)
        order = request.purchase_order_ids
        order.order_line.taxes_id = [(5, 0, 0)]
        order.button_confirm()

        rows = self.CostSheet.rows_for(self.project)
        by_code = {row['cost_code_id']: row for row in rows}
        self.assertIn(self.concrete.id, by_code)
        self.assertEqual(by_code[self.concrete.id]['current_commitment'],
                         3_000_000.0)
        unassigned = [row for row in rows if row['is_unassigned']]
        self.assertFalse(
            [row for row in unassigned if row['current_commitment']],
            "Procurement-created commitment must not land under Unassigned.")

    # -- TEST 7 ---------------------------------------------------------
    def test_07_analytic_matches_a_hand_coded_construction_line(self):
        """One formula, in one place — Construction's."""
        request = self._approved_request()
        request.action_create_rfqs(vendors=self.vendor)
        generated = request.purchase_order_ids.order_line

        hand = self._confirmed_po(
            self.project, self.vendor, [(self.concrete, 3_000_000.0)],
            confirm=False)
        hand.order_line.re_wbs_id = self.wbs

        self.assertEqual(generated.analytic_distribution,
                         hand.order_line.analytic_distribution)
        # One key per line, holding both plans. Two separate keys would post
        # each analytic line at the full amount and double the actual.
        self.assertEqual(len(generated.analytic_distribution), 1)

    # -- TEST 8 ---------------------------------------------------------
    def test_08_each_line_keeps_its_own_cost_code(self):
        concrete_item = self._product(price=1_000.0, vendor=self.vendor)
        cable = self._product(price=50.0, vendor=self.vendor)
        request = self._request(
            self.project, [(concrete_item, 10.0)],
            default_cost_code_id=self.concrete.id,
            line_defaults={'cost_code_id': self.concrete.id})
        self.RequestLine.create({
            'request_id': request.id,
            'product_id': cable.id,
            'qty': 100.0,
            'uom_id': cable.uom_id.id,
            'cost_code_id': self.electrical.id,
        })
        request.action_submit()
        request.action_approve()
        request.action_create_rfqs(vendors=self.vendor)

        codes = request.purchase_order_ids.order_line.mapped(
            're_cost_code_id')
        self.assertEqual(codes, self.concrete | self.electrical)
        by_product = {
            line.product_id: line.re_cost_code_id
            for line in request.purchase_order_ids.order_line}
        self.assertEqual(by_product[concrete_item], self.concrete)
        self.assertEqual(by_product[cable], self.electrical,
                         "The header default must not overwrite a line code.")

    # -- TEST 9 ---------------------------------------------------------
    def test_09_a_request_cannot_borrow_another_projects_wbs(self):
        other = self._project()
        foreign = self._wbs(other, 'Z.9')
        product = self._product(price=10.0)
        request = self._request(self.project, [(product, 1.0)])
        with self.assertRaises(ValidationError):
            request.line_ids[0].wbs_id = foreign

    # -- TEST 12 --------------------------------------------------------
    def test_12_required_on_site_is_not_the_vendors_promised_date(self):
        product = self._product(price=100.0, vendor=self.vendor)
        on_site = self.today
        request = self._request(
            self.project, [(product, 5.0)],
            line_defaults={'cost_code_id': self.concrete.id,
                           'required_on_site_date': on_site})
        request.action_submit()
        request.action_approve()
        request.action_create_rfqs(vendors=self.vendor)

        po_line = request.purchase_order_ids.order_line
        self.assertEqual(request.line_ids.required_on_site_date, on_site,
                         "The requisition keeps saying what the works need.")
        po_line.date_planned = po_line.date_planned.replace(year=2099)
        self.assertEqual(
            request.line_ids.required_on_site_date, on_site,
            "A vendor moving their promise does not move the site need.")

    # -- TEST 13 --------------------------------------------------------
    def test_13_an_unknown_lead_time_produces_no_target_date(self):
        plan = self._plan()
        unknown = self._plan_line(plan, 100_000.0,
                                  required_on_site_date=self.today)
        self.assertFalse(unknown.has_lead_time)
        self.assertFalse(
            unknown.target_award_date,
            "Required date minus zero is not a plan, it is a fiction.")
        self.assertFalse(unknown.target_rfq_date)
        self.assertFalse(unknown.is_long_lead)

        known = self._plan_line(plan, 100_000.0,
                                required_on_site_date=self.today,
                                lead_time_days=120)
        self.assertTrue(known.has_lead_time)
        self.assertTrue(known.target_award_date)
        self.assertTrue(known.target_rfq_date)
        self.assertLess(known.target_rfq_date, known.target_award_date)
        self.assertTrue(known.is_long_lead, "120 days is past the threshold.")

    def test_13b_the_long_lead_threshold_is_configuration(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_procurement.long_lead_threshold_days', '30')
        plan = self._plan()
        line = self._plan_line(plan, 1_000.0,
                               required_on_site_date=self.today,
                               lead_time_days=45)
        self.assertTrue(line.is_long_lead)
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_procurement.long_lead_threshold_days', '90')

    # -- TEST 14 --------------------------------------------------------
    def test_14_a_legacy_confirmed_order_survives_classification(self):
        """Migration classifies. It does not rewrite commercial history."""
        request = self._approved_request()
        request.action_create_rfqs(vendors=self.vendor)
        order = request.purchase_order_ids
        order.order_line.taxes_id = [(5, 0, 0)]
        order.button_confirm()
        commitment_before = self._commitment(self.project)
        order_count_before = len(request.purchase_order_ids)

        request.invalidate_recordset()
        request._classify_procurement_legacy()

        self.assertEqual(order.state, 'purchase',
                         "A confirmed order is not reverted to an enquiry.")
        self.assertEqual(self._commitment(self.project), commitment_before)
        self.assertEqual(len(request.purchase_order_ids), order_count_before,
                         "Classification creates no new enquiry.")
        self.assertEqual(request.legacy_status, 'linked_rfq_po')

    def test_14b_classification_names_what_is_missing(self):
        product = self._product(price=100.0)
        orphan = self._request(None, [(product, 1.0)])
        uncoded = self._request(self.project, [(product, 1.0)])

        (orphan | uncoded)._classify_procurement_legacy()
        self.assertEqual(orphan.legacy_status, 'needs_project')
        self.assertEqual(uncoded.legacy_status, 'needs_cost_code')

        # And the same question asked the way the migration asks it: from the
        # lines, with the stored coverage aggregates deliberately blanked, as
        # they are on a freshly migrated row.
        # And the same question asked the way the migration asks it, where
        # the coding fields are not yet in the registry and the answer has to
        # be supplied from SQL.
        coded_request = self._request(
            self.project, [(product, 1.0)],
            line_defaults={'cost_code_id': self.concrete.id,
                           'wbs_id': self.wbs.id})
        (uncoded | coded_request)._classify_procurement_legacy(
            uncoded_ids={uncoded.id}, unassigned_wbs_ids={uncoded.id})
        self.assertEqual(
            uncoded.legacy_status, 'needs_cost_code',
            "Absent coding must not read as coded.")
        self.assertEqual(coded_request.legacy_status, 'valid')

        coded = self._request(
            self.project, [(product, 1.0)],
            line_defaults={'cost_code_id': self.concrete.id,
                           'wbs_id': self.wbs.id})
        coded._classify_procurement_legacy()
        self.assertEqual(coded.legacy_status, 'valid')

    # -- TEST 15 --------------------------------------------------------
    def test_15_an_approved_basis_cannot_be_quietly_rewritten(self):
        request = self._approved_request()
        request.action_create_rfqs(vendors=self.vendor)
        self.assertEqual(request.state, 'sourcing')
        line = request.line_ids[0]

        with self.assertRaises(UserError):
            line.qty = 4_500.0
        with self.assertRaises(UserError):
            line.cost_code_id = self.electrical
        self.assertEqual(line.qty, 3_000.0)

        with self.assertRaises(UserError):
            request.action_revise()

        request.action_revise(reason='Structural revision C increased the pour')
        self.assertEqual(request.state, 'draft')
        self.assertEqual(request.revision, 1)

        history = request.revision_ids
        self.assertEqual(len(history), 1)
        self.assertEqual(history.revision, 0)
        self.assertEqual(history.estimated_total, 3_000_000.0)
        self.assertIn('3000.0', history.basis)

        line.qty = 4_500.0
        self.assertEqual(request.estimated_total, 4_500_000.0)
        self.assertEqual(
            history.estimated_total, 3_000_000.0,
            "What was approved stays readable after the request changes.")

    def test_15b_a_draft_request_is_edited_freely(self):
        product = self._product(price=100.0, vendor=self.vendor)
        request = self._request(self.project, [(product, 10.0)])
        request.line_ids.qty = 20.0
        self.assertEqual(request.line_ids.qty, 20.0)

    # -- TEST 16 --------------------------------------------------------
    def test_16_one_requisition_line_may_reach_several_orders(self):
        first = self._vendor('Split A')
        second = self._vendor('Split B')
        product = self._product(price=1_000.0, vendor=first)
        request = self._request(
            self.project, [(product, 100.0)],
            line_defaults={'cost_code_id': self.concrete.id})
        request.action_submit()
        request.action_approve()
        request.action_create_rfqs(vendors=first | second)

        line = request.line_ids[0]
        self.assertEqual(len(line.po_line_ids), 2,
                         "The relation must already allow a split award.")
        self.assertEqual(len(request.purchase_order_ids), 2)
        self.assertEqual(request.rfq_count, 2)
        self.assertEqual(request.ordered_count, 0)
        self.assertEqual(line.ordered_qty, 0.0,
                         "Nothing is ordered until an order is confirmed.")
        self.assertEqual(line.remaining_qty, 100.0)

    def test_16b_ordered_quantity_follows_confirmation(self):
        request = self._approved_request()
        request.action_create_rfqs(vendors=self.vendor)
        order = request.purchase_order_ids
        order.order_line.taxes_id = [(5, 0, 0)]
        order.order_line.product_qty = 1_800.0
        order.button_confirm()

        request.invalidate_recordset()
        line = request.line_ids[0]
        self.assertEqual(line.ordered_qty, 1_800.0)
        self.assertEqual(line.remaining_qty, 1_200.0)
        self.assertEqual(
            request.state, 'partially_ordered',
            "M3P — 1,800 of 3,000 on a confirmed order is not 'ordered'. The "
            "remainder is still reserved and still has to be bought.")
        self.assertEqual(request.ordered_count, 1)

    # -- TEST 17 --------------------------------------------------------
    def test_17_procurement_does_not_touch_construction_finance(self):
        """The whole milestone in one assertion set."""
        Controls = self.env['realestate.construction.controls']
        budget_before = sum(
            row.get('current', 0.0)
            for row in Controls.budget_by_cost_code(self.project).values())

        plan = self._plan()
        self._plan_line(plan, 3_000_000.0)
        plan.action_review()
        plan.action_approve()

        request = self._approved_request()
        request.action_create_rfqs(vendors=self.vendor)

        budget_after = sum(
            row.get('current', 0.0)
            for row in Controls.budget_by_cost_code(self.project).values())
        self.assertEqual(budget_after, budget_before)
        self.assertEqual(budget_after, 10_000_000.0)
        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._actual(self.project), 0.0)

    def test_17b_two_approved_requests_still_reserve_nothing(self):
        """An explicit M2 gap, asserted rather than glossed over.

        Reservation is M3's. Until it exists, two approved requests can each
        plan to spend the same budget, and the report says so.
        """
        self._approved_request()
        self._approved_request()
        self.assertEqual(
            self._commitment(self.project), 0.0,
            "No commitment — and equally, no reservation. M3 owns that.")

    # -- Data quality (M2P) ---------------------------------------------
    def test_18_uncoded_lines_are_counted_not_hidden(self):
        product = self._product(price=100.0)
        request = self._request(self.project, [(product, 1.0)])
        self.assertEqual(request.lines_missing_cost_code, 1)
        self.assertEqual(request.lines_missing_wbs, 1)
        self.assertFalse(request.is_fully_coded)
        self.assertEqual(request.coding_status, 'unassigned')
        self.assertIn('cost code', request.data_quality_warnings.lower())

        request.line_ids.write({'cost_code_id': self.concrete.id,
                                'wbs_id': self.wbs.id})
        request.invalidate_recordset()
        self.assertTrue(request.is_fully_coded)
        self.assertEqual(request.coding_status, 'coded')
        self.assertFalse(request.data_quality_warnings)

    def test_18b_an_unknown_estimate_is_not_a_zero_estimate(self):
        product = self.env['product.product'].create({
            'name': 'Unpriced item', 'type': 'consu', 'purchase_ok': True,
            'standard_price': 0.0, 'uom_id': self.uom_unit.id,
        })
        request = self._request(self.project, [(product, 5.0)])
        line = request.line_ids[0]
        self.assertFalse(line.estimate_is_known)
        self.assertEqual(line.estimated_cost, 0.0)
        self.assertIn('estimate', request.data_quality_warnings.lower())

    # -- Source classification (M2E) -------------------------------------
    def test_19b_a_request_records_where_the_need_came_from(self):
        """An exact relation, not a text field — and no guessing."""
        product = self._product(price=100.0)
        change = self.env['realestate.construction.change.order'].search(
            [], limit=1)
        manual = self._request(self.project, [(product, 1.0)])
        self.assertEqual(manual.source_type, 'manual',
                         "Nothing to go on means nothing is claimed.")

        work_item = self.env['realestate.work.item'].create({
            'name': 'Concrete works %d' % self._next(),
            'code': 'WI%03d' % self._next(),
            'uom_id': self.uom_unit.id,
        })
        boq = self.env['realestate.boq'].create({
            'project_id': self.project.id,
            'company_id': self.company.id,
            'line_ids': [(0, 0, {
                'work_item_id': work_item.id,
                'uom_id': self.uom_unit.id,
                'description': 'Concrete works',
                'quantity': 100.0,
                'unit_rate': 500.0,
                'cost_code_id': self.concrete.id,
            })],
        })
        from_boq = self._request(self.project, [(product, 1.0)],
                                 boq_line_id=boq.line_ids[0].id)
        self.assertEqual(from_boq.source_type, 'boq')
        self.assertEqual(from_boq.boq_line_id, boq.line_ids[0])

        if change:
            from_change = self._request(self.project, [(product, 1.0)],
                                        change_order_id=change.id)
            self.assertEqual(from_change.source_type, 'change_order')

    # -- Deprecated flow -------------------------------------------------
    def test_19_the_old_one_click_action_refuses_rather_than_pretends(self):
        request = self._approved_request()
        with self.assertRaises(UserError):
            request.action_create_purchase_orders()
        self.assertFalse(request.purchase_order_ids)
        self.assertEqual(self._commitment(self.project), 0.0)
