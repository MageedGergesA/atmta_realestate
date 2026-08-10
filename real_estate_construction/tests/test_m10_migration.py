# -*- coding: utf-8 -*-
"""M10D–F — migration classifies, and never invents.

The real double-upgrade is proved by the release gate, which installs and then
upgrades the same database twice. These tests prove the properties the gate
cannot see from outside: that every classifier is read-only, that running it
again produces the same answer, and that ambiguous legacy data stays ambiguous
instead of being resolved by whichever source happened to sort first.
"""

from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestMigrationClassification(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Migration = self.env['realestate.construction.budget.migration']
        self.Audit = self.env['realestate.construction.integrity.audit']
        self.contractor = self._contractor()
        self.civil = self._cost_code('MG-CIV', 'Civil', 'subcontract')

    def _legacy_project(self, expected=0.0, milestones=(), boq=None):
        """A project shaped the way a pre-M1 database shaped them."""
        project = self._project(budget=expected)
        for amount in milestones:
            self._milestone(project, budget=amount)
        if boq:
            self._boq(project, quantities=boq)
        return project

    def test_a_single_source_is_deterministic(self):
        project = self._legacy_project(expected=5_000_000.0)
        row = [r for r in self.Migration.classify()
               if r['project_id'] == project.id][0]
        self.assertEqual(row['status'], 'deterministic')
        self.assertEqual(row['recommended_source'], 'expected_budget')

    def test_sources_that_disagree_stay_ambiguous(self):
        project = self._legacy_project(expected=5_000_000.0,
                                       milestones=(1_000_000.0,))
        row = [r for r in self.Migration.classify()
               if r['project_id'] == project.id][0]

        self.assertEqual(row['status'], 'ambiguous')
        self.assertFalse(row['recommended_source'],
                         "Picking one silently is how a wrong baseline "
                         "becomes the number everything is measured against.")
        self.assertFalse(
            self.env['realestate.construction.budget'].current_for(project),
            "Classification creates nothing.")

    def test_classification_writes_nothing_and_repeats_exactly(self):
        self._legacy_project(expected=2_000_000.0, milestones=(750_000.0,))
        self._legacy_project(expected=3_000_000.0)

        def counts():
            return {
                model: self.env[model].search_count([])
                for model in ('realestate.construction.budget',
                              'realestate.construction.budget.line',
                              'realestate.construction.cost.code',
                              'realestate.construction.wbs',
                              'realestate.construction.milestone')
            }

        before = counts()
        first = self.Migration.classify()
        middle = counts()
        second = self.Migration.classify()
        after = counts()

        self.assertEqual(before, middle, "The classifier wrote something.")
        self.assertEqual(middle, after)
        self.assertEqual(first, second,
                         "The same database must classify the same way twice.")

    def test_no_baseline_is_invented_for_a_project_with_no_sources(self):
        project = self._legacy_project()
        row = [r for r in self.Migration.classify()
               if r['project_id'] == project.id][0]
        self.assertEqual(row['status'], 'no_source')
        self.assertFalse(
            self.env['realestate.construction.budget'].current_for(project))

    def test_creating_a_draft_budget_is_a_separate_deliberate_act(self):
        project = self._legacy_project(expected=4_000_000.0,
                                       boq=((100.0, 1_000.0),))
        self.assertFalse(
            self.env['realestate.construction.budget'].current_for(project))

        boq = self.env['realestate.boq'].search(
            [('project_id', '=', project.id)], limit=1)
        budget = self.Migration.create_draft_budget_from_boq(boq)

        self.assertEqual(budget.state, 'draft',
                         "Even a deterministic source produces a draft that "
                         "somebody has to approve.")
        self.assertFalse(
            self.env['realestate.construction.budget'].current_for(project),
            "A draft is not a baseline.")

    def test_the_audit_is_idempotent_and_read_only(self):
        project = self._legacy_project(expected=1_000_000.0,
                                       milestones=(400_000.0,))
        self._po(project, self.contractor, [(None, 250_000.0)])

        first = self.Audit.run(project)
        second = self.Audit.run(project)
        self.assertEqual(
            [(f['key'], f['count']) for f in first['findings']],
            [(f['key'], f['count']) for f in second['findings']])
        self.assertIn('uncoded_commitment',
                      [f['key'] for f in first['findings']])

    def test_the_audit_severities_are_meaningful(self):
        project = self._legacy_project(expected=1_000_000.0)
        self._po(project, self.contractor, [(None, 100_000.0)])
        report = self.Audit.run(project)

        for finding in report['findings']:
            self.assertIn(finding['severity'],
                          ('critical', 'high', 'medium', 'low'))
            self.assertTrue(finding['finding'])
            self.assertTrue(finding['expected'])
            self.assertTrue(finding['remediation'])

        uncoded = [f for f in report['findings']
                   if f['key'] == 'uncoded_commitment'][0]
        self.assertEqual(uncoded['severity'], 'medium',
                         "An uncoded line is a data-quality problem, not a "
                         "corruption of money.")

    def test_a_healthy_project_produces_no_critical_findings(self):
        project = self._project()
        self._configure_construction_accounts()
        self._baselined(project, 5_000_000.0, code=self.civil)
        self._po(project, self.contractor, [(self.civil, 2_000_000.0)])
        self._post_bill(project, self.civil, 500_000.0)

        report = self.Audit.run(project)
        self.assertFalse(
            report['blocking'],
            "Critical findings on a clean project: %s"
            % [f['key'] for f in report['blocking']])

    def test_legacy_retention_is_reported_not_rewritten(self):
        project = self._project()
        self._configure_construction_accounts()
        certificate = self._certificate(project, self.contractor,
                                        amount=200_000.0, retention_pct=5.0,
                                        cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()
        bill = certificate.vendor_bill_id
        lines_before = len(bill.line_ids)

        # Reproduce a pre-M7 posting.
        certificate.sudo().write({'retention_posted_correctly': False})

        report = self.Audit.run(project)
        finding = [f for f in report['findings']
                   if f['key'] == 'legacy_retention']
        self.assertTrue(finding)
        self.assertEqual(finding[0]['severity'], 'high')
        self.assertIn('Finance', finding[0]['remediation'])

        self.assertEqual(len(bill.line_ids), lines_before,
                         "The audit must not touch a posted entry.")
        self.assertEqual(bill.state, 'posted')

    def test_project_access_is_not_activated_by_migration(self):
        """M10D §9 — naming members is opt-in and stays opt-in."""
        project = self._legacy_project(expected=1_000_000.0)
        self.assertFalse(
            project.construction_member_ids,
            "A migration that populated teams would lock a live deployment "
            "out of its own projects on upgrade day.")


@tagged('post_install', '-at_install', 'atmta_construction')
class TestMigrationIdempotencyOfDerivedState(ConstructionCommon):
    """Derived state must survive being recomputed, twice."""

    def test_recomputing_control_figures_changes_nothing(self):
        project = self._project()
        contractor = self._contractor()
        civil = self._cost_code('MI-CIV', 'Civil', 'subcontract')
        self._baselined(project, 8_000_000.0, code=civil)
        package = self._package(project, contractor, value=6_000_000.0,
                                award=True)
        self._approve_and_implement(self._change_order(
            project, lines=[(civil, 'commitment', 1_000_000.0)],
            package=package))
        self._post_bill(project, civil, 2_000_000.0)

        Sheet = self.env['realestate.construction.cost.sheet']
        before = Sheet.totals_for(project)

        self.env.invalidate_all()
        self.env['realestate.construction.contract.package']._recompute_model(
            ['current_contract_value', 'approved_variation_amount'])
        self.env['realestate.construction.budget']._recompute_model(
            ['original_amount', 'approved_change_amount'])
        self.env.invalidate_all()

        after = Sheet.totals_for(project)
        for key in ('current_budget', 'current_commitment', 'actual_cost'):
            self.assertEqual(after[key], before[key], key)
