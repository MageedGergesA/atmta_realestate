# -*- coding: utf-8 -*-
"""A closed tender driven through evaluation, without Construction.

Built on Sourcing's lifecycle fixture so Evaluation, Award and Purchase all
reproduce their findings on one shape of tender.
"""
from odoo.addons.atmta_procurement_sourcing.tests.common import \
    SourcingLifecycleCommon


class EvaluationLifecycleCommon(SourcingLifecycleCommon):

    def _plan(self, event):
        plan = self.env['realestate.procurement.evaluation.plan'].create({
            'sourcing_event_id': event.id,
            'sourcing_version_id': event.current_version_id.id,
            'evaluation_method': 'pass_fail_lowest',
            'evaluation_currency_id': self.company.currency_id.id,
            'financial_formula': 'lowest_ratio',
            'criterion_ids': [(0, 0, {
                'name': 'Valid trade licence', 'criterion_type': 'mandatory',
                'max_score': 0.0, 'weight': 0.0})],
        })
        plan.action_freeze()
        return plan

    def _round(self, event=None, open_=True):
        event = event or self._closed_with_bids()
        round_ = self.env['realestate.procurement.evaluation.round'].create({
            'sourcing_event_id': event.id,
            'plan_id': self._plan(event).id,
        })
        if open_:
            round_.action_open_technical()
        return round_

    def _pass_technical(self, round_):
        Sheet = self.env['realestate.procurement.technical.evaluation']
        for candidate in round_.candidate_ids.filtered('in_technical'):
            sheet = Sheet.create({'round_id': round_.id,
                                  'candidate_id': candidate.id})
            sheet.line_ids.write({'result': 'pass'})
            sheet.action_submit()
        round_.action_finalise_technical()
        return round_

    def _commercial_round(self, event=None):
        round_ = self._pass_technical(self._round(event))
        round_.action_open_commercial()
        round_.action_normalise()
        return round_

    def _finalised_round(self, event=None):
        round_ = self._commercial_round(event)
        round_.action_finalise()
        return round_
