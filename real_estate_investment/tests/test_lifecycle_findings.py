# -*- coding: utf-8 -*-
"""Findings of the operations lifecycle run, each pinned by a test.

Every test here failed before its fix, for the reason its docstring gives.
"""

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests.common import new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from ..models.feasibility import _calc_payback
from .common import InvestmentCommon


@tagged('post_install', '-at_install')
class TestInvestmentLifecycleFindings(InvestmentCommon):

    def _button(self, name):
        arch = self.Study.get_view(
            self.env.ref('real_estate_investment.view_feasibility_form').id, 'form')['arch']
        return etree.fromstring(arch).xpath("//button[@name='%s']" % name)[0]

    # 8b -----------------------------------------------------------------
    def test_an_analyst_opens_the_dashboard(self):
        """The dashboard read realestate.project, which an Analyst cannot."""
        analyst = new_test_user(self.env, login='inv_analyst',
                                groups='base.group_user,real_estate_investment.group_investment_user')
        self.assertFalse(self.env['realestate.project'].with_user(analyst).has_access('read'))
        self.project.write({'latitude': 30.0, 'longitude': 31.0})
        study = self._study([-100.0, 60.0, 60.0], project_id=self.project.id)
        data = self.env['realestate.investment.dashboard'].with_user(analyst).get_data()
        self.assertEqual(data['kpis']['total_studies'], self.Study.search_count([]))
        top = {row['id']: row for row in data['top_list']}
        self.assertEqual(top[study.id]['project'], self.project.name)
        # Project coordinates are project data the analyst has no right to read.
        self.assertEqual(data['map_projs'], [])

    # 10 / U4 ------------------------------------------------------------
    def test_pull_from_project_only_on_a_draft(self):
        """Pull from Project replaced an approved study's cash flows."""
        study = self._study([-100.0, 60.0, 60.0], project_id=self.project.id)
        study.action_approve()
        npv = study.npv
        with self.assertRaises(UserError):
            study.action_pull_project_estimates()
        self.assertEqual(study.npv, npv)
        self.assertEqual(len(study.cash_flow_ids), 3)
        button = self._button('action_pull_project_estimates')
        self.assertTrue(button.get('confirm'))
        self.assertTrue(safe_eval(button.get('invisible'), {'state': 'approved', 'project_id': self.project.id}))
        self.assertFalse(safe_eval(button.get('invisible'), {'state': 'draft', 'project_id': self.project.id}))

    def test_pull_from_project_on_a_draft_still_works(self):
        study = self._study([-5.0], project_id=self.project.id)
        study.action_pull_project_estimates()
        self.assertEqual(len(study.cash_flow_ids), 4)
        self.assertEqual(study.total_outflow, 1000000.0)
        self.assertEqual(study.total_inflow, 1500000.0)

    def test_state_changes_check_the_source_state(self):
        """approve / reject / archive accepted any state."""
        approved = self._study([-100.0, 150.0])
        approved.action_approve()
        with self.assertRaises(UserError):
            approved.action_approve()
        with self.assertRaises(UserError):
            approved.action_reject()
        draft = self._study([-100.0, 150.0])
        with self.assertRaises(UserError):
            draft.action_archive_study()
        approved.action_archive_study()
        self.assertEqual(approved.state, 'archived')
        with self.assertRaises(UserError):
            approved.action_archive_study()
        rejected = self._study([-100.0, 50.0])
        rejected.action_reject()
        rejected.action_archive_study()
        self.assertEqual(rejected.state, 'archived')

    def test_archive_button_hidden_on_a_draft(self):
        """Archive showed on a draft study."""
        invisible = self._button('action_archive_study').get('invisible')
        for state, hidden in (('draft', True), ('approved', False), ('rejected', False), ('archived', True)):
            self.assertEqual(bool(safe_eval(invisible, {'state': state})), hidden, state)

    # 11 -----------------------------------------------------------------
    def test_payback_hand_checked(self):
        """A study whose year 0 is already positive got a negative payback."""
        # Already recovered before period 1: nothing to pay back.
        self.assertEqual(_calc_payback([100.0, 100.0]), 0.0)
        # Cumulative -100, -40, +20: recovered 40/60 of the way into year 2.
        self.assertAlmostEqual(_calc_payback([-100.0, 60.0, 60.0]), 1 + 40.0 / 60.0)
        # Cumulative -100, -100, 0: recovered exactly at the end of year 2.
        self.assertAlmostEqual(_calc_payback([-100.0, 0.0, 100.0]), 2.0)
        # Cumulative +50, -50, +50: the dip is recovered half-way into year 2.
        self.assertAlmostEqual(_calc_payback([50.0, -100.0, 100.0]), 1.5)
        # Cumulative -100, -50: never recovered.
        self.assertIsNone(_calc_payback([-100.0, 50.0]))
        study = self._study([100.0, 100.0])
        self.assertEqual(study.payback_period, 0.0)

    # U5 -----------------------------------------------------------------
    def test_irr_help_matches_the_stored_percent(self):
        study = self._study([-100.0, 110.0])
        self.assertAlmostEqual(study.irr, 10.0, places=4)
        help_text = self.Study._fields['irr'].help
        self.assertNotIn('decimal', help_text.lower())
