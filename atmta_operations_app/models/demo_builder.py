# -*- coding: utf-8 -*-
"""Demo layer: the operations workload — maintenance and service.

Operations is the one screen whose job is to show what is going WRONG, so a
demo where everything is zero demonstrates nothing at all. These records are
spread across every state and deliberately include the awkward ones: requests
past their scheduled date, tickets nobody has picked up, and tickets whose SLA
has already expired.

They are created here rather than in Rental or Customer Service because this
is the module that shows them together, and neither of those modules should
grow demo data for a screen it does not own.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

MODULE = 'atmta_operations_app'
SENTINEL = '%s.demo_maintenance_1' % MODULE

#: (summary, state, days from today for scheduled_date, cost)
MAINTENANCE_PLAN = [
    ("Air-conditioning service — split unit not cooling", 'done', -40, 1800.0),
    ("Leaking kitchen mixer tap", 'done', -22, 450.0),
    ("Lobby lighting — three fittings out", 'in_progress', -6, 950.0),
    ("Lift annual inspection", 'in_progress', -3, 6500.0),
    # Past its scheduled date and still only scheduled. This is the row the
    # dashboard exists to put in front of somebody.
    ("Water pump vibration on the roof", 'scheduled', -9, 3200.0),
    ("Fire alarm panel fault code", 'scheduled', -2, 2400.0),
    ("Repaint stairwell handrails", 'scheduled', 8, 1500.0),
    ("Garage door sensor alignment", 'scheduled', 15, 700.0),
    ("Balcony tile grouting", 'draft', 0, 1100.0),
    ("Intercom handset replacement", 'draft', 0, 600.0),
    ("Pool filtration media change", 'draft', 0, 2800.0),
]

#: (title, state, priority, assigned, days since raised, sla hours)
TICKET_PLAN = [
    ("Handover snag not closed after two visits", 'in_progress', '3', True, 9, 48),
    ("Service charge invoice queried", 'assigned', '2', True, 4, 72),
    ("Request for a second parking bay", 'new', '1', False, 3, 120),
    ("No hot water in the master bathroom", 'in_progress', '3', True, 2, 24),
    ("Noise complaint — unit above", 'new', '2', False, 1, 72),
    ("Access card not working at the gate", 'waiting', '1', True, 6, 96),
    ("Request copy of the title deed", 'new', '0', False, 11, 240),
    ("Lift out of service for three days", 'resolved', '3', True, 20, 24),
    ("Damp patch on the ceiling", 'resolved', '2', True, 31, 72),
]


class OperationsDemoBuilder(models.AbstractModel):
    _name = 'realestate.demo.operations'
    _description = 'Demo Builder — Maintenance and Service Workload'

    @api.model
    def _xmlid(self, suffix, record):
        self.env['ir.model.data']._update_xmlids([{
            'xml_id': '%s.%s' % (MODULE, suffix), 'record': record,
            'noupdate': True}])
        return record

    @api.model
    def _units(self, limit):
        return self.env['realestate.property'].search(
            [('hierarchy_level', '=', 'unit'),
             ('company_id', 'in', self.env.companies.ids)], limit=limit)

    @api.model
    def _build(self):
        if self.env.ref(SENTINEL, raise_if_not_found=False):
            return True
        self._build_maintenance()
        self._build_tickets()
        return True

    # ------------------------------------------------------------------
    @api.model
    def _build_maintenance(self):
        Maintenance = self.env['realestate.maintenance.request']
        units = self._units(len(MAINTENANCE_PLAN))
        if not units:
            _logger.warning("Demo: no unit available, maintenance skipped")
            return
        today = fields.Date.context_today(self)
        technician = self.env.ref('base.user_admin', raise_if_not_found=False)

        for index, (summary, state, offset, cost) in enumerate(MAINTENANCE_PLAN, start=1):
            unit = units[(index - 1) % len(units)]
            vals = {
                'name': summary,
                'property_id': unit.id,
                'description': summary,
                # A request is RAISED in the past and may be SCHEDULED in
                # the future. Dating the request itself ahead of today made
                # the open-requests history end below the headline count --
                # correctly, because a request cannot have been open before
                # it existed.
                'request_date': today - relativedelta(days=5 + index * 3),
                'cost': cost,
            }
            if state != 'draft':
                vals['scheduled_date'] = today + relativedelta(days=offset)
            if state == 'done':
                vals['completion_date'] = today + relativedelta(days=offset + 2)
                vals['actual_cost'] = cost
            if technician and state in ('scheduled', 'in_progress', 'done'):
                vals['assigned_to'] = technician.id
            try:
                with self.env.cr.savepoint():
                    request = Maintenance.create(vals)
                    # Set the state last: create() may force a default, and a
                    # request that is 'done' with no completion date is not a
                    # record anybody would recognise.
                    if request.state != state:
                        request.state = state
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: maintenance '%s' failed: %s", summary, error)
                continue
            self._xmlid('demo_maintenance_%d' % index, request)

    # ------------------------------------------------------------------
    @api.model
    def _build_tickets(self):
        if 'realestate.customer.ticket' not in self.env:
            return
        Ticket = self.env['realestate.customer.ticket']
        now = fields.Datetime.now()
        units = self._units(len(TICKET_PLAN))
        partners = self.env['res.partner'].search(
            [('customer_rank', '>', 0)], limit=len(TICKET_PLAN))
        if not partners:
            partners = self.env['res.partner'].search(
                [('is_company', '=', False)], limit=len(TICKET_PLAN))
        if not partners:
            _logger.warning("Demo: no partner available, tickets skipped")
            return
        agent = self.env.ref('base.user_admin', raise_if_not_found=False)

        for index, (title, state, priority, assigned, age, sla_hours) in enumerate(
                TICKET_PLAN, start=1):
            vals = {
                'title': title,
                'partner_id': partners[(index - 1) % len(partners)].id,
                'priority': priority,
                # The deadline is measured from when the ticket was RAISED, so
                # an old ticket on a short SLA is breached and a new one on a
                # long SLA is not. Stamping every deadline from now would make
                # the breach counter say zero for ever.
                'sla_deadline': now - relativedelta(days=age) + relativedelta(hours=sla_hours),
                'sla_deadline_manual': True,
            }
            if units:
                vals['property_id'] = units[(index - 1) % len(units)].id
            if assigned and agent:
                vals['assignee_id'] = agent.id
            try:
                with self.env.cr.savepoint():
                    ticket = Ticket.create(vals)
                    if ticket.state != state:
                        ticket.state = state
                    # Writing the state does not stamp when it happened, and
                    # "Resolved This Month" counts by that timestamp -- so a
                    # demo that only sets the state reports zero resolved
                    # however many resolved tickets it created.
                    if state in ('resolved', 'closed') and not ticket.resolved_on:
                        ticket.resolved_on = now - relativedelta(days=max(age - 2, 0))
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: ticket '%s' failed: %s", title, error)
                continue
            self._xmlid('demo_ticket_%d' % index, ticket)
