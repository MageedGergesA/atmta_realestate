# -*- coding: utf-8 -*-
"""Phase 0 — the baseline, and the defects, proved rather than asserted.

This module shipped with no tests. Before designing anything, the audit needs
evidence: every claim in `PHASE_0_AUDIT_REPORT.md` about how the current system
behaves is backed by a test here.

Tests whose name begins with `test_defect_` **document behaviour that is
wrong**. They assert the wrong answer on purpose, so that the audit's claims
are reproducible and so that the milestone which fixes each one has a test that
must be inverted — a defect that cannot be demonstrated cannot be shown to be
fixed either.
"""

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestWhatBudgetMeans(ConstructionCommon):
    """Q1 — three different numbers are called "budget"."""

    def test_there_are_three_unreconciled_budgets(self):
        project = self._project(budget=10_000_000.0)
        self._milestone(project, budget=1_000_000.0)
        self._milestone(project, budget=2_000_000.0)
        boq = self._boq(project, quantities=((100.0, 5_000.0),))

        # 1. The project's scalar, owned by Developer.
        self.assertEqual(project.expected_budget, 10_000_000.0)
        # 2. The sum of milestone budgets.
        self.assertEqual(sum(project.milestone_ids.mapped('budget_amount')),
                         3_000_000.0)
        # 3. The BOQ total.
        self.assertEqual(boq.total_amount, 500_000.0)

        # Nothing reconciles them, and nothing notices.
        self.assertNotEqual(
            project.expected_budget,
            sum(project.milestone_ids.mapped('budget_amount')),
            "If these ever agree it is a coincidence, not a control.")


