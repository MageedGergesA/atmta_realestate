# -*- coding: utf-8 -*-
"""M1 — WBS, cost codes, and the analytic integration underneath them.

The two structures answer different questions and are deliberately not one
tree: the WBS says *where in the works*, the cost code says *what kind of
money*. The tests below hold that separation, the company rules around it, and
the analytic plumbing that lets the ledger be read back by project × cost code.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestWBS(ConstructionCommon):

    def test_a_wbs_builds_its_dotted_code_from_its_ancestors(self):
        project = self._project()
        concrete = self._wbs(project, code='03', name='Concrete')
        slabs = self._wbs(project, code='02', name='Slabs', parent_id=concrete.id)
        ground = self._wbs(project, code='01', name='Ground Floor',
                           parent_id=slabs.id)

        self.assertEqual(concrete.complete_code, '03')
        self.assertEqual(slabs.complete_code, '03.02')
        self.assertEqual(ground.complete_code, '03.02.01')
        self.assertEqual(ground.level, 2)

    def test_renaming_a_parent_code_repaths_its_children(self):
        project = self._project()
        parent = self._wbs(project, code='03', name='Concrete')
        child = self._wbs(project, code='02', name='Slabs', parent_id=parent.id)

        parent.code = '04'

        self.assertEqual(child.complete_code, '04.02')

    def test_siblings_cannot_share_a_code(self):
        project = self._project()
        parent = self._wbs(project, code='03', name='Concrete')
        self._wbs(project, code='02', name='Slabs', parent_id=parent.id)

        with self.assertRaises(Exception):
            self._wbs(project, code='02', name='Beams', parent_id=parent.id)
            self.env.flush_all()

    def test_the_same_code_may_repeat_under_a_different_parent(self):
        project = self._project()
        concrete = self._wbs(project, code='03', name='Concrete')
        masonry = self._wbs(project, code='04', name='Masonry')

        first = self._wbs(project, code='01', name='Ground',
                          parent_id=concrete.id)
        second = self._wbs(project, code='01', name='Ground',
                           parent_id=masonry.id)

        self.assertEqual(first.complete_code, '03.01')
        self.assertEqual(second.complete_code, '04.01')

    def test_a_node_cannot_be_its_own_ancestor(self):
        """Refused by `_parent_store`'s own recursion check.

        The framework raises `UserError` while maintaining `parent_path`,
        before `@api.constrains` gets a chance — so that is what is asserted.
        The model's own `_has_cycle()` constraint stays as the guard for any
        path that does not go through parent_path maintenance.

        (Odoo's `assertRaises` override cannot take a tuple of exceptions: it
        calls `issubclass(exception, AccessError)` on whatever it is given.)
        """
        project = self._project()
        parent = self._wbs(project, code='03', name='Concrete')
        child = self._wbs(project, code='02', name='Slabs',
                          parent_id=parent.id)

        with self.assertRaises(UserError):
            parent.parent_id = child

    def test_a_node_cannot_be_parented_into_another_project(self):
        """A cost booked under 03.02 must not change project silently."""
        project = self._project()
        other = self._project()
        here = self._wbs(project, code='03', name='Concrete')
        there = self._wbs(other, code='03', name='Concrete')

        with self.assertRaises(ValidationError):
            here.parent_id = there

    def test_the_company_follows_the_project(self):
        project = self._project()
        node = self._wbs(project, code='03', name='Concrete')

        self.assertEqual(node.company_id, project.company_id)

    def test_a_leaf_knows_it_is_one(self):
        project = self._project()
        parent = self._wbs(project, code='03', name='Concrete')
        child = self._wbs(project, code='02', name='Slabs',
                          parent_id=parent.id)
        parent.invalidate_recordset()

        self.assertFalse(parent.is_leaf)
        self.assertTrue(child.is_leaf)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCostCode(ConstructionCommon):

    def test_a_cost_code_is_a_catalogue_entry_by_default(self):
        code = self._cost_code('SUB-CIV', 'Civil Subcontract', 'subcontract')

        self.assertFalse(
            code.project_id,
            "A catalogue code belongs to every project, which is what makes "
            "projects comparable.")
        self.assertEqual(code.company_id, self.company)

    def test_a_code_may_be_scoped_to_one_project_when_it_must_be(self):
        project = self._project()
        code = self._cost_code('ODD-01', 'Something peculiar', 'other',
                               project_id=project.id)

        self.assertEqual(code.project_id, project)

    def test_two_codes_cannot_share_a_code_in_one_company(self):
        self._cost_code('SUB-CIV', 'Civil Subcontract', 'subcontract')

        with self.assertRaises(Exception):
            self._cost_code('SUB-CIV', 'Duplicate', 'subcontract')
            self.env.flush_all()

    def test_a_code_cannot_roll_up_into_itself(self):
        parent = self._cost_code('SUB', 'Subcontracts', 'subcontract')
        child = self._cost_code('SUB-CIV', 'Civil', 'subcontract',
                                parent_id=parent.id)

        with self.assertRaises(ValidationError):
            parent.parent_id = child

    def test_a_project_scoped_code_must_match_its_project_company(self):
        other_company = self.env['res.company'].create({'name': 'CC Other Co'})
        project = self._project()
        project.company_id = other_company

        with self.assertRaises(ValidationError):
            self._cost_code('X-01', 'Mismatched', 'other',
                            project_id=project.id)

    def test_scope_and_category_are_separate_fields(self):
        """The brief's rule, asserted as a shape rather than a sentence."""
        cost_code = self.env['realestate.construction.cost.code']
        wbs = self.env['realestate.construction.wbs']

        self.assertIn('category', cost_code._fields)
        self.assertNotIn(
            'category', wbs._fields,
            "The WBS must not carry a cost category — that is what mixes the "
            "two hierarchies the brief separates.")
        self.assertNotIn(
            'wbs_id', cost_code._fields,
            "A cost code is not a place; it must not point at one.")


