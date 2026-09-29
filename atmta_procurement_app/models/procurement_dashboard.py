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
                {'key': 'my_approvals', 'label': _("Approvals Waiting for Me"), 'model': Step,
                 'domain': pending + ['|', ('approver_user_id', '=', uid),
                                      '&', ('approver_user_id', '=', False),
                                      '|', ('group_id', '=', False),
                                      ('group_id', 'in', self.env.user.groups_id.ids)],
                 'warning_above': 0},
                {'key': 'my_technical_evaluations', 'label': _("My Technical Evaluations"),
                 'model': 'realestate.procurement.technical.evaluation',
                 'domain': [('evaluator_id', '=', uid), ('state', 'in', ('draft', 'reopened'))]},
                {'key': 'inspections_to_do', 'label': _("Material Inspections to Do"),
                 'model': 'realestate.procurement.receipt.inspection', 'domain': [('state', '=', 'draft')]},
            ]},
            {'id': 'demand', 'title': _("Demand"), 'icon': 'fa-clipboard', 'tiles': [
                {'key': 'requests_submitted', 'label': _("Requisitions Awaiting Approval"), 'model': Request,
                 'domain': [('state', '=', 'submitted')]},
                {'key': 'requests_unsourced', 'label': _("Approved, Not Yet Sourced"), 'model': Request,
                 'domain': [('state', '=', 'approved')]},
                {'key': 'requests_overdue', 'label': _("Past Needed-By Date"), 'model': Request,
                 'domain': request_open + [('needed_by', '<', today)], 'warning_above': 0},
                {'key': 'requests_urgent', 'label': _("Urgent Requisitions"), 'model': Request,
                 'domain': request_open + [('priority', '=', '1')]},
            ]},
            {'id': 'sourcing', 'title': _("Sourcing & Award"), 'icon': 'fa-gavel', 'tiles': [
                {'key': 'tenders_open', 'label': _("Tenders Open"), 'model': Event,
                 'domain': [('state', '=', 'published')]},
                {'key': 'tenders_closing', 'label': _("Tenders Closing in 7 Days"), 'model': Event,
                 'domain': [('state', '=', 'published'), ('close_datetime', '>=', now),
                            ('close_datetime', '<=', now + timedelta(days=7))]},
                {'key': 'evaluations_open', 'label': _("Evaluations in Progress"),
                 'model': 'realestate.procurement.evaluation.round',
                 'domain': [('state', 'in', EVALUATION_OPEN)]},
                {'key': 'awards_review', 'label': _("Awards in Review"), 'model': 'realestate.procurement.award',
                 'domain': [('state', '=', 'review')]},
                {'key': 'awards_to_issue', 'label': _("Approved Awards to Issue"),
                 'model': 'realestate.procurement.award', 'domain': [('state', '=', 'approved')]},
            ]},
            {'id': 'purchasing', 'title': _("Purchasing"), 'icon': 'fa-shopping-cart', 'tiles': [
                {'key': 'pos_to_approve', 'label': _("Purchase Orders to Approve"), 'model': PO,
                 'domain': project_po + [('state', '=', 'to approve')]},
                {'key': 'pos_confirmed', 'label': _("Confirmed Purchase Orders"), 'model': PO,
                 'domain': project_po + [('state', '=', 'purchase')]},
                {'key': 'pos_direct', 'label': _("Direct Purchases"), 'model': PO,
                 'domain': project_po + [('state', 'in', ('purchase', 'done')),
                                         ('re_governance_status', '=', 'direct')],
                 'warning_above': 0,
                 'hint': _("Project purchase orders confirmed without an approved requisition.")},
            ]},
            {'id': 'controls', 'title': _("Controls & Vendors"), 'icon': 'fa-shield', 'tiles': [
                {'key': 'approvals_stale', 'label': _("Approvals Waiting Over 7 Days"), 'model': Step,
                 'domain': pending + [('waiting_days', '>', 7)], 'warning_above': 0},
                {'key': 'exceptions_requested', 'label': _("Control Exceptions to Decide"),
                 'model': 'realestate.procurement.control.exception', 'domain': [('state', '=', 'requested')]},
                {'key': 'qualifications_expiring', 'label': _("Qualifications Expiring in 30 Days"),
                 'model': 'realestate.procurement.vendor.qualification',
                 'domain': [('is_current', '=', True), ('expiry_date', '>=', today),
                            ('expiry_date', '<=', today + timedelta(days=30))]},
                {'key': 'vendors_restricted', 'label': _("Restricted or Suspended Vendors"),
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
