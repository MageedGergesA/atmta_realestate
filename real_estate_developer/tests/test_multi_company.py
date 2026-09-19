# -*- coding: utf-8 -*-
"""Phase 44 — multi-company isolation.

Before 0.3 not one model in this module had a ``company_id`` and not one
``ir.rule`` existed, so every user of every company saw every project, every
unit release and every deal. These tests exist so that cannot silently come
back.
"""

from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from .common import DeveloperCommon


@tagged('post_install', '-at_install', 'atmta_developer')
class TestDeveloperMultiCompany(DeveloperCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = cls.env['res.company'].create({'name': 'Developer Co B'})
        cls.project_b = cls.Project.create({
            'name': 'Coastline', 'code': 'CL',
            'company_id': cls.company_b.id,
        })
        cls.user_a = cls.env['res.users'].create({
            'name': 'Agent A', 'login': 'dev_agent_a',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'groups_id': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('real_estate_developer.group_dev_manager').id,
            ])],
        })

    def test_every_model_carries_a_company(self):
        for model in ('realestate.project', 'realestate.phase',
                      'realestate.unit.release.batch', 'realestate.unit.block',
                      'realestate.unit.reservation', 'realestate.sale.contract',
                      'realestate.sale.installment'):
            self.assertIn(
                'company_id', self.env[model]._fields,
                "%s has no company_id, so no record rule can isolate it" % model)

    def test_a_user_cannot_read_another_company_project(self):
        visible = self.Project.with_user(self.user_a).search([])
        self.assertIn(self.project, visible)
        self.assertNotIn(self.project_b, visible)

    def test_record_rules_do_not_admit_company_less_records(self):
        """A NULL company must not be visible to everyone.

        This is the leak the frozen Module 1 rules were written to avoid, and
        the same reasoning applies here.
        """
        # The rule moved to `atmta_project_core` with the `company_id` it
        # filters on (ATMTA V2 Wave 2). Its domain is unchanged, so what
        # this test asserts is unchanged too.
        rule = self.env.ref('atmta_project_core.rule_project_company')
        self.assertNotIn(
            'company_id', rule.domain_force.replace(
                "[('company_id', 'in', company_ids)]", ''),
            "the domain should be exactly the allowed-companies test")
        self.assertEqual(rule.domain_force, "[('company_id', 'in', company_ids)]")

    def test_release_batch_cannot_span_companies(self):
        """`check_company` must reject a unit from another company."""
        foreign_unit = self.Property.create({
            'name': 'CL-U-001', 'property_code': 'CL-U-001',
            'hierarchy_level': 'unit', 'usage_category': 'apartment',
            'area_sqm': 90.0, 'company_id': self.company_b.id,
            'project_id': self.project_b.id,
        })
        with self.assertRaises(Exception):
            self.Release.create({
                'project_id': self.project.id,
                'property_ids': [(6, 0, foreign_unit.ids)],
            })

    def test_deal_company_follows_the_unit(self):
        """A reservation cannot belong to a company its unit does not."""
        unit = self.units[0]
        self.assertEqual(unit.company_id, self.company)
        Reservation = self.env['realestate.unit.reservation']
        self.assertEqual(
            Reservation._fields['company_id'].related,
            'property_id.company_id',
            "company must be derived from the unit, not entered by hand")
        self.assertTrue(
            Reservation._fields['company_id'].readonly,
            "a deal's company is not something a user types")

    def test_agents_are_scoped_to_their_own_deals(self):
        """Phase 46, first step: an agent does not see the whole company."""
        agent = self.env['res.users'].create({
            'name': 'Agent B', 'login': 'dev_agent_b',
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('real_estate_developer.group_dev_agent').id,
            ])],
        })
        rule = self.env.ref('real_estate_developer.rule_sale_contract_agent_own')
        self.assertTrue(
            rule.groups,
            "an agent rule with no groups would be global and would restrict "
            "managers too")
        self.assertIn(
            self.env.ref('real_estate_developer.group_dev_agent'), rule.groups)
        self.assertIn('agent_id', rule.domain_force)
        # The manager rule must re-open the full company view.
        manager_rule = self.env.ref(
            'real_estate_developer.rule_sale_contract_manager_all')
        self.assertEqual(manager_rule.domain_force, "[(1, '=', 1)]")
        self.assertTrue(agent.has_group('real_estate_developer.group_dev_agent'))

    def test_agents_can_no_longer_edit_projects(self):
        """A project is a commercial master, not a deal record."""
        agent = self.env['res.users'].create({
            'name': 'Agent C', 'login': 'dev_agent_c',
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('real_estate_developer.group_dev_agent').id,
            ])],
        })
        with self.assertRaises(AccessError):
            self.project.with_user(agent).write({'name': 'Renamed by an agent'})
