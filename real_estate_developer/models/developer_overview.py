# -*- coding: utf-8 -*-
"""The Development & Sales Overview payload.

Same shape, same components and the same reading order as the Rental and
Brokerage overviews: KPIs, then why, then what is wrong, then what this user
has to do, then the detail.

What this screen is for
-----------------------
A developer sells inventory once and then collects for years, so the screen
has to answer two different questions side by side:

* **Are we selling?** -- released inventory, sell-through, contracted value.
* **Are we collecting?** -- scheduled against collected, and what is overdue.

Those are not the same number and a dashboard that blurs them lets a project
look healthy while the money never arrives. Sales value is taken from signed
contracts by signing date; collections are taken from the *payments*, by
payment date, because an instalment carries no payment date of its own and
summing what fell due answers a different question entirely.
"""

import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TREND_MONTHS = 6
LIVE_CONTRACT = ('signed', 'active', 'financially_cleared', 'handed_over')
OPEN_INSTALMENT = ('pending', 'invoiced', 'partially_paid', 'overdue')
LIVE_RESERVATION = ('hold', 'pending_payment', 'booked', 'confirmed')


def _delta(series):
    if len(series) < 2 or not series[-2]:
        return None
    return (series[-1] - series[-2]) / abs(series[-2]) * 100.0


