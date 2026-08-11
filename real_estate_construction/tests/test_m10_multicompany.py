# -*- coding: utf-8 -*-
"""M10L — the multi-company torture test.

Odoo lets a user hold several companies active at once, which is exactly the
configuration where isolation quietly fails: a record rule that reads
`env.company` instead of `company_ids` looks correct in every single-company
test and leaks the moment somebody ticks two boxes.

So every assertion here is made with **both** companies allowed and one
active, which is the shape of the real deployment.
"""

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestMultiCompanyIsolation(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.company_a = self.company
        self.company_b = self.env['res.company'].create({'name': 'M10 Co B'})

        self.contractor = self._contractor()
        self.civil_a = self._cost_code('MC-A-CIV', 'Civil A', 'subcontract')

        self.project_a = self._project(name='M10 Project A')
        self._baselined(self.project_a, 100_000_000.0, code=self.civil_a)
        self._po(self.project_a, self.contractor,
                 [(self.civil_a, 60_000_000.0)])
        self._post_bill(self.project_a, self.civil_a, 20_000_000.0)

        # Company B is built explicitly rather than through the fixtures,
        # because the fixtures default to the active company — quietly
        # building B's data inside A is the exact confusion this file exists
        # to detect.
        self.project_b = self._project(name='M10 Project B',
                                       company_id=self.company_b.id)
        self.civil_b = self.env[
            'realestate.construction.cost.code'].create({
                'code': 'MC-B-CIV', 'name': 'Civil B',
                'category': 'subcontract',
                'company_id': self.company_b.id})
        budget_b = self.env['realestate.construction.budget'].create({
            'project_id': self.project_b.id,
            'company_id': self.company_b.id,
            'line_ids': [(0, 0, {
                'cost_code_id': self.civil_b.id,
                'description': 'Works',
                'amount_mode': 'lumpsum',
                'original_amount': 50_000_000.0,
            })],
        })
        budget_b.action_submit()
        budget_b.action_approve()
        budget_b.action_baseline()

    def _user(self, login, companies, *groups):
        # Reading a project needs the developer role as well as the
        # construction one: the tower and the audit both start from
        # `realestate.project`, which lives in another module.
        base_groups = [
            self.env.ref('base.group_user').id,
            self.env.ref('base.group_multi_company').id,
            self.env.ref('atmta_real_estate.group_realestate_user').id,
        ]
        developer = self.env.ref('real_estate_developer.group_dev_manager',
                                 raise_if_not_found=False)
        if developer:
            base_groups.append(developer.id)
        return self.env['res.users'].create({
            'name': login, 'login': login,
            'company_id': companies[0].id,
            'company_ids': [(6, 0, [c.id for c in companies])],
            'groups_id': [(6, 0, base_groups + [
                self.env.ref('real_estate_construction.%s' % g).id
                for g in groups])],
        })

    # ------------------------------------------------------------------
    def test_a_user_of_company_a_never_sees_company_b(self):
        user = self._user('m10.a.only', [self.company_a],
                          'group_construction_manager')
        Budget = self.env['realestate.construction.budget'].with_user(user)
        visible = Budget.search([])
        self.assertTrue(visible)
        self.assertFalse(
            visible.filtered(lambda b: b.company_id == self.company_b),
            "Company B budgets reached a company A manager.")

    def _company_b_commitment(self, amount):
        """A confirmed purchase order on Project B, inside company B.

        Built here rather than in the fixtures because `_po()` takes the
        active company, and a company A order pointing at a company B project
        is not a record any deployment should hold.
        """
        contractor = self._contractor()
        product = contractor._ensure_service_product()
        order = self.env['purchase.order'].with_company(
            self.company_b).create({
                'company_id': self.company_b.id,
                'partner_id': contractor.partner_id.id,
                're_project_id': self.project_b.id,
                'order_line': [(0, 0, {
                    'product_id': product.id,
                    'name': self.civil_b.display_name,
                    'product_qty': 1.0,
                    'price_unit': amount,
                    'taxes_id': [(5, 0, 0)],
                    're_cost_code_id': self.civil_b.id,
                })],
            })
        order.with_company(self.company_b).button_confirm()
        return order

    def test_both_companies_active_still_scopes_each_project(self):
        """The configuration where a naive rule leaks.

        Project B is given a contribution of every kind Project A's payload
        aggregates — a baselined budget in `setUp`, and here a confirmed
        commitment and a posted actual. An isolation test whose neighbour
        contributes nothing has nothing to leak and cannot fail.
        """
        po_b = self._company_b_commitment(7_000_000.0)
        line_b = self._analytic_line(self.project_b, self.civil_b,
                                     3_000_000.0)

        user = self._user('m10.both', [self.company_a, self.company_b],
                          'group_construction_manager')
        allowed = user.company_ids.ids

        tower = self.env[
            'realestate.construction.control.tower'].with_user(user)
        payload_a = tower.with_context(
            allowed_company_ids=allowed).payload(self.project_a)
        cost_a = payload_a['cost']

        # 1. A's authorised figures, exactly. Any summing leak moves them:
        #    150M, 67M and 23M are what B's budget, commitment and actual
        #    would make of them.
        self.assertEqual(cost_a['current_budget'], 100_000_000.0)
        self.assertEqual(cost_a['current_commitment'], 60_000_000.0)
        self.assertEqual(cost_a['actual_cost'], 20_000_000.0)

        # 2. Row identity — cost codes compared as ids. `cost_code_id` is a
        #    real identifier on the row, unlike anything in the totals dict.
        codes_a = {row['cost_code_id']
                   for row in payload_a['cost_sheet']['rows']}
        self.assertIn(self.civil_a.id, codes_a)
        self.assertNotIn(self.civil_b.id, codes_a,
                         "Company B's cost code reached Project A's sheet.")

        # 3. The records behind the numbers. The drilldown domain is what the
        #    client runs, so it is the thing that has to be scoped; searching
        #    it with `sudo()` is deliberate, because a domain that only looks
        #    isolated because a record rule filtered it afterwards is not
        #    isolated — it is lucky.
        Sheet = self.env['realestate.construction.cost.sheet'].with_user(
            user).with_context(allowed_company_ids=allowed)

        commitment = Sheet.drilldown(self.project_a, 'current_commitment')
        po_lines = self.env[commitment['res_model']].sudo().search(
            commitment['domain'])
        self.assertTrue(po_lines)
        self.assertEqual(po_lines.order_id.re_project_id, self.project_a)
        self.assertEqual(po_lines.company_id, self.company_a)
        self.assertNotIn(po_b.order_line.id, po_lines.ids,
                         "Company B's purchase order line reached Project A.")
        self.assertAlmostEqual(sum(po_lines.mapped('price_subtotal')),
                               cost_a['current_commitment'], places=2)

        actual = Sheet.drilldown(self.project_a, 'actual_cost')
        analytic_lines = self.env[actual['res_model']].sudo().search(
            actual['domain'])
        self.assertTrue(analytic_lines)
        self.assertEqual(analytic_lines.company_id, self.company_a)
        self.assertNotIn(line_b.id, analytic_lines.ids,
                         "Company B's analytic posting reached Project A.")
        self.assertAlmostEqual(-sum(analytic_lines.mapped('amount')),
                               cost_a['actual_cost'], places=2)

        # 4. And B answers with B's own figures, not A's and not both.
        payload_b = tower.with_context(
            allowed_company_ids=allowed).payload(self.project_b)
        self.assertEqual(payload_b['cost']['current_budget'], 50_000_000.0)
        self.assertEqual(payload_b['cost']['current_commitment'],
                         7_000_000.0)
        self.assertEqual(payload_b['cost']['actual_cost'], 3_000_000.0)

    def test_switching_the_active_company_does_not_change_a_projects_figures(self):
        """A project's cost is a property of the project, not of a toggle."""
        user = self._user('m10.switch', [self.company_a, self.company_b],
                          'group_construction_manager')
        Sheet = self.env[
            'realestate.construction.cost.sheet'].with_user(user)

        as_a = Sheet.with_context(
            allowed_company_ids=[self.company_a.id]).totals_for(
                self.project_a)
        as_both = Sheet.with_context(
            allowed_company_ids=[self.company_a.id,
                                 self.company_b.id]).totals_for(
                                     self.project_a)

        self.assertEqual(as_a['current_budget'], as_both['current_budget'])
        self.assertEqual(as_a['actual_cost'], as_both['actual_cost'])

    def test_a_company_b_user_cannot_read_a_company_a_project_record(self):
        user = self._user('m10.b.only', [self.company_b],
                          'group_construction_manager')
        budget = self.env['realestate.construction.budget'].search(
            [('project_id', '=', self.project_a.id)], limit=1)
        self.assertTrue(budget)

        found = self.env['realestate.construction.budget'].with_user(
            user).search([('id', '=', budget.id)])
        self.assertFalse(found, "A company B user found a company A budget.")

        with self.assertRaises(AccessError):
            budget.with_user(user).read(['name'])

    def test_the_company_rule_is_global_and_not_a_privilege(self):
        """A manager is not exempt: isolation is not something to be granted."""
        Rule = self.env['ir.rule'].sudo()
        for model in ('realestate.construction.budget',
                      'realestate.construction.payment.certificate',
                      'realestate.construction.claim'):
            rules = Rule.search([('model_id.model', '=', model)])
            company_rules = rules.filtered(lambda r: not r.groups)
            self.assertTrue(
                company_rules,
                "%s has no global company rule, so a group could bypass it."
                % model)

    def test_an_integrity_audit_stays_inside_the_company(self):
        user = self._user('m10.audit', [self.company_a],
                          'group_construction_manager')
        report = self.env[
            'realestate.construction.integrity.audit'].with_user(user).run(
                company=self.company_a)
        self.assertIn(self.project_a.id, report['project_ids'])
        self.assertNotIn(self.project_b.id, report['project_ids'])

    def test_a_cross_company_record_is_refused_at_creation(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.env['realestate.construction.claim'].create({
                'title': 'Cross-company claim',
                'project_id': self.project_a.id,
                'company_id': self.company_b.id,
                'claim_type': 'other',
                'claimed_cost': 1.0,
                'cost_claimed_known': True,
            })

    def test_an_audit_names_the_checks_it_could_not_run(self):
        """It must not elevate, and it must not pretend it ran everything."""
        user = self._user('m10.limited', [self.company_a],
                          'group_construction_user')
        report = self.env[
            'realestate.construction.integrity.audit'].with_user(user).run(
                company=self.company_a)

        skipped = [f for f in report['findings']
                   if f['key'] == 'checks_not_run']
        if skipped:
            self.assertEqual(skipped[0]['severity'], 'low')
            self.assertIn('does not elevate', skipped[0]['remediation'])
