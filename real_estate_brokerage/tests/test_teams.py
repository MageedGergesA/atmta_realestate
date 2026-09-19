# -*- coding: utf-8 -*-
"""M2 / M20 — sales teams, and the project scope Module 2 deferred.

No `realestate.sales.team` exists. Odoo's `crm.team` plus `crm.team.member`
already provide teams and multi-team membership; what they lack is the notion
that a team is authorised to sell certain projects, and that is what is added.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import BrokerageCommon


@tagged('post_install', '-at_install', 'atmta_brokerage')
class TestTeamsAreNative(BrokerageCommon):

    def test_no_second_team_model_was_created(self):
        self.assertNotIn('realestate.sales.team', self.env,
                         "A duplicate team model was created; crm.team exists")

    def test_membership_is_odoo_s_model(self):
        self.assertIn('crm.team.member', self.env)
        self.env['crm.team.member'].create(
            {'crm_team_id': self.re_team.id, 'user_id': self.agent.id})
        teams = self.Team.search([('member_ids', 'in', [self.agent.id])])
        self.assertIn(self.re_team, teams)

    def test_odoo_multi_membership_is_off_by_default(self):
        """M2 said to test this before enforcing rules on it — so here it is.

        Odoo 18 gates multiple team memberships on the
        `sales_team.membership_multi` parameter. In the default mono mode,
        joining a second team ARCHIVES the first. A design that silently
        assumed a user could be on two teams would therefore have been wrong
        on every default install.
        """
        self.assertFalse(
            self.env['ir.config_parameter'].sudo().get_param(
                'sales_team.membership_multi', False),
            "Multi-membership is enabled in this database; the mono-mode "
            "assertion below is not testing what it claims")

        second = self.Team.create({'name': 'Second RE Team'})
        self._join(self.re_team)
        self._join(second)

        active = self.env['crm.team.member'].search(
            [('user_id', '=', self.agent.id)])
        self.assertEqual(len(active), 1,
                         "Odoo no longer archives the previous membership in "
                         "mono mode; revisit the team-scoping design")
        self.assertEqual(active.crm_team_id, second)

    def test_multi_membership_works_when_enabled(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'sales_team.membership_multi', True)
        second = self.Team.create({'name': 'Second RE Team'})
        self._join(self.re_team)
        self._join(second)

        teams = self.Team.search([('member_ids', 'in', [self.agent.id])])
        self.assertIn(self.re_team, teams)
        self.assertIn(second, teams)

    def test_a_team_carries_authorised_projects(self):
        self.assertIn('allowed_realestate_project_ids', self.Team._fields)
        self.re_team.allowed_realestate_project_ids = [(6, 0, self.project.ids)]
        self.assertTrue(self.re_team.realestate_project_scoped)

    def test_an_unrestricted_team_stays_unrestricted(self):
        """Adding the field must change nothing until somebody uses it."""
        team = self.Team.create({'name': 'Unscoped Team'})
        self.assertFalse(team.realestate_project_scoped)
        self.assertFalse(team.allowed_realestate_project_ids)


@tagged('post_install', '-at_install', 'atmta_brokerage')
class TestProjectAuthority(BrokerageCommon):
    """The question 'may this user work this project?' is asked in one place."""

    def setUp(self):
        super().setUp()
        self.other_project = self.env['realestate.project'].create({
            'name': 'Other Project', 'code': 'OTH',
            'company_id': self.company.id, 'commercial_state': 'selling'})

    def test_an_unrestricted_user_returns_none_not_empty(self):
        """`None` means 'everything'; `[]` would mean 'nothing'.

        Conflating the two is how project scoping ends up either useless or
        locking every agent out of the whole database.
        """
        allowed = self.Team._re_allowed_project_ids_for_user(self.agent)
        self.assertIsNone(allowed)

    def test_a_scoped_team_limits_its_members(self):
        self.re_team.allowed_realestate_project_ids = [(6, 0, self.project.ids)]
        self._join(self.re_team)

        allowed = self.Team._re_allowed_project_ids_for_user(self.agent)
        self.assertEqual(allowed, self.project.ids)

    def test_two_teams_grant_the_union(self):
        """Being on a second team grants access; it does not intersect it away.

        Requires Odoo's multi-membership mode, which is off by default — see
        `test_odoo_multi_membership_is_off_by_default`.
        """
        self.env['ir.config_parameter'].sudo().set_param(
            'sales_team.membership_multi', True)
        second = self.Team.create({
            'name': 'Other Project Team',
            'allowed_realestate_project_ids': [(6, 0, self.other_project.ids)]})
        self.re_team.allowed_realestate_project_ids = [(6, 0, self.project.ids)]
        for team in (self.re_team, second):
            self.env['crm.team.member'].create(
                {'crm_team_id': team.id, 'user_id': self.agent.id})

        allowed = set(self.Team._re_allowed_project_ids_for_user(self.agent))
        self.assertEqual(allowed, set(self.project.ids + self.other_project.ids))

    def test_scoping_works_in_mono_membership_mode_too(self):
        """The default install must not be left without project scoping."""
        self.re_team.allowed_realestate_project_ids = [(6, 0, self.project.ids)]
        self._join(self.re_team)
        self.assertEqual(
            self.Team._re_allowed_project_ids_for_user(self.agent),
            self.project.ids)

    def test_an_unscoped_team_does_not_silently_grant_everything(self):
        """Joining a general sales team must not unlock every project.

        The tempting rule — "any unrestricted team makes the user
        unrestricted" — is the wrong way round: it means adding a real-estate
        agent to a generic team quietly hands them the whole inventory. So a
        team that names no projects contributes nothing to real-estate project
        access, and scoping stays opt-in and additive.
        """
        self.env['ir.config_parameter'].sudo().set_param(
            'sales_team.membership_multi', True)
        self.re_team.allowed_realestate_project_ids = [(6, 0, self.project.ids)]
        unscoped = self.Team.create({'name': 'General Team'})
        for team in (self.re_team, unscoped):
            self.env['crm.team.member'].create(
                {'crm_team_id': team.id, 'user_id': self.agent.id})

        allowed = self.Team._re_allowed_project_ids_for_user(self.agent)
        self.assertEqual(
            allowed, self.project.ids,
            "A generic team widened a scoped agent's project access")

    def test_a_user_on_only_unscoped_teams_is_unrestricted(self):
        """The other half of the rule: scoping applies only where configured,
        so a deployment that never uses it is unaffected."""
        unscoped = self.Team.create({'name': 'Only General'})
        self._join(unscoped)
        self.assertIsNone(
            self.Team._re_allowed_project_ids_for_user(self.agent))

    def test_the_guard_refuses_an_unauthorised_project(self):
        self.re_team.allowed_realestate_project_ids = [(6, 0, self.project.ids)]
        self.re_team._re_check_project_allowed(self.project)
        with self.assertRaises(UserError) as err:
            self.re_team._re_check_project_allowed(self.other_project)
        self.assertIn('not authorised', str(err.exception))

    def test_the_guard_is_a_no_op_for_an_unscoped_team(self):
        team = self.Team.create({'name': 'No Scope'})
        self.assertTrue(team._re_check_project_allowed(self.other_project))
