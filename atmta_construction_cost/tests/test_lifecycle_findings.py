# -*- coding: utf-8 -*-
"""Regressions for what the construction lifecycle run found in cost."""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestClaimKpiForSiteUsers(TransactionCase):
    """B9 — the Control Tower's health and risk panels asked for claim KPIs as
    a site user, who may not read claims, and the panel failed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env['realestate.project'].create({
            'name': 'KPI Project', 'code': 'KPI001',
            'company_id': cls.env.company.id})
        cls.site_user = new_test_user(
            cls.env, login='kpi_site_user',
            groups='base.group_user,atmta_roles.group_construction_site_user')

    def test_a_site_user_gets_claim_counts_and_no_claim_money(self):
        Kpi = self.env['realestate.construction.claim.kpi']
        kpis = Kpi.with_user(self.site_user).for_project(self.project)
        self.assertEqual(kpis['open_claims'], 0)
        self.assertEqual(kpis['claims_awaiting_response'], 0)
        self.assertIsNone(kpis['claimed_cost'])
        self.assertIsNone(kpis['determined_cost'])
        self.assertIsNone(kpis['exposure']['claim_only'])
        self.assertIsNone(kpis['exposure']['potential_commercial'])

    def test_a_user_who_may_read_claims_still_gets_the_money(self):
        kpis = self.env['realestate.construction.claim.kpi'].for_project(
            self.project)
        self.assertEqual(kpis['claimed_cost'], 0.0)
        self.assertEqual(kpis['exposure']['potential_commercial'], 0.0)


@tagged('post_install', '-at_install')
class TestDrilldownIsOnlyOfferedWhenItOpens(TransactionCase):
    """R1 — the cost sheet handed back a drilldown its own readers cannot open.

    `current_commitment` opens `purchase.order.line` and `actual_cost` opens
    `account.analytic.line`. Both leave the construction domain, and no
    construction role carries Purchase or Accounting rights, so the action the
    cost sheet returned to them could only ever end in an AccessError. The
    aggregate stays theirs — it is a project control figure — but the column
    must say it does not open rather than offer a click that fails.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.Sheet = env['realestate.construction.cost.sheet']
        # Project read belongs to the application owning the project screens,
        # which is not installed here; grant what a deployment would have.
        env['ir.model.access'].create({
            'name': 'test project reader',
            'model_id': env['ir.model']._get('realestate.project').id,
            'group_id': env.ref('base.group_user').id,
            'perm_read': True,
        })
        cls.project = env['realestate.project'].create({
            'name': 'Drilldown access', 'code': 'DDA1',
            'company_id': env.company.id})
        cls.manager = new_test_user(
            env, login='dda_manager',
            groups='base.group_user,'
                   'atmta_roles.group_construction_manager')

    def _assert_not_offered(self, column, model):
        action = self.Sheet.with_user(self.manager).drilldown(
            self.project, column)
        if action:
            # Why offering it is the bug: the reader cannot act on what they
            # were handed. Demonstrated rather than asserted about the groups,
            # because the click is what the user experiences.
            with self.assertRaises(AccessError):
                self.env[action['res_model']].with_user(self.manager).search(
                    action['domain'])
            self.fail(
                "drilldown() offered %s on %s to a Construction Manager, and "
                "opening it raises AccessError." % (column, model))
        self.assertFalse(action)

    def test_a_construction_manager_is_not_offered_the_commitment_drilldown(
            self):
        self._assert_not_offered('current_commitment', 'purchase.order.line')

    def test_a_construction_manager_is_not_offered_the_actual_drilldown(self):
        self._assert_not_offered('actual_cost', 'account.analytic.line')

    def test_the_columns_they_can_open_are_still_offered(self):
        """The fix drops what fails, not what works."""
        action = self.Sheet.with_user(self.manager).drilldown(
            self.project, 'original_budget')
        self.assertTrue(action)
        self.assertEqual(action['res_model'],
                         'realestate.construction.budget.line')
        self.env[action['res_model']].with_user(self.manager).search(
            action['domain'])

    def test_a_reader_with_the_rights_still_gets_both_actions(self):
        columns = [('current_commitment', 'purchase.order.line')]
        # The analytic domain reads `project.analytic_account_id`, a field the
        # module above this one declares. It is there in any deployment and
        # absent from this module's own install, so the column is asserted
        # only where it can be built.
        if 'analytic_account_id' in self.env['realestate.project']._fields:
            columns.append(('actual_cost', 'account.analytic.line'))
        for column, model in columns:
            action = self.Sheet.drilldown(self.project, column)
            self.assertTrue(action, "%s stopped opening for a reader who may "
                                    "open it." % column)
            self.assertEqual(action['res_model'], model)
