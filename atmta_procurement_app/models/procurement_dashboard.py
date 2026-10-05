# -*- coding: utf-8 -*-
"""Procurement dashboard: what waits for me, and where demand is stuck.

Every tile reads one of the atmta_procurement_* models with the user's own
rights, so a requester, a buyer, an evaluator and an inspector each see the
figures their role can act on.
"""

from datetime import timedelta

from odoo import _, fields, models

REQUEST_CLOSED = ('received', 'done', 'rejected', 'cancelled')
EVALUATION_OPEN = ('technical_open', 'technical_final', 'commercial_open', 'commercial_final')
AGE_BUCKETS = ((0, 2), (3, 7), (8, 14), (15, None))

#: An undecided step and a step somebody is waiting for are not the same
#: thing. `_check_may_decide` refuses a decision unless the requisition is
#: still submitted, so a pending step on any other requisition is work nobody
#: can ever clear. `action_cancel` now withdraws the steps it leaves behind,
#: but requisitions cancelled before that fix still carry pending ones — and
#: cancellation never covered the other half anyway: a requisition that moved
#: forward to approved, ordered or received keeps its pending steps too.
#: Counting either kind fills an approval inbox that cannot be emptied.
PENDING_STEP = [('decision', '=', 'pending'),
                ('request_id.state', '=', 'submitted')]


