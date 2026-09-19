# -*- coding: utf-8 -*-
"""Regressions for what the construction lifecycle run found in cost."""
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