class DeveloperOverview(models.AbstractModel):
    _inherit = 'realestate.developer.dashboard'

    @api.model
    def _can_read(self, model_name):
        return model_name in self.env and self.env[model_name].has_access('read')

    # ==================================================================
    @api.model
    def get_overview(self, scope='team', filters=None):
        filters = dict(filters or {})
        today = fields.Date.context_today(self)
        company = self.env.company
        months = [(today.replace(day=1) - relativedelta(months=offset))
                  for offset in range(TREND_MONTHS - 1, -1, -1)]

        return {
            'title': _("Development & Sales"),
            'company_name': company.display_name,
            'currency_id': company.currency_id.id,
            'as_of': today.isoformat(),
            'scope': scope,
            'supports_scope': False,
            'quick_actions': self._overview_quick_actions(),
            'filters': self._overview_filters(),
            'kpis': self._overview_kpis(today, filters, months),
            'inventory': self._inventory_funnel(filters),
            'velocity': self._sales_velocity(today, filters, months),
            'collections': self._collections_chart(today, filters, months),
            'alerts': self._attention(today, filters),
            'work_queue': self._work_queue(today),
            'projects_table': self._projects_table(filters),
            'plans_table': self._plans_table(filters),
            'map': self._project_map(filters),
            'price_bands': self._price_by_type(filters),
            'ageing': self._instalment_ageing(today, filters),
            'pipeline': self._pipeline(today, filters),
            'handovers': self._upcoming_handovers(today, filters),
            'top_buyers': self._top_buyers(filters),
        }

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------
    @api.model
    def _overview_filters(self):
        filters = []
        if self._can_read('realestate.project'):
            projects = self.env['realestate.project'].search(
                [('commercial_state', '!=', 'closed')], limit=40)
            filters.append({
                'key': 'project_id', 'label': _("Project"), 'icon': 'fa-building',
                'all_label': _("All Projects"),
                'options': [{'key': p.id, 'label': p.display_name} for p in projects],
            })
        if self._can_read('realestate.payment.plan'):
            plans = self.env['realestate.payment.plan'].search(
                [('state', '=', 'active')], limit=30)
            filters.append({
                'key': 'payment_plan_id', 'label': _("Payment Plan"),
                'icon': 'fa-calendar', 'all_label': _("All Payment Plans"),
                'options': [{'key': p.id, 'label': p.display_name} for p in plans],
            })
        return filters

    @api.model
    def _unit_domain(self, filters):
        return ([('project_id', '=', int(filters['project_id']))]
                if filters.get('project_id') else [])

    @api.model
    def _contract_domain(self, filters):
        domain = []
        if filters.get('project_id'):
            domain.append(('property_id.project_id', '=', int(filters['project_id'])))
        if filters.get('payment_plan_id'):
            domain.append(('payment_plan_id', '=', int(filters['payment_plan_id'])))
        return domain

    # ------------------------------------------------------------------
    # KPIs
    # ------------------------------------------------------------------
    @api.model
    def _overview_kpis(self, today, filters, months):
        company = self._company_domain()
        unit_filter = self._unit_domain(filters)
        contract_filter = self._contract_domain(filters)
        month_start = today.replace(day=1)
        kpis = []

        if self._can_read('realestate.property'):
            Property = self.env['realestate.property']
            available = Property.search_count(
                unit_filter + [('project_id', '!=', False),
                               ('is_available_for_sale', '=', True)])
            kpis.append({
                'key': 'units_available', 'label': _("Units Available for Sale"),
                'icon': 'fa-home', 'tone': 'info', 'value': available,
                'format': 'integer', 'higher_is_better': True,
                'hint': _("Released, unblocked and uncommitted. A stock, not a "
                          "flow, so it carries no trend line."),
                'drill': True,
            })
            released = Property.search_count(
                unit_filter + [('project_id', '!=', False),
                               ('hierarchy_level', '=', 'unit'),
                               ('is_released_for_sale', '=', True)])
            committed = Property.search_count(
                unit_filter + [('project_id', '!=', False),
                               ('hierarchy_level', '=', 'unit'),
                               ('commercial_status', 'in',
                                ('reserved', 'contracted', 'sold'))])
            kpis.append({
                'key': 'sell_through', 'label': _("Sell-Through"),
                'icon': 'fa-pie-chart', 'tone': 'success',
                'value': round(committed / released * 100.0, 1) if released else 0.0,
                'format': 'percent', 'higher_is_better': True,
                'hint': _("Committed units as a share of released inventory. "
                          "Unreleased stock is excluded -- it was never for "
                          "sale, so counting it would understate the figure."),
                'drill': True,
            })

        if self._can_read('realestate.unit.reservation'):
            kpis.append({
                'key': 'reservations_active', 'label': _("Active Reservations"),
                'icon': 'fa-bookmark-o', 'tone': 'primary',
                'value': self.env['realestate.unit.reservation'].search_count(
                    [('state', 'in', list(LIVE_RESERVATION))]
                    + ([('property_id.project_id', '=', int(filters['project_id']))]
                       if filters.get('project_id') else [])),
                'format': 'integer', 'higher_is_better': True,
                'hint': _("Held, awaiting payment, booked or confirmed."),
                'drill': True,
            })

        if self._can_read('realestate.sale.contract'):
            Contract = self.env['realestate.sale.contract']
            signed = company + contract_filter + [('state', 'in', list(LIVE_CONTRACT))]
            value = self._monthly_sum(
                'realestate.sale.contract', signed, 'signing_date',
                'sale_price', months)
            kpis.append({
                'key': 'sales_value_mtd', 'label': _("Contracted This Month"),
                'icon': 'fa-handshake-o', 'tone': 'success',
                'value': self._sum(
                    Contract, signed + [('signing_date', '>=', month_start),
                                        ('signing_date', '<=', today)], 'sale_price'),
                'format': 'monetary', 'spark': value,
                'delta_percent': _delta(value),
                'comparison_label': _("vs last month"),
                'higher_is_better': True,
                'hint': _("Sale price of contracts signed between the 1st and "
                          "today. Contracted value, not cash."),
                'drill': True,
            })

        if self._can_read_collections():
            received = self.env['account.payment'].search(
                self._collected_mtd_domain(today))
            collected = self._monthly_collections(today, months)
            kpis.append({
                'key': 'collected_mtd', 'label': _("Collected This Month"),
                'icon': 'fa-credit-card', 'tone': 'success',
                'value': sum(received.mapped('amount')),
                'format': 'monetary', 'spark': collected,
                'delta_percent': _delta(collected),
                'comparison_label': _("vs last month"),
                'higher_is_better': True,
                'hint': _("Money actually received against sale instalments, "
                          "by payment date -- not what fell due."),
                'drill': True,
            })

        if self._can_read('realestate.sale.installment'):
            Instalment = self.env['realestate.sale.installment']
            overdue = (contract_filter and
                       [('sale_contract_id', 'any', contract_filter)] or [])
            overdue += [('state', 'in', list(OPEN_INSTALMENT)),
                        ('date_due', '<', today), ('is_cancelled', '=', False)]
            kpis.append({
                'key': 'instalments_overdue', 'label': _("Overdue Instalments"),
                'icon': 'fa-exclamation-circle', 'tone': 'danger',
                'value': self._sum(Instalment, overdue, 'residual_amount'),
                'format': 'monetary',
                # Arrears rising is bad news wearing a positive sign.
                'higher_is_better': False,
                'warning': True,
                'warning_label': _("Collection needed"),
                'hint': _("Unpaid balance of instalments past their due date."),
                'drill': True,
            })
        return kpis

    # ------------------------------------------------------------------
    @api.model
    def _monthly_sum(self, model, domain, field, measure, months):
        if not self._can_read(model):
            return []
        groups = self.env[model]._read_group(
            domain + [(field, '>=', months[0])],
            groupby=['%s:month' % field], aggregates=['%s:sum' % measure])
        found = {}
        for bucket, total in groups:
            key = bucket.date() if hasattr(bucket, 'date') else bucket
            if key:
                found['%d-%02d' % (key.year, key.month)] = total or 0.0
        # A month with no sales is a real zero. Dropping it would shift every
        # later point left and draw a trend that never happened.
        return [round(found.get('%d-%02d' % (m.year, m.month), 0.0), 2)
                for m in months]

    @api.model
    def _monthly_collections(self, today, months):
        """Cash received against instalments, per month, by payment date."""
        if not self._can_read_collections():
            return []
        Payment = self.env['account.payment']
        domain = self._company_domain() + [
            ('date', '>=', months[0]), ('date', '<=', today),
            ('payment_type', '=', 'inbound'), ('partner_type', '=', 'customer'),
            ('state', 'not in', ('draft', 'canceled', 'rejected')),
            ('currency_id', '=', self.env.company.currency_id.id),
        ]
        received = Payment.search(domain)
        instalment_moves = self.env['realestate.sale.installment'].search(
            [('move_id', 'in', received.reconciled_invoice_ids.ids)]).move_id
        by_month = {}
        for payment in received:
            if not (payment.reconciled_invoice_ids & instalment_moves):
                continue
            key = '%d-%02d' % (payment.date.year, payment.date.month)
            by_month[key] = by_month.get(key, 0.0) + payment.amount
        return [round(by_month.get('%d-%02d' % (m.year, m.month), 0.0), 2)
                for m in months]

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    @api.model
    def _inventory_funnel(self, filters):
        """Where every unit in the project stands commercially.

        One unit, one bucket -- `commercial_status` is a single field, so the
        segments add up to the inventory instead of overlapping.
        """
        if not self._can_read('realestate.property'):
            return {'segments': [], 'total': 0}
        Property = self.env['realestate.property']
        base = (self._unit_domain(filters)
                + [('hierarchy_level', '=', 'unit'), ('project_id', '!=', False)])
        states = [
            ('available', _("Available"), 'success'),
            ('held', _("On Hold"), 'info'),
            ('reserved', _("Reserved"), 'warning'),
            ('contracted', _("Contracted"), 'primary'),
            ('sold', _("Sold"), 'primary'),
            ('unreleased', _("Not Released"), 'neutral'),
            ('blocked', _("Blocked"), 'danger'),
        ]
        segments = [{'key': 'units_%s' % key, 'label': label, 'tone': tone,
                     'value': Property.search_count(
                         base + [('commercial_status', '=', key)])}
                    for key, label, tone in states]
        released = sum(s['value'] for s in segments
                       if s['key'] != 'units_unreleased')
        # The total is every unit in the project, not the released subset: the
        # table below counts `is_released_for_sale`, which is a different
        # question, and two panels on one screen labelled "released" with
        # different numbers is worse than either.
        return {'segments': [s for s in segments if s['value']],
                'total': released + sum(s['value'] for s in segments
                                        if s['key'] == 'units_unreleased'),
                'total_label': _("Units in Inventory")}

    @api.model
    def _sales_velocity(self, today, filters, months):
        if not self._can_read('realestate.sale.contract'):
            return None
        Contract = self.env['realestate.sale.contract']
        signed = (self._company_domain() + self._contract_domain(filters)
                  + [('state', 'in', list(LIVE_CONTRACT))])
        value = self._monthly_sum('realestate.sale.contract', signed,
                                  'signing_date', 'sale_price', months)
        groups = Contract._read_group(
            signed + [('signing_date', '>=', months[0])],
            groupby=['signing_date:month'], aggregates=['__count'])
        found = {}
        for bucket, count in groups:
            key = bucket.date() if hasattr(bucket, 'date') else bucket
            if key:
                found['%d-%02d' % (key.year, key.month)] = count
        units = [found.get('%d-%02d' % (m.year, m.month), 0) for m in months]
        return {'labels': [m.strftime('%b') for m in months],
                'value': value, 'units': units}

    @api.model
    def _collections_chart(self, today, filters, months):
        """Scheduled against collected, month by month.

        The gap between the two bars IS the collection problem, which is why
        they belong on one chart rather than two cards.
        """
        if not self._can_read('realestate.sale.installment'):
            return None
        contract_filter = self._contract_domain(filters)
        base = ([('sale_contract_id', 'any', contract_filter)]
                if contract_filter else []) + [('is_cancelled', '=', False)]
        scheduled = self._monthly_sum(
            'realestate.sale.installment', base, 'date_due',
            'current_amount', months)
        collected = self._monthly_sum(
            'realestate.sale.installment', base, 'date_due',
            'paid_amount', months)
        total_due = sum(scheduled)
        return {
            'labels': [m.strftime('%b') for m in months],
            'scheduled': scheduled,
            'collected': collected,
            'rate': round(sum(collected) / total_due * 100.0, 1) if total_due else 0.0,
        }

    # ------------------------------------------------------------------
    # Problems and work
    # ------------------------------------------------------------------
    @api.model
    def _attention(self, today, filters):
        rows = []
        contract_filter = self._contract_domain(filters)
        if self._can_read('realestate.sale.installment'):
            Instalment = self.env['realestate.sale.installment']
            overdue = (([('sale_contract_id', 'any', contract_filter)]
                        if contract_filter else [])
                       + [('state', 'in', list(OPEN_INSTALMENT)),
                          ('is_cancelled', '=', False)])
            rows.append({
                'key': 'instalments_overdue_count',
                'label': _("Instalments past due"),
                'sublabel': _("Unpaid after their due date"),
                'value': Instalment.search_count(
                    overdue + [('date_due', '<', today)]),
                'severity': 'critical', 'icon': 'fa-exclamation-circle',
            })
            rows.append({
                'key': 'instalments_due_30', 'label': _("Falling due in 30 days"),
                'sublabel': _("Invoice and chase before they age"),
                'value': Instalment.search_count(
                    overdue + [('date_due', '>=', today),
                               ('date_due', '<=', today + timedelta(days=30))]),
                'severity': 'warning', 'icon': 'fa-calendar',
            })
        if self._can_read('realestate.unit.reservation'):
            rows.append({
                'key': 'reservations_expiring', 'label': _("Holds expiring in 48 hours"),
                'sublabel': _("The unit returns to the market"),
                'value': self.env['realestate.unit.reservation'].search_count([
                    ('state', 'in', ('hold', 'pending_payment')),
                    ('hold_expiry_at', '!=', False),
                    ('hold_expiry_at', '<=',
                     fields.Datetime.to_datetime(today + timedelta(days=2)))]),
                'severity': 'warning', 'icon': 'fa-hourglass-half',
            })
        if self._can_read('realestate.sale.contract'):
            rows.append({
                'key': 'contracts_unsigned', 'label': _("Contracts awaiting signature"),
                'value': self.env['realestate.sale.contract'].search_count(
                    contract_filter + [('state', '=', 'pending_signature')]),
                'icon': 'fa-pencil-square-o',
            })
        if self._can_read('realestate.property'):
            rows.append({
                'key': 'units_unreleased', 'label': _("Built but never released"),
                'sublabel': _("Inventory nobody can buy"),
                'value': self.env['realestate.property'].search_count(
                    self._unit_domain(filters)
                    + [('hierarchy_level', '=', 'unit'),
                       ('project_id', '!=', False),
                       ('commercial_status', '=', 'unreleased'),
                       ('construction_status', 'in', ('ready', 'delivered'))]),
                'icon': 'fa-lock',
            })
        return rows

    @api.model
    def _work_queue(self, today):
        uid = self.env.uid
        rows = []
        if self._can_read('realestate.sale.contract'):
            Contract = self.env['realestate.sale.contract']
            rows += [
                {'key': 'contracts_to_approve', 'label': _("Contracts to Approve"),
                 'value': Contract.search_count([('state', '=', 'pending_approval')]),
                 'severity': 'warning', 'icon': 'fa-check-square-o'},
                {'key': 'contracts_unsigned_mine', 'label': _("My Contracts Awaiting Signature"),
                 'value': Contract.search_count(
                     [('agent_id', '=', uid), ('state', '=', 'pending_signature')]),
                 'icon': 'fa-pencil-square-o'},
                {'key': 'contracts_draft', 'label': _("Draft Contracts"),
                 'value': Contract.search_count([('state', '=', 'draft')]),
                 'icon': 'fa-file-o'},
            ]
        if self._can_read('realestate.unit.reservation'):
            rows.append({
                'key': 'reservations_pending_payment',
                'label': _("Reservations Awaiting Payment"),
                'value': self.env['realestate.unit.reservation'].search_count(
                    [('state', '=', 'pending_payment')]),
                'severity': 'warning', 'icon': 'fa-money'},
            )
        if self._can_read('realestate.sale.installment'):
            rows.append({
                'key': 'instalments_to_invoice', 'label': _("Instalments to Invoice"),
                'value': self.env['realestate.sale.installment'].search_count([
                    ('state', '=', 'pending'), ('is_cancelled', '=', False),
                    ('date_due', '<=', today + timedelta(days=7))]),
                'icon': 'fa-file-text-o'},
            )
        return rows

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------
    @api.model
    def _projects_table(self, filters):
        """Inventory and money per project, ranked by contracted value."""
        if not (self._can_read('realestate.project')
                and self._can_read('realestate.property')):
            return []
        Property = self.env['realestate.property']
        Contract = self.env['realestate.sale.contract']
        # Limiting the SEARCH was wrong: it took the first twenty projects by
        # id and the only one carrying inventory was the twenty-third, so the
        # table read "no project has released inventory yet" while the
        # donut beside it counted forty-two released units. Narrow to the
        # projects that actually hold units, and limit the OUTPUT instead.
        with_units = Property._read_group(
            [('hierarchy_level', '=', 'unit'), ('project_id', '!=', False)],
            groupby=['project_id'], aggregates=['__count'])
        project_ids = [group[0].id for group in with_units if group[0]]
        if filters.get('project_id'):
            wanted = int(filters['project_id'])
            project_ids = [pid for pid in project_ids if pid == wanted]
        projects = self.env['realestate.project'].browse(project_ids)
        can_read_contracts = self._can_read('realestate.sale.contract')

        rows = []
        for project in projects:
            base = [('project_id', '=', project.id),
                    ('hierarchy_level', '=', 'unit')]
            units = Property.search_count(base)
            if not units:
                continue
            released = Property.search_count(
                base + [('is_released_for_sale', '=', True)])
            committed = Property.search_count(
                base + [('commercial_status', 'in',
                         ('reserved', 'contracted', 'sold'))])
            value = 0.0
            if can_read_contracts:
                value = self._sum(
                    Contract, [('property_id.project_id', '=', project.id),
                               ('state', 'in', list(LIVE_CONTRACT))], 'sale_price')
            rows.append({
                'id': project.id,
                'project': project.display_name,
                'units': units,
                'released': released,
                'committed': committed,
                'value': value,
                'sell_through': round(committed / released * 100.0, 1) if released else 0.0,
            })
        rows.sort(key=lambda row: -row['value'])
        return rows[:10]

    @api.model
    def _plans_table(self, filters):
        """Which payment plan actually collects.

        A plan that sells well and collects badly is the most expensive thing
        a developer can have, and no single KPI shows it.
        """
        if not (self._can_read('realestate.payment.plan')
                and self._can_read('realestate.sale.contract')
                and self._can_read('realestate.sale.installment')):
            return []
        Contract = self.env['realestate.sale.contract']
        Instalment = self.env['realestate.sale.installment']
        plans = self.env['realestate.payment.plan'].search(
            [('state', 'in', ('active', 'expired'))], limit=15)
        rows = []
        for plan in plans:
            contracts = Contract.search(
                [('payment_plan_id', '=', plan.id),
                 ('state', 'in', list(LIVE_CONTRACT))])
            if not contracts:
                continue
            base = [('sale_contract_id', 'in', contracts.ids),
                    ('is_cancelled', '=', False)]
            scheduled = self._sum(Instalment, base, 'current_amount')
            collected = self._sum(Instalment, base, 'paid_amount')
            rows.append({
                'id': plan.id,
                'plan': plan.display_name,
                'contracts': len(contracts),
                'scheduled': scheduled,
                'collected': collected,
                'rate': round(collected / scheduled * 100.0, 1) if scheduled else 0.0,
            })
        rows.sort(key=lambda row: -row['scheduled'])
        return rows

    # ==================================================================
    # Geography
    # ==================================================================
    #: Commercial state of a project -> the tone its pin wears.
    PROJECT_TONE = {
        'selling': 'success',
        'pre_launch': 'info',
        'planning': 'neutral',
        'sold_out': 'primary',
        'closed': 'neutral',
    }

    @api.model
    def _project_map(self, filters):
        """Projects as pins, with their plot boundary where one is drawn.

        A boundary says far more than a pin -- it shows the land, not a guess
        at its centre -- so the polygon is sent whenever the project has one.
        """
        if not self._can_read('realestate.project'):
            return {'points': [], 'legend': []}
        domain = (self._company_domain()
                  + [('latitude', '!=', 0), ('longitude', '!=', 0)])
        if filters.get('project_id'):
            domain.append(('id', '=', int(filters['project_id'])))
        projects = self.env['realestate.project'].search(domain, limit=200)

        boundaries = {}
        if projects and self._can_read('realestate.project.boundary.point'):
            for point in self.env['realestate.project.boundary.point'].search_read(
                    [('project_id', 'in', projects.ids)],
                    ['project_id', 'latitude', 'longitude'],
                    order='project_id, sequence'):
                boundaries.setdefault(point['project_id'][0], []).append(
                    [point['latitude'], point['longitude']])

        Property = self.env['realestate.property']
        counts = {}
        if self._can_read('realestate.property'):
            for project, total in Property._read_group(
                    [('project_id', 'in', projects.ids),
                     ('hierarchy_level', '=', 'unit')],
                    groupby=['project_id'], aggregates=['__count']):
                if project:
                    counts[project.id] = total

        # Tallied by STATE, not by tone. Planning and Closed share the
        # `neutral` tone, so counting tones gave both legend rows the same
        # number and the figures did not add up to the pin count.
        points, tally = [], {}
        for project in projects:
            tone = self.PROJECT_TONE.get(project.commercial_state, 'primary')
            tally[project.commercial_state] = tally.get(
                project.commercial_state, 0) + 1
            units = counts.get(project.id, 0)
            points.append({
                'id': project.id,
                'label': project.display_name,
                'sublabel': '%s · %s · %s' % (
                    project.city or _("Location not set"),
                    _("%s units", units),
                    dict(project._fields['commercial_state'].selection).get(
                        project.commercial_state, project.commercial_state)),
                'lat': project.latitude,
                'lng': project.longitude,
                'tone': tone,
                'boundary': boundaries.get(project.id, []),
            })
        legend_labels = dict(self.env['realestate.project']._fields[
            'commercial_state'].selection)
        legend = [{'tone': tone, 'label': legend_labels.get(state, state),
                   'count': tally[state]}
                  for state, tone in self.PROJECT_TONE.items()
                  if tally.get(state)]
        return {'points': points, 'legend': legend}

    # ==================================================================
    # Further analysis
    # ==================================================================
    @api.model
    def _price_by_type(self, filters):
        """Average price per square metre by unit type.

        Headline inventory counts hide the economics: forty apartments and
        eight shops are not the same business, and the rate per sqm is where
        that shows. Units with no area are excluded rather than counted as
        zero, which would drag every average towards nothing.
        """
        if not self._can_read('realestate.property'):
            return []
        Property = self.env['realestate.property']
        base = (self._unit_domain(filters)
                + [('hierarchy_level', '=', 'unit'), ('project_id', '!=', False),
                   ('area_sqm', '>', 0), ('base_price', '>', 0)])
        field = Property._fields.get('usage_category')
        labels = dict(field.selection) if field and not callable(
            field.selection) else {}
        rows = []
        for category, in Property._read_group(base, groupby=['usage_category']):
            units = Property.search(base + [('usage_category', '=', category)])
            if not units:
                continue
            area = sum(units.mapped('area_sqm'))
            value = sum(units.mapped('base_price'))
            sold = len(units.filtered(
                lambda unit: unit.commercial_status in
                ('reserved', 'contracted', 'sold')))
            rows.append({
                'id': False,
                'type': labels.get(category, category or _("Unclassified")),
                'units': len(units),
                'avg_area': round(area / len(units), 1),
                'rate': round(value / area, 0) if area else 0.0,
                'absorption': round(sold / len(units) * 100.0, 1),
            })
        rows.sort(key=lambda row: -row['units'])
        return rows

    @api.model
    def _instalment_ageing(self, today, filters):
        """Overdue balance by how long it has been overdue.

        Thirty days late and a year late are not the same receivable, and a
        single arrears total treats them as if they were.
        """
        if not self._can_read('realestate.sale.installment'):
            return None
        Instalment = self.env['realestate.sale.installment']
        contract_filter = self._contract_domain(filters)
        base = (([('sale_contract_id', 'any', contract_filter)]
                 if contract_filter else [])
                + [('state', 'in', list(OPEN_INSTALMENT)),
                   ('is_cancelled', '=', False)])
        buckets = [
            (_("1-30 days"), 1, 30), (_("31-60 days"), 31, 60),
            (_("61-90 days"), 61, 90), (_("91-180 days"), 91, 180),
            (_("Over 180 days"), 181, 36500),
        ]
        labels, amounts = [], []
        for label, start, end in buckets:
            amounts.append(round(self._sum(Instalment, base + [
                ('date_due', '<=', today - timedelta(days=start)),
                ('date_due', '>=', today - timedelta(days=end)),
            ], 'residual_amount'), 2))
            labels.append(label)
        return {'labels': labels, 'amounts': amounts}

    @api.model
    def _pipeline(self, today, filters):
        """Reservation to contract, stage by stage, with the drop-off visible.

        Counts, not money: a funnel is about how many survive each step, and
        mixing value into it hides a high conversion of small deals behind one
        large one.
        """
        rows = []
        if self._can_read('realestate.unit.reservation'):
            Reservation = self.env['realestate.unit.reservation']
            project = ([('property_id.project_id', '=', int(filters['project_id']))]
                       if filters.get('project_id') else [])
            for key, label, states, tone in (
                    ('res_hold', _("On Hold"), ('hold',), 'info'),
                    ('res_pending', _("Awaiting Payment"), ('pending_payment',), 'warning'),
                    ('res_booked', _("Booked"), ('booked', 'confirmed'), 'primary'),
                    ('res_lost', _("Expired or Cancelled"),
                     ('expired', 'cancelled', 'rejected'), 'danger')):
                rows.append({'key': key, 'label': label, 'tone': tone,
                             'value': Reservation.search_count(
                                 project + [('state', 'in', list(states))])})
        if self._can_read('realestate.sale.contract'):
            contract = self._contract_domain(filters)
            rows.append({'key': 'contracts_signed', 'label': _("Contracted"),
                         'tone': 'success',
                         'value': self.env['realestate.sale.contract'].search_count(
                             contract + [('state', 'in', list(LIVE_CONTRACT))])})
        return {'segments': [row for row in rows if row['value']],
                'total': sum(row['value'] for row in rows),
                'total_label': _("In the Pipeline")}

    @api.model
    def _upcoming_handovers(self, today, filters):
        """Contracts due to hand over, and whether the money is in.

        Handing over a unit whose instalments are unpaid is how a developer
        loses its only leverage, so the outstanding balance is on the row.
        """
        if not self._can_read('realestate.sale.contract'):
            return []
        contracts = self.env['realestate.sale.contract'].search(
            self._contract_domain(filters)
            + [('state', 'in', list(LIVE_CONTRACT)),
               ('expected_handover_date', '!=', False),
               ('expected_handover_date', '<=', today + relativedelta(months=12))],
            order='expected_handover_date', limit=10)
        rows = []
        for contract in contracts:
            outstanding = contract.scheduled_amount - contract.paid_amount
            rows.append({
                'id': contract.id,
                'contract': contract.name,
                'unit': contract.property_id.display_name or '—',
                'buyer': contract.partner_id.display_name or '—',
                'handover': fields.Date.to_string(contract.expected_handover_date),
                'outstanding': max(outstanding, 0.0),
                'status': _("Cleared") if outstanding <= 0 else _("Balance due"),
                'status_tone': 'success' if outstanding <= 0 else 'danger',
            })
        return rows

    @api.model
    def _top_buyers(self, filters):
        """Who the exposure is concentrated in."""
        if not self._can_read('realestate.sale.contract'):
            return []
        contracts = self.env['realestate.sale.contract'].search(
            self._contract_domain(filters)
            + [('state', 'in', list(LIVE_CONTRACT))])
        by_partner = {}
        for contract in contracts:
            entry = by_partner.setdefault(contract.partner_id.id, {
                'id': contract.partner_id.id,
                'buyer': contract.partner_id.display_name,
                'contracts': 0, 'value': 0.0, 'outstanding': 0.0,
            })
            entry['contracts'] += 1
            entry['value'] += contract.sale_price
            entry['outstanding'] += max(
                contract.scheduled_amount - contract.paid_amount, 0.0)
        rows = sorted(by_partner.values(), key=lambda row: -row['value'])
        return rows[:8]

    # ------------------------------------------------------------------
    @api.model
    def _overview_quick_actions(self):
        actions = []
        if self._can_read('realestate.unit.reservation'):
            actions.append({'key': 'new_reservation', 'label': _("New Reservation"),
                            'icon': 'fa-plus'})
        if self._can_read('realestate.sale.contract'):
            actions.append({'key': 'new_contract', 'label': _("New Contract"),
                            'icon': 'fa-file-text-o'})
        if self._can_read('realestate.property'):
            actions.append({'key': 'inventory', 'label': _("Inventory"),
                            'icon': 'fa-th'})
        return actions

    # ==================================================================
    # Drill-through: one place builds the figure and the list behind it
    # ==================================================================
    @api.model
    def _drill_targets(self, today):
        month_start = today.replace(day=1)
        uid = self.env.uid
        open_inst = list(OPEN_INSTALMENT)
        return {
            'units_available': (_("Units Available for Sale"), 'realestate.property',
                                [('project_id', '!=', False),
                                 ('is_available_for_sale', '=', True)]),
            'sell_through': (_("Committed Units"), 'realestate.property',
                             [('project_id', '!=', False),
                              ('hierarchy_level', '=', 'unit'),
                              ('commercial_status', 'in',
                               ('reserved', 'contracted', 'sold'))]),
            'reservations_active': (_("Active Reservations"),
                                    'realestate.unit.reservation',
                                    [('state', 'in', list(LIVE_RESERVATION))]),
            'sales_value_mtd': (_("Contracts Signed This Month"),
                                'realestate.sale.contract',
                                [('state', 'in', list(LIVE_CONTRACT)),
                                 ('signing_date', '>=', month_start),
                                 ('signing_date', '<=', today)]),
            'instalments_overdue': (_("Overdue Instalments"),
                                    'realestate.sale.installment',
                                    [('state', 'in', open_inst),
                                     ('date_due', '<', today),
                                     ('is_cancelled', '=', False)]),
            'instalments_overdue_count': (_("Overdue Instalments"),
                                          'realestate.sale.installment',
                                          [('state', 'in', open_inst),
                                           ('date_due', '<', today),
                                           ('is_cancelled', '=', False)]),
            'instalments_due_30': (_("Instalments Due in 30 Days"),
                                   'realestate.sale.installment',
                                   [('state', 'in', open_inst),
                                    ('date_due', '>=', today),
                                    ('date_due', '<=', today + timedelta(days=30)),
                                    ('is_cancelled', '=', False)]),
            'instalments_to_invoice': (_("Instalments to Invoice"),
                                       'realestate.sale.installment',
                                       [('state', '=', 'pending'),
                                        ('is_cancelled', '=', False),
                                        ('date_due', '<=', today + timedelta(days=7))]),
            'reservations_expiring': (_("Holds Expiring"), 'realestate.unit.reservation',
                                      [('state', 'in', ('hold', 'pending_payment')),
                                       ('hold_expiry_at', '!=', False),
                                       ('hold_expiry_at', '<=',
                                        fields.Datetime.to_datetime(
                                            today + timedelta(days=2)))]),
            'reservations_pending_payment': (_("Reservations Awaiting Payment"),
                                             'realestate.unit.reservation',
                                             [('state', '=', 'pending_payment')]),
            'contracts_unsigned': (_("Awaiting Signature"), 'realestate.sale.contract',
                                   [('state', '=', 'pending_signature')]),
            'contracts_unsigned_mine': (_("My Contracts Awaiting Signature"),
                                        'realestate.sale.contract',
                                        [('agent_id', '=', uid),
                                         ('state', '=', 'pending_signature')]),
            'contracts_to_approve': (_("Contracts to Approve"),
                                     'realestate.sale.contract',
                                     [('state', '=', 'pending_approval')]),
            'contracts_draft': (_("Draft Contracts"), 'realestate.sale.contract',
                                [('state', '=', 'draft')]),
            'units_unreleased': (_("Built but Unreleased"), 'realestate.property',
                                 [('hierarchy_level', '=', 'unit'),
                                  ('project_id', '!=', False),
                                  ('commercial_status', '=', 'unreleased'),
                                  ('construction_status', 'in',
                                   ('ready', 'delivered'))]),
        }

    @api.model
    def action_drill(self, key, scope='team'):
        today = fields.Date.context_today(self)
        # Collections is its own method because the figure is a filtered set
        # of payment ids, not a domain anybody can rebuild from the key.
        if key == 'collected_mtd':
            return self.action_collected_mtd()
        target = self._drill_targets(today).get(key)
        if not target and key.startswith('units_'):
            target = (_("Units"), 'realestate.property',
                      [('hierarchy_level', '=', 'unit'),
                       ('project_id', '!=', False),
                       ('commercial_status', '=', key[len('units_'):])])
        if not target:
            raise UserError(_("Unknown dashboard figure '%s'.", key))
        name, model, domain = target
        if not self._can_read(model):
            raise UserError(_("These records are not available to you."))
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': model,
            'views': [[False, 'list'], [False, 'form']],
            'view_mode': 'list,form',
            'domain': domain,
            'context': {'create': False},
            'target': 'current',
        }

    @api.model
    def action_quick(self, key):
        models_by_key = {
            'new_reservation': 'realestate.unit.reservation',
            'new_contract': 'realestate.sale.contract',
        }
        if key == 'inventory':
            if not self._can_read('realestate.property'):
                raise UserError(_("These records are not available to you."))
            return {
                'type': 'ir.actions.act_window',
                'name': _("Inventory"),
                'res_model': 'realestate.property',
                'views': [[False, 'list'], [False, 'form']],
                'view_mode': 'list,form',
                'domain': [('hierarchy_level', '=', 'unit'),
                           ('project_id', '!=', False)],
                'target': 'current',
            }
        model = models_by_key.get(key)
        if not model or not self._can_read(model):
            raise UserError(_("Unknown dashboard action '%s'.", key))
        return {
            'type': 'ir.actions.act_window',
            'name': _("New"),
            'res_model': model,
            'views': [[False, 'form']],
            'view_mode': 'form',
            'target': 'current',
        }
