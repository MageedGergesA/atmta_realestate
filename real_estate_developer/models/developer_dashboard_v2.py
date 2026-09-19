# -*- coding: utf-8 -*-
"""Phase 41 — the developer dashboard, computed in the database.

The payload shape is **unchanged**. The existing OWL front end reads specific
keys (`kpis`, `proj_states`, `unit_states`, `velocity`, `top_projects`,
`map_projs`, …), and rebuilding that front end is a separate piece of work.
What changes is how the numbers are produced.

The audit found three problems, all in the aggregation:

* **No company filter anywhere.** Every KPI counted every company's inventory,
  contracts and money. With the record rules added in M1 this partly self-heals,
  but a dashboard must not depend on that: `search_count` with no company clause
  also admits `company_id = False`.
* **Python loops over full recordsets.** `sum(SaleContract.search([...])
  .mapped('sale_price'))` reads every contract into memory to produce one
  number; `proj_totals` did the same and then grouped by hand. At the scale
  Phase 48 specifies — thousands of contracts, hundreds of thousands of
  instalments — that is the difference between a dashboard and a timeout.
* **Figures that could not be right.** `paid_mtd` summed instalments whose
  *state* was `paid`, of which there were none, because nothing created
  instalments at all (§4.1). Unit counts read the legacy `state` field rather
  than the commercial dimension that M1 made authoritative.

Every figure below comes from `_read_group`, scoped to `self.env.companies`.

One key is no longer always present. `paid_mtd` is now read from the payments
(`_collected_mtd_domain`) and is omitted for a user who may not read them,
because a money figure nobody can open should not be rendered as a zero. The
front end renders that card only when the key is there.
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DeveloperDashboardV2(models.AbstractModel):
    _inherit = 'realestate.developer.dashboard'

    # ------------------------------------------------------------------
    # Scoping
    # ------------------------------------------------------------------
    def _company_domain(self):
        """Every query starts here.

        `self.env.companies` is the set the user has actually switched on, so
        the dashboard follows the company switcher rather than showing whatever
        the record rules happen to permit.
        """
        return [('company_id', 'in', self.env.companies.ids)]

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    @api.model
    def get_data(self):
        Project = self.env['realestate.project']
        Reservation = self.env['realestate.unit.reservation']
        Contract = self.env['realestate.sale.contract']
        Installment = self.env['realestate.sale.installment']
        Property = self.env['realestate.property']

        today = fields.Date.context_today(self)
        next_30 = today + timedelta(days=30)
        company = self._company_domain()
        in_project = company + [('project_id', '!=', False)]

        # ---- Inventory, one grouped query for every commercial status ----
        status_counts = dict(Property._read_group(
            in_project, groupby=['commercial_status'], aggregates=['__count']))
        total_units = sum(status_counts.values())
        available_units = Property.search_count(
            in_project + [('is_available_for_sale', '=', True)])
        sold_units = status_counts.get('sold', 0)
        contracted_units = status_counts.get('contracted', 0)

        active_projects = Project.search_count(
            company + [('commercial_state', 'in', ('pre_launch', 'selling'))])
        construction_projects = Project.search_count(
            company + [('state', '=', 'construction')])
        active_reservations = Reservation.search_count(
            company + [('state', 'in', ('hold', 'pending_payment', 'booked'))])
        signed_contracts = Contract.search_count(
            company + [('state', 'in', ('signed', 'active',
                                        'financially_cleared'))])

        # ---- Money, summed by the database ----
        total_contracted = self._sum(Contract, company + [
            ('state', 'in', ('signed', 'active', 'financially_cleared',
                             'handed_over')),
        ], 'sale_price')

        kpis = {
            'active_projects': active_projects,
            'construction_projects': construction_projects,
            'total_units': total_units,
            'sold_units': sold_units,
            'available_units': available_units,
            'sales_progress': round(
                (sold_units + contracted_units) / total_units * 100.0, 1
            ) if total_units else 0.0,
            'active_reservations': active_reservations,
            'signed_contracts': signed_contracts,
            'total_contracted': total_contracted,
        }
        # "Revenue MTD / collected" is read from the payments, and only when
        # the user may read them: a figure nobody can open is dropped, never
        # shown as a zero that reads as "nothing came in this month".
        if self._can_read_collections():
            kpis['paid_mtd'] = self._sum(
                self.env['account.payment'], self._collected_mtd_domain(today),
                'amount')

        # ---- Projects by state ----
        proj_state_counts = dict(Project._read_group(
            company, groupby=['state'], aggregates=['__count']))
        proj_states = {
            st: proj_state_counts.get(st, 0)
            for st in ('planning', 'construction', 'marketing', 'handover',
                       'completed', 'cancelled')
        }

        # ---- Units by state ----
        # Kept on the legacy `state` keys the front end already renders, but
        # counted with one grouped query instead of five counts.
        legacy_counts = dict(Property._read_group(
            in_project, groupby=['state'], aggregates=['__count']))
        unit_states = {
            st: legacy_counts.get(st, 0)
            for st in ('available', 'reserved', 'rented', 'maintenance',
                       'inactive')
        }

        # ---- Sales velocity, six months in ONE query ----
        velocity_labels, velocity_count, velocity_revenue = self._velocity(
            Contract, company, today)

        # ---- Top projects, grouped rather than looped ----
        top_projects = [
            (project.name, total or 0.0)
            for project, total in Contract._read_group(
                company + [('state', 'in', ('signed', 'active',
                                            'financially_cleared',
                                            'handed_over')),
                           ('project_id', '!=', False)],
                groupby=['project_id'], aggregates=['sale_price:sum'],
                order='sale_price:sum desc', limit=5)
        ]

        # ---- Collections ----
        upcoming_collections = self._sum(Installment, company + [
            ('state', 'in', ('pending', 'invoiced', 'partially_paid')),
            ('date_due', '>=', today), ('date_due', '<=', next_30),
        ], 'residual_amount')

        overdue_domain = company + [
            ('state', 'in', ('invoiced', 'partially_paid', 'overdue',
                             'pending')),
            ('date_due', '<', today),
            ('is_cancelled', '=', False),
        ]
        overdue_amount = self._sum(Installment, overdue_domain, 'residual_amount')
        overdue_count = Installment.search_count(overdue_domain)

        return {
            'kpis': kpis,
            'proj_states': proj_states,
            'unit_states': unit_states,
            'velocity': {
                'labels': velocity_labels,
                'count': velocity_count,
                'revenue': velocity_revenue,
            },
            'top_projects': top_projects,
            'upcoming_collections': upcoming_collections,
            'overdue_amount': overdue_amount,
            'overdue_count': overdue_count,
            'map_projs': self._map_projects(Project, company),
            'expiring_reservations': self._expiring_reservations(
                Reservation, company),
            'upcoming_installments': self._upcoming_installments(
                Installment, company, today, next_30),
            'recent_contracts': self._recent_contracts(Contract, company),
            'currency': self.env.company.currency_id.symbol or '',
        }

    # ------------------------------------------------------------------
    # Collections
    # ------------------------------------------------------------------
    @api.model
    def _can_read_collections(self):
        """Whether this user may read everything the collected figure needs."""
        for model in ('account.payment', 'account.move',
                      'realestate.sale.installment'):
            if model not in self.env or not self.env[model].has_access('read'):
                return False
        return True

    @api.model
    def _collected_mtd_domain(self, today):
        """Instalment money actually received this month, by payment date.

        An instalment carries no payment date: `paid_amount` is derived from
        its invoice's residual, so summing it over the instalments *due* this
        month answers "how much of what fell due has been paid", not "how much
        money came in" -- money banked today against an instalment due in two
        years moved the figure by nothing, and money banked two months ago
        against an instalment falling due this month moved it in full. A
        collections figure is read as cash, so it is taken from the payments.

        Same rule as the Executive dashboard's `_collected_tiles`, deliberately
        to the letter: the two screens label the same figure, so they must
        answer the same question. It stays a *sales* figure -- only the
        payments reconciled with an instalment's invoice are counted, not every
        customer receipt -- and the domain is returned rather than a number so
        that `action_collected_mtd` opens exactly what was summed.
        """
        Payment = self.env['account.payment']
        domain = self._company_domain() + [
            ('date', '>=', today.replace(day=1)), ('date', '<=', today),
            ('payment_type', '=', 'inbound'), ('partner_type', '=', 'customer'),
            # Draft, cancelled and rejected payments are not money received.
            ('state', 'not in', ('draft', 'canceled', 'rejected')),
            # One currency only, so amounts are never added across rates.
            ('currency_id', '=', self.env.company.currency_id.id),
        ]
        received = Payment.search(domain)
        instalment_moves = self.env['realestate.sale.installment'].search(
            [('move_id', 'in', received.reconciled_invoice_ids.ids)]).move_id
        collected = received.filtered(
            lambda payment: payment.reconciled_invoice_ids & instalment_moves)
        return domain + [('id', 'in', collected.ids)]

    @api.model
    def action_collected_mtd(self):
        """Open the payments the "Revenue MTD" figure is the sum of."""
        if not self._can_read_collections():
            raise UserError(_("These records are not available to you."))
        today = fields.Date.context_today(self)
        return {
            'type': 'ir.actions.act_window',
            'name': _("Payments Received"),
            'res_model': 'account.payment',
            'views': [[False, 'list'], [False, 'form']],
            'view_mode': 'list,form',
            'domain': self._collected_mtd_domain(today),
            'context': {'create': False},
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @api.model
    def _sum(self, model, domain, field):
        """One SUM in the database, never a mapped() over a recordset."""
        groups = model._read_group(domain, aggregates=['%s:sum' % field])
        return groups[0][0] or 0.0 if groups else 0.0

    @api.model
    def _velocity(self, Contract, company, today):
        """Signed contracts and value per month, for the last six months.

        The old version ran six searches and loaded every matching contract.
        This is one grouped query; the months are then read off it, so a month
        with no sales still appears rather than being missing from the chart.
        """
        first_month = (today.replace(day=1) - relativedelta(months=5))
        groups = Contract._read_group(
            company + [
                ('signing_date', '>=', first_month),
                ('signing_date', '<=', today),
                ('state', 'in', ('signed', 'active', 'financially_cleared',
                                 'handed_over')),
            ],
            groupby=['signing_date:month'],
            aggregates=['__count', 'sale_price:sum'],
        )
        by_month = {}
        for month, count, total in groups:
            if month:
                by_month[(month.year, month.month)] = (count, total or 0.0)

        labels, counts, revenue = [], [], []
        for index in range(5, -1, -1):
            month_start = today.replace(day=1) - relativedelta(months=index)
            count, total = by_month.get(
                (month_start.year, month_start.month), (0, 0.0))
            labels.append(month_start.strftime('%b %Y'))
            counts.append(count)
            revenue.append(round(total, 2))
        return labels, counts, revenue

    @api.model
    def _map_projects(self, Project, company):
        rows = Project.search_read(
            company + [('latitude', '!=', 0), ('longitude', '!=', 0)],
            ['id', 'name', 'code', 'latitude', 'longitude', 'state', 'city',
             'project_type', 'expected_budget', 'expected_revenue'])
        ids = [row['id'] for row in rows]
        by_project = {}
        if ids:
            points = self.env['realestate.project.boundary.point'].search_read(
                [('project_id', 'in', ids)],
                ['project_id', 'sequence', 'latitude', 'longitude'],
                order='project_id, sequence')
            for point in points:
                by_project.setdefault(point['project_id'][0], []).append(
                    [point['latitude'], point['longitude']])
        for row in rows:
            row['boundary'] = by_project.get(row['id'], [])
        return rows

    @api.model
    def _expiring_reservations(self, Reservation, company):
        soon = fields.Datetime.now() + timedelta(hours=24)
        records = Reservation.search(
            company + [('state', 'in', ('hold', 'pending_payment')),
                       ('hold_expiry_at', '<=', soon)],
            order='hold_expiry_at asc', limit=10)
        return [{
            'id': rec.id,
            'name': rec.name,
            'property': rec.property_id.display_name if rec.property_id else '',
            'partner': rec.partner_id.name or '',
            'expiry': rec.hold_expiry_at.isoformat() if rec.hold_expiry_at else None,
        } for rec in records]

    @api.model
    def _upcoming_installments(self, Installment, company, today, next_30):
        records = Installment.search(
            company + [('state', 'in', ('pending', 'invoiced',
                                        'partially_paid')),
                       ('date_due', '>=', today), ('date_due', '<=', next_30)],
            order='date_due asc', limit=15)
        return [{
            'id': rec.id,
            'contract': rec.sale_contract_id.name or '',
            'partner': rec.partner_id.name or '',
            'amount': rec.residual_amount or rec.current_amount,
            'date_due': rec.date_due.isoformat() if rec.date_due else None,
            'kind': rec.kind,
        } for rec in records]

    @api.model
    def _recent_contracts(self, Contract, company):
        records = Contract.search(
            company + [('state', 'in', ('signed', 'active',
                                        'financially_cleared', 'handed_over'))],
            order='signing_date desc, id desc', limit=10)
        return [{
            'id': rec.id,
            'name': rec.name,
            'partner': rec.partner_id.name or '',
            'property': rec.property_id.display_name if rec.property_id else '',
            'sale_price': rec.sale_price,
            'progress': rec.progress,
            'state': rec.state,
        } for rec in records]
