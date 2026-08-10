# -*- coding: utf-8 -*-
"""M9 — the real-browser gate for the Control Tower.

A reporting layer is only worth what people believe of it, so this gate is
aimed squarely at the ways a dashboard loses that: a zero standing in for an
unknown, a pending figure sitting inside a baseline, a drilldown that opens
something other than the number it came from, and a NaN.

Nothing here types a credential — `start_tour` authenticates server-side.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import HttpCase, tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestControlTowerBrowser(ConstructionCommon, HttpCase):

    def _seed(self):
        project = self._project(name='M9 Tower Project')
        contractor = self._contractor()
        civil = self._cost_code('M9T-CIV', 'Civil', 'subcontract')
        mep = self._cost_code('M9T-MEP', 'MEP', 'subcontract')
        self._configure_construction_accounts()

        self._baselined(project, 100_000_000.0, code=civil)
        package = self._package(project, contractor, value=80_000_000.0,
                                award=True,
                                current_completion_date=fields.Date.to_date(
                                    '2027-12-31'))
        self._approve_and_implement(self._change_order(
            project, lines=[(civil, 'budget', 5_000_000.0)]))
        self._approve_and_implement(self._change_order(
            project, lines=[(civil, 'commitment', 10_000_000.0)],
            package=package))
        self._post_bill(project, civil, 40_000_000.0)

        forecast = self._forecast(project)
        line = forecast.line_ids.filtered(
            lambda l: l.cost_code_id == civil)[:1]
        line.write({'method': 'manual', 'manual_etc': 55_000_000.0,
                    'manual_reason': 'Priced remaining scope.'})
        forecast.action_submit()
        forecast.action_approve()

        # A commitment on a cost code the approved forecast never saw, so the
        # sheet has a genuine N/A for the tour to find.
        self._po(project, contractor, [(mep, 2_000_000.0)])
        # And an uncoded line, so the data-control worklist has something real.
        self._po(project, contractor, [(None, 400_000.0)])

        self._change_event(project, estimated_cost=8_000_000.0)
        claim = self._claim(project, package, claimed_cost=5_000_000.0,
                            claimed_days=60.0)
        claim.action_submit()
        self._risk(project, cost_exposure=1_000_000.0)
        self._issue(project)
        return project

    def test_the_control_tower_opens_and_tells_the_truth(self):
        project = self._seed()
        # The tower reads the project itself, so the browser user needs the
        # real-estate roles as well as the construction ones. Granting only
        # the construction groups produced a tower that mounted and then
        # showed an access error — which is what the gate is for.
        groups = (
            self.env.ref('real_estate_construction.group_construction_manager')
            | self.env.ref('real_estate_construction.group_construction_cost')
            | self.env.ref(
                'real_estate_construction.group_construction_commercial')
            | self.env.ref('atmta_real_estate.group_realestate_user'))
        developer_group = self.env.ref('real_estate_developer.group_dev_manager',
                                       raise_if_not_found=False)
        if developer_group:
            groups |= developer_group
        self.env.ref('base.user_admin').groups_id |= groups
        self.env.flush_all()

        self.start_tour('/odoo', 'construction_control_tower_tour',
                        login='admin', timeout=300)

    def test_an_empty_project_renders_without_breaking(self):
        """M9 §21 — no NaN, no Infinity, no crashed panel."""
        self._project()
        payload = self.env[
            'realestate.construction.control.tower'].payload(self._project())
        self.assertEqual(payload['health']['status'], 'no_data')
        for section, content in payload.items():
            if isinstance(content, dict):
                self.assertNotIn('failed', content,
                                 "Panel %s failed on an empty project."
                                 % section)