@tagged('post_install', '-at_install', 'atmta_construction')
class TestWhatActualCostMeans(ConstructionCommon):
    """Q3 / Q4 / Q5 — actual cost is manual entry, and the ledger is ignored."""

    def test_a_typed_cost_line_no_longer_becomes_actual_cost_by_itself(self):
        """FIXED IN M2 — was
        `test_actual_cost_is_the_sum_of_manually_typed_cost_lines`.

        Actual cost is the posted ledger. A cost line still exists and still
        holds real information, but it arrives unclassified (`legacy`), and an
        unclassified number is not a cost anybody has incurred.
        """
        project = self._project()
        line = self.CostLine.create({
            'name': 'Concrete', 'project_id': project.id, 'amount': 250_000.0})
        project.invalidate_recordset()

        self.assertEqual(line.cost_basis, 'legacy')
        self.assertFalse(line.counts_as_actual)
        self.assertEqual(project.actual_cost, 0.0)
        self.assertEqual(
            self.env['realestate.construction.controls'].project_totals(
                project)['actual_cost'], 0.0)

    def test_a_declared_accrual_is_reported_beside_the_ledger_not_inside_it(self):
        """Real cost with no accounting document yet — labour, plant — is a
        control figure, and still never touches the ledger total."""
        project = self._project()
        self.CostLine.create({
            'name': 'Site labour, week 12', 'project_id': project.id,
            'amount': 80_000.0, 'cost_basis': 'accrual'})
        project.invalidate_recordset()

        self.assertEqual(project.actual_cost, 80_000.0)
        self.assertEqual(
            self.env['realestate.construction.controls'].project_totals(
                project)['actual_cost'], 0.0,
            "An accrual is not a posted cost, and the ledger figure must not "
            "quietly absorb it.")

    def _posted_bill_for(self, project, code, amount):
        contractor = self._contractor()
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': contractor.partner_id.id,
            'invoice_date': self.today,
            'company_id': self.company.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Civil works', 'quantity': 1, 'price_unit': amount,
                'analytic_distribution': self.env[
                    'realestate.construction.analytic'].distribution_for(
                        project, code),
            })],
        })
        bill.action_post()
        return bill

    def test_a_posted_vendor_bill_is_actual_cost(self):
        """FIXED IN M2 — was `test_defect_a_posted_vendor_bill_is_not_actual_cost`."""
        project = self._project()
        code = self._cost_code('SUB-CIV', 'Civil', 'subcontract')

        self._posted_bill_for(project, code, 100_000.0)

        self.assertEqual(
            self.env['realestate.construction.controls'].project_totals(
                project)['actual_cost'], 100_000.0)

    def test_the_same_money_cannot_be_counted_twice(self):
        """FIXED IN M2 — was `test_defect_the_same_money_can_be_counted_twice`.

        A cost line naming a posted bill is ledger-backed: a reference to money
        the ledger already carries, not a second copy of it.
        """
        project = self._project()
        code = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        bill = self._posted_bill_for(project, code, 100_000.0)
        self.CostLine.create({
            'name': 'Same money, typed by the cost controller',
            'project_id': project.id,
            'amount': 100_000.0,
            'vendor_bill_id': bill.id,
            'cost_basis': 'ledger_backed',
        })
        project.invalidate_recordset()

        totals = self.env['realestate.construction.controls'].project_totals(
            project)
        self.assertEqual(totals['actual_cost'], 100_000.0, "Once, not twice.")
        self.assertEqual(project.actual_cost, 0.0,
                         "And the legacy total does not add it back.")


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCommitment(ConstructionCommon):
    """Q2 / Q16 / Q17 — what, if anything, represents a commitment."""

    def test_defect_commitment_is_not_project_scoped(self):
        """A contractor's "committed" is every PO for that vendor, anywhere.

        Two different projects, one contractor: the commitment figure each
        project would read is the same number, and it is the sum of both.
        """
        project_a = self._project()
        project_b = self._project()
        contractor = self._contractor()
        milestone_a = self._milestone(project_a, budget=400_000.0,
                                      contractor_id=contractor.id)
        milestone_b = self._milestone(project_b, budget=600_000.0,
                                      contractor_id=contractor.id)

        wizard = self.env['realestate.subcontract.po.wizard'].create({
            'contractor_id': contractor.id,
            'milestone_ids': [(6, 0, (milestone_a | milestone_b).ids)],
        })
        wizard.action_create_po()
        contractor.invalidate_recordset()

        po = contractor.contract_po_id
        self.assertEqual(po.amount_untaxed, 1_000_000.0)
        self.assertEqual(
            contractor.total_po_committed, po.amount_total,
            "DEFECT: commitment is one contractor-level total covering both "
            "projects. Neither project can say what is committed to it.")
        self.assertNotIn(
            'committed_amount', project_a._fields,
            "No project-level commitment field exists at all.")

    def test_defect_commitment_is_tax_inclusive_but_budget_is_not(self):
        """The two sides of "budget vs committed" are measured differently.

        `total_po_committed` sums `purchase.order.amount_total`, which includes
        tax. Milestone budgets, BOQ amounts and `project.expected_budget` are
        all tax-exclusive. Comparing them compares two different quantities,
        and the gap is the tax rate — here 15%, silently.
        """
        project = self._project()
        contractor = self._contractor()
        milestone = self._milestone(project, budget=1_000_000.0,
                                    contractor_id=contractor.id)
        wizard = self.env['realestate.subcontract.po.wizard'].create({
            'contractor_id': contractor.id,
            'milestone_ids': [(6, 0, milestone.ids)],
        })
        wizard.action_create_po()
        contractor.invalidate_recordset()
        po = contractor.contract_po_id

        self.assertEqual(milestone.budget_amount, po.amount_untaxed)
        self.assertGreater(
            contractor.total_po_committed, milestone.budget_amount,
            "DEFECT: the milestone is fully committed and the commitment "
            "figure already exceeds its budget, by the tax alone.")

    def test_a_confirmed_po_is_a_commitment_but_reaches_no_project_total(self):
        project = self._project()
        contractor = self._contractor()
        milestone = self._milestone(project, budget=750_000.0,
                                    contractor_id=contractor.id)
        wizard = self.env['realestate.subcontract.po.wizard'].create({
            'contractor_id': contractor.id,
            'milestone_ids': [(6, 0, milestone.ids)],
        })
        wizard.action_create_po()

        po = contractor.contract_po_id
        self.assertEqual(po.state, 'purchase')
        self.assertEqual(po.amount_untaxed, 750_000.0)
        project.invalidate_recordset()
        self.assertEqual(
            project.actual_cost, 0.0,
            "Correct: a commitment is not an actual. But nothing else records "
            "it against the project either.")


