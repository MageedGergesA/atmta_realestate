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
HANDOVER_LIVE = ('scheduled', 'inspection', 'snagging')


class OperationsDashboard(models.AbstractModel):
    _name = 'realestate.operations.dashboard'
    _inherit = 'atmta.dashboard.provider'
    _description = 'Property Operations Dashboard'

    def _dashboard_title(self):
        return _("Property Operations")

    def _dashboard_supports_scope(self):
        return True

    # ------------------------------------------------------------------
    # The second fact on each card
    # ------------------------------------------------------------------
    def _counts(self, today, now):
        """Everything the context lines need, read once.

        A bare count answers "how many" and stops. What makes it judgeable is
        what it is out of, how old the worst one is, or what it is made of --
        so each figure is gathered here and spent on the cards below, rather
        than each card running its own queries.
        """
        c = {}
        if self._can_read('realestate.snagging.issue'):
            Snag = self.env['realestate.snagging.issue']
            open_snags = [('state', 'in', SNAG_OPEN)]
            c['snags_open'] = Snag.search_count(open_snags)
            c['snags_critical'] = Snag.search_count(
                open_snags + [('severity', '=', 'critical')])
            oldest = Snag.search(open_snags, order='reported_date', limit=1)
            c['snag_oldest_days'] = (
                (today - oldest.reported_date).days
                if oldest and oldest.reported_date else 0)
        if self._can_read('realestate.customer.ticket'):
            Ticket = self.env['realestate.customer.ticket']
            live = [('state', 'in', TICKET_OPEN)]
            c['tickets_open'] = Ticket.search_count(live)
            c['tickets_unassigned'] = Ticket.search_count(
                live + [('assignee_id', '=', False)])
            c['tickets_urgent'] = Ticket.search_count(live + [('priority', '=', '3')])
            c['tickets_breached'] = Ticket.search_count(live + [('sla_breached', '=', True)])
            oldest = Ticket.search(live + [('assignee_id', '=', False)],
                                   order='create_date', limit=1)
            c['ticket_unowned_days'] = (
                (now - oldest.create_date).days if oldest and oldest.create_date else 0)
            last_month_start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            c['resolved_last_month'] = Ticket.search_count(
                [('state', 'in', ('resolved', 'closed')),
                 ('resolved_on', '>=', last_month_start),
                 ('resolved_on', '<', today.replace(day=1))])
        if self._can_read('realestate.maintenance.request'):
            Maintenance = self.env['realestate.maintenance.request']
            live = [('state', 'in', MAINTENANCE_OPEN)]
            c['maint_open'] = Maintenance.search_count(live)
            c['maint_undated'] = Maintenance.search_count([('state', '=', 'draft')])
            late = Maintenance.search(
                [('state', '=', 'scheduled'), ('scheduled_date', '<', today)],
                order='scheduled_date', limit=1)
            c['maint_late_days'] = (
                (today - late.scheduled_date).days
                if late and late.scheduled_date else 0)
        if self._can_read('realestate.handover'):
            Handover = self.env['realestate.handover']
            c['handovers_live'] = Handover.search_count(
                [('state', 'in', list(HANDOVER_LIVE))])
            c['handovers_done'] = Handover.search_count([('state', '=', 'completed')])
        if self._can_read('realestate.warranty'):
            c['warranties_active'] = self.env['realestate.warranty'].search_count(
                [('end_date', '>=', today)])
        return c

    @staticmethod
    def _days(count, singular, plural):
        return singular if count == 1 else plural

    def _dashboard_sections(self, scope):
        today = self._today()
        now = fields.Datetime.now()
        c = self._counts(today, now)
        Handover, Snag, Warranty = 'realestate.handover', 'realestate.snagging.issue', 'realestate.warranty'
        Ticket, Maintenance = 'realestate.customer.ticket', 'realestate.maintenance.request'
        snag_open = [('state', 'in', SNAG_OPEN)]
        ticket_open = [('state', 'in', TICKET_OPEN)]
        maintenance_open = [('state', 'in', MAINTENANCE_OPEN)]
        return [
            {'id': 'handover', 'title': _("Handover"), 'icon': 'fa-key',
             'subtitle': _("Getting a finished unit to the person who bought it"),
             'tiles': [
                {'key': 'handovers_week', 'label': _("Handovers This Week"), 'model': Handover,
                 'context': _("%(live)s in progress · %(done)s handed over to date", live=c.get('handovers_live', 0), done=c.get('handovers_done', 0)),
                 'domain': [('state', '=', 'scheduled'), ('scheduled_date', '>=', now),
                            ('scheduled_date', '<=', now + timedelta(days=7))],
                 'icon': 'fa-calendar-check-o', 'tone': 'primary'},
                {'key': 'handovers_in_progress', 'label': _("In Inspection or Snagging"), 'model': Handover,
                 'context': _("%s critical snag(s) across them", c.get('snags_critical', 0)),
                 'domain': [('state', 'in', ('inspection', 'snagging'))],
                 'icon': 'fa-search', 'tone': 'info'},
                {'key': 'critical_snags', 'label': _("Critical Snags Open"), 'model': Snag,
                 'context': _("of %s open snag(s)", c.get('snags_open', 0)),
                 'domain': snag_open + [('severity', '=', 'critical')], 'warning_above': 0,
                 'icon': 'fa-times-circle', 'tone': 'danger', 'higher_is_better': False,
                 'warning_label': _("Blocks handover"),
                 'hint': _("A defect the buyer should not be asked to accept."),
                 'trend_open_field': 'reported_date',
                 'trend_close_field': 'resolved_date',
                 'trend_domain': [('severity', '=', 'critical')],
                 'trend_granularity': 'week', 'trend_buckets': 8},
                {'key': 'overdue_snags', 'label': _("Snags Past Target Date"), 'model': Snag,
                 'context': _("oldest open snag is %s day(s) old", c.get('snag_oldest_days', 0)),
                 'domain': snag_open + [('target_resolution_date', '<', today)], 'warning_above': 0,
                 'icon': 'fa-clock-o', 'tone': 'warning', 'higher_is_better': False,
                 'hint': _("Promised by a date that has been and gone.")},
                {'key': 'warranties_expiring', 'label': _("Warranties Expiring in 30 Days"), 'model': Warranty,
                 'context': _("of %s active warranty/ies", c.get('warranties_active', 0)),
                 'domain': [('state', '=', 'active'), ('end_date', '>=', today),
                            ('end_date', '<=', today + timedelta(days=30))],
                 'icon': 'fa-shield', 'tone': 'neutral', 'higher_is_better': False,
                 'hint': _("After this date the cost of a defect moves to the "
                           "owner.")},
            ]},
            {'id': 'service', 'title': _("Service"), 'icon': 'fa-life-ring',
             'subtitle': _("What residents and buyers have actually asked for"),
             'tiles': [
                # No sparkline. "Open tickets" is a STOCK: a ticket raised
                # three weeks ago may still be open today, so `create_date`
                # draws tickets *raised* per week and labels it as tickets
                # open -- a confident line that answers a different question.
                # The flow beside it, "Resolved This Month", is the one that
                # can honestly carry a trend.
                {'key': 'tickets_open', 'label': _("Open Tickets"), 'model': Ticket,
                 'context': _("%(un)s unassigned · %(urg)s urgent · %(sla)s past SLA", un=c.get('tickets_unassigned', 0), urg=c.get('tickets_urgent', 0), sla=c.get('tickets_breached', 0)),
                 'domain': ticket_open, 'user_field': 'assignee_id',
                 'icon': 'fa-ticket', 'tone': 'primary',
                 'higher_is_better': False,
                 'trend_open_field': 'create_date',
                 'trend_close_field': 'resolved_on',
                 'trend_granularity': 'week', 'trend_buckets': 8},
                {'key': 'tickets_unassigned', 'label': _("Unassigned Tickets"), 'model': Ticket,
                 'context': _("oldest has waited %s day(s) for an owner", c.get('ticket_unowned_days', 0)),
                 'domain': ticket_open + [('assignee_id', '=', False)], 'warning_above': 0,
                 'icon': 'fa-user-plus', 'tone': 'warning', 'higher_is_better': False,
                 'hint': _("Nobody owns these yet, so nobody is working on them.")},
                {'key': 'tickets_breached', 'label': _("SLA Breached"), 'model': Ticket,
                 'context': _("of %s open ticket(s)", c.get('tickets_open', 0)),
                 'domain': ticket_open + [('sla_breached', '=', True)], 'user_field': 'assignee_id',
                 'warning_above': 0, 'icon': 'fa-exclamation-triangle', 'tone': 'danger',
                 'higher_is_better': False,
                 'hint': _("Past the deadline that was promised to the customer.")},
                {'key': 'tickets_urgent', 'label': _("Urgent and Open"), 'model': Ticket,
                 'context': _("of %s open ticket(s)", c.get('tickets_open', 0)),
                 'domain': ticket_open + [('priority', '=', '3')],
                 'user_field': 'assignee_id', 'icon': 'fa-fire', 'tone': 'danger',
                 'higher_is_better': False,
                 'trend_open_field': 'create_date',
                 'trend_close_field': 'resolved_on',
                 # Priority identifies the KIND of ticket and does not change
                 # as it moves through its life, so it belongs in the trend
                 # filter. The open/closed state does not.
                 'trend_domain': [('priority', '=', '3')],
                 'trend_granularity': 'week', 'trend_buckets': 8},
                {'key': 'tickets_resolved_month', 'label': _("Resolved This Month"), 'model': Ticket,
                 'context': _("%s resolved last month", c.get('resolved_last_month', 0)),
                 'domain': [('state', 'in', ('resolved', 'closed')),
                            ('resolved_on', '>=', self._month_start())],
                 'user_field': 'assignee_id', 'icon': 'fa-check-circle', 'tone': 'success',
                 'trend_field': 'resolved_on', 'trend_granularity': 'month'},
            ]},
            {'id': 'maintenance', 'title': _("Maintenance"), 'icon': 'fa-wrench',
             'subtitle': _("Keeping the asset working, before somebody has to report it"),
             'tiles': [
                # Same reasoning: a stock, so no trend. `request_date` would
                # plot requests raised, not requests still open.
                {'key': 'maintenance_open', 'label': _("Open Requests"), 'model': Maintenance,
                 'context': _("%s with no date agreed", c.get('maint_undated', 0)),
                 'domain': maintenance_open, 'user_field': 'assigned_to',
                 'icon': 'fa-wrench', 'tone': 'primary',
                 'higher_is_better': False,
                 'trend_open_field': 'request_date',
                 'trend_close_field': 'completion_date',
                 'trend_granularity': 'month', 'trend_buckets': 6},
                {'key': 'maintenance_unscheduled', 'label': _("Not Scheduled Yet"), 'model': Maintenance,
                 'context': _("of %s open request(s)", c.get('maint_open', 0)),
                 'domain': [('state', '=', 'draft')], 'user_field': 'assigned_to',
                 'icon': 'fa-calendar-times-o', 'tone': 'warning', 'higher_is_better': False,
                 'hint': _("Raised, but no date has been agreed with anybody.")},
                {'key': 'maintenance_in_progress', 'label': _("In Progress"), 'model': Maintenance,
                 'context': _("of %s open request(s)", c.get('maint_open', 0)),
                 'domain': [('state', '=', 'in_progress')], 'user_field': 'assigned_to',
                 'icon': 'fa-cogs', 'tone': 'info'},
                {'key': 'maintenance_late', 'label': _("Past Scheduled Date"), 'model': Maintenance,
                 'context': _("worst is %s day(s) late", c.get('maint_late_days', 0)),
                 'domain': [('state', '=', 'scheduled'), ('scheduled_date', '<', today)],
                 'user_field': 'assigned_to', 'warning_above': 0,
                 'icon': 'fa-exclamation-circle', 'tone': 'danger', 'higher_is_better': False,
                 'hint': _("The date came and the work did not.")},
                {'key': 'maintenance_cost_open', 'label': _("Estimated Cost, Open Work"),
                 'context': _("across %s open request(s)", c.get('maint_open', 0)),
                 'model': Maintenance, 'domain': maintenance_open, 'measure': 'cost',
                 'user_field': 'assigned_to', 'icon': 'fa-money', 'tone': 'neutral',
                 'higher_is_better': False, 'format': 'monetary',
                 'hint': _("Estimates on work not yet done. Not a committed cost.")},
            ]},
        ]

    def _month_start(self):
        today = self._today()
        return today.replace(day=1)

    def _selection_chart(self, key, title, subtitle, model_name, domain, field_name,
                         series_label, span='o_ad_col_4'):
        if not self._can_read(model_name):
            return None
        Model = self.env[model_name]
        labels_by_key = dict(Model._fields[field_name]._description_selection(self.env))
        counts = dict(Model._read_group(domain, groupby=[field_name], aggregates=['__count']))
        keys = [k for k in labels_by_key if counts.get(k)]
        return {
            'key': key, 'title': title, 'subtitle': subtitle, 'type': 'bar',
            'span': span,
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
            self._selection_chart(
                'tickets_by_priority', _("Open Tickets by Priority"),
                _("Urgency, not volume -- one urgent outranks ten routine"),
                'realestate.customer.ticket', [('state', 'in', TICKET_OPEN)],
                'priority', _("Tickets")),
        ]
        return [chart for chart in charts if chart]

    # ------------------------------------------------------------------
    # Problems, work and detail
    # ------------------------------------------------------------------
    def _dashboard_alerts(self, scope):
        """What is going wrong, worst first.

        Ordered by what actually blocks somebody rather than by how many
        there are: one critical snag stops a handover, twenty minor ones do
        not.
        """
        today = self._today()
        rows = []
        if self._can_read('realestate.snagging.issue'):
            Snag = self.env['realestate.snagging.issue']
            rows.append({
                'key': 'critical_snags', 'label': _("Critical snags open"),
                'sublabel': _("A defect the buyer should not be asked to accept"),
                'value': Snag.search_count(
                    [('state', 'in', SNAG_OPEN), ('severity', '=', 'critical')]),
                'severity': 'critical', 'icon': 'fa-times-circle'})
        if self._can_read('realestate.customer.ticket'):
            Ticket = self.env['realestate.customer.ticket']
            rows.append({
                'key': 'tickets_breached', 'label': _("SLA breached"),
                'sublabel': _("Past a deadline promised to a customer"),
                'value': Ticket.search_count(
                    [('state', 'in', TICKET_OPEN), ('sla_breached', '=', True)]),
                'severity': 'critical', 'icon': 'fa-exclamation-triangle'})
            rows.append({
                'key': 'tickets_unassigned', 'label': _("Tickets with no owner"),
                'sublabel': _("Nobody is working on these"),
                'value': Ticket.search_count(
                    [('state', 'in', TICKET_OPEN), ('assignee_id', '=', False)]),
                'severity': 'warning', 'icon': 'fa-user-plus'})
        if self._can_read('realestate.maintenance.request'):
            Maintenance = self.env['realestate.maintenance.request']
            rows.append({
                'key': 'maintenance_late', 'label': _("Maintenance past its date"),
                'sublabel': _("Scheduled, and the date has passed"),
                'value': Maintenance.search_count(
                    [('state', '=', 'scheduled'), ('scheduled_date', '<', today)]),
                'severity': 'warning', 'icon': 'fa-exclamation-circle'})
            rows.append({
                'key': 'maintenance_unscheduled', 'label': _("Maintenance with no date"),
                'value': Maintenance.search_count([('state', '=', 'draft')]),
                'icon': 'fa-calendar-times-o'})
        if self._can_read('realestate.handover'):
            rows.append({
                'key': 'handovers_snagging', 'label': _("Handovers stuck in snagging"),
                'value': self.env['realestate.handover'].search_count(
                    [('state', '=', 'snagging')]),
                'icon': 'fa-wrench'})
        return rows

    def _dashboard_work_queue(self, scope):
        rows = []
        uid = self.env.uid
        if self._can_read('realestate.customer.ticket'):
            Ticket = self.env['realestate.customer.ticket']
            rows.append({'key': 'my_tickets', 'label': _("My Open Tickets"),
                         'value': Ticket.search_count(
                             [('state', 'in', TICKET_OPEN), ('assignee_id', '=', uid)]),
                         'severity': 'warning', 'icon': 'fa-ticket'})
            rows.append({'key': 'tickets_waiting', 'label': _("Waiting on Someone Else"),
                         'value': Ticket.search_count([('state', '=', 'waiting')]),
                         'icon': 'fa-hourglass-half'})
        if self._can_read('realestate.maintenance.request'):
            Maintenance = self.env['realestate.maintenance.request']
            rows.append({'key': 'my_maintenance', 'label': _("My Maintenance Work"),
                         'value': Maintenance.search_count(
                             [('state', 'in', MAINTENANCE_OPEN), ('assigned_to', '=', uid)]),
                         'icon': 'fa-wrench'})
            rows.append({'key': 'maintenance_to_schedule', 'label': _("Requests to Schedule"),
                         'value': Maintenance.search_count([('state', '=', 'draft')]),
                         'icon': 'fa-calendar'})
        if self._can_read('realestate.snagging.issue'):
            rows.append({'key': 'snags_unassigned', 'label': _("Snags to Assign"),
                         'value': self.env['realestate.snagging.issue'].search_count(
                             [('state', '=', 'open')]),
                         'icon': 'fa-list'})
        return rows

    def _dashboard_tables(self, scope):
        tables = []
        today = self._today()
        now = fields.Datetime.now()

        if self._can_read('realestate.customer.ticket'):
            Ticket = self.env['realestate.customer.ticket']
            priorities = dict(Ticket._fields['priority']._description_selection(self.env))
            states = dict(Ticket._fields['state']._description_selection(self.env))
            tickets = Ticket.search([('state', 'in', TICKET_OPEN)],
                                    order='sla_deadline', limit=12)
            rows = []
            for ticket in tickets:
                overdue = bool(ticket.sla_deadline and ticket.sla_deadline < now)
                rows.append({
                    'id': ticket.id,
                    'ticket': ticket.title or ticket.name,
                    'customer': ticket.partner_id.display_name or '—',
                    'priority': priorities.get(ticket.priority, ticket.priority),
                    'priority_tone': {'3': 'danger', '2': 'warning'}.get(
                        ticket.priority, 'neutral'),
                    'owner': ticket.assignee_id.display_name or _("Unassigned"),
                    'stage': states.get(ticket.state, ticket.state),
                    'sla': (_("Breached") if overdue else _("Within SLA")),
                    'sla_tone': 'danger' if overdue else 'success',
                })
            tables.append({
                'key': 'tickets_open', 'title': _("Open Tickets"),
                'icon': 'fa-life-ring', 'span': 'o_ad_col_12',
                'subtitle': _("Nearest deadline first — a breach is a promise already broken"),
                'empty_text': _("No ticket is open."),
                'model': 'realestate.customer.ticket',
                'columns': [
                    {'key': 'ticket', 'label': _("Ticket")},
                    {'key': 'customer', 'label': _("Customer")},
                    {'key': 'priority', 'label': _("Priority"), 'type': 'badge'},
                    {'key': 'owner', 'label': _("Owner")},
                    {'key': 'stage', 'label': _("Stage")},
                    {'key': 'sla', 'label': _("SLA"), 'type': 'badge'},
                ],
                'rows': rows,
            })

        if self._can_read('realestate.maintenance.request'):
            Maintenance = self.env['realestate.maintenance.request']
            states = dict(Maintenance._fields['state']._description_selection(self.env))
            requests = Maintenance.search([('state', 'in', MAINTENANCE_OPEN)],
                                          order='scheduled_date', limit=12)
            rows = []
            for request in requests:
                late = bool(request.scheduled_date and request.scheduled_date < today
                            and request.state == 'scheduled')
                rows.append({
                    'id': request.id,
                    'request': request.name,
                    'unit': request.property_id.display_name or '—',
                    'stage': states.get(request.state, request.state),
                    'stage_tone': {'in_progress': 'info', 'scheduled': 'primary',
                                   'draft': 'warning'}.get(request.state, 'neutral'),
                    'scheduled': (fields.Date.to_string(request.scheduled_date)
                                  if request.scheduled_date else _("Not scheduled")),
                    'cost': request.cost,
                    'status': (_("Late") if late
                               else (_("Scheduled") if request.scheduled_date
                                     else _("No date"))),
                    'status_tone': ('danger' if late
                                    else ('success' if request.scheduled_date else 'warning')),
                })
            tables.append({
                'key': 'maintenance_open', 'title': _("Maintenance in Hand"),
                'icon': 'fa-wrench', 'span': 'o_ad_col_12',
                'subtitle': _("Undated work sorts first: a request with no date is "
                              "not a plan"),
                'empty_text': _("No maintenance request is open."),
                'model': 'realestate.maintenance.request',
                'columns': [
                    {'key': 'request', 'label': _("Request")},
                    {'key': 'unit', 'label': _("Unit")},
                    {'key': 'stage', 'label': _("Stage"), 'type': 'badge'},
                    {'key': 'scheduled', 'label': _("Scheduled")},
                    {'key': 'cost', 'label': _("Estimate"), 'numeric': True,
                     'format': 'monetary'},
                    {'key': 'status', 'label': _("Status"), 'type': 'badge'},
                ],
                'rows': rows,
            })
        return tables

    def _dashboard_quick_actions(self):
        return [
            {'key': 'new_maintenance', 'label': _("New Maintenance Request"), 'icon': 'fa-plus',
             'model': 'realestate.maintenance.request'},
            {'key': 'handovers', 'label': _("Handovers"), 'icon': 'fa-key',
             'action': 'real_estate_handover.action_handover'},
            {'key': 'tickets', 'label': _("Tickets"), 'icon': 'fa-life-ring',
             'action': 'real_estate_customer_service.action_customer_ticket'},
        ]
