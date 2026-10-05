# -*- coding: utf-8 -*-
"""The Procurement Overview payload.

Why this replaces the tile grid
-------------------------------
The previous screen rendered nineteen tiles in five labelled sections. Most
were zero, every one carried the same visual weight, and the first viewport
told the reader almost nothing: "Approvals Waiting for Me 0" sat at exactly
the same size and prominence as "Urgent Requisitions 4".

So the nineteen counts are not gone -- they have moved to where each belongs:

* six headline figures a procurement manager is actually measured on;
* everything that is *wrong* into one Attention Required list;
* everything that is *mine to do* into one personal work queue.

A counted row in a list takes roughly a fifth of the space a card does and
reads faster, because the numbers line up. That is the whole point: the
screen should answer what is happening, why, what needs me, in that order --
not present twenty equal boxes and leave the reader to rank them.
"""

import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

REQUEST_CLOSED = ('received', 'done', 'rejected', 'cancelled')
REQUEST_OPEN = [('state', 'not in', REQUEST_CLOSED)]
EVALUATION_OPEN = ('technical_open', 'technical_final',
                   'commercial_open', 'commercial_final')
TREND_MONTHS = 6


def _delta(series):
    if len(series) < 2 or not series[-2]:
        return None
    return (series[-1] - series[-2]) / abs(series[-2]) * 100.0


