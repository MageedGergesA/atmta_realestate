# -*- coding: utf-8 -*-
"""Exposure — one event, seen at several stages, counted once.

The same problem travels through this system under different names:

    Risk            something that may happen
    Issue           it happened
    Change Event    it may cost money
    Claim           somebody is asking to be paid for it
    Change Order    somebody authorised paying for it
    Actual          it has been paid

Every one of those is a legitimate record. Adding them together is not. A
project with a 2M risk that became a 2M issue, a 2M change event and a 2M
claim has 2M of exposure, not 8M — and a dashboard that says 8M will be
disbelieved by the only people whose belief matters.

So exposure is reported by **layer**, deduplicated along the source links each
record already carries, and the layers are never summed into one headline
number by this module.
"""
from odoo import _, api, fields, models

#: The ladder, in order. A record belongs to the highest rung it has reached,
#: and is not counted on any rung below.
LAYERS = ('approved', 'claim', 'change', 'issue', 'risk')


class ConstructionExposure(models.AbstractModel):
    _name = 'realestate.construction.exposure'
    _description = 'Potential Commercial Exposure'

    @api.model
    def for_project(self, project):
        """Exposure by layer, with each underlying event counted once.

        Returns the layers separately *and* a `potential_commercial` figure
        that is the deduplicated union of the unapproved layers — never their
        sum. `approved` is reported apart from all of it: authorised money is
        not exposure, it is baseline, and M4 already owns it.
        """
        Risk = self.env['realestate.construction.risk']
        Issue = self.env['realestate.construction.issue']
        Event = self.env['realestate.construction.change.event']
        Claim = self.env['realestate.construction.claim']

        risks = Risk.search([
            ('project_id', '=', project.id),
            ('state', 'not in', ('closed',)),
        ])
        issues = Issue.search([
            ('project_id', '=', project.id),
            ('state', 'not in', ('closed', 'cancelled')),
        ])
        events = Event.search([
            ('project_id', '=', project.id),
            ('state', 'not in', ('rejected', 'cancelled', 'void')),
        ])
        claims = Claim.search([
            ('project_id', '=', project.id),
            ('state', 'in', ('notice', 'preparing', 'submitted',
                             'under_review', 'assessed',
                             'determination_pending', 'determined',
                             'settlement', 'disputed')),
        ])

        # ---- Walk the ladder from the top. A risk whose issue produced a
        # ---- change event is represented by the change event, once.
        claimed_events = set()
        for claim in claims:
            claimed_events.update(claim.change_event_ids.ids)
            if claim.change_order_id:
                claimed_events.update(
                    claim.change_order_id.change_event_id.ids
                    if 'change_event_id' in claim.change_order_id._fields
                    else [])

        superseded_issues = {e.source_id for e in events
                             if e.source_model ==
                             'realestate.construction.issue' and e.source_id}
        superseded_risks = set()
        for issue in issues:
            if issue.change_event_id and issue.source_risk_id:
                superseded_risks.add(issue.source_risk_id.id)
        for risk in risks:
            if risk.change_event_ids:
                superseded_risks.add(risk.id)
            if risk.issue_id:
                # The issue represents it from here on.
                superseded_risks.add(risk.id)

        live_events = events.filtered(lambda e: e.id not in claimed_events)
        live_issues = issues.filtered(
            lambda i: i.id not in superseded_issues and not i.change_event_id)
        live_risks = risks.filtered(
            lambda r: r.id not in superseded_risks
            and r.state != 'materialised')

        claim_amount = sum(claims.mapped('claimed_cost'))
        change_amount = sum(live_events.mapped('estimated_cost_impact'))
        issue_amount = sum(live_issues.mapped('estimated_cost_impact'))
        risk_amount = sum(
            live_risks.filtered('include_in_forecast').mapped(
                'cost_exposure_likely'))
        risk_identified = sum(live_risks.mapped('cost_exposure_likely'))

        return {
            'project_id': project.id,
            'currency_id': project.currency_id.id,

            # Each layer, deduplicated against the ones above it.
            'claim_only': claim_amount,
            'change_only': change_amount,
            'issue_only': issue_amount,
            'risk_only': risk_amount,

            # Everything identified as a risk, whether or not a cost
            # controller chose to carry it. Reported separately precisely so
            # nobody mistakes "identified" for "included".
            'risk_identified': risk_identified,

            # The union of the unapproved layers. Not a sum of the four
            # figures above plus their superseded ancestors.
            'potential_commercial': (claim_amount + change_amount
                                     + issue_amount + risk_amount),

            'counts': {
                'risks': len(live_risks),
                'issues': len(live_issues),
                'change_events': len(live_events),
                'claims': len(claims),
            },
        }

    @api.model
    def explain(self, project):
        """One sentence for a dashboard, saying what the number is not."""
        exposure = self.for_project(project)
        return _(
            "Potential commercial exposure %(amount)s across %(claims)s "
            "claim(s), %(events)s change event(s), %(issues)s issue(s) and "
            "%(risks)s carried risk(s). Each underlying event is counted "
            "once, at the furthest stage it has reached. Authorised change "
            "is not included — it is in the budget.",
            amount=project.currency_id.format(
                exposure['potential_commercial']),
            claims=exposure['counts']['claims'],
            events=exposure['counts']['change_events'],
            issues=exposure['counts']['issues'],
            risks=exposure['counts']['risks'])


