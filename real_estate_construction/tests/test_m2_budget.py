# -*- coding: utf-8 -*-
"""M2 — budget baselines: lifecycle, immutability, and the legacy three."""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestBudgetLifecycle(ConstructionCommon):

    def test_a_budget_walks_draft_to_baseline(self):
        budget = self._budget(self._project())

        self.assertEqual(budget.state, 'draft')
        budget.action_submit()
        self.assertEqual(budget.state, 'review')
        budget.action_approve()
        self.assertEqual(budget.state, 'approved')
        self.assertEqual(budget.approved_by_id, self.env.user)
        budget.action_baseline()
        self.assertEqual(budget.state, 'baselined')
        self.assertTrue(budget.baseline_date)

    def test_an_empty_budget_authorises_nothing_and_cannot_be_submitted(self):
        budget = self.env['realestate.construction.budget'].create(
            {'project_id': self._project().id})

        with self.assertRaises(UserError):
            budget.action_submit()

    def test_a_project_can_have_only_one_baseline(self):
        project = self._project()
        first = self._baselined(project, 1_000_000.0)
        second = self._budget(project, 2_000_000.0)
        second.action_submit()
        second.action_approve()

        with self.assertRaises(UserError):
            second.action_baseline()

        self.assertEqual(
            self.env['realestate.construction.budget'].current_for(project),
            first)

    def test_the_money_on_a_baselined_budget_cannot_be_edited(self):
        budget = self._baselined(self._project(), 1_000_000.0)

        with self.assertRaises(UserError):
            budget.line_ids[0].original_amount = 2_000_000.0

    def test_a_line_cannot_be_removed_from_a_baseline(self):
        budget = self._baselined(self._project(), 1_000_000.0)

        with self.assertRaises(UserError):
            budget.line_ids[0].unlink()

    def test_a_baselined_budget_cannot_be_deleted_or_cancelled(self):
        budget = self._baselined(self._project(), 1_000_000.0)

        with self.assertRaises(UserError):
            budget.action_cancel()
        with self.assertRaises(UserError):
            budget.unlink()

    def test_a_revision_carries_the_lines_and_leaves_the_original_alone(self):
        project = self._project()
        original = self._baselined(project, 1_000_000.0)

        original.action_create_revision()
        revision = self.env['realestate.construction.budget'].search([
            ('supersedes_id', '=', original.id)])

        self.assertEqual(revision.version, 2)
        self.assertEqual(revision.state, 'draft')
        self.assertEqual(len(revision.line_ids), len(original.line_ids))
        self.assertEqual(original.state, 'baselined')
        self.assertEqual(original.original_amount, 1_000_000.0)

    def test_superseding_moves_the_baseline_and_keeps_the_history(self):
        project = self._project()
        original = self._baselined(project, 1_000_000.0)
        original.action_create_revision()
        revision = self.env['realestate.construction.budget'].search([
            ('supersedes_id', '=', original.id)])
        revision.line_ids[0].original_amount = 1_500_000.0
        revision.action_submit()
        revision.action_approve()

        original.action_supersede()

        self.assertEqual(original.state, 'superseded')
        self.assertEqual(original.superseded_by_id, revision)
        self.assertEqual(revision.state, 'baselined')
        self.assertEqual(
            self.env['realestate.construction.budget'].current_for(project),
            revision)
        self.assertEqual(
            original.original_amount, 1_000_000.0,
            "The first baseline still says what was first approved.")

    def test_a_baseline_cannot_be_withdrawn_with_nothing_to_replace_it(self):
        budget = self._baselined(self._project(), 1_000_000.0)

        with self.assertRaises(UserError):
            budget.action_supersede()

    def test_a_preparer_cannot_approve_their_own_budget(self):
        """Maker/checker, enforced server-side and configurable."""
        preparer = self.env['res.users'].create({
            'name': 'Preparer', 'login': 'budget_preparer_%d' % self._next(),
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_construction.group_construction_user').id,
            ])],
        })
        project = self._project()
        budget = self._budget(project).with_user(preparer)
        budget.sudo().write({'create_uid': preparer.id})
        budget.sudo().action_submit()

        with self.assertRaises(UserError):
            budget.action_approve()

    def test_self_approval_can_be_allowed_for_a_one_person_company(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.allow_self_approval', 'True')
        budget = self._budget(self._project())
        budget.action_submit()

        budget.action_approve()

        self.assertEqual(budget.state, 'approved')


@tagged('post_install', '-at_install', 'atmta_construction')
class TestBudgetArithmetic(ConstructionCommon):

    def test_current_budget_is_original_plus_approved_changes(self):
        budget = self._baselined(self._project(), 1_000_000.0)

        self.assertEqual(budget.original_amount, 1_000_000.0)
        self.assertEqual(budget.approved_change_amount, 0.0)
        self.assertEqual(budget.current_amount, 1_000_000.0)

        # M4 writes this; simulated here to prove the equation holds.
        budget.line_ids[0].sudo().write({'approved_change_amount': 250_000.0})
        budget.invalidate_recordset()

        self.assertEqual(budget.original_amount, 1_000_000.0)
        self.assertEqual(budget.current_amount, 1_250_000.0)

    def test_a_quantity_line_multiplies_and_a_lump_sum_does_not(self):
        project = self._project()
        code = self._cost_code('MAT-CON', 'Concrete', 'material')
        budget = self.env['realestate.construction.budget'].create({
            'project_id': project.id,
            'line_ids': [
                (0, 0, {'cost_code_id': code.id, 'amount_mode': 'quantity',
                        'quantity': 250.0, 'unit_rate': 400.0}),
                (0, 0, {'cost_code_id': code.id, 'amount_mode': 'lumpsum',
                        'original_amount': 75_000.0}),
            ],
        })

        self.assertEqual(budget.line_ids[0].original_amount, 100_000.0)
        self.assertEqual(budget.line_ids[1].original_amount, 75_000.0)
        self.assertEqual(budget.original_amount, 175_000.0)

    def test_a_zero_line_is_allowed_and_contributes_nothing(self):
        project = self._project()
        code = self._cost_code('OTH-01', 'Placeholder', 'other')
        budget = self.env['realestate.construction.budget'].create({
            'project_id': project.id,
            'line_ids': [(0, 0, {'cost_code_id': code.id,
                                 'amount_mode': 'lumpsum',
                                 'original_amount': 0.0})],
        })

        self.assertEqual(budget.original_amount, 0.0)

    def test_rounding_holds_across_many_lines(self):
        """Currency rounding, asserted rather than hoped for."""
        project = self._project()
        code = self._cost_code('MAT-01', 'Sundries', 'material')
        budget = self.env['realestate.construction.budget'].create({
            'project_id': project.id,
            'line_ids': [
                (0, 0, {'cost_code_id': code.id, 'amount_mode': 'quantity',
                        'quantity': 3.0, 'unit_rate': 33.33})
                for _ in range(3)
            ],
        })

        self.assertAlmostEqual(budget.original_amount, 299.97, places=2)

    def test_contingency_is_reported_separately(self):
        project = self._project()
        works = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        contingency = self._cost_code('CONT-01', 'Contingency', 'contingency',
                                      is_contingency=True)
        budget = self.env['realestate.construction.budget'].create({
            'project_id': project.id,
            'line_ids': [
                (0, 0, {'cost_code_id': works.id, 'amount_mode': 'lumpsum',
                        'original_amount': 900_000.0}),
                (0, 0, {'cost_code_id': contingency.id,
                        'amount_mode': 'lumpsum',
                        'original_amount': 100_000.0}),
            ],
        })

        self.assertEqual(budget.original_amount, 1_000_000.0)
        self.assertEqual(
            budget.contingency_amount, 100_000.0,
            "Contingency is money set aside, and has to be visible as such "
            "rather than hidden inside a works code.")


@tagged('post_install', '-at_install', 'atmta_construction')
class TestBudgetCompany(ConstructionCommon):

    def test_a_budget_cannot_belong_to_another_company_than_its_project(self):
        other = self.env['res.company'].create({'name': 'Budget Other Co'})
        project = self._project()
        project.company_id = other

        with self.assertRaises(ValidationError):
            self.env['realestate.construction.budget'].create({
                'project_id': project.id,
                'company_id': self.company.id,
            })

    def test_a_line_cannot_use_another_projects_wbs(self):
        project = self._project()
        other_project = self._project()
        stranger = self._wbs(other_project, code='03', name='Elsewhere')
        code = self._cost_code('SUB-CIV', 'Civil', 'subcontract')

        with self.assertRaises(ValidationError):
            self.env['realestate.construction.budget'].create({
                'project_id': project.id,
                'line_ids': [(0, 0, {'cost_code_id': code.id,
                                     'wbs_id': stranger.id,
                                     'amount_mode': 'lumpsum',
                                     'original_amount': 1.0})],
            })


@tagged('post_install', '-at_install', 'atmta_construction')
class TestLegacyBudgetClassification(ConstructionCommon):
    """§37 — the three legacy budgets, classified and never auto-promoted."""

    def setUp(self):
        super().setUp()
        self.Migration = self.env['realestate.construction.budget.migration']

    def _row(self, project):
        rows = self.Migration.classify(project)
        return rows[0]

    def test_a_project_with_only_an_expected_budget_is_deterministic(self):
        project = self._project(budget=5_000_000.0)

        row = self._row(project)

        self.assertEqual(row['status'], 'deterministic')
        self.assertEqual(row['recommended_source'], 'expected_budget')

    def test_a_project_with_only_milestones_is_deterministic(self):
        project = self._project(budget=0.0)
        self._milestone(project, budget=750_000.0)

        row = self._row(project)

        self.assertEqual(row['status'], 'deterministic')
        self.assertEqual(row['recommended_source'], 'milestone_total')

    def test_a_project_with_only_a_boq_is_deterministic(self):
        project = self._project(budget=0.0)
        self._boq(project, quantities=((100.0, 1_000.0),))

        row = self._row(project)

        self.assertEqual(row['status'], 'deterministic')
        self.assertEqual(row['recommended_source'], 'boq_total')

    def test_sources_that_agree_prefer_the_one_with_the_most_structure(self):
        project = self._project(budget=100_000.0)
        self._milestone(project, budget=100_000.0)
        self._boq(project, quantities=((100.0, 1_000.0),))

        row = self._row(project)

        self.assertEqual(row['status'], 'deterministic')
        self.assertEqual(row['recommended_source'], 'boq_total')

    def test_sources_that_disagree_are_ambiguous_and_recommend_nothing(self):
        project = self._project(budget=10_000_000.0)
        self._milestone(project, budget=3_000_000.0)

        row = self._row(project)

        self.assertEqual(row['status'], 'ambiguous')
        self.assertFalse(row['recommended_source'])
        self.assertEqual(row['spread'], 7_000_000.0)

    def test_a_project_with_nothing_says_so(self):
        project = self._project(budget=0.0)

        self.assertEqual(self._row(project)['status'], 'no_source')

    def test_a_baselined_project_is_legacy_only(self):
        project = self._project(budget=10_000_000.0)
        self._baselined(project, 9_000_000.0)

        row = self._row(project)

        self.assertEqual(row['status'], 'legacy_only')
        self.assertEqual(
            row['expected_budget'], 10_000_000.0,
            "The legacy number is preserved, not overwritten to agree.")

    def test_classifying_writes_nothing_and_repeats_exactly(self):
        project = self._project(budget=10_000_000.0)
        self._milestone(project, budget=3_000_000.0)

        first = self._row(project)
        second = self._row(project)

        self.assertEqual(first, second)
        self.assertEqual(project.expected_budget, 10_000_000.0)
        self.assertFalse(
            self.env['realestate.construction.budget'].search(
                [('project_id', '=', project.id)]),
            "Classification proposes. It does not baseline.")


@tagged('post_install', '-at_install', 'atmta_construction')
class TestBudgetFromBOQ(ConstructionCommon):
    """§8 — an approved BOQ seeds a draft, never a baseline."""

    def setUp(self):
        super().setUp()
        self.Migration = self.env['realestate.construction.budget.migration']

    def test_a_boq_becomes_a_draft_budget(self):
        project = self._project()
        code = self._cost_code('MAT-CON', 'Concrete', 'material')
        boq = self._boq(project, quantities=((100.0, 1_000.0),))
        boq.line_ids.cost_code_id = code

        budget = self.Migration.create_draft_budget_from_boq(boq)

        self.assertEqual(budget.state, 'draft')
        self.assertEqual(budget.source, 'boq')
        self.assertEqual(budget.source_boq_id, boq)
        self.assertEqual(len(budget.line_ids), 1)
        self.assertEqual(budget.original_amount, 100_000.0)
        self.assertEqual(budget.line_ids.quantity, 100.0)
        self.assertEqual(budget.line_ids.unit_rate, 1_000.0)

    def test_a_draft_boq_cannot_seed_a_budget(self):
        project = self._project()
        boq = self._boq(project, quantities=((10.0, 10.0),), approve=False)

        with self.assertRaises(UserError):
            self.Migration.create_draft_budget_from_boq(boq)

    def test_unmapped_boq_lines_are_reported_not_guessed(self):
        project = self._project()
        code = self._cost_code('MAT-CON', 'Concrete', 'material')
        boq = self._boq(project, quantities=((100.0, 1_000.0),
                                             (50.0, 200.0)))
        boq.line_ids[0].cost_code_id = code

        budget = self.Migration.create_draft_budget_from_boq(boq)

        self.assertEqual(
            len(budget.line_ids), 1,
            "The unmapped line is not carried over with an invented code.")
        messages = budget.message_ids.mapped('body')
        self.assertTrue(any('no cost code' in (m or '') for m in messages),
                        "...and the omission is stated on the record.")
