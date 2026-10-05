# -*- coding: utf-8 -*-
"""Handover on the shared dashboard provider.

A smaller app than Rental or Development, so it gets the shared *provider*
rather than a hand-written screen: the same cards, filter bar, alert list and
work queue, declared as tile specs instead of a bespoke component. Same look,
a fifth of the code, and a new tile is four lines rather than a template
change.
"""

from datetime import timedelta

from odoo import _, fields, models

SNAG_OPEN = ('open', 'assigned', 'in_progress')
HANDOVER_LIVE = ('scheduled', 'inspection', 'snagging')


class HandoverProvider(models.AbstractModel):
    _name = 'realestate.handover.provider'
    _inherit = 'atmta.dashboard.provider'
    _description = 'Handover Dashboard'

    def _dashboard_title(self):
        return _("Handover")

    def _dashboard_sections(self, scope):
        today = self._today()
        Handover = 'realestate.handover'
        Snag = 'realestate.snagging.issue'
        return [{
            'id': 'handover', 'title': _("Handover"), 'icon': 'fa-key', 'tiles': [
                {'key': 'ho_scheduled', 'label': _("Scheduled"), 'model': Handover,
                 'domain': [('state', '=', 'scheduled')],
                 'icon': 'fa-calendar', 'tone': 'primary'},
                {'key': 'ho_inspection', 'label': _("In Inspection"), 'model': Handover,
                 'domain': [('state', '=', 'inspection')],
                 'icon': 'fa-search', 'tone': 'info'},
                {'key': 'ho_snagging', 'label': _("In Snagging"), 'model': Handover,
                 'domain': [('state', '=', 'snagging')],
                 'icon': 'fa-wrench', 'tone': 'warning'},
                {'key': 'ho_completed', 'label': _("Handed Over"), 'model': Handover,
                 'domain': [('state', '=', 'completed')],
                 'icon': 'fa-check-circle', 'tone': 'success',
                 # Handing a unit over is the event; a monthly trend is a real
                 # series rather than a reconstruction of a current state.
                 'trend_field': 'create_date', 'trend_granularity': 'month'},
                {'key': 'snags_open', 'label': _("Open Snags"), 'model': Snag,
                 'domain': [('state', 'in', list(SNAG_OPEN))],
                 'icon': 'fa-exclamation-triangle', 'tone': 'warning',
                 'higher_is_better': False,
                 'hint': _("Reported and not yet resolved or verified.")},
                {'key': 'snags_critical', 'label': _("Critical Snags"), 'model': Snag,
                 'domain': [('state', 'in', list(SNAG_OPEN)),
                            ('severity', '=', 'critical')],
                 'icon': 'fa-times-circle', 'tone': 'danger',
                 'higher_is_better': False, 'warning_above': 0,
                 'warning_label': _("Blocks handover"),
                 'hint': _("A critical snag is a defect the buyer should not "
                           "be asked to accept.")},
            ]},
            {'id': 'quality', 'title': _("Build Quality"), 'icon': 'fa-star',
             'subtitle': _("What the snag list says about what was built"),
             'tiles': [
                 {'key': 'snags_per_handover', 'label': _("Snags per Handover"),
                  'value': self._snags_per_handover(), 'format': 'decimal',
                  'icon': 'fa-list-ol', 'tone': 'info',
                  'higher_is_better': False,
                  'hint': _("Total snags raised divided by handovers started. "
                            "A count of snags alone rises simply because more "
                            "units were inspected; this does not.")},
                 {'key': 'snags_closure', 'label': _("Snag Closure Rate"),
                  'value': self._closure_rate(), 'format': 'percent',
                  'icon': 'fa-check', 'tone': 'success',
                  'hint': _("Resolved or verified as a share of every snag "
                            "ever raised.")},
                 {'key': 'snags_stale', 'label': _("Snags Open Over 30 Days"),
                  'model': Snag,
                  'domain': [('state', 'in', list(SNAG_OPEN)),
                             ('reported_date', '<=', today - timedelta(days=30))],
                  'icon': 'fa-clock-o', 'tone': 'warning',
                  'higher_is_better': False,
                  'hint': _("Age is the honest measure of a snag list. A long "
                            "list that turns over is healthier than a short "
                            "one that never moves.")},
                 {'key': 'snags_verified', 'label': _("Verified Closed"),
                  'model': Snag, 'domain': [('state', '=', 'verified')],
                  'icon': 'fa-check-circle', 'tone': 'success',
                  'hint': _("Signed off by somebody other than the person who "
                            "fixed it.")},
                 {'key': 'warranties_active', 'label': _("Active Warranties"),
                  'model': 'realestate.warranty',
                  'domain': [('end_date', '>=', today)],
                  'icon': 'fa-shield', 'tone': 'primary'},
                 {'key': 'warranties_expiring', 'label': _("Warranties Expiring 90d"),
                  'model': 'realestate.warranty',
                  'domain': [('end_date', '>=', today),
                             ('end_date', '<=', today + timedelta(days=90))],
                  'icon': 'fa-hourglass-end', 'tone': 'warning',
                  'higher_is_better': False},
             ]}]

    # ------------------------------------------------------------------
    def _snags_per_handover(self):
        """Snags raised per handover started.

        A raw snag count rises simply because more units were inspected, so
        it says nothing about build quality. This does.
        """
        if not (self._can_read('realestate.snagging.issue')
                and self._can_read('realestate.handover')):
            return 0.0
        handovers = self.env['realestate.handover'].search_count([])
        if not handovers:
            return 0.0
        snags = self.env['realestate.snagging.issue'].search_count([])
        return round(snags / handovers, 1)

    def _closure_rate(self):
        if not self._can_read('realestate.snagging.issue'):
            return 0.0
        Snag = self.env['realestate.snagging.issue']
        total = Snag.search_count([])
        if not total:
            return 0.0
        closed = Snag.search_count([('state', 'in', ('resolved', 'verified'))])
        return round(closed * 100.0 / total, 1)

    # ------------------------------------------------------------------
    def _dashboard_charts(self, scope):
        Snag = 'realestate.snagging.issue'
        if not self._can_read(Snag):
            return []
        Model = self.env[Snag]
        charts = []

        severities = dict(Model._fields['severity']._description_selection(self.env))
        open_counts = dict(Model._read_group(
            [('state', 'in', list(SNAG_OPEN))],
            groupby=['severity'], aggregates=['__count']))
        keys = [k for k in severities if open_counts.get(k)]
        if keys:
            charts.append({
                'key': 'snags_by_severity', 'title': _("Open Snags by Severity"),
                # Counting snags without their severity is the same mistake as
                # counting arrears without their age.
                'subtitle': _("One critical is not the same as ten minors"),
                'type': 'doughnut', 'icon': 'fa-exclamation-triangle',
                'span': 'o_ad_col_6',
                'labels': [severities[k] for k in keys],
                'series': [{'name': 'count', 'label': _("Snags"),
                            'data': [open_counts[k] for k in keys]}],
                'drill': [{'key': 'snags_%s' % k, 'label': severities[k],
                           'model': Snag,
                           'domain': [('state', 'in', list(SNAG_OPEN)),
                                      ('severity', '=', k)]} for k in keys],
            })

        # The snag funnel. Where work piles up is more useful than how much of
        # it there is: a wall of "resolved" means nobody is verifying.
        states = dict(Model._fields['state']._description_selection(self.env))
        state_counts = dict(Model._read_group([], groupby=['state'],
                                              aggregates=['__count']))
        order = ['open', 'assigned', 'in_progress', 'resolved', 'verified',
                 'rejected']
        funnel = [k for k in order if state_counts.get(k)]
        if funnel:
            charts.append({
                'key': 'snag_funnel', 'title': _("Snag Funnel"),
                'subtitle': _("Where the work piles up — a wall of resolved "
                              "means nobody is verifying"),
                'type': 'bar', 'icon': 'fa-filter', 'span': 'o_ad_col_6',
                'labels': [states[k] for k in funnel],
                'series': [{'name': 'count', 'label': _("Snags"),
                            'data': [state_counts[k] for k in funnel]}],
                'drill': [{'key': 'snagstate_%s' % k, 'label': states[k],
                           'model': Snag, 'domain': [('state', '=', k)]}
                          for k in funnel],
            })
        return charts

    # ------------------------------------------------------------------
    def _dashboard_tables(self, scope):
        tables = []
        if self._can_read('realestate.handover'):
            today = self._today()
            handovers = self.env['realestate.handover'].search(
                [('state', 'in', list(HANDOVER_LIVE))], limit=15)
            rows = []
            for handover in handovers:
                snags = (handover.snagging_issue_ids
                         if 'snagging_issue_ids' in handover._fields
                         else self.env['realestate.snagging.issue'].browse())
                open_snags = snags.filtered(lambda s: s.state in SNAG_OPEN)
                critical = open_snags.filtered(lambda s: s.severity == 'critical')
                contract = handover.sale_contract_id
                rows.append({
                    'id': handover.id,
                    'unit': (contract.property_id.display_name
                             if contract and contract.property_id else handover.display_name),
                    'buyer': (contract.partner_id.display_name
                              if contract and contract.partner_id else '—'),
                    'stage': dict(handover._fields['state']._description_selection(
                        self.env)).get(handover.state, handover.state),
                    'stage_tone': {'scheduled': 'primary', 'inspection': 'info',
                                   'snagging': 'warning'}.get(handover.state, 'neutral'),
                    'open_snags': len(open_snags),
                    'critical': len(critical),
                    'status': (_("Blocked") if critical
                               else (_("Snagging") if open_snags else _("Clear"))),
                    'status_tone': ('danger' if critical
                                    else ('warning' if open_snags else 'success')),
                })
            # Worst first: a unit with a critical snag is the one that
            # will miss its date.
            rows.sort(key=lambda row: (-row['critical'], -row['open_snags']))
            tables.append({
                'key': 'handovers_live', 'title': _("Handovers in Progress"),
                'icon': 'fa-key', 'span': 'o_ad_col_12',
                'subtitle': _("Worst first — a critical snag is what misses the date"),
                'empty_text': _("No handover is in progress."),
                'model': 'realestate.handover',
                'columns': [
                    {'key': 'unit', 'label': _("Unit")},
                    {'key': 'buyer', 'label': _("Buyer")},
                    {'key': 'stage', 'label': _("Stage"), 'type': 'badge'},
                    {'key': 'open_snags', 'label': _("Open Snags"), 'numeric': True},
                    {'key': 'critical', 'label': _("Critical"), 'numeric': True},
                    {'key': 'status', 'label': _("Status"), 'type': 'badge'},
                ],
                'rows': rows,
            })

        if self._can_read('realestate.snagging.issue'):
            today = self._today()
            snags = self.env['realestate.snagging.issue'].search(
                [('state', 'in', list(SNAG_OPEN))],
                order='reported_date', limit=12)
            severities = dict(self.env['realestate.snagging.issue']
                              ._fields['severity']._description_selection(self.env))
            rows = []
            for snag in snags:
                age = (today - snag.reported_date).days if snag.reported_date else 0
                rows.append({
                    'id': snag.id,
                    'snag': snag.description or snag.name,
                    'severity': severities.get(snag.severity, snag.severity),
                    'severity_tone': {'critical': 'danger', 'major': 'warning',
                                      'minor': 'neutral'}.get(snag.severity, 'neutral'),
                    'reported': str(snag.reported_date or '—'),
                    'age': age,
                    'status': (_("Overdue") if age > 30
                               else (_("Ageing") if age > 14 else _("Fresh"))),
                    'status_tone': ('danger' if age > 30
                                    else ('warning' if age > 14 else 'success')),
                })
            tables.append({
                'key': 'snags_oldest', 'title': _("Oldest Open Snags"),
                'icon': 'fa-clock-o', 'span': 'o_ad_col_12',
                'subtitle': _("Age is the honest measure of a snag list"),
                'empty_text': _("No snag is open."),
                'model': 'realestate.snagging.issue',
                'columns': [
                    {'key': 'snag', 'label': _("Defect")},
                    {'key': 'severity', 'label': _("Severity"), 'type': 'badge'},
                    {'key': 'reported', 'label': _("Reported")},
                    {'key': 'age', 'label': _("Days Open"), 'numeric': True},
                    {'key': 'status', 'label': _("Age"), 'type': 'badge'},
                ],
                'rows': rows,
            })
        return tables

    def _dashboard_alerts(self, scope):
        today = self._today()
        rows = []
        if self._can_read('realestate.snagging.issue'):
            Snag = self.env['realestate.snagging.issue']
            rows.append({
                'key': 'snags_critical', 'label': _("Critical snags open"),
                'sublabel': _("A defect the buyer should not be asked to accept"),
                'value': Snag.search_count([('state', 'in', list(SNAG_OPEN)),
                                            ('severity', '=', 'critical')]),
                'severity': 'critical', 'icon': 'fa-times-circle'})
            rows.append({
                'key': 'snags_stale', 'label': _("Snags open over 30 days"),
                'value': Snag.search_count([
                    ('state', 'in', list(SNAG_OPEN)),
                    ('reported_date', '<=', today - timedelta(days=30))]),
                'severity': 'warning', 'icon': 'fa-clock-o'})
        if self._can_read('realestate.handover'):
            rows.append({
                'key': 'ho_snagging', 'label': _("Handovers stuck in snagging"),
                'value': self.env['realestate.handover'].search_count(
                    [('state', '=', 'snagging')]),
                'severity': 'warning', 'icon': 'fa-wrench'})
        if self._can_read('realestate.warranty'):
            rows.append({
                'key': 'warranties_expiring', 'label': _("Warranties expiring in 90 days"),
                'value': self.env['realestate.warranty'].search_count(
                    [('end_date', '>=', today),
                     ('end_date', '<=', today + timedelta(days=90))]),
                'icon': 'fa-shield'})
        return rows

    def _dashboard_work_queue(self, scope):
        rows = []
        if self._can_read('realestate.snagging.issue'):
            Snag = self.env['realestate.snagging.issue']
            rows.append({'key': 'snags_unassigned', 'label': _("Snags Unassigned"),
                         'value': Snag.search_count([('state', '=', 'open')]),
                         'severity': 'warning', 'icon': 'fa-user-plus'})
            rows.append({'key': 'snags_resolved', 'label': _("Snags Awaiting Verification"),
                         'value': Snag.search_count([('state', '=', 'resolved')]),
                         'icon': 'fa-check-square-o'})
        if self._can_read('realestate.handover'):
            Handover = self.env['realestate.handover']
            rows.append({'key': 'ho_inspection', 'label': _("Inspections to Run"),
                         'value': Handover.search_count([('state', '=', 'inspection')]),
                         'icon': 'fa-search'})
            rows.append({'key': 'ho_scheduled', 'label': _("Handovers Scheduled"),
                         'value': Handover.search_count([('state', '=', 'scheduled')]),
                         'icon': 'fa-calendar'})
        return rows

    def _dashboard_quick_actions(self):
        return [{'key': 'handovers', 'label': _("Handovers"), 'icon': 'fa-key',
                 'model': 'realestate.handover'},
                {'key': 'snags', 'label': _("Snag List"), 'icon': 'fa-list',
                 'model': 'realestate.snagging.issue'}]
