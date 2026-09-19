# -*- coding: utf-8 -*-
"""Property Operations dashboard: handover, service and maintenance in one view.

Each section reads a model owned by another application and is shown only to the
roles that application grants; this module counts nothing it could not open.
"""

from datetime import timedelta

from odoo import _, fields, models

TICKET_OPEN = ('new', 'assigned', 'in_progress', 'waiting')
SNAG_OPEN = ('open', 'assigned', 'in_progress')
MAINTENANCE_OPEN = ('draft', 'scheduled', 'in_progress')


class OperationsDashboard(models.AbstractModel):
    _name = 'realestate.operations.dashboard'
    _inherit = 'atmta.dashboard.provider'
    _description = 'Property Operations Dashboard'

    def _dashboard_title(self):
        return _("Property Operations")

    def _dashboard_supports_scope(self):
        return True

    def _dashboard_sections(self, scope):
        today = self._today()
        now = fields.Datetime.now()
        Handover, Snag, Warranty = 'realestate.handover', 'realestate.snagging.issue', 'realestate.warranty'
        Ticket, Maintenance = 'realestate.customer.ticket', 'realestate.maintenance.request'
        snag_open = [('state', 'in', SNAG_OPEN)]
        ticket_open = [('state', 'in', TICKET_OPEN)]
        maintenance_open = [('state', 'in', MAINTENANCE_OPEN)]
        return [
            {'id': 'handover', 'title': _("Handover"), 'icon': 'fa-key', 'tiles': [
                {'key': 'handovers_week', 'label': _("Handovers This Week"), 'model': Handover,
                 'domain': [('state', '=', 'scheduled'), ('scheduled_date', '>=', now),
                            ('scheduled_date', '<=', now + timedelta(days=7))]},
                {'key': 'handovers_in_progress', 'label': _("In Inspection or Snagging"), 'model': Handover,
                 'domain': [('state', 'in', ('inspection', 'snagging'))]},
                {'key': 'critical_snags', 'label': _("Critical Snags Open"), 'model': Snag,
                 'domain': snag_open + [('severity', '=', 'critical')], 'warning_above': 0},
                {'key': 'overdue_snags', 'label': _("Snags Past Target Date"), 'model': Snag,
                 'domain': snag_open + [('target_resolution_date', '<', today)], 'warning_above': 0},
                {'key': 'warranties_expiring', 'label': _("Warranties Expiring in 30 Days"), 'model': Warranty,
                 'domain': [('state', '=', 'active'), ('end_date', '>=', today),
                            ('end_date', '<=', today + timedelta(days=30))]},
            ]},
            {'id': 'service', 'title': _("Service"), 'icon': 'fa-life-ring', 'tiles': [
                {'key': 'tickets_open', 'label': _("Open Tickets"), 'model': Ticket,
                 'domain': ticket_open, 'user_field': 'assignee_id'},
                {'key': 'tickets_unassigned', 'label': _("Unassigned Tickets"), 'model': Ticket,
                 'domain': ticket_open + [('assignee_id', '=', False)], 'warning_above': 0},
                {'key': 'tickets_breached', 'label': _("SLA Breached"), 'model': Ticket,
                 'domain': ticket_open + [('sla_breached', '=', True)], 'user_field': 'assignee_id',
                 'warning_above': 0},
            ]},
            {'id': 'maintenance', 'title': _("Maintenance"), 'icon': 'fa-wrench', 'tiles': [
                {'key': 'maintenance_open', 'label': _("Open Requests"), 'model': Maintenance,
                 'domain': maintenance_open, 'user_field': 'assigned_to'},
                {'key': 'maintenance_unscheduled', 'label': _("Not Scheduled Yet"), 'model': Maintenance,
                 'domain': [('state', '=', 'draft')], 'user_field': 'assigned_to'},
                {'key': 'maintenance_in_progress', 'label': _("In Progress"), 'model': Maintenance,
                 'domain': [('state', '=', 'in_progress')], 'user_field': 'assigned_to'},
                {'key': 'maintenance_late', 'label': _("Past Scheduled Date"), 'model': Maintenance,
                 'domain': [('state', '=', 'scheduled'), ('scheduled_date', '<', today)],
                 'user_field': 'assigned_to', 'warning_above': 0},
            ]},
        ]

    def _selection_chart(self, key, title, subtitle, model_name, domain, field_name, series_label):
        if not self._can_read(model_name):
            return None
        Model = self.env[model_name]
        labels_by_key = dict(Model._fields[field_name]._description_selection(self.env))
        counts = dict(Model._read_group(domain, groupby=[field_name], aggregates=['__count']))
        keys = [k for k in labels_by_key if counts.get(k)]
        return {
            'key': key, 'title': title, 'subtitle': subtitle, 'type': 'bar',
            'labels': [labels_by_key[k] for k in keys],
            'series': [{'name': 'count', 'label': series_label, 'data': [counts[k] for k in keys]}],
            'drill': [{'key': f'{key}_{k}', 'label': labels_by_key[k], 'model': model_name,
                       'domain': domain + [(field_name, '=', k)]} for k in keys],
        }

    def _dashboard_charts(self, scope):
        mine = [('assigned_to', '=', self.env.uid)] if scope == 'mine' else []
        charts = [
            self._selection_chart(
                'snags_by_severity', _("Open Snags by Severity"), _("What stands between a unit and its buyer"),
                'realestate.snagging.issue', [('state', 'in', SNAG_OPEN)], 'severity', _("Open snags")),
            self._selection_chart(
                'maintenance_by_state', _("Open Maintenance by Stage"), _("Where the requests are waiting"),
                'realestate.maintenance.request', [('state', 'in', MAINTENANCE_OPEN)] + mine, 'state',
                _("Requests")),
        ]
        return [chart for chart in charts if chart]

    def _dashboard_quick_actions(self):
        return [
            {'key': 'new_maintenance', 'label': _("New Maintenance Request"), 'icon': 'fa-plus',
             'model': 'realestate.maintenance.request'},
            {'key': 'handovers', 'label': _("Handovers"), 'icon': 'fa-key',
             'action': 'real_estate_handover.action_handover'},
            {'key': 'tickets', 'label': _("Tickets"), 'icon': 'fa-life-ring',
             'action': 'real_estate_customer_service.action_customer_ticket'},
        ]