@tagged('post_install', '-at_install', 'atmta_construction')
class TestBOQCertificationControl(ConstructionCommon):
    """Q6 / Q7 / Q8 / Q9 — the quantity control, and where it fails."""

    def test_defect_the_certified_percentage_is_a_compute_writing_its_own_dependency(self):
        """`current_certified_pct` is a plain field that a compute assigns to.

        `_compute_amounts` lists `current_certified_pct` in its own
        `@api.depends` and then writes it in detail mode. Nothing declares the
        field computed, so no read of it triggers the method: whether it holds
        the derived value depends on whether something else caused the compute
        to run first.

        This was observed both ways during the audit — a quantity-based
        certificate rejected by `action_certify()` as empty while its lines
        totalled 40,000, and the same construction certifying normally in a
        later run. The exact trigger is compute-scheduling dependent and is
        deliberately **not** asserted here; what is asserted is the structure
        that makes the outcome depend on scheduling at all.
        """
        field = self.Certificate._fields['current_certified_pct']
        self.assertFalse(
            field.compute,
            "The percentage is not a computed field...")

        compute = type(self.Certificate)._compute_amounts
        depends = getattr(compute, '_depends', ())
        self.assertIn(
            'current_certified_pct', depends,
            "...yet the compute that writes it declares it as its own "
            "dependency. A value derived this way is only as reliable as the "
            "order the fields happened to be read in.")

    def test_certifying_within_the_remaining_quantity_records_it(self):
        """The happy path, for the record: this much does work."""
        project = self._project()
        contractor = self._contractor()
        boq = self._boq(project, quantities=((100.0, 1_000.0),))
        line = boq.line_ids[0]

        certificate = self._certificate(
            project, contractor, boq_line=line, qty=40.0,
            contract_value=100_000.0)
        self.assertEqual(certificate.gross_amount, 40_000.0)
        certificate.action_certify()
        line.invalidate_recordset()

        self.assertEqual(certificate.state, 'certified')
        self.assertEqual(line.certified_qty, 40.0)
        self.assertEqual(line.remaining_qty, 60.0)

    def test_certifying_beyond_the_boq_quantity_is_refused(self):
        """Q8 — was: the quantity guard was dead code. M7 made it real.

        The old guard compared the certified quantity against
        `quantity - certified_qty + (this line's own qty)`. The line was
        already in `certification_line_ids` when the constraint ran, so its
        own quantity was added back into the ceiling and the test became
        `1000 > 100 - 0 + 1000` — never true.

        M7 checks at certification, against the **authorised** quantity, from
        the database. The contract value is left high on purpose so the
        percentage ceiling cannot fire and take the credit.
        """
        project = self._project()
        contractor = self._contractor()
        boq = self._boq(project, quantities=((100.0, 1_000.0),))
        line = boq.line_ids[0]

        certificate = self._certificate(
            project, contractor, boq_line=line, qty=1_000.0,
            contract_value=10_000_000.0)
        self.assertEqual(certificate.gross_amount, 1_000_000.0)

        with self.assertRaises(UserError):
            certificate.action_certify()

        line.invalidate_recordset()
        self.assertEqual(line.certified_qty, 0.0)
        self.assertEqual(line.authorised_quantity, 100.0)
        self.assertFalse(line.is_over_certified)

    def test_two_certificates_cannot_certify_the_same_quantity(self):
        """Q9 — was: nothing re-checked at certification time.

        `certified_qty` counts only certificates already in `certified`,
        `invoiced` or `paid`, so two drafts were each invisible to the other
        and both could be certified in full — two people billing the same wall
        on the same day, no unlucky interleaving required.

        M7 re-reads the certified quantity inside `action_certify()`, under an
        advisory lock, so the second one is refused.
        """
        project = self._project()
        contractor = self._contractor()
        boq = self._boq(project, quantities=((100.0, 1_000.0),))
        line = boq.line_ids[0]

        first = self._certificate(project, contractor, boq_line=line,
                                  qty=100.0, contract_value=1_000_000.0)
        second = self._certificate(project, contractor, boq_line=line,
                                   qty=100.0, contract_value=1_000_000.0)
        self.assertEqual(first.gross_amount, 100_000.0)
        self.assertEqual(second.gross_amount, 100_000.0)

        first.action_certify()
        with self.assertRaises(UserError):
            second.action_certify()

        line.invalidate_recordset()
        self.assertEqual(line.certified_qty, 100.0)
        self.assertEqual(line.remaining_qty, 0.0)

    def test_an_approved_boq_is_revised_rather_than_edited(self):
        """Rule 3 — approved financial history must be immutable.

        There used to be no write guard at all: the quantity a contractor is
        allowed to certify against could be changed after approval, by anyone,
        leaving no revision behind. M7 refuses the edit and offers a revision.
        """
        project = self._project()
        boq = self._boq(project, quantities=((100.0, 1_000.0),))
        self.assertEqual(boq.state, 'approved')
        line = boq.line_ids[0]

        with self.assertRaises(UserError):
            line.quantity = 500.0

        boq.invalidate_recordset()
        self.assertEqual(line.quantity, 100.0)

        revision = boq.action_create_revision()
        self.assertEqual(revision.revision, boq.revision + 1)
        self.assertEqual(revision.supersedes_id, boq)
        self.assertEqual(boq.superseded_by_id, revision)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestRetention(ConstructionCommon):
    """Q10 — how retention is represented in the accounts."""

    def test_retention_is_held_as_a_liability(self):
        """Retention is withheld money the developer still owes.

        It used to post as a negative line with no account of its own, so it
        netted against the expense: the bill was smaller, the payable was
        smaller, and nothing said "we are holding 5,000 of this contractor's
        money". M7 gives it the liability account it belongs in.
        """
        project = self._project()
        project._get_or_create_analytic_account()
        self._configure_construction_accounts()
        contractor = self._contractor(retention=5.0)
        certificate = self._certificate(
            project, contractor, contract_value=1_000_000.0, pct=10.0)
        certificate.action_certify()
        certificate.action_create_vendor_bill()
        bill = certificate.vendor_bill_id

        self.assertEqual(certificate.gross_amount, 100_000.0)
        self.assertEqual(certificate.retention_amount, 5_000.0)
        self.assertEqual(certificate.net_payable, 95_000.0)

        retention_lines = bill.invoice_line_ids.filtered(
            lambda l: l.price_unit < 0)
        self.assertEqual(len(retention_lines), 1)
        self.assertEqual(
            retention_lines.account_id,
            self.company.construction_retention_account_id,
            "Retention belongs in its own liability account.")
        self.assertNotEqual(
            retention_lines.account_id,
            bill.invoice_line_ids.filtered(
                lambda l: l.price_unit > 0)[:1].account_id)
        self.assertEqual(certificate.retention_movement_id.amount, 5_000.0)

    def test_retention_outstanding_is_a_register_scoped_to_a_project(self):
        """Was: a number on the contractor, unscoped and unposted."""
        project = self._project()
        self._configure_construction_accounts()
        contractor = self._contractor(retention=10.0)
        certificate = self._certificate(
            project, contractor, contract_value=500_000.0, pct=20.0)
        certificate.action_certify()
        certificate.action_create_vendor_bill()
        contractor.invalidate_recordset()

        Retention = self.env['realestate.construction.retention']
        self.assertEqual(contractor.total_retention_held, 10_000.0)
        self.assertEqual(
            Retention.balance(project=project, contractor=contractor),
            10_000.0)
        self.assertEqual(
            Retention.balance(project=self._project(), contractor=contractor),
            0.0, "Held retention is scoped to the project that withheld it.")
        self.assertIn('company_id', self.Certificate._fields)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestProgressMeaning(ConstructionCommon):
    """Q12 / Q13 — what "progress" means, and what it is not."""

    def test_project_progress_is_milestone_weighted_only(self):
        project = self._project()
        self._milestone(project, weight=30.0, completion_percentage=100.0)
        self._milestone(project, weight=70.0, completion_percentage=0.0)
        project.invalidate_recordset()

        self.assertEqual(project.construction_progress, 30.0)

    def test_defect_boq_certification_does_not_move_project_progress(self):
        """Financial progress and physical progress are different numbers, and
        the system reports only one of them under a name that implies both."""
        project = self._project()
        contractor = self._contractor()
        self._milestone(project, weight=100.0, completion_percentage=0.0)
        boq = self._boq(project, quantities=((100.0, 1_000.0),))
        certificate = self._certificate(
            project, contractor, boq_line=boq.line_ids[0], qty=100.0,
            contract_value=100_000.0)
        self.assertEqual(certificate.gross_amount, 100_000.0)
        certificate.action_certify()
        project.invalidate_recordset()
        boq.invalidate_recordset()

        self.assertEqual(boq.certified_pct, 100.0)
        self.assertEqual(
            project.construction_progress, 0.0,
            "The BOQ is fully certified and the project reports 0% progress. "
            "Neither number is wrong; they measure different things and only "
            "one is named.")