class ProcurementOverview(models.AbstractModel):
    _inherit = 'realestate.procurement.dashboard'

    # ==================================================================
    @api.model
    def get_overview(self, scope='team', filters=None):
        filters = dict(filters or {})
        today = self._today()
        company = self.env.company
        months = [(today.replace(day=1) - relativedelta(months=offset))
                  for offset in range(TREND_MONTHS - 1, -1, -1)]
        return {
            'title': _("Procurement"),
            'company_name': company.display_name,
            'currency_id': company.currency_id.id,
            'as_of': today.isoformat(),
            'scope': scope,
            'supports_scope': False,
            'quick_actions': self._overview_quick_actions(),
            'filters': self._dashboard_filters(),
            'kpis': self._overview_kpis(today, filters, months),
            'demand_stages': self._demand_stages(filters),
            'sourcing_funnel': self._sourcing_funnel(filters),
            'lead_time': self._lead_time(today, filters, months),
            'alerts': self._attention(today, filters),
            'work_queue': self._work_queue(today),
            'tables': self._dashboard_tables(scope),
        }

    # ------------------------------------------------------------------
    @api.model
    def _request_domain(self, filters):
        domain = []
        if filters.get('project_id'):
            domain.append(('project_id', '=', int(filters['project_id'])))
        return domain

    @api.model
    def _event_domain(self, filters):
        domain = []
        if filters.get('category_id'):
            domain.append(('category_id', '=', int(filters['category_id'])))
        return domain

    @api.model
    def _monthly_counts(self, model, domain, field, months):
        if not self._can_read(model):
            return []
        groups = self.env[model]._read_group(
            domain + [(field, '>=', months[0])],
            groupby=['%s:month' % field], aggregates=['__count'])
        found = {}
        for bucket, count in groups:
            key = bucket.date() if hasattr(bucket, 'date') else bucket
            if key:
                found['%d-%02d' % (key.year, key.month)] = count
        # A month with no requisitions is a real zero; dropping it would shift
        # every later point left and draw a trend that never happened.
        return [found.get('%d-%02d' % (m.year, m.month), 0) for m in months]

    # ==================================================================
    # The six figures the screen is read for
    # ==================================================================
    @api.model
    def _overview_kpis(self, today, filters, months):
        Request = 'realestate.material.request'
        Event = 'realestate.procurement.sourcing.event'
        kpis = []
        request_filter = self._request_domain(filters)

        if self._can_read(Request):
            Model = self.env[Request]
            raised = self._monthly_counts(
                Request, request_filter, 'request_date', months)
            kpis.append({
                'key': 'requests_open', 'label': _("Open Requisitions"),
                'icon': 'fa-clipboard', 'tone': 'primary',
                'value': Model.search_count(request_filter + REQUEST_OPEN),
                'format': 'integer', 'spark': raised,
                'delta_percent': _delta(raised),
                'comparison_label': _("raised vs last month"),
                'higher_is_better': True,
                'hint': _("Requisitions not yet received, done, rejected or "
                          "cancelled."),
                'drill': True,
            })
            kpis.append({
                'key': 'requests_overdue', 'label': _("Past Needed-By Date"),
                'icon': 'fa-exclamation-circle', 'tone': 'danger',
                'value': Model.search_count(
                    request_filter + REQUEST_OPEN + [('needed_by', '<', today)]),
                'format': 'integer',
                # Demand arriving late is bad news wearing a positive sign.
                'higher_is_better': False,
                'warning': True, 'warning_label': _("Site is waiting"),
                'hint': _("Open requisitions whose needed-by date has passed."),
                'drill': True,
            })

        if self._can_read(Event):
            Model = self.env[Event]
            now = fields.Datetime.now()
            event_filter = self._event_domain(filters)
            kpis.append({
                'key': 'tenders_open', 'label': _("Tenders in Market"),
                'icon': 'fa-gavel', 'tone': 'info',
                'value': Model.search_count(
                    event_filter + [('state', '=', 'published')]),
                'format': 'integer', 'higher_is_better': True,
                'hint': _("Published and not yet closed."),
                'drill': True,
            })
            # Response rate is the figure that says whether the supplier base
            # is actually engaged. A tender nobody bids on is a failed tender
            # however many were sent out.
            invitations = self.env['realestate.procurement.sourcing.invitation']
            if self._can_read(invitations._name):
                sent = invitations.search_count([('state', '!=', 'draft')])
                responded = invitations.search_count([('state', '=', 'responded')])
                kpis.append({
                    'key': 'bid_response_rate', 'label': _("Bid Response Rate"),
                    'icon': 'fa-reply', 'tone': 'success',
                    'value': round(responded / sent * 100.0, 1) if sent else 0.0,
                    'format': 'percent', 'higher_is_better': True,
                    'hint': _("Invitations that produced a bid. A tender "
                              "nobody bids on is a failed tender however many "
                              "invitations went out."),
                    'drill': True,
                })

        if self._can_read('purchase.order'):
            PO = self.env['purchase.order']
            committed = PO.search([('is_realestate_po', '=', True),
                                   ('state', 'in', ('purchase', 'done'))])
            kpis.append({
                'key': 'committed_spend', 'label': _("Committed Spend"),
                'icon': 'fa-shopping-cart', 'tone': 'primary',
                'value': sum(committed.mapped('amount_total')),
                'format': 'monetary', 'higher_is_better': True,
                'hint': _("Value of confirmed project purchase orders."),
                'drill': True,
            })

        if self._can_read('realestate.procurement.vendor.profile'):
            Profile = self.env['realestate.procurement.vendor.profile']
            total = Profile.search_count([])
            blocked = Profile.search_count(
                [('governance_status', 'in', ('restricted', 'suspended'))])
            kpis.append({
                'key': 'vendors_restricted', 'label': _("Vendors in Good Standing"),
                'icon': 'fa-id-card-o', 'tone': 'success',
                'value': round((total - blocked) / total * 100.0, 1) if total else 0.0,
                'format': 'percent', 'higher_is_better': True,
                'hint': _("Share of registered vendors that are neither "
                          "restricted nor suspended."),
                'drill': True,
            })
        return kpis

    # ==================================================================
    # Analysis
    # ==================================================================
    @api.model
    def _demand_stages(self, filters):
        """Open requisitions by stage -- where demand is actually stuck."""
        Request = 'realestate.material.request'
        if not self._can_read(Request):
            return {'segments': [], 'total': 0}
        Model = self.env[Request]
        labels = dict(Model._fields['state']._description_selection(self.env))
        tones = {'draft': 'neutral', 'submitted': 'warning', 'approved': 'info',
                 'sourcing': 'primary', 'partially_ordered': 'primary',
                 'ordered': 'success', 'partial': 'success'}
        counts = dict(Model._read_group(
            self._request_domain(filters) + REQUEST_OPEN,
            groupby=['state'], aggregates=['__count']))
        segments = [{'key': 'requests_%s' % state, 'label': labels.get(state, state),
                     'value': count, 'tone': tones.get(state, 'primary')}
                    for state, count in counts.items() if count]
        return {'segments': segments,
                'total': sum(s['value'] for s in segments),
                'total_label': _("Open Requisitions")}

    @api.model
    def _sourcing_funnel(self, filters):
        """Invited, responded, declined, silent.

        The drop-off between invited and responded is the single most useful
        number about a supplier base, and it is invisible in a tile that only
        counts open tenders.
        """
        Invitation = 'realestate.procurement.sourcing.invitation'
        if not self._can_read(Invitation):
            return {'segments': [], 'total': 0}
        Model = self.env[Invitation]
        buckets = [
            ('responded', _("Responded"), 'success'),
            ('acknowledged', _("Acknowledged"), 'info'),
            ('invited', _("Invited, Silent"), 'warning'),
            ('declined', _("Declined"), 'danger'),
            ('no_bid', _("No Bid"), 'danger'),
            ('no_response', _("No Response"), 'neutral'),
        ]
        segments = []
        for state, label, tone in buckets:
            count = Model.search_count([('state', '=', state)])
            if count:
                segments.append({'key': 'invitations_%s' % state, 'label': label,
                                 'value': count, 'tone': tone})
        return {'segments': segments,
                'total': sum(s['value'] for s in segments),
                'total_label': _("Invitations")}

    @api.model
    def _lead_time(self, today, filters, months):
        """Requisitions raised each month, against those still open.

        Raised-versus-cleared is the cheapest honest measure of whether the
        function is keeping up with demand.
        """
        Request = 'realestate.material.request'
        if not self._can_read(Request):
            return None
        request_filter = self._request_domain(filters)
        raised = self._monthly_counts(Request, request_filter, 'request_date', months)
        closed = self._monthly_counts(
            Request, request_filter + [('state', 'in', REQUEST_CLOSED)],
            'request_date', months)
        return {'labels': [m.strftime('%b') for m in months],
                'raised': raised, 'closed': closed}

    # ==================================================================
    # Problems and work
    # ==================================================================
    @api.model
    def _attention(self, today, filters):
        rows = []
        now = fields.Datetime.now()
        Request = 'realestate.material.request'
        request_filter = self._request_domain(filters)

        if self._can_read(Request):
            Model = self.env[Request]
            rows.append({
                'key': 'requests_overdue', 'label': _("Requisitions past needed-by"),
                'sublabel': _("Site is waiting on these"),
                'value': Model.search_count(
                    request_filter + REQUEST_OPEN + [('needed_by', '<', today)]),
                'severity': 'critical', 'icon': 'fa-exclamation-circle'})
            rows.append({
                'key': 'requests_urgent', 'label': _("Urgent requisitions open"),
                'value': Model.search_count(
                    request_filter + REQUEST_OPEN + [('priority', '=', '1')]),
                'severity': 'warning', 'icon': 'fa-bolt'})

        Event = 'realestate.procurement.sourcing.event'
        if self._can_read(Event):
            closing = self.env[Event].search([
                ('state', '=', 'published'),
                ('close_datetime', '>=', now),
                ('close_datetime', '<=', now + timedelta(days=7))])
            silent = closing.filtered(
                lambda event: not event.invitation_ids.filtered(
                    lambda i: i.state == 'responded'))
            rows.append({
                'key': 'tenders_closing', 'label': _("Tenders closing in 7 days"),
                'sublabel': _("%s with no bid received", len(silent)),
                'value': len(closing),
                'severity': 'critical' if silent else 'warning',
                'icon': 'fa-hourglass-end'})

        Step = 'realestate.procurement.approval.step'
        if self._can_read(Step):
            rows.append({
                'key': 'approvals_stale', 'label': _("Approvals waiting over 7 days"),
                'sublabel': _("Decisions nobody has made"),
                'value': self.env[Step].search_count(
                    [('decision', '=', 'pending'),
                     ('request_id.state', '=', 'submitted'),
                     ('waiting_days', '>', 7)]),
                'severity': 'warning', 'icon': 'fa-clock-o'})

        if self._can_read('purchase.order'):
            rows.append({
                'key': 'pos_direct', 'label': _("Purchases that bypassed a requisition"),
                'sublabel': _("Confirmed without an approved requisition"),
                'value': self.env['purchase.order'].search_count(
                    [('is_realestate_po', '=', True),
                     ('state', 'in', ('purchase', 'done')),
                     ('re_governance_status', '=', 'direct')]),
                'severity': 'critical', 'icon': 'fa-unlock-alt'})

        Qualification = 'realestate.procurement.vendor.qualification'
        if self._can_read(Qualification):
            rows.append({
                'key': 'qualifications_expiring',
                'label': _("Qualifications expiring in 30 days"),
                'value': self.env[Qualification].search_count(
                    [('is_current', '=', True), ('expiry_date', '>=', today),
                     ('expiry_date', '<=', today + timedelta(days=30))]),
                'severity': 'warning', 'icon': 'fa-id-card-o'})

        if self._can_read('realestate.procurement.vendor.profile'):
            rows.append({
                'key': 'vendors_restricted', 'label': _("Restricted or suspended vendors"),
                'value': self.env['realestate.procurement.vendor.profile'].search_count(
                    [('governance_status', 'in', ('restricted', 'suspended'))]),
                'icon': 'fa-ban'})

        if self._can_read('realestate.procurement.control.exception'):
            rows.append({
                'key': 'exceptions_requested', 'label': _("Control exceptions to decide"),
                'value': self.env['realestate.procurement.control.exception'].search_count(
                    [('state', '=', 'requested')]),
                'icon': 'fa-shield'})
        return rows

    @api.model
    def _work_queue(self, today):
        uid = self.env.uid
        rows = []
        Step = 'realestate.procurement.approval.step'
        if self._can_read(Step):
            rows.append({
                'key': 'my_approvals', 'label': _("Approvals Waiting for Me"),
                'value': self.env[Step].search_count(
                    [('decision', '=', 'pending'),
                     ('request_id.state', '=', 'submitted'),
                     '|', ('approver_user_id', '=', uid),
                     '&', ('approver_user_id', '=', False),
                     '|', ('group_id', '=', False),
                     ('group_id', 'in', self.env.user.groups_id.ids)]),
                'severity': 'warning', 'icon': 'fa-check-square-o'})
        if self._can_read('realestate.procurement.technical.evaluation'):
            rows.append({
                'key': 'my_technical_evaluations', 'label': _("My Technical Evaluations"),
                'value': self.env['realestate.procurement.technical.evaluation'
                                  ].search_count(
                    [('evaluator_id', '=', uid), ('state', 'in', ('draft', 'reopened'))]),
                'icon': 'fa-list-ol'})
        if self._can_read('realestate.procurement.evaluation.round'):
            rows.append({
                'key': 'evaluations_open', 'label': _("Evaluations in Progress"),
                'value': self.env['realestate.procurement.evaluation.round'].search_count(
                    [('state', 'in', list(EVALUATION_OPEN))]),
                'icon': 'fa-balance-scale'})
        if self._can_read('realestate.procurement.award'):
            Award = self.env['realestate.procurement.award']
            rows.append({'key': 'awards_review', 'label': _("Awards in Review"),
                         'value': Award.search_count([('state', '=', 'review')]),
                         'icon': 'fa-eye'})
            rows.append({'key': 'awards_to_issue', 'label': _("Approved Awards to Issue"),
                         'value': Award.search_count([('state', '=', 'approved')]),
                         'severity': 'warning', 'icon': 'fa-paper-plane'})
        if self._can_read('purchase.order'):
            rows.append({
                'key': 'pos_to_approve', 'label': _("Purchase Orders to Approve"),
                'value': self.env['purchase.order'].search_count(
                    [('is_realestate_po', '=', True), ('state', '=', 'to approve')]),
                'severity': 'warning', 'icon': 'fa-shopping-cart'})
        if self._can_read('realestate.procurement.receipt.inspection'):
            rows.append({
                'key': 'inspections_to_do', 'label': _("Material Inspections to Do"),
                'value': self.env['realestate.procurement.receipt.inspection'
                                  ].search_count([('state', '=', 'draft')]),
                'icon': 'fa-search'})
        if self._can_read('realestate.material.request'):
            rows.append({
                'key': 'requests_unsourced', 'label': _("Approved, Not Yet Sourced"),
                'value': self.env['realestate.material.request'].search_count(
                    [('state', '=', 'approved')]),
                'icon': 'fa-clipboard'})
        return rows

    # ==================================================================
    @api.model
    def _overview_quick_actions(self):
        actions = []
        if self._can_read('realestate.material.request'):
            actions.append({'key': 'new_request', 'label': _("New Requisition"),
                            'icon': 'fa-plus'})
        if self._can_read('realestate.procurement.sourcing.event'):
            actions.append({'key': 'tenders', 'label': _("Tenders"),
                            'icon': 'fa-gavel'})
        if self._can_read('purchase.order'):
            actions.append({'key': 'purchase_orders', 'label': _("Purchase Orders"),
                            'icon': 'fa-shopping-cart'})
        return actions

    # ==================================================================
    # Drill-through
    # ==================================================================
    @api.model
    def _overview_targets(self, today):
        now = fields.Datetime.now()
        uid = self.env.uid
        return {
            'requests_open': (_("Open Requisitions"), 'realestate.material.request',
                              REQUEST_OPEN),
            'requests_overdue': (_("Past Needed-By"), 'realestate.material.request',
                                 REQUEST_OPEN + [('needed_by', '<', today)]),
            'requests_urgent': (_("Urgent Requisitions"), 'realestate.material.request',
                                REQUEST_OPEN + [('priority', '=', '1')]),
            'requests_unsourced': (_("Approved, Not Sourced"),
                                   'realestate.material.request',
                                   [('state', '=', 'approved')]),
            'tenders_open': (_("Tenders in Market"),
                             'realestate.procurement.sourcing.event',
                             [('state', '=', 'published')]),
            'tenders_closing': (_("Tenders Closing"),
                                'realestate.procurement.sourcing.event',
                                [('state', '=', 'published'),
                                 ('close_datetime', '>=', now),
                                 ('close_datetime', '<=', now + timedelta(days=7))]),
            'bid_response_rate': (_("Invitations That Produced a Bid"),
                                  'realestate.procurement.sourcing.invitation',
                                  [('state', '=', 'responded')]),
            'committed_spend': (_("Confirmed Purchase Orders"), 'purchase.order',
                                [('is_realestate_po', '=', True),
                                 ('state', 'in', ('purchase', 'done'))]),
            'pos_direct': (_("Purchases Without a Requisition"), 'purchase.order',
                           [('is_realestate_po', '=', True),
                            ('state', 'in', ('purchase', 'done')),
                            ('re_governance_status', '=', 'direct')]),
            'pos_to_approve': (_("Purchase Orders to Approve"), 'purchase.order',
                               [('is_realestate_po', '=', True),
                                ('state', '=', 'to approve')]),
            'vendors_restricted': (_("Restricted or Suspended Vendors"),
                                   'realestate.procurement.vendor.profile',
                                   [('governance_status', 'in',
                                     ('restricted', 'suspended'))]),
            'qualifications_expiring': (_("Qualifications Expiring"),
                                        'realestate.procurement.vendor.qualification',
                                        [('is_current', '=', True),
                                         ('expiry_date', '>=', today),
                                         ('expiry_date', '<=', today + timedelta(days=30))]),
            'approvals_stale': (_("Approvals Waiting Over 7 Days"),
                                'realestate.procurement.approval.step',
                                [('decision', '=', 'pending'),
                                 ('request_id.state', '=', 'submitted'),
                                 ('waiting_days', '>', 7)]),
            'my_approvals': (_("Approvals Waiting for Me"),
                             'realestate.procurement.approval.step',
                             [('decision', '=', 'pending'),
                              ('request_id.state', '=', 'submitted'),
                              '|', ('approver_user_id', '=', uid),
                              '&', ('approver_user_id', '=', False),
                              '|', ('group_id', '=', False),
                              ('group_id', 'in', self.env.user.groups_id.ids)]),
            'my_technical_evaluations': (_("My Technical Evaluations"),
                                         'realestate.procurement.technical.evaluation',
                                         [('evaluator_id', '=', uid),
                                          ('state', 'in', ('draft', 'reopened'))]),
            'evaluations_open': (_("Evaluations in Progress"),
                                 'realestate.procurement.evaluation.round',
                                 [('state', 'in', list(EVALUATION_OPEN))]),
            'awards_review': (_("Awards in Review"), 'realestate.procurement.award',
                              [('state', '=', 'review')]),
            'awards_to_issue': (_("Approved Awards"), 'realestate.procurement.award',
                                [('state', '=', 'approved')]),
            'inspections_to_do': (_("Material Inspections"),
                                  'realestate.procurement.receipt.inspection',
                                  [('state', '=', 'draft')]),
            'exceptions_requested': (_("Control Exceptions"),
                                     'realestate.procurement.control.exception',
                                     [('state', '=', 'requested')]),
        }

    @api.model
    def action_overview_drill(self, key):
        today = self._today()
        target = self._overview_targets(today).get(key)
        if not target and key.startswith('requests_'):
            target = (_("Requisitions"), 'realestate.material.request',
                      REQUEST_OPEN + [('state', '=', key[len('requests_'):])])
        if not target and key.startswith('invitations_'):
            target = (_("Invitations"),
                      'realestate.procurement.sourcing.invitation',
                      [('state', '=', key[len('invitations_'):])])
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
    def action_overview_quick(self, key):
        targets = {
            'new_request': ('realestate.material.request', 'form'),
            'tenders': ('realestate.procurement.sourcing.event', 'list'),
            'purchase_orders': ('purchase.order', 'list'),
        }
        target = targets.get(key)
        if not target or not self._can_read(target[0]):
            raise UserError(_("Unknown dashboard action '%s'.", key))
        model, mode = target
        views = [[False, 'form']] if mode == 'form' else [[False, 'list'], [False, 'form']]
        return {
            'type': 'ir.actions.act_window', 'name': _("Procurement"),
            'res_model': model, 'views': views,
            'view_mode': 'form' if mode == 'form' else 'list,form',
            'domain': [('is_realestate_po', '=', True)] if model == 'purchase.order' else [],
            'target': 'current',
        }
