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

from odoo import _, models

LIVE_LEASE = ('active', 'notice')
RESERVATION_ACTIVE = ('hold', 'pending_payment', 'booked')
INSTALMENT_OPEN = ('invoiced', 'partially_paid', 'overdue', 'pending')
CHECK_ON_HAND = ('draft', 'registered')


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
        return [
            {'key': 'occupancy', 'label': _("Occupancy"), 'model': Property, 'domain': occupied_domain,
             'value': round(occupied / leasable * 100.0, 1) if leasable else 0.0, 'format': 'percent',
             'action_name': _("Occupied Units"), 'hint': _("Occupied units as a share of leasable units.")},
            {'key': 'outstanding_rent', 'label': _("Outstanding Rent"), 'model': 'realestate.contract.payment',
             'domain': self._money('realestate.contract.payment',
                                   [('state', '=', 'invoiced'), ('amount_residual', '>', 0)]),
             'measure': 'amount_residual', 'warning_above': 0},
            {'key': 'leases_expiring', 'label': _("Leases Expiring in 90 Days"), 'model': 'realestate.contract',
             'domain': [('lifecycle_state', 'in', LIVE_LEASE), ('end_date', '>=', today),
                        ('end_date', '<=', today + timedelta(days=90))]},
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
             'hint': _("Money received this month against sale instalments, by "
                       "payment date — whenever the instalment itself falls due.")},
        ]

    def _sales_tiles(self, today):
        Instalment = 'realestate.sale.installment'
        return [
            {'key': 'units_for_sale', 'label': _("Units Available for Sale"), 'model': 'realestate.property',
             'domain': [('project_id', '!=', False), ('is_available_for_sale', '=', True)]},
            {'key': 'reservations', 'label': _("Active Reservations"), 'model': 'realestate.unit.reservation',
             'domain': [('state', 'in', RESERVATION_ACTIVE)]},
            {'key': 'instalments_overdue', 'label': _("Overdue Instalments"), 'model': Instalment,
             'domain': self._money(Instalment, [('state', 'in', INSTALMENT_OPEN), ('date_due', '<', today),
                                                ('is_cancelled', '=', False)]),
             'measure': 'residual_amount', 'warning_above': 0},
        ] + self._collected_tiles(today)

    def _construction_tiles(self, today):
        return [
            {'key': 'projects_building', 'label': _("Projects in Construction"), 'model': 'realestate.project',
             'domain': [('state', '=', 'construction')]},
            {'key': 'milestones_delayed', 'label': _("Delayed Milestones"),
             'model': 'realestate.construction.milestone', 'domain': [('state', '=', 'delayed')],
             'warning_above': 0},
        ]

    def _treasury_tiles(self, today):
        Check = 'realestate.check'
        return [
            {'key': 'cheques_due_30', 'label': _("Cheques Due in 30 Days"), 'model': Check,
             'domain': self._money(Check, [('state', 'in', CHECK_ON_HAND), ('due_date', '>=', today),
                                           ('due_date', '<=', today + timedelta(days=30))]),
             'measure': 'amount'},
            {'key': 'bounces_unresolved', 'label': _("Unresolved Bounced Cheques"), 'model': 'realestate.check.bounce',
             'domain': [('resolution', '=', 'pending')], 'warning_above': 0},
        ]

    def _dashboard_sections(self, scope):
        today = self._today()
        return [
            {'id': 'leasing', 'title': _("Leasing"), 'icon': 'fa-building-o', 'tiles': self._leasing_tiles(today)},
            {'id': 'sales', 'title': _("Sales"), 'icon': 'fa-handshake-o', 'tiles': self._sales_tiles(today)},
            {'id': 'construction', 'title': _("Construction"), 'icon': 'fa-cubes',
             'tiles': self._construction_tiles(today)},
            {'id': 'treasury', 'title': _("Treasury"), 'icon': 'fa-bank', 'tiles': self._treasury_tiles(today)},
            {'id': 'investment', 'title': _("Investment"), 'icon': 'fa-line-chart', 'tiles': [
                {'key': 'studies_approved', 'label': _("Approved Feasibility Studies"),
                 'model': 'realestate.investment.feasibility', 'domain': [('state', '=', 'approved')]},
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
        return [{
            'key': 'receivables_at_risk', 'title': _("Receivables at Risk"),
            'subtitle': _("Money due and not received, in %s", self.env.company.currency_id.name),
            'type': 'bar', 'labels': labels,
            'series': [{'name': 'amount', 'label': _("Amount"), 'data': data, 'format': 'monetary'}],
            'drill': drill,
        }]