@tagged('post_install', '-at_install', 'atmta_construction')
class TestForecastAndChangeControl(ConstructionCommon):
    """Q14 / Q15 — change orders and forecasting do not exist."""

    def test_change_control_exists_and_can_move_a_baseline(self):
        """FIXED IN M4 — was `test_defect_no_change_order_model_exists`."""
        for model in ('realestate.construction.change.event',
                      'realestate.construction.change.order',
                      'realestate.construction.budget.change.line',
                      'realestate.construction.commitment.change'):
            self.assertIsNotNone(self.env.get(model))

    def test_forecasting_exists_and_produces_etc_and_eac(self):
        """FIXED IN M3 — was `test_defect_no_forecast_model_exists`."""
        self.assertIsNotNone(
            self.env.get('realestate.construction.forecast'))
        Line = self.env['realestate.construction.forecast.line']
        for field in ('etc_amount', 'eac_amount', 'forecast_variance'):
            self.assertIn(field, Line._fields)

    def test_the_legacy_variance_now_runs_on_declared_accruals_only(self):
        """FIXED IN M2 — was
        `test_the_only_variance_is_actual_minus_a_scalar_budget`.

        `project.cost_variance` survives for the views and reports that use it,
        but no longer counts unclassified typed numbers as spend. It still says
        nothing about whether the project will finish on budget — that is M3's
        question, and this field is not it.
        """
        project = self._project(budget=1_000_000.0)
        self.CostLine.create({
            'name': 'Unclassified', 'project_id': project.id,
            'amount': 250_000.0})
        project.invalidate_recordset()
        self.assertEqual(project.cost_variance, -1_000_000.0)

        self.CostLine.create({
            'name': 'Declared accrual', 'project_id': project.id,
            'amount': 250_000.0, 'cost_basis': 'accrual'})
        project.invalidate_recordset()

        self.assertEqual(project.cost_variance, -750_000.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestMultiCompanyGaps(ConstructionCommon):
    """Q18 — company and project isolation."""

    CONSTRUCTION_MODELS = (
        'realestate.contractor',
        'realestate.construction.milestone',
        'realestate.construction.task',
        'realestate.construction.cost.line',
        'realestate.construction.payment.certificate',
        'realestate.construction.payment.certificate.line',
        'realestate.boq',
        'realestate.boq.line',
        'realestate.work.item',
        'realestate.owner.progress.billing',
        'realestate.construction.labor.log',
    )

    #: Carrying a company is a per-model fix, done as each milestone reaches
    #: the model. M7 did the money-bearing ones; the rest are named here so
    #: the gap stays counted rather than forgotten.
    WITH_COMPANY = (
        'realestate.construction.payment.certificate',
        'realestate.construction.payment.certificate.line',
        'realestate.boq',
        'realestate.boq.line',
        'realestate.owner.progress.billing',
    )

    def test_the_money_bearing_models_now_carry_a_company(self):
        without = [name for name in self.WITH_COMPANY
                   if 'company_id' not in self.env[name]._fields]

        self.assertFalse(
            without,
            "Certificates, BOQs and owner billing post to a ledger, so they "
            "must say whose ledger: %s" % without)

    def test_defect_the_remaining_models_still_have_no_company(self):
        """The unfixed remainder, kept counted rather than forgotten."""
        remaining = [name for name in self.CONSTRUCTION_MODELS
                     if name not in self.WITH_COMPANY]
        without = [name for name in remaining
                   if 'company_id' not in self.env[name]._fields]

        self.assertEqual(
            without, remaining,
            "DEFECT: these construction models still carry no company.")

    def test_every_construction_model_is_guarded_by_a_record_rule(self):
        """Q18 — was: not one record rule existed. M9 closed it.

        Two families are required of every model that holds project data: a
        global company rule, so multi-company isolation is not something a
        privilege can be granted past, and a project rule, so a project can be
        restricted to its team.
        """
        rules = self.env['ir.rule'].sudo().search([
            ('model_id.model', 'in', list(self.CONSTRUCTION_MODELS))])
        self.assertTrue(rules, "Construction data must be guarded.")

        by_model = {}
        for rule in rules:
            by_model.setdefault(rule.model_id.model, []).append(rule)

        #: Global catalogues and legacy models without a company are handled
        #: by `test_m9_security.py`, which records the decision for each one.
        company_scoped = [
            'realestate.construction.payment.certificate',
            'realestate.construction.payment.certificate.line',
            'realestate.boq',
            'realestate.boq.line',
            'realestate.owner.progress.billing',
        ]
        for model in company_scoped:
            model_rules = by_model.get(model, [])
            self.assertTrue(
                any(not rule.groups for rule in model_rules),
                "%s has no global company rule." % model)

    def test_a_certificate_cannot_bill_a_vendor_of_another_company(self):
        """Was: nothing checked that the project, the contractor and the bill
        agreed about which company they belong to."""
        other = self.env['res.company'].create({'name': 'Other Construction Co'})
        project = self._project()
        contractor = self._contractor()
        contractor.partner_id.company_id = other

        with self.assertRaises(ValidationError):
            self._certificate(project, contractor,
                              contract_value=100_000.0, pct=50.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestAnalyticIntegration(ConstructionCommon):
    """Rule 4 — the single analytic dimension, and how it is chosen."""

    def test_a_project_gets_one_analytic_account(self):
        project = self._project()

        account = project._get_or_create_analytic_account()

        self.assertTrue(account)
        self.assertEqual(project.analytic_account_id, account)

    def test_the_analytic_plan_is_referenced_not_guessed(self):
        """FIXED IN M1 — was `test_defect_the_analytic_plan_is_whichever_one_sorts_first`.

        The audit found `Plan.search([], limit=1)`, which filed project cost
        centres under whichever plan sorted first. The defect test that proved
        it is inverted here rather than deleted, so the fix keeps a test of its
        own in the file that documented the bug.
        """
        self.env['account.analytic.plan'].sudo().create(
            {'name': 'AAA Not A Project Plan'})
        project = self._project()

        account = project._get_or_create_analytic_account()

        self.assertEqual(
            account.plan_id,
            self.env.ref('atmta_construction_core.analytic_plan_re_projects'))

    def test_the_analytic_account_takes_the_projects_company(self):
        """FIXED IN M1 — was
        `test_defect_the_analytic_account_takes_the_users_company_not_the_projects`.
        """
        other = self.env['res.company'].create({'name': 'Analytic Other Co'})
        project = self._project()
        project.company_id = other

        account = project._get_or_create_analytic_account()

        self.assertEqual(account.company_id, other)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestPerformanceShape(ConstructionCommon):
    """M46 — how the dashboard gets its numbers today."""

    def test_the_dashboard_returns_data(self):
        project = self._project()
        self._milestone(project)
        data = self.env['realestate.construction.dashboard'].get_data()

        self.assertIn('kpis', data)
        self.assertIn('total_budget', data['kpis'])

    def test_defect_the_dashboard_reads_every_record_in_the_database(self):
        """`Milestone.search([])`, `CostLine.search([])`, then Python sums —
        with no company filter, no project filter and no aggregation."""
        import inspect
        source = inspect.getsource(
            type(self.env['realestate.construction.dashboard']).get_data)

        self.assertIn('search([])', source)
        self.assertNotIn('_read_group', source,
                         "DEFECT: no database aggregation anywhere.")
