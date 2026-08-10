# -*- coding: utf-8 -*-
"""M4 — the change-management test matrix (§66–§82)."""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import ConstructionCommon


class ChangeCommon(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.Event = self.env['realestate.construction.change.event']
        self.Order = self.env['realestate.construction.change.order']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        self.mep = self._cost_code('SUB-MEP', 'MEP', 'subcontract')
        self.contingency = self._cost_code(
            'CONT-01', 'Contingency', 'contingency', is_contingency=True)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestChangeEvent(ChangeCommon):

    def test_an_event_walks_its_lifecycle(self):
        event = self._change_event(self.project, estimated_cost=500_000.0)

        event.action_review()
        self.assertEqual(event.state, 'under_review')
        event.action_price()
        event.action_assess()
        self.assertEqual(event.state, 'assessed')
        event.action_change_required()
        self.assertEqual(event.state, 'change_required')

    def test_an_event_closed_as_no_change_stops_being_exposure(self):
        event = self._change_event(self.project, estimated_cost=500_000.0)
        self.assertEqual(
            self.Event.exposure(self.project)['potential_cost'], 500_000.0)

        event.action_review()
        event.action_assess()
        event.action_no_change()

        self.assertEqual(
            self.Event.exposure(self.project)['potential_cost'], 0.0)

    def test_an_event_with_an_implemented_order_cannot_be_dismissed(self):
        event = self._change_event(self.project, estimated_cost=500_000.0)
        order = self._change_order(
            self.project, order_type='contractor_variation',
            contractor=self.contractor, event=event,
            lines=[(self.civil, 'commitment', 500_000.0)])
        self._approve_and_implement(order)

        with self.assertRaises(UserError):
            event.action_no_change()
        with self.assertRaises(UserError):
            event.action_cancel()

    def test_a_duplicate_source_reference_is_flagged_never_merged(self):
        first = self._change_event(self.project, estimated_cost=100.0,
                                   source_reference='SI-014')
        second = self._change_event(self.project, estimated_cost=100.0,
                                    source_reference='SI-014')

        bodies = ' '.join(second.message_ids.mapped('body'))
        self.assertIn('Possible duplicate', bodies)
        self.assertIn(first.name, bodies)
        self.assertTrue(first.exists(), "Nothing was merged away.")
        self.assertEqual(
            self.Event.exposure(self.project)['potential_cost'], 200.0,
            "Both estimates still count until somebody decides.")

    def test_an_event_produces_a_change_order_carrying_its_estimate(self):
        event = self._change_event(
            self.project, estimated_cost=750_000.0,
            cost_code_id=self.civil.id, contractor_id=self.contractor.id)
        event.action_review()
        event.action_assess()

        event.action_create_change_order()
        order = event.change_order_ids

        self.assertEqual(len(order), 1)
        self.assertEqual(order.change_event_id, event)
        self.assertEqual(order.line_ids.estimated_amount, 750_000.0)
        self.assertEqual(order.line_ids.impact_side, 'commitment')

    def test_one_event_may_produce_several_orders(self):
        event = self._change_event(self.project, estimated_cost=1_000_000.0)
        cost = self._change_order(
            self.project, contractor=self.contractor, event=event,
            lines=[(self.civil, 'commitment', 1_000_000.0)])
        revenue = self._change_order(
            self.project, order_type='owner_variation', event=event,
            partner_id=self.env['res.partner'].create({'name': 'Owner'}).id,
            lines=[(self.civil, 'revenue', 1_300_000.0)])

        self.assertEqual(len(event.change_order_ids), 2)
        self._approve_and_implement(cost)
        self._approve_and_implement(revenue)
        event.invalidate_recordset()
        self.assertEqual(event.approved_cost_change, 1_000_000.0)
        self.assertEqual(event.approved_revenue_change, 1_300_000.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestChangeOrderAmounts(ChangeCommon):
    """§67 — every stage survives."""

    def test_each_negotiation_stage_stays_distinguishable(self):
        order = self.Order.create({
            'title': 'Façade revision',
            'project_id': self.project.id,
            'contractor_id': self.contractor.id,
            'line_ids': [(0, 0, {
                'cost_code_id': self.civil.id,
                'impact_side': 'commitment',
                'estimated_amount': 1_500_000.0,
                'proposed_amount': 1_900_000.0,
                'submitted_amount': 1_900_000.0,
                'assessed_amount': 1_650_000.0,
                'negotiated_amount': 1_700_000.0,
            })],
        })
        self._approve_and_implement(order)

        self.assertEqual(order.estimated_amount, 1_500_000.0)
        self.assertEqual(order.submitted_amount, 1_900_000.0)
        self.assertEqual(order.assessed_amount, 1_650_000.0)
        self.assertEqual(order.negotiated_amount, 1_700_000.0)
        self.assertEqual(
            order.approved_amount, 1_700_000.0,
            "Approved defaults to the negotiated figure and does not "
            "overwrite any earlier one.")

    def test_a_resubmission_keeps_the_previous_quotation(self):
        order = self._change_order(
            self.project, contractor=self.contractor,
            lines=[(self.civil, 'commitment', 1_900_000.0)])
        order.action_submit()
        order.action_negotiate()
        order.line_ids.negotiated_amount = 1_700_000.0
        order.action_submit()

        revisions = order.revision_ids.sorted('revision')
        self.assertEqual(len(revisions), 2)
        self.assertEqual(revisions[0].submitted_amount, 1_900_000.0)
        self.assertEqual(order.revision, 2)

    def test_markups_are_priced_beside_the_scope(self):
        order = self._change_order(
            self.project, contractor=self.contractor,
            lines=[(self.civil, 'commitment', 1_000_000.0)])
        markup = self.env[
            'realestate.construction.change.order.markup'].create({
                'order_id': order.id, 'markup_type': 'overhead',
                'computation': 'percentage', 'percentage': 10.0})
        markup.invalidate_recordset()
        order.invalidate_recordset()

        self.assertEqual(markup.amount, 100_000.0)
        self.assertEqual(order.markup_total, 100_000.0)
        self.assertEqual(
            order.negotiated_amount, 1_000_000.0,
            "The scope figure is the scope figure; the markup is visible "
            "separately rather than hidden inside a rate.")

    def test_line_level_impact_across_several_cost_codes(self):
        """§13 — concrete +400K, MEP +250K, finishes −100K, net +550K."""
        finishes = self._cost_code('FIN-01', 'Finishes', 'subcontract')
        order = self._change_order(
            self.project, contractor=self.contractor,
            lines=[(self.civil, 'commitment', 400_000.0),
                   (self.mep, 'commitment', 250_000.0),
                   (finishes, 'commitment', -100_000.0)])
        self._approve_and_implement(order)

        self.assertEqual(order.approved_amount, 550_000.0)
        self.assertEqual(order.gross_impact, 750_000.0)
        totals = self.Controls.project_totals(self.project)
        self.assertEqual(totals['approved_commitment_changes'], 550_000.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestBudgetPropagation(ChangeCommon):

    def test_a_budget_transfer_moves_money_without_increasing_the_total(self):
        """§20/§72 — Code A −500K, Code B +500K, total unchanged."""
        budget = self.env['realestate.construction.budget'].create({
            'project_id': self.project.id,
            'line_ids': [
                (0, 0, {'cost_code_id': self.civil.id,
                        'amount_mode': 'lumpsum',
                        'original_amount': 6_000_000.0}),
                (0, 0, {'cost_code_id': self.mep.id,
                        'amount_mode': 'lumpsum',
                        'original_amount': 4_000_000.0}),
            ]})
        budget.action_submit()
        budget.action_approve()
        budget.action_baseline()

        order = self._change_order(
            self.project, order_type='budget_transfer',
            lines=[(self.civil, 'budget', -500_000.0),
                   (self.mep, 'budget', 500_000.0)])
        self._approve_and_implement(order)

        totals = self.Controls.project_totals(self.project)
        self.assertEqual(totals['original_budget'], 10_000_000.0)
        self.assertEqual(totals['approved_budget_changes'], 0.0)
        self.assertEqual(totals['current_budget'], 10_000_000.0)

        rows = {r['cost_code_id']: r
                for r in self.Controls.cost_report_rows(self.project)}
        self.assertEqual(rows[self.civil.id]['current_budget'], 5_500_000.0)
        self.assertEqual(rows[self.mep.id]['current_budget'], 4_500_000.0)

    def test_a_transfer_that_does_not_net_to_zero_is_refused(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        order = self._change_order(
            self.project, order_type='budget_transfer',
            lines=[(self.civil, 'budget', -500_000.0),
                   (self.mep, 'budget', 800_000.0)])
        order.action_submit()
        order.action_request_approval()

        with self.assertRaises(UserError):
            order.action_approve()

    def test_a_contingency_drawdown_funds_scope_without_growing_the_budget(self):
        """§22/§73 — contingency −1M, MEP +1M, project total unchanged."""
        budget = self.env['realestate.construction.budget'].create({
            'project_id': self.project.id,
            'line_ids': [
                (0, 0, {'cost_code_id': self.mep.id,
                        'amount_mode': 'lumpsum',
                        'original_amount': 9_000_000.0}),
                (0, 0, {'cost_code_id': self.contingency.id,
                        'amount_mode': 'lumpsum',
                        'original_amount': 1_000_000.0}),
            ]})
        budget.action_submit()
        budget.action_approve()
        budget.action_baseline()

        order = self._change_order(
            self.project, order_type='contingency_drawdown',
            lines=[(self.contingency, 'contingency', -1_000_000.0),
                   (self.mep, 'budget', 1_000_000.0)])
        self._approve_and_implement(order)

        totals = self.Controls.project_totals(self.project)
        self.assertEqual(
            totals['current_budget'], 10_000_000.0,
            "Drawing on contingency spends money already authorised; it is "
            "not new authority.")
        rows = {r['cost_code_id']: r
                for r in self.Controls.cost_report_rows(self.project)}
        self.assertEqual(rows[self.mep.id]['current_budget'], 10_000_000.0)
        self.assertEqual(rows[self.contingency.id]['current_budget'], 0.0)

    def test_a_budget_change_without_a_baseline_says_so(self):
        order = self._change_order(
            self.project, order_type='budget_change',
            lines=[(self.civil, 'budget', 1_000_000.0)])
        order.action_submit()
        order.action_request_approval()
        order.action_approve()

        with self.assertRaises(UserError):
            order.action_implement()

    def test_budget_change_records_cannot_be_edited_or_deleted(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        order = self._change_order(
            self.project, order_type='budget_change',
            lines=[(self.civil, 'budget', 2_000_000.0)])
        self._approve_and_implement(order)
        change = order.budget_change_ids

        with self.assertRaises(UserError):
            change.write({'amount': 5_000_000.0})
        with self.assertRaises(UserError):
            change.unlink()


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCommitmentPropagation(ChangeCommon):

    def test_a_package_variation_keeps_its_original_value(self):
        """§24 — original 20M, variation +3M, current 23M."""
        package = self._package(self.project, self.contractor,
                                value=20_000_000.0, award=True)
        package.cost_code_ids = self.civil

        order = self._change_order(
            self.project, contractor=self.contractor, package=package,
            lines=[(self.civil, 'commitment', 3_000_000.0)])
        self._approve_and_implement(order)
        package.invalidate_recordset()

        self.assertEqual(package.original_contract_value, 20_000_000.0)
        self.assertEqual(package.approved_variation_amount, 3_000_000.0)
        self.assertEqual(package.current_contract_value, 23_000_000.0)

    def test_a_linked_package_and_po_count_a_variation_once(self):
        """§26/§70 — package 8M, PO 8M, variation 2M → 10M. Never 12M or 18M."""
        package = self._package(self.project, self.contractor,
                                value=8_000_000.0, award=True)
        self._po(self.project, self.contractor, [(self.civil, 8_000_000.0)],
                 package=package)
        self.assertEqual(
            self.Controls.project_totals(self.project)['current_commitment'],
            8_000_000.0)

        order = self._change_order(
            self.project, contractor=self.contractor, package=package,
            lines=[(self.civil, 'commitment', 2_000_000.0)])
        self._approve_and_implement(order)

        totals = self.Controls.project_totals(self.project)
        self.assertEqual(totals['current_commitment'], 10_000_000.0)
        self.assertNotEqual(totals['current_commitment'], 12_000_000.0)
        self.assertNotEqual(totals['current_commitment'], 18_000_000.0)

    def test_a_variation_folded_into_its_po_is_not_counted_again(self):
        """When the order is amended, the order carries the new value."""
        package = self._package(self.project, self.contractor,
                                value=8_000_000.0, award=True)
        po = self._po(self.project, self.contractor,
                      [(self.civil, 8_000_000.0)], package=package)
        order = self._change_order(
            self.project, contractor=self.contractor, package=package,
            lines=[(self.civil, 'commitment', 2_000_000.0)])
        self._approve_and_implement(order)

        # Amend the order and say so: the PO is now the operative document.
        po.order_line[0].price_unit = 10_000_000.0
        order.commitment_change_ids.write({
            'reflected_in_purchase_order': True,
            'purchase_order_id': po.id})

        totals = self.Controls.project_totals(self.project)
        self.assertEqual(
            totals['current_commitment'], 10_000_000.0,
            "Still 10M — the change moved from one source to the other, not "
            "into both.")

    def test_an_omission_reduces_the_commitment(self):
        """§76 — commitment 10M, certified 2M, omission −1M → 9M."""
        package = self._package(self.project, self.contractor,
                                value=10_000_000.0, award=True)
        package.cost_code_ids = self.civil
        certificate = self._certificate(
            self.project, self.contractor, contract_value=10_000_000.0,
            pct=20.0)
        certificate.action_certify()

        order = self._change_order(
            self.project, contractor=self.contractor, package=package,
            lines=[(self.civil, 'commitment', -1_000_000.0)])
        self._approve_and_implement(order)
        package.invalidate_recordset()

        self.assertEqual(package.current_contract_value, 9_000_000.0)

    def test_an_omission_below_certified_value_is_refused(self):
        """§77 — original 10M, certified 8M, omission −5M → refuse."""
        package = self._package(self.project, self.contractor,
                                value=10_000_000.0, award=True)
        package.cost_code_ids = self.civil
        certificate = self._certificate(
            self.project, self.contractor, contract_value=10_000_000.0,
            pct=80.0)
        certificate.action_certify()

        order = self._change_order(
            self.project, contractor=self.contractor, package=package,
            lines=[(self.civil, 'commitment', -5_000_000.0)])
        order.action_submit()
        order.action_request_approval()
        order.action_approve()

        with self.assertRaises(UserError):
            order.action_implement()

    def test_an_approved_change_never_creates_actual(self):
        """§46/§79."""
        self._baselined(self.project, 20_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 8_000_000.0)])
        order = self._change_order(
            self.project, contractor=self.contractor,
            lines=[(self.civil, 'commitment', 2_000_000.0)])
        self._approve_and_implement(order)

        self.assertEqual(
            self.Controls.project_totals(self.project)['actual_cost'], 0.0)

        self._post_bill(self.project, self.civil, 1_500_000.0)

        self.assertEqual(
            self.Controls.project_totals(self.project)['actual_cost'],
            1_500_000.0,
            "Actual moves when Accounting posts, and only then.")

    def test_commitment_may_exceed_budget_without_the_budget_moving(self):
        """§45."""
        self._baselined(self.project, 5_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 5_000_000.0)])
        order = self._change_order(
            self.project, contractor=self.contractor,
            lines=[(self.civil, 'commitment', 2_000_000.0)])
        self._approve_and_implement(order)

        totals = self.Controls.project_totals(self.project)
        self.assertEqual(totals['current_budget'], 5_000_000.0)
        self.assertEqual(totals['current_commitment'], 7_000_000.0)
        self.assertTrue(totals['over_committed'])


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCostVersusRevenue(ChangeCommon):
    """§28/§71 — cost +1M, revenue +1.3M, margin +300K, never merged."""

    def test_cost_and_revenue_changes_are_tracked_separately(self):
        owner = self.env['res.partner'].create({'name': 'Project Owner'})
        event = self._change_event(self.project, estimated_cost=1_000_000.0)
        cost = self._change_order(
            self.project, contractor=self.contractor, event=event,
            lines=[(self.civil, 'commitment', 1_000_000.0)])
        revenue = self._change_order(
            self.project, order_type='owner_variation', event=event,
            partner_id=owner.id,
            lines=[(self.civil, 'revenue', 1_300_000.0)])

        self._approve_and_implement(cost)
        self._approve_and_implement(revenue)

        exposure = self.Event.exposure(self.project)
        self.assertEqual(exposure['approved_cost'], 1_000_000.0)
        self.assertEqual(exposure['approved_revenue'], 1_300_000.0)
        self.assertEqual(exposure['change_margin'], 300_000.0)

        totals = self.Controls.project_totals(self.project)
        self.assertEqual(
            totals['approved_commitment_changes'], 1_000_000.0,
            "Revenue does not leak into the cost commitment.")

    def test_a_revenue_change_writes_an_owner_record(self):
        owner = self.env['res.partner'].create({'name': 'Project Owner'})
        order = self._change_order(
            self.project, order_type='owner_variation', partner_id=owner.id,
            lines=[(self.civil, 'revenue', 500_000.0)])

        self._approve_and_implement(order)

        change = self.env['realestate.construction.revenue.change'].search(
            [('change_order_id', '=', order.id)])
        self.assertEqual(len(change), 1)
        self.assertEqual(change.amount, 500_000.0)
        self.assertEqual(change.partner_id, owner)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestChangeGovernance(ChangeCommon):

    def test_implementation_is_idempotent(self):
        """§63/§82 — twice is once."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        order = self._change_order(
            self.project, order_type='budget_change',
            lines=[(self.civil, 'budget', 2_000_000.0)])
        self._approve_and_implement(order)

        order.action_implement()
        order.action_implement()

        self.assertEqual(len(order.budget_change_ids), 1)
        self.assertEqual(
            self.Controls.project_totals(self.project)[
                'approved_budget_changes'], 2_000_000.0)

    def test_an_implemented_change_cannot_be_edited(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        order = self._change_order(
            self.project, order_type='budget_change',
            lines=[(self.civil, 'budget', 2_000_000.0)])
        self._approve_and_implement(order)

        with self.assertRaises(UserError):
            order.write({'order_type': 'internal_change'})
        with self.assertRaises(UserError):
            order.line_ids.write({'approved_amount': 9.0})
        with self.assertRaises(UserError):
            order.action_cancel()

    def test_a_preparer_cannot_approve_their_own_change(self):
        """The rule the fixtures lift, put back."""
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.self_approval_limit', '0')
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        order = self._change_order(
            self.project, order_type='budget_change',
            lines=[(self.civil, 'budget', 2_000_000.0)])
        order.action_submit()
        order.action_request_approval()

        with self.assertRaises(UserError):
            order.action_approve()

    def test_authority_is_judged_on_gross_not_net_impact(self):
        """§39 — +5M and −5M nets to zero and is still a decision."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        order = self._change_order(
            self.project, order_type='budget_transfer',
            lines=[(self.civil, 'budget', -5_000_000.0),
                   (self.mep, 'budget', 5_000_000.0)])

        self.assertEqual(order.negotiated_amount, 0.0)
        self.assertEqual(
            order.gross_impact, 10_000_000.0,
            "Authority is judged on what moved, not on what it netted to.")

    def test_approval_below_authority_is_refused_server_side(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.change_approval_limit_manager', '100')
        site_user = self.env['res.users'].create({
            'name': 'Site Engineer', 'login': 'site_eng_%d' % self._next(),
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_construction.group_construction_user').id])],
        })
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        order = self._change_order(
            self.project, order_type='budget_change',
            lines=[(self.civil, 'budget', 2_000_000.0)])
        order.action_submit()
        order.action_request_approval()

        with self.assertRaises(UserError):
            order.with_user(site_user).action_approve()

    def test_approvals_are_recorded_with_who_and_how_much(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        order = self._change_order(
            self.project, order_type='budget_change',
            lines=[(self.civil, 'budget', 2_000_000.0)])
        self._approve_and_implement(order)

        approval = order.approval_ids
        self.assertEqual(len(approval), 1)
        self.assertEqual(approval.decision, 'approved')
        self.assertEqual(approval.user_id, self.env.user)
        self.assertEqual(approval.amount_at_decision, 2_000_000.0)

    def test_a_change_is_tax_exclusive(self):
        """§15/§78 — a 1M variation is 1M of commitment, whatever the VAT."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        order = self._change_order(
            self.project, contractor=self.contractor,
            lines=[(self.civil, 'commitment', 1_000_000.0)])
        self._approve_and_implement(order)

        totals = self.Controls.project_totals(self.project)
        self.assertEqual(totals['approved_commitment_changes'], 1_000_000.0)

        vat = self._vat(15.0)
        self._post_bill(self.project, self.civil, 1_000_000.0, tax=vat)

        self.assertEqual(
            self.Controls.project_totals(self.project)['actual_cost'],
            1_000_000.0,
            "The bill carries VAT; project cost does not.")

    def test_a_change_cannot_cross_companies(self):
        other = self.env['res.company'].create({'name': 'Change Other Co'})
        other_project = self._project()
        other_project.company_id = other

        with self.assertRaises(ValidationError):
            self.Order.create({
                'title': 'Cross-company',
                'project_id': other_project.id,
                'company_id': self.company.id,
                'line_ids': [(0, 0, {'cost_code_id': self.civil.id,
                                     'impact_side': 'budget',
                                     'estimated_amount': 1.0})],
            })

    def test_a_package_from_another_project_is_refused(self):
        other_project = self._project()
        stranger = self._package(other_project, self.contractor, value=1.0)

        with self.assertRaises(ValidationError):
            self._change_order(
                self.project, contractor=self.contractor, package=stranger,
                lines=[(self.civil, 'commitment', 1.0)])


@tagged('post_install', '-at_install', 'atmta_construction')
class TestChangeReporting(ChangeCommon):

    def test_potential_and_approved_never_mix(self):
        """§34/§58."""
        self._change_event(self.project, estimated_cost=2_000_000.0)
        approved = self._change_order(
            self.project, contractor=self.contractor,
            lines=[(self.civil, 'commitment', 1_000_000.0)])
        self._approve_and_implement(approved)

        exposure = self.Event.exposure(self.project)

        self.assertEqual(exposure['potential_cost'], 2_000_000.0)
        self.assertEqual(exposure['approved_cost'], 1_000_000.0)
        self.assertEqual(exposure['potential_count'], 1)
        self.assertEqual(exposure['approved_count'], 1)

    def test_exposure_counts_match_their_drilldown(self):
        for _ in range(3):
            self._change_event(self.project, estimated_cost=100_000.0)

        exposure = self.Event.exposure(self.project)
        drilldown = self.Event.search([
            ('project_id', '=', self.project.id),
            ('state', 'in', ('draft', 'identified', 'under_review',
                             'pricing', 'assessed', 'change_required'))])

        self.assertEqual(exposure['potential_count'], len(drilldown))
        self.assertEqual(exposure['potential_cost'],
                         sum(drilldown.mapped('estimated_cost_impact')))

    def test_the_cost_report_shows_approved_changes_not_potential_ones(self):
        """§84."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._change_event(self.project, estimated_cost=3_000_000.0,
                           cost_code_id=self.civil.id)
        order = self._change_order(
            self.project, order_type='budget_change',
            lines=[(self.civil, 'budget', 1_000_000.0)])
        self._approve_and_implement(order)

        rows = self.Controls.cost_report_rows(self.project)
        row = rows[0]

        self.assertEqual(row['approved_budget_changes'], 1_000_000.0)
        self.assertEqual(
            row['current_budget'], 11_000_000.0,
            "The potential 3M is nowhere near the current budget.")

    def test_another_companys_changes_are_invisible(self):
        other_company = self.env['res.company'].create({'name': 'CX Other'})
        other_project = self._project()
        other_project.company_id = other_company
        self._change_event(self.project, estimated_cost=2_000_000.0)

        exposure = self.Event.exposure(other_project)

        self.assertEqual(exposure['potential_cost'], 0.0)
        self.assertEqual(exposure['approved_cost'], 0.0)