@tagged('post_install', '-at_install', 'atmta_construction')
class TestAnalyticArchitecture(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Analytic = self.env['realestate.construction.analytic']

    def test_the_project_plan_is_referenced_not_guessed(self):
        """The defect the audit found: `Plan.search([], limit=1)`."""
        self.env['account.analytic.plan'].sudo().create(
            {'name': 'AAA Departments'})
        project = self._project()

        account = project._get_or_create_analytic_account()

        self.assertEqual(
            account.plan_id,
            self.env.ref('real_estate_construction.analytic_plan_re_projects'),
            "A project cost centre must land in the Real Estate Projects "
            "plan, whatever else exists in the database.")

    def test_the_project_analytic_account_takes_the_projects_company(self):
        """The other half of the same defect."""
        other = self.env['res.company'].create({'name': 'Analytic Other Co'})
        project = self._project()
        project.company_id = other

        account = project._get_or_create_analytic_account()

        self.assertEqual(account.company_id, other)

    def test_an_existing_analytic_account_is_never_moved(self):
        """History is not ours to rewrite."""
        project = self._project()
        first = project._get_or_create_analytic_account()

        second = project._get_or_create_analytic_account()

        self.assertEqual(first, second)

    def test_a_cost_code_account_lands_in_the_cost_code_plan(self):
        code = self._cost_code('SUB-CIV', 'Civil Subcontract', 'subcontract')

        account = code._get_or_create_analytic_account()

        self.assertEqual(
            account.plan_id,
            self.env.ref('real_estate_construction.analytic_plan_cost_codes'))
        self.assertEqual(account.company_id, code.company_id)

    def test_a_distribution_carries_one_account_from_each_plan(self):
        project = self._project()
        code = self._cost_code('SUB-CIV', 'Civil Subcontract', 'subcontract')

        distribution = self.Analytic.distribution_for(project, code)

        # ONE key, comma-joined — Odoo's encoding for "one posting, coded on
        # two plans". Two separate keys would be two distributions, and Odoo
        # would write two analytic lines each for the full amount.
        self.assertEqual(len(distribution), 1)
        key, percentage = next(iter(distribution.items()))
        self.assertEqual(percentage, 100.0)
        self.assertEqual(
            set(key.split(',')),
            {str(project.analytic_account_id.id),
             str(code.analytic_account_id.id)})

    def test_a_distribution_without_a_cost_code_is_still_valid(self):
        """A project-level fee knows its project and nothing finer. Refusing it
        would push people back to posting with no analytic at all."""
        project = self._project()

        distribution = self.Analytic.distribution_for(project)

        self.assertEqual(
            distribution, {str(project.analytic_account_id.id): 100.0})

    def test_a_distribution_with_nothing_at_all_is_false_not_empty(self):
        self.assertFalse(self.Analytic.distribution_for(None))

    def test_the_catalogue_does_not_multiply_accounts_by_project(self):
        """Scale, asserted rather than assumed.

        Ten projects sharing five cost codes need five cost-code accounts, not
        fifty. This is the whole reason the codes are a catalogue.
        """
        codes = [self._cost_code('CC%02d' % i, 'Code %d' % i, 'material')
                 for i in range(5)]
        projects = [self._project() for _ in range(10)]
        for project in projects:
            for code in codes:
                self.Analytic.distribution_for(project, code)

        cost_code_accounts = self.env['account.analytic.account'].search([
            ('plan_id', '=', self.env.ref(
                'real_estate_construction.analytic_plan_cost_codes').id)])

        self.assertEqual(len(cost_code_accounts), 5)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestActualFromTheLedger(ConstructionCommon):
    """Rule 1 — actual cost is what the ledger says, read back by cost code."""

    def setUp(self):
        super().setUp()
        self.Analytic = self.env['realestate.construction.analytic']

    def test_a_project_with_no_cost_centre_matches_nothing(self):
        """An empty domain would match the whole ledger — the opposite of the
        truth for a project that has never been posted against."""
        project = self._project()

        domain = self.Analytic.actual_domain(project)

        self.assertEqual(domain, [('id', '=', 0)])
        self.assertFalse(
            self.env['account.analytic.line'].search(domain))

    def test_the_domain_selects_this_projects_postings_only(self):
        project = self._project()
        other = self._project()
        code = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        mine = self._analytic_line(project, code, 1_000.0)
        theirs = self._analytic_line(other, code, 2_000.0)

        found = self.env['account.analytic.line'].search(
            self.Analytic.actual_domain(project))

        self.assertIn(mine, found)
        self.assertNotIn(theirs, found)

    def test_actual_by_cost_code_aggregates_in_the_database(self):
        project = self._project()
        civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        mep = self._cost_code('SUB-MEP', 'MEP', 'subcontract')
        self._analytic_line(project, civil, 1_000.0)
        self._analytic_line(project, civil, 500.0)
        self._analytic_line(project, mep, 250.0)

        actuals = self.Analytic.actual_by_cost_code(project)

        self.assertEqual(actuals.get(civil.id), 1_500.0)
        self.assertEqual(actuals.get(mep.id), 250.0)

    def test_actual_is_reported_as_a_positive_cost(self):
        """Analytic lines carry expenditure as negative. A cost report showing
        costs as negative numbers is read wrong by everyone who opens it."""
        project = self._project()
        code = self._cost_code('MAT-CON', 'Concrete', 'material')
        line = self._analytic_line(project, code, 1_000.0)

        self.assertLess(line.amount, 0.0)
        self.assertEqual(
            self.Analytic.actual_by_cost_code(project).get(code.id), 1_000.0)

    def test_another_companys_project_is_not_included(self):
        project = self._project()
        code = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        self._analytic_line(project, code, 1_000.0)

        other_company = self.env['res.company'].create({'name': 'Ledger Other'})
        other_project = self._project()
        other_project.company_id = other_company

        self.assertEqual(self.Analytic.actual_by_cost_code(other_project), {})