class ConstructionClaimKpi(models.AbstractModel):
    """The M8 KPIs. Every one of them drills to the records behind it."""
    _name = 'realestate.construction.claim.kpi'
    _description = 'Claims, Delay, Risk and Issue KPIs'

    @api.model
    def for_project(self, project):
        Claim = self.env['realestate.construction.claim']
        Delay = self.env['realestate.construction.delay.event']
        Risk = self.env['realestate.construction.risk']
        Issue = self.env['realestate.construction.issue']
        Notice = self.env['realestate.construction.notice']
        today = fields.Date.context_today(self)
        base = [('project_id', '=', project.id)]

        open_claims = Claim.search(base + [
            ('state', 'in', ['notice', 'preparing', 'submitted',
                             'under_review', 'assessed',
                             'determination_pending', 'determined',
                             'settlement', 'disputed'])])
        awaiting = open_claims.filtered(
            lambda c: c.state in ('submitted', 'under_review'))
        ages = [c.age_days for c in open_claims if c.age_days]

        packages = self.env[
            'realestate.construction.contract.package'].search(base)
        risks = Risk.search(base + [('state', '!=', 'closed')])
        issues = Issue.search(base + [('state', 'not in',
                                       ('closed', 'cancelled'))])
        delays = Delay.search(base + [('state', 'not in',
                                       ('closed', 'void'))])
        unreviewed = delays.filtered(
            lambda d: not d.notice_ids and not d.claim_ids)

        return {
            'project_id': project.id,
            'currency_id': project.currency_id.id,

            'open_claims': len(open_claims),
            'claimed_cost': sum(open_claims.mapped('claimed_cost')),
            'determined_cost': sum(open_claims.mapped('determined_cost')),
            'claims_awaiting_response': len(awaiting),
            'average_claim_age_days': round(sum(ages) / len(ages), 1)
            if ages else 0.0,
            'pending_eot_days': sum(packages.mapped('claimed_eot_days')),
            'approved_eot_days': sum(packages.mapped('approved_eot_days')),

            'open_delay_events': len(delays),
            'ongoing_delays': len(delays.filtered('is_ongoing')),
            'delays_without_notice_review': len(unreviewed),
            'late_notices': Notice.search_count(base + [('is_late', '=', True)]),

            'open_risks': len(risks),
            'high_risks': len(risks.filtered(
                lambda r: r.severity in ('high', 'critical'))),
            'risks_without_mitigation': len(risks.filtered(
                lambda r: not r.has_mitigation)),
            'overdue_mitigation_actions': sum(
                risks.mapped('overdue_action_count')),
            'selected_risk_exposure': sum(risks.filtered(
                'include_in_forecast').mapped('cost_exposure_likely')),

            'open_issues': len(issues),
            'critical_issues': len(issues.filtered(
                lambda i: i.priority in ('high', 'critical'))),
            'overdue_issues': len(issues.filtered('is_overdue')),

            # Named so nobody mistakes it for the sum of the four registers.
            'exposure': self.env[
                'realestate.construction.exposure'].for_project(project),
        }
