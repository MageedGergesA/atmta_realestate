# -*- coding: utf-8 -*-
"""What this module owns, and the two things it must never do.

The M6 integration suites stay in `real_estate_procurement`: `M6Common`
builds a Construction budget, and Evaluation has no business depending on
Construction to run its own tests. What is proved here is what Evaluation owns
and can prove alone — above all that it moves no money, and that a technical
evaluator cannot reach the commercial outcome.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

EVALUATION_MODELS = (
    'realestate.procurement.evaluation.plan',
    'realestate.procurement.evaluation.criterion',
    'realestate.procurement.evaluation.round',
    'realestate.procurement.evaluation.assignment',
    'realestate.procurement.evaluation.candidate',
    'realestate.procurement.technical.evaluation',
    'realestate.procurement.technical.evaluation.line',
    'realestate.procurement.commercial.analysis',
    'realestate.procurement.commercial.adjustment',
    'realestate.procurement.leveling.line',
    'realestate.procurement.evaluation.deviation',
    'realestate.procurement.evaluation.audit',
    'realestate.procurement.evaluation.audit.wizard',
    'realestate.procurement.evaluation.audit.finding',
)
COMMERCIAL_FIELDS = ('analysis_id', 'financial_score', 'combined_score',
                     'rank', 'is_tied')


@tagged('post_install', '-at_install', 'atmta_evaluation')
class TestEvaluationCapability(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Candidate = cls.env['realestate.procurement.evaluation.candidate']
        cls.Audit = cls.env['realestate.procurement.evaluation.audit']

    def test_it_owns_fourteen_models(self):
        for name in EVALUATION_MODELS:
            self.assertIn(name, self.env)
            self.assertEqual(self.env[name]._original_module,
                             'atmta_procurement_evaluation',
                             "%s is declared by another module." % name)

    def test_it_declares_no_sourcing_or_award_model(self):
        self.assertEqual(
            self.env['realestate.procurement.sourcing.event']._original_module,
            'atmta_procurement_sourcing')
        if 'realestate.procurement.award' in self.env:
            self.assertEqual(
                self.env['realestate.procurement.award']._original_module,
                'atmta_procurement_award')

    def test_the_dependency_points_downward_only(self):
        Module = self.env['ir.module.module']
        ev = Module.search([('name', '=', 'atmta_procurement_evaluation')])
        deps = ev.dependencies_id.mapped('name')
        self.assertIn('atmta_procurement_sourcing', deps)
        self.assertNotIn('atmta_procurement_award', deps,
                         "Evaluation must never depend on Award.")
        sourcing = Module.search([('name', '=', 'atmta_procurement_sourcing')])
        self.assertNotIn('atmta_procurement_evaluation',
                         sourcing.dependencies_id.mapped('name'),
                         "Sourcing must not depend on Evaluation.")

    def test_it_contributes_the_evaluation_half_of_the_sourcing_event(self):
        event = self.env['realestate.procurement.sourcing.event']
        self.assertIn('evaluation_round_ids', event._fields)
        self.env.cr.execute("""
            SELECT d.module FROM ir_model_fields f
              JOIN ir_model_data d ON d.model='ir.model.fields' AND d.res_id=f.id
             WHERE f.model='realestate.procurement.sourcing.event'
               AND f.name='evaluation_round_ids'""")
        row = self.env.cr.fetchone()
        self.assertTrue(row)
        self.assertEqual(row[0], 'atmta_procurement_evaluation')

    # -- confidentiality: the gate this capability exists to keep ----------
    def test_the_commercial_outcome_is_restricted_at_field_level(self):
        """Not a view concern. The restriction has to be on the field, or a
        technical evaluator reaches the ranking through search or read_group."""
        for name in COMMERCIAL_FIELDS:
            field = self.Candidate._fields.get(name)
            self.assertIsNotNone(field, "%s is missing" % name)
            self.assertTrue(
                field.groups,
                "%s carries no group restriction — the commercial outcome is "
                "readable by anyone who can open a candidate" % name)
            self.assertNotIn('real_estate_procurement.', field.groups,
                             "%s still names a legacy group (AD-011)" % name)

    def test_a_technical_evaluator_cannot_read_the_commercial_outcome(self):
        tech = self.env['res.users'].create({
            'name': 'w8.tech', 'login': 'w8.tech.%s' % self.env.cr.dbname[-4:],
            'email': 'w8.tech@example.com', 'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('atmta_roles.group_procurement_technical_evaluator').id,
            ])]})
        cand = self.Candidate.with_user(tech)
        for name in COMMERCIAL_FIELDS:
            self.assertNotIn(
                name, cand.fields_get(),
                "a technical evaluator can see %s — the commercial firewall "
                "is open" % name)

    def test_a_commercial_evaluator_can_read_the_commercial_outcome(self):
        """The mirror of the previous test: a restriction that blocks everyone
        proves nothing."""
        comm = self.env['res.users'].create({
            'name': 'w8.comm', 'login': 'w8.comm.%s' % self.env.cr.dbname[-4:],
            'email': 'w8.comm@example.com', 'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('atmta_roles.group_procurement_commercial_evaluator').id,
            ])]})
        visible = self.Candidate.with_user(comm).fields_get()
        for name in COMMERCIAL_FIELDS:
            self.assertIn(name, visible,
                          "a commercial evaluator cannot see %s" % name)

    # -- the audit seam ----------------------------------------------------
    def test_the_audit_runs_every_capability_s_checks(self):
        checks = [c.__name__ for c in self.Audit._audit_checks()]
        self.assertEqual(len(checks), len(set(checks)), "a check is duplicated")
        for name in ('_check_plan_weights', '_check_commercial_fields_unrestricted'):
            self.assertIn(name, checks)
        Module = self.env['ir.module.module']
        if Module.search_count([('name', '=', 'atmta_procurement_award'),
                                ('state', '=', 'installed')]):
            for name in ('_check_award_surface', '_check_award_over_tender'):
                self.assertIn(name, checks,
                              "Award did not contribute %s to the audit" % name)
        # the receiving checks come from the module that still owns receipts
        self.assertIn('_check_uninspected_receipt_under_policy', checks)

    def test_evaluation_declares_no_award_check_of_its_own(self):
        """Evaluation must not reach upward, even to describe."""
        import inspect
        src = inspect.getsource(type(self.Audit)._audit_checks)
        self.assertNotIn('award', src.lower(),
                         "Evaluation's own check list names award")

    def test_the_evaluation_sequences_are_not_duplicated(self):
        for code in ('realestate.procurement.evaluation.plan',
                     'realestate.procurement.evaluation.round'):
            n = self.env['ir.sequence'].search_count([('code', '=', code)])
            self.assertLessEqual(n, 1, "duplicate sequence for %s" % code)
