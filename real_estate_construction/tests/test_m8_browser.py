# -*- coding: utf-8 -*-
"""M8 — the real-browser gate for claims, delays, EOT, risk and issues.

Nothing here types a credential — `start_tour` authenticates server-side.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import HttpCase, tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestClaimsBrowser(ConstructionCommon, HttpCase):

    def test_the_claims_screens_open_and_render(self):
        project = self._project()
        contractor = self._contractor()
        civil = self._cost_code('SUB-CIV-M8T', 'Civil', 'subcontract')
        completion = fields.Date.to_date('2027-12-31')
        package = self._package(project, contractor, value=10_000_000.0,
                                award=True,
                                current_completion_date=completion)
        package.write({'notice_required': True, 'notice_period_days': 28})

        event = self._delay_event(project, package,
                                  event_type='access_delay')
        event.action_open()

        awareness = fields.Date.to_date('2027-03-01')
        notice = self._notice(project, package, awareness_date=awareness,
                              notice_date=awareness + relativedelta(days=35),
                              delay_event_id=event.id)

        claim = self._claim(project, package, claimed_cost=5_000_000.0,
                            claimed_days=60.0, notice=notice,
                            delay_event_ids=[(6, 0, event.ids)])
        Submission = self.env['realestate.construction.claim.submission']
        Submission.create({'claim_id': claim.id, 'claimed_cost': 5_000_000.0,
                           'claimed_days': 60.0}).action_issue()
        Submission.create({'claim_id': claim.id, 'claimed_cost': 6_000_000.0,
                           'claimed_days': 75.0}).action_issue()
        self.env['realestate.construction.claim.cost.line'].create({
            'claim_id': claim.id, 'category': 'site_overhead',
            'description': 'Prolongation', 'cost_code_id': civil.id,
            'quantity': 30.0, 'rate': 20_000.0})
        claim.action_submit()
        self.env['realestate.construction.claim.determination'].create({
            'claim_id': claim.id, 'determined_cost': 3_500_000.0,
            'determined_days': 30.0,
            'reasons': '<p>Assessed against the programme.</p>',
        }).action_issue()

        eot = self._eot(claim, claimed_days=60.0, determined_days=30.0)
        eot.action_determine()
        eot.action_implement()

        risk = self._risk(project, cost_exposure=2_000_000.0)
        risk.action_assess()
        risk.action_monitor()
        issue = risk.action_materialise(reason='The supplier failed.')
        issue.estimated_cost_impact = 2_000_000.0

        self.env.ref('base.user_admin').groups_id |= (
            self.env.ref('real_estate_construction.group_construction_manager')
            | self.env.ref(
                'real_estate_construction.group_construction_commercial'))
        self.env.flush_all()

        self.start_tour('/odoo', 'construction_claims_tour',
                        login='admin', timeout=240)
