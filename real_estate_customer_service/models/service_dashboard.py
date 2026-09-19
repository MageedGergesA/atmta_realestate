# -*- coding: utf-8 -*-
"""Customer Service dashboard: the ticket queue, the SLA and the backlog trend."""

from datetime import datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, fields, models
from odoo.tools.misc import format_date

OPEN_STATES = ('new', 'assigned', 'in_progress', 'waiting')


class ServiceDashboard(models.AbstractModel):
    _name = 'realestate.service.dashboard'
    _inherit = 'atmta.dashboard.provider'
    _description = 'Customer Service Dashboard'

    def _dashboard_title(self):
        return _("Customer Service")

    def _dashboard_supports_scope(self):
        return True

    def _dashboard_sections(self, scope):
        Ticket = 'realestate.customer.ticket'
        now = fields.Datetime.now()
        open_ = [('state', 'in', OPEN_STATES)]
        month_start = datetime.combine(self._today().replace(day=1), time.min)
        return [
            {'id': 'queue', 'title': _("Ticket Queue"), 'icon': 'fa-inbox', 'tiles': [
                {'key': 'open', 'label': _("Open Tickets"), 'model': Ticket,
                 'domain': open_, 'user_field': 'assignee_id'},
                {'key': 'unassigned', 'label': _("Unassigned"), 'model': Ticket,
                 'domain': open_ + [('assignee_id', '=', False)], 'warning_above': 0,
                 'hint': _("Open tickets nobody is working on yet.")},
                {'key': 'urgent', 'label': _("Urgent"), 'model': Ticket,
                 'domain': open_ + [('priority', '=', '3')], 'user_field': 'assignee_id',
                 'warning_above': 0},
                {'key': 'waiting', 'label': _("Waiting on Customer"), 'model': Ticket,
                 'domain': [('state', '=', 'waiting')], 'user_field': 'assignee_id'},
            ]},
            {'id': 'sla', 'title': _("Service Level"), 'icon': 'fa-clock-o', 'tiles': [
                {'key': 'breached', 'label': _("SLA Breached"), 'model': Ticket,
                 'domain': open_ + [('sla_breached', '=', True)], 'user_field': 'assignee_id',
                 'warning_above': 0},
                {'key': 'due_24h', 'label': _("SLA Due in 24 Hours"), 'model': Ticket,
                 'domain': open_ + [('sla_breached', '=', False), ('sla_deadline', '!=', False),
                                    ('sla_deadline', '<=', now + timedelta(hours=24))],
                 'user_field': 'assignee_id'},
                {'key': 'resolved_month', 'label': _("Resolved This Month"), 'model': Ticket,
                 'domain': [('resolved_on', '>=', month_start), ('state', 'in', ('resolved', 'closed'))],
                 'user_field': 'assignee_id'},
            ]},
        ]

    def _dashboard_charts(self, scope):
        Ticket = self.env['realestate.customer.ticket']
        if not self._can_read(Ticket._name):
            return []
        mine = [('assignee_id', '=', self.env.uid)] if scope == 'mine' else []
        open_ = [('state', 'in', OPEN_STATES)] + mine

        by_category = Ticket._read_group(open_, groupby=['category_id'], aggregates=['__count'])
        no_category = _("No Category")
        categories = {
            'key': 'open_by_category',
            'title': _("Open Tickets by Category"),
            'subtitle': _("Where the queue is coming from"),
            'type': 'bar',
            'labels': [category.display_name if category else no_category for category, _count in by_category],
            'series': [{'name': 'open', 'label': _("Open"), 'data': [count for _category, count in by_category]}],
            'drill': [{
                'key': f'category_{category.id}', 'label': category.display_name or no_category,
                'model': Ticket._name,
                'domain': open_ + [('category_id', '=', category.id or False)],
            } for category, _count in by_category],
        }

        first = self._today().replace(day=1) - relativedelta(months=5)
        labels, opened, resolved, drill = [], [], [], []
        for offset in range(6):
            start = datetime.combine(first + relativedelta(months=offset), time.min)
            end = start + relativedelta(months=1)
            labels.append(format_date(self.env, start.date(), date_format='MMM y'))
            period = [('create_date', '>=', start), ('create_date', '<', end)] + mine
            opened.append(Ticket.search_count(period))
            # A cancelled ticket keeps its resolved date but was not resolved.
            resolved.append(Ticket.search_count(
                [('resolved_on', '>=', start), ('resolved_on', '<', end),
                 ('state', 'in', ('resolved', 'closed'))] + mine))
            drill.append({'key': f'month_{offset}', 'label': labels[-1],
                          'model': Ticket._name, 'domain': period})
        trend = {
            'key': 'opened_vs_resolved',
            'title': _("Opened vs Resolved"),
            'subtitle': _("The last six months"),
            'type': 'bar',
            'labels': labels,
            'series': [
                {'name': 'opened', 'label': _("Opened"), 'data': opened},
                {'name': 'resolved', 'label': _("Resolved"), 'data': resolved},
            ],
            'drill': drill,
        }
        return [categories, trend]

    def _dashboard_quick_actions(self):
        return [
            {'key': 'new_ticket', 'label': _("New Ticket"), 'icon': 'fa-plus',
             'model': 'realestate.customer.ticket'},
            {'key': 'tickets', 'label': _("All Tickets"), 'icon': 'fa-list',
             'action': 'real_estate_customer_service.action_customer_ticket'},
        ]
