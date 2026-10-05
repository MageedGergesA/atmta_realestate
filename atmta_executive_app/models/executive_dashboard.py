# -*- coding: utf-8 -*-
"""Executive dashboard: one headline per application, in the company currency.

It invents no figure. Occupancy is the Rental dashboard's own rule, the
instalment and cheque figures use the domains the Developer and Treasury
dashboards count with, and every tile opens the records it counted. The one
figure that is not another dashboard's is collections, which is read from the
payments because that is the only place the date money arrived is recorded.
Money is filtered to the company currency so amounts in different currencies
are never added together.
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, models

LIVE_LEASE = ('active', 'notice')
RESERVATION_ACTIVE = ('hold', 'pending_payment', 'booked')
INSTALMENT_OPEN = ('invoiced', 'partially_paid', 'overdue', 'pending')
CHECK_ON_HAND = ('draft', 'registered')
#: An NCR is finished when it is closed, verified, rejected or void. 'draft'
#: and 'reopened' are not finished, so "not closed" has to name the terminal
#: states rather than guess at a generic pair.
NCR_CLOSED = ('closed', 'verified', 'rejected', 'void')


class ExecutiveDashboard(models.AbstractModel):
    _name = 'realestate.executive.dashboard'
    _inherit = 'atmta.dashboard.provider'
    _description = 'Executive Dashboard'

    def _dashboard_title(self):
        return _("Suite Overview")

    def _money(self, model_name, domain):
        """Restrict a money domain to the company currency when the model has one."""
        if 'currency_id' in self.env[model_name]._fields:
            return domain + [('currency_id', '=', self.env.company.currency_id.id)]
        return domain

    def _money_note(self):
        """What `_money` leaves out, said on the tile.

        A total that silently drops the cheque written in dollars is not the
        figure its label promises; Rental's own arrears tile names the
        currency in its hint, and these tiles count the same kind of money.
        """
        return _("Counted in %s only: amounts in another currency are not added in.",
                 self.env.company.currency_id.name)

    # ------------------------------------------------------------------
    # Tile specs
    # ------------------------------------------------------------------
    def _leasing_tiles(self, today):
        Property = 'realestate.property'
        if not self._can_read(Property) or not self._can_read('realestate.contract'):
            return []
        occupied_domain = [('is_leasable', '=', True)] + \
            self.env['realestate.rental.dashboard']._occupied_on(today)
        Model = self.env[Property]
        leasable = Model.search_count([('is_leasable', '=', True)] + self._company_domain(Property))
        occupied = Model.search_count(occupied_domain + self._company_domain(Property))
        # A share of nothing is not 0%. With no leasable unit there is no
        # occupancy to report, and a zero would be read as an empty portfolio,
        # so the figure is dropped the way a tile nobody may read is.
        occupancy = [
            {'key': 'occupancy', 'label': _("Occupancy"), 'model': Property, 'domain': occupied_domain,
             'value': round(occupied / leasable * 100.0, 1), 'format': 'percent',
             'action_name': _("Occupied Units"), 'icon': 'fa-pie-chart', 'tone': 'success',
             'hint': _("Occupied units as a share of leasable units.")},
        ] if leasable else []
        return occupancy + [
            {'key': 'outstanding_rent', 'label': _("Outstanding Rent"), 'model': 'realestate.contract.payment',
             'domain': self._money('realestate.contract.payment',
                                   [('state', '=', 'invoiced'), ('amount_residual', '>', 0)]),
             'measure': 'amount_residual', 'warning_above': 0, 'icon': 'fa-money',
             'tone': 'danger', 'higher_is_better': False,
             'warning_label': _("Collection needed"), 'hint': self._money_note()},
            {'key': 'leases_expiring', 'label': _("Leases Expiring in 90 Days"), 'model': 'realestate.contract',
             'domain': [('lifecycle_state', 'in', LIVE_LEASE), ('end_date', '>=', today),
                        ('end_date', '<=', today + timedelta(days=90))],
             'icon': 'fa-calendar-times-o', 'tone': 'warning', 'higher_is_better': False},
            {'key': 'leases_live', 'label': _("Live Leases"), 'model': 'realestate.contract',
             'domain': [('lifecycle_state', 'in', LIVE_LEASE)],
             'icon': 'fa-file-text-o', 'tone': 'primary',
             'trend_field': 'start_date', 'trend_granularity': 'month'},
            {'key': 'units_leasable', 'label': _("Leasable Units"), 'model': Property,
             'domain': [('is_leasable', '=', True)],
             'icon': 'fa-building', 'tone': 'neutral'},
        ]

    def _collected_tiles(self, today):
        """Instalment money actually received this month, by payment date.

        An instalment carries no payment date: `paid_amount` is derived from
        its invoice's residual, so summing it over the instalments *due* this
        month answers "how much of what fell due has been paid", not "how much
        money came in" -- 920,000 banked today against an instalment due in two
        years moved the figure by nothing. A collections figure is read as
        cash, so it is taken from the payments themselves.

        It stays a *sales* figure: only the payments reconciled with an
        instalment's invoice are counted, not every customer receipt (rent has
        its own tile). A payment is counted for its full amount, which is what
        the list the tile opens shows; the wizard the instalment pays through
        registers one payment per invoice, so the two are the same money.
        """
        Instalment = 'realestate.sale.installment'
        # The payments are the figure, so without the right to read them there
        # is no figure -- a tile the user cannot open is dropped, never shown
        # as a zero.
        if not (self._can_read('account.payment') and self._can_read('account.move')
                and self._can_read(Instalment)):
            return []
        Payment = 'account.payment'
        domain = self._money(Payment, [
            ('date', '>=', today.replace(day=1)), ('date', '<=', today),
            ('payment_type', '=', 'inbound'), ('partner_type', '=', 'customer'),
            # Draft, cancelled and rejected payments are not money received.
            ('state', 'not in', ('draft', 'canceled', 'rejected')),
        ])
        received = self.env[Payment].search(domain + self._company_domain(Payment))
        instalment_moves = self.env[Instalment].search(
            [('move_id', 'in', received.reconciled_invoice_ids.ids)]).move_id
        collected = received.filtered(lambda p: p.reconciled_invoice_ids & instalment_moves)
        return [
            {'key': 'collected_month', 'label': _("Instalments Collected This Month"),
             'model': Payment, 'domain': domain + [('id', 'in', collected.ids)],
             'measure': 'amount', 'action_name': _("Payments Received"),
             'icon': 'fa-credit-card', 'tone': 'success',
             # Cash received is a flow, so a month-by-month trend is a real
             # series rather than a reconstruction of a current state.
             'trend_field': 'date', 'trend_granularity': 'month', 'trend_buckets': 6,
             'hint': _("Money received this month against sale instalments, by "
                       "payment date — whenever the instalment itself falls due.")
                     + " " + self._money_note()},
        ]

    def _sales_tiles(self, today):
        Instalment = 'realestate.sale.installment'
        return [
            {'key': 'units_for_sale', 'label': _("Units Available for Sale"), 'model': 'realestate.property',
             'domain': [('project_id', '!=', False), ('is_available_for_sale', '=', True)],
             'icon': 'fa-home', 'tone': 'info'},
            {'key': 'reservations', 'label': _("Active Reservations"), 'model': 'realestate.unit.reservation',
             'domain': [('state', 'in', RESERVATION_ACTIVE)],
             'icon': 'fa-bookmark-o', 'tone': 'primary'},
            {'key': 'instalments_overdue', 'label': _("Overdue Instalments"), 'model': Instalment,
             'domain': self._money(Instalment, [('state', 'in', INSTALMENT_OPEN), ('date_due', '<', today),
                                                ('is_cancelled', '=', False)]),
             'measure': 'residual_amount', 'warning_above': 0, 'icon': 'fa-exclamation-circle',
             'tone': 'danger', 'higher_is_better': False,
             'warning_label': _("Past due"), 'hint': self._money_note()},
            {'key': 'units_sold', 'label': _("Units Sold"), 'model': 'realestate.property',
             'domain': [('project_id', '!=', False), ('commercial_status', '=', 'sold')],
             'icon': 'fa-handshake-o', 'tone': 'success'},
        ] + self._collected_tiles(today)

    def _construction_tiles(self, today):
        return [
            {'key': 'projects_building', 'label': _("Projects in Construction"), 'model': 'realestate.project',
             'domain': [('state', '=', 'construction')], 'icon': 'fa-cubes', 'tone': 'info'},
            {'key': 'milestones_delayed', 'label': _("Delayed Milestones"),
             'model': 'realestate.construction.milestone', 'domain': [('state', '=', 'delayed')],
             'warning_above': 0, 'icon': 'fa-clock-o', 'tone': 'warning',
             'higher_is_better': False, 'warning_label': _("Behind schedule")},
            {'key': 'ncrs_open', 'label': _("Open Non-Conformances"),
             'model': 'realestate.construction.ncr',
             'domain': [('state', 'not in', NCR_CLOSED)],
             'icon': 'fa-exclamation-triangle', 'tone': 'danger',
             'higher_is_better': False,
             'hint': _("Work that did not meet specification and has not yet "
                       "been put right.")},
            {'key': 'forecast_overrun', 'label': _("Forecast Over Budget"),
             'value': self._forecast_overrun(), 'format': 'monetary',
             'icon': 'fa-line-chart', 'tone': 'danger', 'higher_is_better': False,
             'qualifier': 'modelled',
             'hint': _("Across projects with an approved forecast: how far the "
                       "estimate at completion exceeds the control budget. A "
                       "project with no approved forecast contributes nothing "
                       "-- that is an absent number, not a zero.")},
        ]

    def _treasury_tiles(self, today):
        Check = 'realestate.check'
        return [
            {'key': 'cheques_due_30', 'label': _("Cheques Due in 30 Days"), 'model': Check,
             'domain': self._money(Check, [('state', 'in', CHECK_ON_HAND), ('due_date', '>=', today),
                                           ('due_date', '<=', today + timedelta(days=30))]),
             'measure': 'amount', 'icon': 'fa-bank', 'tone': 'info',
             'hint': self._money_note()},
            {'key': 'bounces_unresolved', 'label': _("Unresolved Bounced Cheques"), 'model': 'realestate.check.bounce',
             'domain': [('resolution', '=', 'pending')], 'warning_above': 0,
             'icon': 'fa-times-circle', 'tone': 'danger', 'higher_is_better': False,
             'warning_label': _("Unresolved")},
            {'key': 'cheques_on_hand', 'label': _("Cheques On Hand"), 'model': Check,
             'domain': self._money(Check, [('state', 'in', CHECK_ON_HAND)]),
             'measure': 'amount', 'icon': 'fa-archive', 'tone': 'neutral',
             'qualifier': 'paper',
             'hint': _("Face value of paper held. Not money until it clears.")
                     + ' ' + self._money_note()},
            {'key': 'cheques_cleared', 'label': _("Cleared To Date"), 'model': Check,
             'domain': self._money(Check, [('state', '=', 'cleared')]),
             'measure': 'amount', 'icon': 'fa-check-circle', 'tone': 'success',
             'qualifier': 'cash',
             'hint': _("Bank-confirmed. This is money.") + ' ' + self._money_note()},
        ]

    # ------------------------------------------------------------------
    # Cross-application figures
    # ------------------------------------------------------------------
    def _forecast_overrun(self):
        """How far the portfolio's EAC exceeds its control budget.

        Only projects with an APPROVED forecast contribute. A project without
        one has no estimate at completion -- that is an absent number, not a
        zero -- and quietly treating it as on-budget would make the portfolio
        look healthier the less forecasting anybody did.
        """
        if not self._can_read('realestate.project'):
            return 0.0
        Sheet = self.env.get('realestate.construction.cost.sheet')
        if Sheet is None:
            return 0.0
        overrun = 0.0
        for project in self.env['realestate.project'].search(
                self._company_domain('realestate.project')):
            try:
                totals = Sheet.totals_for(project)
            except Exception:                   # noqa: BLE001 - a project may
                continue                        # not be set up for cost control
            if not totals.get('has_forecast'):
                continue
            variance = totals.get('forecast_variance')
            # Variance is budget minus EAC, so an overrun is negative.
            if variance is not None and variance < 0:
                overrun += abs(variance)
        return overrun

    def _portfolio_npv(self):
        if not self._can_read('realestate.investment.feasibility'):
            return 0.0
        studies = self.env['realestate.investment.feasibility'].search(
            [('state', '=', 'approved')])
        return sum(studies.mapped('npv'))

    # ------------------------------------------------------------------
    # What is on fire, across every application
    # ------------------------------------------------------------------
    def _dashboard_alerts(self, scope):
        """The executive's exception list.

        Deliberately cross-application: the point of this screen is that
        nobody has to open five dashboards to find out what is wrong. Ordered
        by consequence, not by which module it came from.
        """
        today = self._today()
        rows = []

        def add(key, label, model, domain, severity=None, icon=None, sublabel=None):
            if not self._can_read(model):
                return
            rows.append({
                'key': key, 'label': label, 'sublabel': sublabel or '',
                'value': self.env[model].search_count(
                    domain + self._company_domain(model)),
                'severity': severity, 'icon': icon or 'fa-circle-o'})

        add('alert_bounced', _("Bounced cheques unresolved"), 'realestate.check.bounce',
            [('resolution', '=', 'pending')], 'critical', 'fa-times-circle',
            _("The instalment behind them is still owed"))
        add('alert_instalments', _("Instalments past due"), 'realestate.sale.installment',
            [('state', 'in', INSTALMENT_OPEN), ('date_due', '<', today),
             ('is_cancelled', '=', False)], 'critical', 'fa-exclamation-circle')
        add('alert_ncr', _("Open non-conformances"), 'realestate.construction.ncr',
            [('state', 'not in', NCR_CLOSED)], 'critical',
            'fa-exclamation-triangle', _("Work that did not meet specification"))
        add('alert_milestones', _("Milestones behind schedule"),
            'realestate.construction.milestone', [('state', '=', 'delayed')],
            'warning', 'fa-clock-o')
        add('alert_arrears', _("Leases in arrears"), 'realestate.contract.payment',
            [('state', '=', 'invoiced'), ('amount_residual', '>', 0),
             ('date_due', '<', today)], 'warning', 'fa-money')
        add('alert_expiring', _("Leases expiring in 90 days"), 'realestate.contract',
            [('lifecycle_state', 'in', LIVE_LEASE), ('end_date', '>=', today),
             ('end_date', '<=', today + timedelta(days=90))], 'warning',
            'fa-calendar-times-o')
        add('alert_snags', _("Critical snags blocking handover"),
            'realestate.snagging.issue',
            [('state', 'in', ('open', 'assigned', 'in_progress')),
             ('severity', '=', 'critical')], 'warning', 'fa-key')
        return rows

    def _dashboard_work_queue(self, scope):
        """Decisions waiting on somebody, not tasks.

        An executive screen's queue is approvals: the things that stop moving
        until a person with authority says yes.
        """
        rows = []

        def add(key, label, model, domain, severity=None, icon=None, sublabel=None):
            if not self._can_read(model):
                return
            rows.append({
                'key': key, 'label': label, 'sublabel': sublabel or '',
                'value': self.env[model].search_count(domain),
                'severity': severity, 'icon': icon or 'fa-gavel'})

        add('wait_budget', _("Budgets Awaiting Approval"),
            'realestate.construction.budget', [('state', '=', 'review')],
            'warning', 'fa-balance-scale')
        add('wait_forecast', _("Forecasts Awaiting Approval"),
            'realestate.construction.forecast', [('state', '=', 'review')],
            None, 'fa-line-chart')
        add('wait_study', _("Feasibility Studies in Draft"),
            'realestate.investment.feasibility', [('state', '=', 'draft')],
            None, 'fa-pencil',
            _("Not a basis for a decision until approved"))
        add('wait_tender', _("Tenders to Award"),
            'realestate.procurement.sourcing.event',
            [('state', '=', 'evaluation')], 'warning', 'fa-gavel')
        add('wait_reservation', _("Reservations Pending Payment"),
            'realestate.unit.reservation', [('state', '=', 'pending_payment')],
            None, 'fa-bookmark-o')
        return rows

    # ------------------------------------------------------------------
    def _dashboard_tables(self, scope):
        """The portfolio, one row per project.

        An executive asks "which project is the problem", and no single
        headline figure can answer that -- it is exactly the question a total
        hides.
        """
        if not self._can_read('realestate.project'):
            return []
        Sheet = self.env.get('realestate.construction.cost.sheet')
        Property = self.env['realestate.property'] if self._can_read(
            'realestate.property') else None
        rows = []
        # Every project is considered, and only the OUTPUT is capped. Limiting
        # the search instead ranks whichever twenty projects the database
        # happened to return first, so the one project that is over budget --
        # the only row this table exists to surface -- can be cut before the
        # sort ever sees it.
        for project in self.env['realestate.project'].search(
                self._company_domain('realestate.project')):
            units = sold = 0
            if Property is not None:
                units = Property.search_count([('project_id', '=', project.id)])
                sold = Property.search_count(
                    [('project_id', '=', project.id),
                     ('commercial_status', '=', 'sold')])
            budget = eac = 0.0
            status, tone = _("No cost data"), 'neutral'
            if Sheet is not None:
                try:
                    totals = Sheet.totals_for(project)
                except Exception:               # noqa: BLE001 - not every
                    totals = None               # project is cost-controlled
                if totals:
                    budget = totals.get('current_budget') or 0.0
                    if totals.get('has_forecast'):
                        eac = totals.get('eac') or 0.0
                        variance = totals.get('forecast_variance')
                        if variance is not None and variance < 0:
                            status, tone = _("Over budget"), 'danger'
                        else:
                            status, tone = _("Within budget"), 'success'
                    elif budget:
                        # Said in words rather than shown as a zero: an EAC
                        # nobody has produced is not an EAC of zero.
                        status, tone = _("No forecast"), 'warning'
            rows.append({
                'id': project.id,
                'project': project.display_name,
                'units': units,
                'sold': sold,
                'budget': budget,
                'eac': eac,
                'status': status,
                'status_tone': tone,
            })
        rows.sort(key=lambda row: (row['status_tone'] != 'danger', -row['budget']))
        rows = rows[:20]
        return [{
            'key': 'portfolio', 'title': _("Portfolio by Project"),
            'icon': 'fa-table', 'span': 'o_ad_col_12',
            'subtitle': _("Projects over budget first — a portfolio total hides "
                          "which one is the problem"),
            'empty_text': _("No project has been created."),
            'model': 'realestate.project',
            'columns': [
                {'key': 'project', 'label': _("Project")},
                {'key': 'units', 'label': _("Units"), 'numeric': True},
                {'key': 'sold', 'label': _("Sold"), 'numeric': True},
                {'key': 'budget', 'label': _("Control Budget"), 'numeric': True,
                 'format': 'monetary'},
                {'key': 'eac', 'label': _("Forecast at Completion"), 'numeric': True,
                 'format': 'monetary'},
                {'key': 'status', 'label': _("Cost Status"), 'type': 'badge'},
            ],
            'rows': rows,
        }]

    def _dashboard_sections(self, scope):
        today = self._today()
        return [
            {'id': 'leasing', 'title': _("Leasing"), 'icon': 'fa-building-o', 'tiles': self._leasing_tiles(today)},
            {'id': 'sales', 'title': _("Sales"), 'icon': 'fa-handshake-o', 'tiles': self._sales_tiles(today)},
            {'id': 'construction', 'title': _("Construction"), 'icon': 'fa-cubes',
             'tiles': self._construction_tiles(today)},
            {'id': 'treasury', 'title': _("Treasury"), 'icon': 'fa-bank', 'tiles': self._treasury_tiles(today)},
            {'id': 'investment', 'title': _("Investment"), 'icon': 'fa-line-chart',
             'subtitle': _("Modelled, not booked — the output of an appraisal, "
                           "not of the ledger"),
             'tiles': [
                {'key': 'studies_approved', 'label': _("Approved Feasibility Studies"),
                 'model': 'realestate.investment.feasibility', 'domain': [('state', '=', 'approved')],
                 'icon': 'fa-check-circle', 'tone': 'success'},
                {'key': 'studies_draft', 'label': _("Studies in Draft"),
                 'model': 'realestate.investment.feasibility', 'domain': [('state', '=', 'draft')],
                 'icon': 'fa-pencil', 'tone': 'warning', 'higher_is_better': False},
                {'key': 'portfolio_npv', 'label': _("Portfolio NPV"),
                 'value': self._portfolio_npv(), 'format': 'monetary',
                 'icon': 'fa-money', 'tone': 'success', 'qualifier': 'modelled',
                 'hint': _("Net present value across approved studies.")},
            ]},
        ]

    def _dashboard_charts(self, scope):
        today = self._today()
        candidates = [
            (_("Rent Arrears"), 'realestate.contract.payment',
             self._money('realestate.contract.payment', [('state', '=', 'invoiced'), ('amount_residual', '>', 0)]),
             'amount_residual'),
            (_("Overdue Instalments"), 'realestate.sale.installment',
             self._money('realestate.sale.installment', [('state', 'in', INSTALMENT_OPEN), ('date_due', '<', today),
                                                          ('is_cancelled', '=', False)]),
             'residual_amount'),
            (_("Matured Cheques Not Presented"), 'realestate.check',
             self._money('realestate.check', [('state', 'in', CHECK_ON_HAND), ('due_date', '<', today)]),
             'amount'),
        ]
        labels, data, drill = [], [], []
        for index, (label, model_name, domain, measure) in enumerate(candidates):
            if not self._can_read(model_name):
                continue
            groups = self.env[model_name]._read_group(
                domain + self._company_domain(model_name), aggregates=[f'{measure}:sum'])
            labels.append(label)
            data.append((groups[0][0] if groups else 0.0) or 0.0)
            drill.append({'key': f'risk_{index}', 'label': label, 'model': model_name, 'domain': domain})
        if not labels:
            return []
        charts = [{
            'key': 'receivables_at_risk', 'title': _("Receivables at Risk"),
            'subtitle': _("Money due and not received, in %s", self.env.company.currency_id.name),
            'type': 'bar', 'span': 'o_ad_col_6', 'labels': labels,
            'series': [{'name': 'amount', 'label': _("Amount"), 'data': data, 'format': 'monetary'}],
            'drill': drill,
        }]
        collections = self._collections_trend(today)
        if collections:
            charts.append(collections)
        return charts

    def _collections_trend(self, today, months=6):
        """Money actually banked, by the month it arrived.

        Read from payments, not from instalments. An instalment carries no
        payment date, so summing what fell due in a month answers a different
        question -- and a collections line that moves when nothing was banked
        is worse than no line at all.
        """
        if not self._can_read('account.payment'):
            return None
        Payment = self.env['account.payment']
        labels, amounts = [], []
        for offset in range(months - 1, -1, -1):
            start = (today.replace(day=1) - relativedelta(months=offset))
            end = start + relativedelta(months=1) - timedelta(days=1)
            groups = Payment._read_group(
                [('state', 'in', ('paid', 'in_process')),
                 ('payment_type', '=', 'inbound'),
                 ('company_id', 'in', self.env.companies.ids),
                 ('currency_id', '=', self.env.company.currency_id.id),
                 ('date', '>=', start), ('date', '<=', end)],
                aggregates=['amount:sum'])
            labels.append(start.strftime('%b'))
            amounts.append(round((groups[0][0] if groups else 0.0) or 0.0, 2))
        if not any(amounts):
            return None
        return {
            'key': 'collections_trend', 'title': _("Collections"),
            'subtitle': _("Money banked, by the month it arrived — in %s",
                          self.env.company.currency_id.name),
            'type': 'bar', 'span': 'o_ad_col_6', 'labels': labels,
            'series': [{'name': 'collected', 'label': _("Collected"),
                        'data': amounts, 'format': 'monetary'}],
        }