class ProcurementDashboard(models.AbstractModel):
    _name = 'realestate.procurement.dashboard'
    _inherit = 'atmta.dashboard.provider'
    _description = 'Procurement Dashboard'

    def _dashboard_title(self):
        return _("Procurement")

    def _dashboard_sections(self, scope):
        today = self._today()
        now = fields.Datetime.now()
        uid = self.env.uid
        Request = 'realestate.material.request'
        Step = 'realestate.procurement.approval.step'
        Event = 'realestate.procurement.sourcing.event'
        PO = 'purchase.order'
        pending = list(PENDING_STEP)
        request_open = [('state', 'not in', REQUEST_CLOSED)]
        project_po = [('is_realestate_po', '=', True)]
        return [
            {'id': 'my_work', 'title': _("My Work"), 'icon': 'fa-inbox', 'tiles': [
                # Waiting for me is what the step's own approve check says it
                # is: the person it names, or anybody in the group it names
                # when it names nobody. Asking only for named steps showed a
                # group approver an empty tile with their work in it.
                {'key': 'my_approvals', 'icon': 'fa-check-square-o', 'tone': 'warning', 'warning_label': _("Waiting for you"), 'label': _("Approvals Waiting for Me"), 'model': Step,
                 'domain': pending + ['|', ('approver_user_id', '=', uid),
                                      '&', ('approver_user_id', '=', False),
                                      '|', ('group_id', '=', False),
                                      ('group_id', 'in', self.env.user.groups_id.ids)],
                 'warning_above': 0},
                {'key': 'my_technical_evaluations', 'icon': 'fa-list-ol', 'tone': 'info', 'label': _("My Technical Evaluations"),
                 'model': 'realestate.procurement.technical.evaluation',
                 'domain': [('evaluator_id', '=', uid), ('state', 'in', ('draft', 'reopened'))]},
                {'key': 'inspections_to_do', 'icon': 'fa-search', 'tone': 'info', 'label': _("Material Inspections to Do"),
                 'model': 'realestate.procurement.receipt.inspection', 'domain': [('state', '=', 'draft')]},
            ]},
            {'id': 'demand', 'title': _("Demand"), 'icon': 'fa-clipboard', 'tiles': [
                {'key': 'requests_submitted', 'icon': 'fa-inbox', 'tone': 'warning', 'label': _("Requisitions Awaiting Approval"), 'model': Request,
                 'domain': [('state', '=', 'submitted')]},
                {'key': 'requests_unsourced', 'icon': 'fa-clipboard', 'tone': 'primary', 'label': _("Approved, Not Yet Sourced"), 'model': Request,
                 'domain': [('state', '=', 'approved')]},
                {'key': 'requests_overdue', 'icon': 'fa-exclamation-circle', 'tone': 'danger', 'higher_is_better': False, 'warning_label': _("Past needed-by"), 'label': _("Past Needed-By Date"), 'model': Request,
                 'domain': request_open + [('needed_by', '<', today)], 'warning_above': 0},
                {'key': 'requests_urgent', 'icon': 'fa-bolt', 'tone': 'danger', 'higher_is_better': False, 'label': _("Urgent Requisitions"), 'model': Request,
                 'domain': request_open + [('priority', '=', '1')]},
            ]},
            {'id': 'sourcing', 'title': _("Sourcing & Award"), 'icon': 'fa-gavel', 'tiles': [
                {'key': 'tenders_open', 'icon': 'fa-gavel', 'tone': 'primary', 'label': _("Tenders Open"), 'model': Event,
                 'domain': [('state', '=', 'published')]},
                {'key': 'tenders_closing', 'icon': 'fa-hourglass-end', 'tone': 'warning', 'label': _("Tenders Closing in 7 Days"), 'model': Event,
                 'domain': [('state', '=', 'published'), ('close_datetime', '>=', now),
                            ('close_datetime', '<=', now + timedelta(days=7))]},
                {'key': 'evaluations_open', 'icon': 'fa-balance-scale', 'tone': 'info', 'label': _("Evaluations in Progress"),
                 'model': 'realestate.procurement.evaluation.round',
                 'domain': [('state', 'in', EVALUATION_OPEN)]},
                {'key': 'awards_review', 'icon': 'fa-eye', 'tone': 'info', 'label': _("Awards in Review"), 'model': 'realestate.procurement.award',
                 'domain': [('state', '=', 'review')]},
                {'key': 'awards_to_issue', 'icon': 'fa-paper-plane', 'tone': 'success', 'label': _("Approved Awards to Issue"),
                 'model': 'realestate.procurement.award', 'domain': [('state', '=', 'approved')]},
            ]},
            {'id': 'purchasing', 'title': _("Purchasing"), 'icon': 'fa-shopping-cart', 'tiles': [
                {'key': 'pos_to_approve', 'icon': 'fa-check-square-o', 'tone': 'warning', 'label': _("Purchase Orders to Approve"), 'model': PO,
                 'domain': project_po + [('state', '=', 'to approve')]},
                {'key': 'pos_confirmed', 'icon': 'fa-shopping-cart', 'tone': 'success', 'label': _("Confirmed Purchase Orders"), 'model': PO,
                 'domain': project_po + [('state', '=', 'purchase')]},
                {'key': 'pos_direct', 'icon': 'fa-unlock-alt', 'tone': 'danger', 'higher_is_better': False, 'warning_label': _("Bypassed requisition"), 'label': _("Direct Purchases"), 'model': PO,
                 'domain': project_po + [('state', 'in', ('purchase', 'done')),
                                         ('re_governance_status', '=', 'direct')],
                 'warning_above': 0,
                 'hint': _("Project purchase orders confirmed without an approved requisition.")},
            ]},
            {'id': 'controls', 'title': _("Controls & Vendors"), 'icon': 'fa-shield', 'tiles': [
                {'key': 'approvals_stale', 'icon': 'fa-clock-o', 'tone': 'danger', 'higher_is_better': False, 'warning_label': _("Stalled"), 'label': _("Approvals Waiting Over 7 Days"), 'model': Step,
                 'domain': pending + [('waiting_days', '>', 7)], 'warning_above': 0},
                {'key': 'exceptions_requested', 'icon': 'fa-shield', 'tone': 'warning', 'label': _("Control Exceptions to Decide"),
                 'model': 'realestate.procurement.control.exception', 'domain': [('state', '=', 'requested')]},
                {'key': 'qualifications_expiring', 'icon': 'fa-id-card-o', 'tone': 'warning', 'higher_is_better': False, 'label': _("Qualifications Expiring in 30 Days"),
                 'model': 'realestate.procurement.vendor.qualification',
                 'domain': [('is_current', '=', True), ('expiry_date', '>=', today),
                            ('expiry_date', '<=', today + timedelta(days=30))]},
                {'key': 'vendors_restricted', 'icon': 'fa-ban', 'tone': 'danger', 'higher_is_better': False, 'label': _("Restricted or Suspended Vendors"),
                 'model': 'realestate.procurement.vendor.profile',
                 'domain': [('governance_status', 'in', ('restricted', 'suspended'))]},
            ]},
        ]

    def _dashboard_charts(self, scope):
        charts = []
        Request = 'realestate.material.request'
        if self._can_read(Request):
            Model = self.env[Request]
            labels = dict(Model._fields['state']._description_selection(self.env))
            domain = [('state', 'not in', REQUEST_CLOSED)] + self._company_domain(Request)
            counts = dict(Model._read_group(domain, groupby=['state'], aggregates=['__count']))
            keys = [k for k in labels if counts.get(k)]
            charts.append({
                'key': 'requests_by_state', 'title': _("Open Requisitions by Stage"),
                'subtitle': _("Where demand is waiting"), 'type': 'bar',
                'labels': [labels[k] for k in keys],
                'series': [{'name': 'count', 'label': _("Requisitions"), 'data': [counts[k] for k in keys]}],
                'drill': [{'key': f'requests_{k}', 'label': labels[k], 'model': Request,
                           'domain': [('state', 'not in', REQUEST_CLOSED), ('state', '=', k)]} for k in keys],
            })
        Step = 'realestate.procurement.approval.step'
        if self._can_read(Step):
            Model = self.env[Step]
            labels, data, drill = [], [], []
            for low, high in AGE_BUCKETS:
                domain = PENDING_STEP + [('waiting_days', '>=', low)]
                if high is not None:
                    domain.append(('waiting_days', '<=', high))
                    label = _("%(low)s-%(high)s days", low=low, high=high)
                else:
                    label = _("%s+ days", low)
                labels.append(label)
                data.append(Model.search_count(domain + self._company_domain(Step)))
                drill.append({'key': f'approvals_{low}', 'label': label, 'model': Step, 'domain': domain})
            charts.append({
                'key': 'approvals_by_age', 'title': _("Pending Approvals by Age"),
                'subtitle': _("How long decisions have been waiting"), 'type': 'bar',
                'labels': labels,
                'series': [{'name': 'count', 'label': _("Approvals"), 'data': data}],
                'drill': drill,
            })
        return charts

    def _dashboard_quick_actions(self):
        return [
            {'key': 'new_request', 'label': _("New Requisition"), 'icon': 'fa-plus',
             'model': 'realestate.material.request'},
            {'key': 'tenders', 'label': _("Tenders"), 'icon': 'fa-gavel',
             'action': 'atmta_procurement_sourcing.action_sourcing_event'},
            {'key': 'purchase_orders', 'label': _("Purchase Orders"), 'icon': 'fa-shopping-cart',
             'action': 'atmta_procurement_purchase.action_realestate_purchase_orders'},
        ]

    # ==================================================================
    # Filters and detail
    #
    # The sections above answer "how many". These answer "which ones", which
    # is the question somebody asks the moment a tile is not zero.
    # ==================================================================
    def _dashboard_filters(self):
        filters = []
        if self._can_read('realestate.project'):
            projects = self.env['realestate.project'].search(
                [('state', 'not in', ('completed', 'cancelled'))], limit=40)
            if projects:
                filters.append({
                    'key': 'project_id', 'label': _("Project"),
                    'icon': 'fa-building', 'all_label': _("All Projects"),
                    'options': [{'key': p.id, 'label': p.display_name}
                                for p in projects],
                })
        if self._can_read('realestate.procurement.vendor.category'):
            categories = self.env['realestate.procurement.vendor.category'].search(
                [], limit=30)
            if categories:
                filters.append({
                    'key': 'category_id', 'label': _("Category"),
                    'icon': 'fa-tags', 'all_label': _("All Categories"),
                    'options': [{'key': c.id, 'label': c.display_name}
                                for c in categories],
                })
        return filters

    def _filter_values(self):
        return self.env.context.get('dashboard_filters') or {}

    def _dashboard_tables(self, scope):
        tables = []
        today = self._today()
        filters = self._filter_values()
        project = filters.get('project_id')
        category = filters.get('category_id')

        Event = 'realestate.procurement.sourcing.event'
        if self._can_read(Event):
            domain = [('state', 'in', ('published', 'review'))]
            if category:
                domain.append(('category_id', '=', int(category)))
            rows = []
            for event in self.env[Event].search(domain, order='close_datetime', limit=10):
                closes_in = ((event.close_datetime.date() - today).days
                             if event.close_datetime else None)
                responded = len(event.invitation_ids.filtered(
                    lambda i: i.state == 'responded'))
                rows.append({
                    'id': event.id,
                    'tender': event.title or event.name,
                    'method': dict(event._fields['sourcing_method'].selection).get(
                        event.sourcing_method, event.sourcing_method),
                    'invited': len(event.invitation_ids),
                    'bids': responded,
                    'closes': _("%s days", closes_in) if closes_in is not None
                              and closes_in >= 0 else _("Closed"),
                    # A tender closing within a week with no bid in is the one
                    # a buyer has to chase today, so the badge says so rather
                    # than leaving them to compare two columns.
                    'status': (_("No bids yet") if not responded
                               else _("%s received", responded)),
                    'status_tone': ('danger' if not responded and
                                    (closes_in is not None and closes_in <= 7)
                                    else ('warning' if not responded else 'success')),
                })
            tables.append({
                'key': 'tenders', 'title': _("Tenders in Market"),
                'icon': 'fa-gavel', 'span': 'o_ad_col_7',
                'columns': [
                    {'key': 'tender', 'label': _("Tender")},
                    {'key': 'method', 'label': _("Method")},
                    {'key': 'invited', 'label': _("Invited"), 'numeric': True},
                    {'key': 'bids', 'label': _("Bids"), 'numeric': True},
                    {'key': 'closes', 'label': _("Closes In"), 'numeric': True},
                    {'key': 'status', 'label': _("Response"), 'type': 'badge'},
                ],
                'rows': rows,
            })

        Request = 'realestate.material.request'
        if self._can_read(Request):
            domain = [('state', 'not in', REQUEST_CLOSED)]
            if project:
                domain.append(('project_id', '=', int(project)))
            rows = []
            for request in self.env[Request].search(
                    domain, order='needed_by', limit=10):
                days = ((request.needed_by - today).days
                        if request.needed_by else None)
                rows.append({
                    'id': request.id,
                    'request': request.name,
                    'scope': request.justification or '—',
                    'state': dict(request._fields['state'].selection).get(
                        request.state, request.state),
                    'needed': fields.Date.to_string(request.needed_by) if request.needed_by else '—',
                    'status': (_("Late") if days is not None and days < 0
                               else (_("%s days", days) if days is not None
                                     else _("No date"))),
                    'status_tone': ('danger' if days is not None and days < 0
                                    else ('warning' if days is not None and days <= 14
                                          else 'success')),
                })
            tables.append({
                'key': 'demand', 'title': _("Demand Waiting"),
                'icon': 'fa-clipboard', 'span': 'o_ad_col_5',
                'columns': [
                    {'key': 'request', 'label': _("Requisition")},
                    {'key': 'state', 'label': _("Stage")},
                    {'key': 'needed', 'label': _("Needed By")},
                    {'key': 'status', 'label': _("Lead Time"), 'type': 'badge'},
                ],
                'rows': rows,
            })
        return tables
