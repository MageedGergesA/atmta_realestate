# -*- coding: utf-8 -*-
"""The Construction Overview payload.

Deliberately NOT built on `realestate.construction.dashboard`, which is
documented as deprecated and says so itself: it aggregates across every
project and every company, calls the sum of milestone budgets "total budget"
when M2 made the baselined budget authoritative, and calls manual cost lines
"actual" when actual means posted ledger cost. Extending that would spread
three wrong numbers onto a prettier screen.

This reads the operational sources directly -- the programme, the packages,
quality and information flow -- and leaves money to the Control Tower, which
is the canonical financial surface. Where this screen shows a figure the
tower also shows, it is computed the same way for the same reason: two
screens disagreeing about progress is worse than one screen not showing it.
"""

import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MILESTONE_LIVE = ('not_started', 'in_progress', 'delayed')
PACKAGE_LIVE = ('awarded', 'active', 'substantially_complete')
RFI_OPEN = ('open', 'under_review', 'reopened')
NCR_OPEN = ('draft', 'open', 'in_progress', 'pending_verification')
TREND_WEEKS = 8


class ConstructionOverview(models.AbstractModel):
    _name = 'realestate.construction.overview'
    _description = 'Construction Overview'

    @api.model
    def _can_read(self, model_name):
        return model_name in self.env and self.env[model_name].has_access('read')

    @api.model
    def _today(self):
        return fields.Date.context_today(self)

    # ==================================================================
    @api.model
    def get_overview(self, scope='team', filters=None):
        filters = dict(filters or {})
        today = self._today()
        company = self.env.company
        return {
            'title': _("Construction"),
            'company_name': company.display_name,
            'currency_id': company.currency_id.id,
            'as_of': today.isoformat(),
            'scope': scope,
            'supports_scope': False,
            'quick_actions': self._quick_actions(),
            'filters': self._filters(),
            'kpis': self._kpis(today, filters),
            'programme': self._programme(filters),
            'quality': self._quality(filters),
            'site_activity': self._site_activity(today, filters),
            'alerts': self._attention(today, filters),
            'work_queue': self._work_queue(today),
            'packages_table': self._packages_table(filters),
            'milestones_table': self._milestones_table(today, filters),
            'map': self._site_map(filters),
        }

    # ------------------------------------------------------------------
    @api.model
    def _filters(self):
        filters = []
        if self._can_read('realestate.project'):
            projects = self.env['realestate.project'].search(
                [('state', 'in', ('planning', 'construction', 'handover'))], limit=40)
            if projects:
                filters.append({
                    'key': 'project_id', 'label': _("Project"), 'icon': 'fa-building',
                    'all_label': _("All Projects"),
                    'options': [{'key': p.id, 'label': p.display_name} for p in projects],
                })
        if self._can_read('realestate.contractor'):
            contractors = self.env['realestate.contractor'].search([], limit=30)
            if contractors:
                filters.append({
                    'key': 'contractor_id', 'label': _("Contractor"),
                    'icon': 'fa-hard-hat' if False else 'fa-users',
                    'all_label': _("All Contractors"),
                    'options': [{'key': c.id, 'label': c.display_name}
                                for c in contractors],
                })
        return filters

    @api.model
    def _project_domain(self, filters):
        return ([('project_id', '=', int(filters['project_id']))]
                if filters.get('project_id') else [])

    @api.model
    def _contractor_domain(self, filters):
        return ([('contractor_id', '=', int(filters['contractor_id']))]
                if filters.get('contractor_id') else [])

    # ==================================================================
    @api.model
    def _kpis(self, today, filters):
        kpis = []
        base = self._project_domain(filters) + self._contractor_domain(filters)

        if self._can_read('realestate.construction.milestone'):
            Milestone = self.env['realestate.construction.milestone']
            milestones = Milestone.search(base + [('state', '!=', 'cancelled')])
            # Weighted, not a plain average: a two-week mobilisation and an
            # eight-month superstructure are not worth the same, and averaging
            # their percentages says they are. Same formula as the Control
            # Tower's physical progress, deliberately.
            weight = sum(milestones.mapped('weight')) or 0.0
            earned = sum(m.weight * (m.completion_percentage or 0.0) / 100.0
                         for m in milestones)
            kpis.append({
                'key': 'physical_progress', 'label': _("Physical Progress"),
                'icon': 'fa-cubes', 'tone': 'success',
                'value': round(earned / weight * 100.0, 1) if weight else 0.0,
                'format': 'percent', 'higher_is_better': True,
                'hint': _("Milestone weight multiplied by completion. Weighted, "
                          "because a two-week mobilisation and an eight-month "
                          "superstructure are not worth the same."),
                'drill': True,
            })
            delayed = Milestone.search_count(base + [('state', '=', 'delayed')])
            kpis.append({
                'key': 'milestones_delayed', 'label': _("Milestones Delayed"),
                'icon': 'fa-clock-o', 'tone': 'danger',
                'value': delayed, 'format': 'integer',
                'higher_is_better': False,
                'warning': bool(delayed), 'warning_label': _("Behind programme"),
                'hint': _("Milestones the site has flagged as slipping."),
                'drill': True,
            })

        if self._can_read('realestate.construction.contract.package'):
            Package = self.env['realestate.construction.contract.package']
            kpis.append({
                'key': 'packages_live', 'label': _("Packages on Site"),
                'icon': 'fa-file-text-o', 'tone': 'primary',
                'value': Package.search_count(
                    self._project_domain(filters) + self._contractor_domain(filters)
                    + [('state', 'in', list(PACKAGE_LIVE))]),
                'format': 'integer', 'higher_is_better': True,
                'hint': _("Awarded, active or substantially complete."),
                'drill': True,
            })

        if self._can_read('realestate.construction.ncr'):
            Ncr = self.env['realestate.construction.ncr']
            open_ncr = Ncr.search(base + [('state', 'in', list(NCR_OPEN))])
            severe = len(open_ncr.filtered(
                lambda n: n.severity in ('major', 'critical')))
            kpis.append({
                'key': 'ncr_open', 'label': _("Open Non-Conformances"),
                'icon': 'fa-exclamation-triangle', 'tone': 'warning',
                'value': len(open_ncr), 'format': 'integer',
                'higher_is_better': False,
                'warning': bool(severe),
                'warning_label': _("%s major or critical", severe),
                'hint': _("Raised and not yet closed out."),
                'drill': True,
            })

        if self._can_read('realestate.construction.rfi'):
            Rfi = self.env['realestate.construction.rfi']
            overdue = Rfi.search_count(
                base + [('state', 'in', list(RFI_OPEN)),
                        ('required_response_date', '<', today)])
            kpis.append({
                'key': 'rfi_overdue', 'label': _("RFIs Past Due"),
                'icon': 'fa-question-circle', 'tone': 'danger',
                'value': overdue, 'format': 'integer',
                'higher_is_better': False,
                'warning': bool(overdue),
                'warning_label': _("Site is blocked"),
                'hint': _("Questions the site asked that nobody has answered "
                          "by the date they needed it."),
                'drill': True,
            })

        if self._can_read('realestate.construction.daily.report'):
            Report = self.env['realestate.construction.daily.report']
            week_start = today - timedelta(days=today.weekday())
            kpis.append({
                'key': 'reports_week', 'label': _("Daily Reports This Week"),
                'icon': 'fa-calendar-check-o', 'tone': 'info',
                'value': Report.search_count(
                    self._project_domain(filters)
                    + [('report_date', '>=', week_start),
                       ('report_date', '<=', today)]),
                'format': 'integer', 'higher_is_better': True,
                'hint': _("A missing daily report is a day nobody can "
                          "reconstruct later."),
                'drill': True,
            })
        return kpis

    # ==================================================================
    @api.model
    def _programme(self, filters):
        if not self._can_read('realestate.construction.milestone'):
            return {'segments': [], 'total': 0}
        Milestone = self.env['realestate.construction.milestone']
        base = self._project_domain(filters) + self._contractor_domain(filters)
        states = [('completed', _("Completed"), 'success'),
                  ('in_progress', _("In Progress"), 'primary'),
                  ('delayed', _("Delayed"), 'danger'),
                  ('not_started', _("Not Started"), 'neutral')]
        segments = [{'key': 'milestones_%s' % key, 'label': label, 'tone': tone,
                     'value': Milestone.search_count(base + [('state', '=', key)])}
                    for key, label, tone in states]
        segments = [s for s in segments if s['value']]
        return {'segments': segments, 'total': sum(s['value'] for s in segments),
                'total_label': _("Milestones")}

    @api.model
    def _quality(self, filters):
        """Open non-conformances by severity.

        Counting NCRs without their severity is the same mistake as counting
        arrears without their age: one critical and twenty minors is a very
        different site from twenty-one minors.
        """
        if not self._can_read('realestate.construction.ncr'):
            return {'segments': [], 'total': 0}
        Ncr = self.env['realestate.construction.ncr']
        base = (self._project_domain(filters) + self._contractor_domain(filters)
                + [('state', 'in', list(NCR_OPEN))])
        levels = [('critical', _("Critical"), 'danger'),
                  ('major', _("Major"), 'warning'),
                  ('minor', _("Minor"), 'info')]
        segments = [{'key': 'ncr_%s' % key, 'label': label, 'tone': tone,
                     'value': Ncr.search_count(base + [('severity', '=', key)])}
                    for key, label, tone in levels]
        segments = [s for s in segments if s['value']]
        return {'segments': segments, 'total': sum(s['value'] for s in segments),
                'total_label': _("Open NCRs")}

    @api.model
    def _site_activity(self, today, filters):
        """Daily reports filed each week, and how many days work stopped."""
        if not self._can_read('realestate.construction.daily.report'):
            return None
        Report = self.env['realestate.construction.daily.report']
        base = self._project_domain(filters)
        labels, filed, stopped = [], [], []
        for offset in range(TREND_WEEKS - 1, -1, -1):
            start = today - timedelta(days=today.weekday() + 7 * offset)
            end = start + timedelta(days=6)
            labels.append(start.strftime('%d %b'))
            window = base + [('report_date', '>=', start), ('report_date', '<=', end)]
            filed.append(Report.search_count(window))
            stopped.append(Report.search_count(
                window + [('weather_stopped_work', '=', True)]))
        return {'labels': labels, 'filed': filed, 'stopped': stopped}

    # ==================================================================
    @api.model
    def _attention(self, today, filters):
        rows = []
        base = self._project_domain(filters) + self._contractor_domain(filters)

        if self._can_read('realestate.construction.milestone'):
            Milestone = self.env['realestate.construction.milestone']
            rows.append({
                'key': 'milestones_delayed', 'label': _("Milestones delayed"),
                'sublabel': _("Flagged as slipping by the site"),
                'value': Milestone.search_count(base + [('state', '=', 'delayed')]),
                'severity': 'critical', 'icon': 'fa-clock-o'})
            rows.append({
                'key': 'milestones_overrun', 'label': _("Past their expected end date"),
                'sublabel': _("Still open after the date they were due"),
                'value': Milestone.search_count(
                    base + [('state', 'in', list(MILESTONE_LIVE)),
                            ('expected_end_date', '<', today)]),
                'severity': 'warning', 'icon': 'fa-calendar-times-o'})

        if self._can_read('realestate.construction.ncr'):
            rows.append({
                'key': 'ncr_critical', 'label': _("Critical non-conformances open"),
                'value': self.env['realestate.construction.ncr'].search_count(
                    base + [('state', 'in', list(NCR_OPEN)),
                            ('severity', '=', 'critical')]),
                'severity': 'critical', 'icon': 'fa-exclamation-triangle'})

        if self._can_read('realestate.construction.rfi'):
            rows.append({
                'key': 'rfi_overdue', 'label': _("RFIs past their response date"),
                'sublabel': _("The site is waiting on an answer"),
                'value': self.env['realestate.construction.rfi'].search_count(
                    base + [('state', 'in', list(RFI_OPEN)),
                            ('required_response_date', '<', today)]),
                'severity': 'critical', 'icon': 'fa-question-circle'})

        if self._can_read('realestate.construction.contract.package'):
            rows.append({
                'key': 'packages_overrun', 'label': _("Packages past completion date"),
                'value': self.env['realestate.construction.contract.package'].search_count(
                    self._project_domain(filters) + self._contractor_domain(filters)
                    + [('state', 'in', list(PACKAGE_LIVE)),
                       ('current_completion_date', '<', today)]),
                'severity': 'warning', 'icon': 'fa-file-text-o'})

        if self._can_read('realestate.construction.daily.report'):
            rows.append({
                'key': 'reports_stopped', 'label': _("Days work stopped, last 30"),
                'sublabel': _("Weather or site conditions"),
                'value': self.env['realestate.construction.daily.report'].search_count(
                    self._project_domain(filters)
                    + [('weather_stopped_work', '=', True),
                       ('report_date', '>=', today - timedelta(days=30))]),
                'icon': 'fa-cloud'})
        return rows

    @api.model
    def _work_queue(self, today):
        uid = self.env.uid
        rows = []
        if self._can_read('realestate.construction.ncr'):
            rows.append({
                'key': 'my_ncr', 'label': _("NCRs Assigned to Me"),
                'value': self.env['realestate.construction.ncr'].search_count(
                    [('assigned_to_id', '=', uid), ('state', 'in', list(NCR_OPEN))]),
                'severity': 'warning', 'icon': 'fa-exclamation-triangle'})
        if self._can_read('realestate.construction.rfi'):
            Rfi = self.env['realestate.construction.rfi']
            rows.append({
                'key': 'my_rfi', 'label': _("RFIs Assigned to Me"),
                'value': Rfi.search_count(
                    [('assigned_to_id', '=', uid), ('state', 'in', list(RFI_OPEN))]),
                'icon': 'fa-question-circle'})
            rows.append({
                'key': 'rfi_open', 'label': _("All Open RFIs"),
                'value': Rfi.search_count([('state', 'in', list(RFI_OPEN))]),
                'icon': 'fa-inbox'})
        if self._can_read('realestate.construction.daily.report'):
            rows.append({
                'key': 'reports_draft', 'label': _("Daily Reports to Submit"),
                'value': self.env['realestate.construction.daily.report'].search_count(
                    [('state', '=', 'draft')]),
                'icon': 'fa-calendar-check-o'})
        if self._can_read('realestate.construction.submittal'):
            rows.append({
                'key': 'submittals_pending', 'label': _("Submittals Awaiting Review"),
                'value': self.env['realestate.construction.submittal'].search_count(
                    [('state', 'in', ('submitted', 'under_review'))]),
                'icon': 'fa-folder-open-o'})
        if self._can_read('realestate.boq'):
            rows.append({
                'key': 'boq_draft', 'label': _("Bills of Quantities in Draft"),
                'value': self.env['realestate.boq'].search_count(
                    [('state', '=', 'draft')]),
                'icon': 'fa-list-ol'})
        return rows

    # ==================================================================
    @api.model
    def _packages_table(self, filters):
        if not self._can_read('realestate.construction.contract.package'):
            return []
        packages = self.env['realestate.construction.contract.package'].search(
            self._project_domain(filters) + self._contractor_domain(filters)
            + [('state', '!=', 'cancelled')], limit=12)
        labels = dict(packages._fields['state'].selection) if packages else {}
        rows = []
        for package in packages:
            rows.append({
                'id': package.id,
                'package': package.title or package.name,
                'contractor': package.contractor_id.display_name or '—',
                'value': package.tender_value,
                'retention': package.retention_pct,
                'status': labels.get(package.state, package.state),
                'status_tone': {'active': 'success', 'awarded': 'primary',
                                'tender': 'info', 'draft': 'neutral',
                                'substantially_complete': 'success',
                                'complete': 'success', 'closed': 'neutral',
                                }.get(package.state, 'info'),
            })
        return rows

    @api.model
    def _milestones_table(self, today, filters):
        """The programme, worst first.

        Sorted by how late it is rather than by sequence: a reader scanning
        this wants the slippage, not the running order they already know.
        """
        if not self._can_read('realestate.construction.milestone'):
            return []
        milestones = self.env['realestate.construction.milestone'].search(
            self._project_domain(filters) + self._contractor_domain(filters)
            + [('state', 'in', list(MILESTONE_LIVE))])
        rows = []
        for milestone in milestones:
            late = ((today - milestone.expected_end_date).days
                    if milestone.expected_end_date else 0)
            rows.append({
                'id': milestone.id,
                'milestone': milestone.name,
                'contractor': milestone.contractor_id.display_name or '—',
                'due': (fields.Date.to_string(milestone.expected_end_date)
                        if milestone.expected_end_date else '—'),
                'progress': milestone.completion_percentage,
                'status': (_("%s days late", late) if late > 0
                           else (_("Delayed") if milestone.state == 'delayed'
                                 else _("On programme"))),
                'status_tone': ('danger' if late > 0 or milestone.state == 'delayed'
                                else 'success'),
                '_late': late,
            })
        rows.sort(key=lambda row: -row['_late'])
        for row in rows:
            row.pop('_late', None)
        return rows[:10]

    @api.model
    def _site_map(self, filters):
        """Where the sites are, toned by whether the programme is slipping."""
        if not self._can_read('realestate.project'):
            return {'points': [], 'legend': []}
        domain = [('latitude', '!=', 0), ('longitude', '!=', 0),
                  ('state', 'in', ('planning', 'construction', 'handover'))]
        if filters.get('project_id'):
            domain.append(('id', '=', int(filters['project_id'])))
        projects = self.env['realestate.project'].search(domain, limit=120)
        Milestone = self.env['realestate.construction.milestone']
        can_read = self._can_read('realestate.construction.milestone')

        points, tally = [], {}
        for project in projects:
            delayed = (Milestone.search_count(
                [('project_id', '=', project.id), ('state', '=', 'delayed')])
                if can_read else 0)
            tone = 'danger' if delayed else 'success'
            tally[tone] = tally.get(tone, 0) + 1
            points.append({
                'id': project.id,
                'label': project.display_name,
                'sublabel': (_("%s milestone(s) delayed", delayed) if delayed
                             else _("On programme")),
                'lat': project.latitude, 'lng': project.longitude,
                'tone': tone, 'boundary': [],
            })
        legend = [{'tone': 'success', 'label': _("On programme"),
                   'count': tally.get('success', 0)},
                  {'tone': 'danger', 'label': _("Slipping"),
                   'count': tally.get('danger', 0)}]
        return {'points': points, 'legend': [row for row in legend if row['count']]}

    # ==================================================================
    @api.model
    def _quick_actions(self):
        actions = []
        if self._can_read('realestate.construction.daily.report'):
            actions.append({'key': 'new_report', 'label': _("Daily Report"),
                            'icon': 'fa-plus'})
        if self._can_read('realestate.construction.ncr'):
            actions.append({'key': 'ncrs', 'label': _("NCRs"),
                            'icon': 'fa-exclamation-triangle'})
        if self._can_read('realestate.construction.rfi'):
            actions.append({'key': 'rfis', 'label': _("RFIs"),
                            'icon': 'fa-question-circle'})
        return actions

    @api.model
    def _targets(self, today):
        return {
            'physical_progress': (_("Milestones"),
                                  'realestate.construction.milestone',
                                  [('state', '!=', 'cancelled')]),
            'milestones_delayed': (_("Delayed Milestones"),
                                   'realestate.construction.milestone',
                                   [('state', '=', 'delayed')]),
            'milestones_overrun': (_("Milestones Past Due"),
                                   'realestate.construction.milestone',
                                   [('state', 'in', list(MILESTONE_LIVE)),
                                    ('expected_end_date', '<', today)]),
            'packages_live': (_("Packages on Site"),
                              'realestate.construction.contract.package',
                              [('state', 'in', list(PACKAGE_LIVE))]),
            'packages_overrun': (_("Packages Past Completion"),
                                 'realestate.construction.contract.package',
                                 [('state', 'in', list(PACKAGE_LIVE)),
                                  ('current_completion_date', '<', today)]),
            'ncr_open': (_("Open Non-Conformances"), 'realestate.construction.ncr',
                         [('state', 'in', list(NCR_OPEN))]),
            'ncr_critical': (_("Critical NCRs"), 'realestate.construction.ncr',
                             [('state', 'in', list(NCR_OPEN)),
                              ('severity', '=', 'critical')]),
            'rfi_overdue': (_("RFIs Past Due"), 'realestate.construction.rfi',
                            [('state', 'in', list(RFI_OPEN)),
                             ('required_response_date', '<', today)]),
            'rfi_open': (_("Open RFIs"), 'realestate.construction.rfi',
                         [('state', 'in', list(RFI_OPEN))]),
            'my_ncr': (_("My NCRs"), 'realestate.construction.ncr',
                       [('assigned_to_id', '=', self.env.uid),
                        ('state', 'in', list(NCR_OPEN))]),
            'my_rfi': (_("My RFIs"), 'realestate.construction.rfi',
                       [('assigned_to_id', '=', self.env.uid),
                        ('state', 'in', list(RFI_OPEN))]),
            'reports_week': (_("Daily Reports"),
                             'realestate.construction.daily.report', []),
            'reports_draft': (_("Daily Reports to Submit"),
                              'realestate.construction.daily.report',
                              [('state', '=', 'draft')]),
            'reports_stopped': (_("Days Work Stopped"),
                                'realestate.construction.daily.report',
                                [('weather_stopped_work', '=', True),
                                 ('report_date', '>=', today - timedelta(days=30))]),
            'submittals_pending': (_("Submittals Awaiting Review"),
                                   'realestate.construction.submittal',
                                   [('state', 'in', ('submitted', 'under_review'))]),
            'boq_draft': (_("Draft Bills of Quantities"), 'realestate.boq',
                          [('state', '=', 'draft')]),
        }

    @api.model
    def action_drill(self, key):
        today = self._today()
        target = self._targets(today).get(key)
        if not target and key.startswith('milestones_'):
            target = (_("Milestones"), 'realestate.construction.milestone',
                      [('state', '=', key[len('milestones_'):])])
        if not target and key.startswith('ncr_'):
            target = (_("Non-Conformances"), 'realestate.construction.ncr',
                      [('state', 'in', list(NCR_OPEN)),
                       ('severity', '=', key[len('ncr_'):])])
        if not target:
            raise UserError(_("Unknown dashboard figure '%s'.", key))
        name, model, domain = target
        if not self._can_read(model):
            raise UserError(_("These records are not available to you."))
        return {
            'type': 'ir.actions.act_window', 'name': name, 'res_model': model,
            'views': [[False, 'list'], [False, 'form']], 'view_mode': 'list,form',
            'domain': domain, 'context': {'create': False}, 'target': 'current',
        }

    @api.model
    def action_quick(self, key):
        targets = {
            'new_report': ('realestate.construction.daily.report', 'form'),
            'ncrs': ('realestate.construction.ncr', 'list'),
            'rfis': ('realestate.construction.rfi', 'list'),
        }
        target = targets.get(key)
        if not target or not self._can_read(target[0]):
            raise UserError(_("Unknown dashboard action '%s'.", key))
        model, mode = target
        return {
            'type': 'ir.actions.act_window', 'name': _("Construction"),
            'res_model': model,
            'views': ([[False, 'form']] if mode == 'form'
                      else [[False, 'list'], [False, 'form']]),
            'view_mode': 'form' if mode == 'form' else 'list,form',
            'target': 'current',
        }
