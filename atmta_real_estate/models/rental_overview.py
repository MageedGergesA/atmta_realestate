# -*- coding: utf-8 -*-
"""The Rental Overview payload.

One call returns the whole screen: six headline KPIs with their trend, the two
analytical charts, the lease-status breakdown, the expirations panel, what
needs attention and what this user has to do.

Why one call and not the old two
--------------------------------
The previous screen asked for tiles, then charts. That was right when the
charts were decoration. They are not any more: the occupancy sparkline on the
KPI card and the occupancy trend chart are the *same series*, and computing it
twice would be both slower and a chance for the headline figure and the chart
beside it to disagree. They are computed once, here.

Honest trends
-------------
A sparkline is only drawn where a real historical series exists.

* Occupancy, Active Leases and Available Units are reconstructed from the
  allocation date ranges, so the history is true even for a lease entered
  retroactively.
* Outstanding Rent uses billed-minus-collected per month, which is what the
  arrears balance actually was.
* Move-ins and move-outs are counted in their own scheduled weeks.

Nothing here plots `create_date` and calls it a trend. Where no honest series
exists the card simply shows no sparkline and no delta, because a confident
line that is wrong is worse than no line at all.
"""

import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

from .lease_states import OCCUPYING_LIFECYCLE

_logger = logging.getLogger(__name__)

LIVE_LEASES = ('active', 'notice')
TREND_MONTHS = 12
AGENT = 'atmta_real_estate.group_rental_agent'
PROPERTY_MANAGER = 'atmta_real_estate.group_property_manager'
RENTAL_MANAGER = 'atmta_real_estate.group_rental_manager'

#: Occupancy the business is steering towards. Drawn as a reference line so
#: "78%" can be read as good or bad without anybody having to remember it.
OCCUPANCY_TARGET = 92.0


def _delta(series):
    """Percentage change between the last two points, or None.

    `None`, not `0`: a flat zero would be rendered as "no change", and there is
    a real difference between "this did not move" and "we cannot say".
    """
    if len(series) < 2:
        return None
    previous = series[-2]
    if not previous:
        return None
    return (series[-1] - previous) / abs(previous) * 100.0


class RentalOverview(models.AbstractModel):
    _inherit = 'realestate.rental.dashboard'

    # ==================================================================
    # Entry point
    # ==================================================================
    @api.model
    def get_overview(self, scope='mine', filters=None):
        scope = self._check_scope(scope)
        filters = dict(filters or {})
        today = fields.Date.context_today(self)
        company_ids = self.env.companies.ids
        company = self.env.company

        occupancy = self._occupancy_series(today, company_ids, filters)
        collection = (self._billed_vs_collected(today, company_ids)
                      if self._can_read('realestate.contract.payment') else None)

        return {
            'title': _("Rental"),
            'company_name': company.display_name,
            'company_id': company.id,
            'currency_id': company.currency_id.id,
            'as_of': today.isoformat(),
            'scope': scope,
            'supports_scope': True,
            'quick_actions': self._visible_quick_actions(),
            'filters': self._overview_filters(),
            'kpis': self._overview_kpis(today, scope, filters, occupancy, collection),
            'occupancy_trend': {
                'labels': occupancy['labels'],
                'values': occupancy['occupancy_pct'],
                'target': OCCUPANCY_TARGET,
            },
            'lease_status': self._lease_status(today, scope),
            'collection': self._collection_panel(collection),
            'expirations': self._expirations(today, scope),
            'alerts': self._attention(today, scope),
            'work_queue': self._work_queue(today, scope),
            'unit_mix': self._unit_mix(today, filters),
            'unit_types': self._occupancy_by_type(today, filters),
            'top_properties': self._top_properties(today, filters),
            'arrears': (self._arrears_aging(company_ids)
                        if self._can_read('realestate.contract.payment') else None),
        }

    # ==================================================================
    # Filters
    # ==================================================================
    @api.model
    def _overview_filters(self):
        """Only the selectors this screen honours.

        A filter that is offered and then ignored is worse than none at all:
        the reader changes it, nothing moves, and they stop trusting the page.
        Every option here is read by `_filter_property_domain` below.
        """
        if not self._can_read('realestate.property'):
            return []
        Property = self.env['realestate.property']
        company = [('company_id', 'in', self.env.companies.ids)]
        buildings = Property.search(
            company + [('hierarchy_level', 'in', ('compound', 'building'))],
            order='complete_name' if 'complete_name' in Property._fields else 'id',
            limit=80)
        usage_field = Property._fields.get('usage_category')
        usages = usage_field.selection if usage_field and not callable(
            usage_field.selection) else []
        filters = [{
            'key': 'building_id',
            'label': _("Property"),
            'icon': 'fa-building-o',
            'all_label': _("All Properties"),
            'options': [{'key': b.id, 'label': b.display_name} for b in buildings],
        }, {
            'key': 'usage_category',
            'label': _("Unit Type"),
            'icon': 'fa-home',
            'all_label': _("All Unit Types"),
            'options': [{'key': key, 'label': label} for key, label in usages],
        }]
        if self._can_read('realestate.contract'):
            tenants = self.env['realestate.contract'].search(
                company + [('lifecycle_state', 'in', LIVE_LEASES)], limit=200
            ).mapped('partner_id')[:80]
            filters.append({
                'key': 'partner_id',
                'label': _("Tenant"),
                'icon': 'fa-user-o',
                'all_label': _("All Tenants"),
                'options': [{'key': p.id, 'label': p.display_name} for p in tenants],
            })
        return filters

    @api.model
    def _filter_property_domain(self, filters):
        """Turn the filter bar's selection into a domain on the unit."""
        domain = []
        building = filters.get('building_id')
        if building:
            # `parent_path` matches the whole subtree, so selecting a compound
            # includes its buildings and their units, which is what a reader
            # picking "Marina Compound" means.
            parent = self.env['realestate.property'].browse(int(building)).exists()
            if parent:
                domain += [('id', 'child_of', parent.id)]
        usage = filters.get('usage_category')
        if usage and usage != 'all':
            domain += [('usage_category', '=', usage)]
        return domain

    @api.model
    def _filter_lease_domain(self, filters):
        domain = []
        unit_domain = self._filter_property_domain(filters)
        if unit_domain:
            domain += [('property_line_ids.property_id', 'any', unit_domain)]
        partner = filters.get('partner_id')
        if partner:
            domain += [('partner_id', '=', int(partner))]
        return domain

    # ==================================================================
    # KPIs
    # ==================================================================
    @api.model
    def _occupancy_series(self, today, company_ids, filters):
        """Occupancy per month, honouring the filter bar.

        Reimplemented here rather than calling `_occupancy_trend` because that
        one cannot be narrowed to a building or a unit type, and an occupancy
        headline that ignores the filter next to it is a bug people only find
        after they have made a decision on it.
        """
        Allocation = self.env['realestate.contract.property.line']
        Property = self.env['realestate.property']
        unit_domain = self._filter_property_domain(filters)
        leasable_domain = ([('is_leasable', '=', True),
                            ('company_id', 'in', company_ids)] + unit_domain)
        leasable_total = Property.search_count(leasable_domain)

        allocation_filter = ([('property_id', 'any', unit_domain)]
                             if unit_domain else [])
        labels, pct, occupied_counts = [], [], []
        for offset in range(TREND_MONTHS - 1, -1, -1):
            month_start = today.replace(day=1) - relativedelta(months=offset)
            month_end = month_start + relativedelta(months=1) - timedelta(days=1)
            # The current month is measured as of today, not as of a month end
            # that has not happened. Otherwise the last point of every trend
            # is a projection nobody asked for.
            as_of = min(month_end, today)
            occupied = Allocation.search_count([
                ('company_id', 'in', company_ids),
                ('start_date', '<=', as_of),
                '|', ('end_date', '=', False), ('end_date', '>=', as_of),
                ('lifecycle_state', 'in',
                 tuple(LIVE_LEASES) + ('ended', 'terminated')),
            ] + allocation_filter)
            labels.append(month_start.strftime('%b'))
            occupied_counts.append(occupied)
            pct.append(round(occupied / leasable_total * 100.0, 1)
                       if leasable_total else 0.0)
        return {
            'labels': labels,
            'occupancy_pct': pct,
            'occupied': occupied_counts,
            'leasable': leasable_total,
            'available': [max(leasable_total - n, 0) for n in occupied_counts],
        }

    @api.model
    def _overview_kpis(self, today, scope, filters, occupancy, collection):
        """The six figures the page is read for."""
        company = [('company_id', 'in', self.env.companies.ids)]
        currency = self.env.company.currency_id
        unit_domain = self._filter_property_domain(filters)
        lease_filter = self._filter_lease_domain(filters)
        mine = [('user_id', '=', self.env.uid)] if scope == 'mine' else []
        kpis = []

        leasable = [('is_leasable', '=', True)] + company + unit_domain

        # ---- Occupancy ------------------------------------------------
        kpis.append({
            'key': 'occupancy_rate',
            'label': _("Occupancy Rate"),
            'icon': 'fa-pie-chart',
            'tone': 'success',
            'value': occupancy['occupancy_pct'][-1] if occupancy['occupancy_pct'] else 0.0,
            'format': 'percent',
            'spark': occupancy['occupancy_pct'],
            'delta_percent': _delta(occupancy['occupancy_pct']),
            'comparison_label': _("vs last month"),
            'higher_is_better': True,
            'hint': _("Occupied rentable units as a share of leasable units, "
                      "measured as of today. Units out of service are still "
                      "counted as leasable."),
            'drill': True,
        })

        # ---- Available units ------------------------------------------
        kpis.append({
            'key': 'available_to_lease',
            'label': _("Available Units"),
            'icon': 'fa-home',
            'tone': 'info',
            'value': self.env['realestate.property'].search_count(
                leasable + [('is_available_for_lease', '=', True)]),
            'format': 'integer',
            'spark': occupancy['available'],
            'delta_percent': _delta(occupancy['available']),
            'comparison_label': _("vs last month"),
            # Fewer empty units is the good direction.
            'higher_is_better': False,
            'hint': _("Leasable units with no live allocation and no block."),
            'drill': True,
        })

        # ---- Active leases --------------------------------------------
        live_leases = ([('lifecycle_state', 'in', list(LIVE_LEASES))]
                       + company + mine + lease_filter)
        kpis.append({
            'key': 'active_leases',
            'label': _("Active Leases"),
            'icon': 'fa-file-text-o',
            'tone': 'primary',
            'value': self.env['realestate.contract'].search_count(live_leases),
            'format': 'integer',
            'spark': occupancy['occupied'],
            'delta_percent': _delta(occupancy['occupied']),
            'comparison_label': _("vs last month"),
            'higher_is_better': True,
            'hint': _("Leases in Active or Notice."),
            'drill': True,
        })

        # ---- Outstanding rent -----------------------------------------
        if self._can_read('realestate.contract.payment'):
            arrears_domain = (self._arrears_domain(self.env.companies.ids)
                              + [('currency_id', '=', currency.id)])
            if lease_filter:
                arrears_domain += [('contract_id', 'any', lease_filter)]
            (outstanding,) = self.env['realestate.contract.payment']._read_group(
                arrears_domain, aggregates=['amount_residual:sum'])[0]
            # What the arrears balance was at each month end: everything billed
            # up to that month, less everything collected against it.
            spark = []
            if collection:
                running = 0.0
                for billed, collected in zip(collection['billed'],
                                             collection['collected']):
                    running += billed - collected
                    spark.append(round(max(running, 0.0), 2))
            kpis.append({
                'key': 'outstanding_rent',
                'label': _("Outstanding Rent"),
                'icon': 'fa-money',
                'tone': 'danger',
                'value': outstanding or 0.0,
                'format': 'monetary',
                'spark': spark,
                'delta_percent': _delta(spark),
                'comparison_label': _("vs last month"),
                # Arrears going up is bad news wearing a positive sign.
                'higher_is_better': False,
                'warning': bool(outstanding),
                'warning_label': _("Collection needed"),
                'hint': _("Unpaid balance of invoiced obligations in %s. "
                          "Amounts in another currency are not added in.",
                          currency.name),
                'drill': True,
            })

        # ---- Move-ins / move-outs in the next 7 days -------------------
        for key, label, icon, model, states, path in (
                ('move_ins_next_7_days', _("Move-Ins (Next 7 Days)"), 'fa-sign-in',
                 'realestate.move.in', ('schedule', 'inspection'), 'contract_id.user_id'),
                ('move_outs_next_7_days', _("Move-Outs (Next 7 Days)"), 'fa-sign-out',
                 'realestate.move.out', ('schedule', 'inspection', 'assessment'),
                 'contract_id.user_id')):
            if not self._can_read(model):
                continue
            scoped = [(path, '=', self.env.uid)] if scope == 'mine' else []
            week_start = self._day_start_utc(today)
            week_end = self._day_start_utc(today + timedelta(days=7))
            base = company + scoped + [('state', 'in', list(states))]
            value = self.env[model].search_count(
                base + [('scheduled_date', '>=', week_start),
                        ('scheduled_date', '<', week_end)])
            # The comparable figure is the SAME window one week back, not a
            # calendar month: "next 7 days" against "last month" compares two
            # different questions.
            previous = self.env[model].search_count(
                base + [('scheduled_date', '>=', self._day_start_utc(today - timedelta(days=7))),
                        ('scheduled_date', '<', week_start)])
            kpis.append({
                'key': key,
                'label': label,
                'icon': icon,
                'tone': 'primary' if key.startswith('move_in') else 'warning',
                'value': value,
                'format': 'integer',
                'spark': self._weekly_counts(model, base, today),
                'delta_percent': ((value - previous) / abs(previous) * 100.0
                                  if previous else None),
                'comparison_label': _("vs last week"),
                'higher_is_better': key.startswith('move_in'),
                'hint': _("Scheduled and not yet completed."),
                'drill': True,
            })
        return kpis

    @api.model
    def _weekly_counts(self, model, base, today, weeks=8):
        """How many fell in each of the last ``weeks`` weeks, oldest first."""
        counts = []
        for offset in range(weeks - 1, -1, -1):
            start = self._day_start_utc(today - timedelta(days=7 * (offset + 1)))
            end = self._day_start_utc(today - timedelta(days=7 * offset))
            counts.append(self.env[model].search_count(
                base + [('scheduled_date', '>=', start),
                        ('scheduled_date', '<', end)]))
        return counts

    # ==================================================================
    # Lease status donut
    # ==================================================================
    @api.model
    def _lease_status(self, today, scope):
        """Active leases split by how soon they end, plus the other states.

        This replaces three separate "Expiring in N days" cards. A lease is
        counted in exactly one bucket -- the earliest it qualifies for -- so
        the segments add up to the total instead of triple-counting the lease
        that expires next week.
        """
        if not self._can_read('realestate.contract'):
            return {'segments': [], 'total': 0}
        Contract = self.env['realestate.contract']
        company = [('company_id', 'in', self.env.companies.ids)]
        mine = [('user_id', '=', self.env.uid)] if scope == 'mine' else []
        base = company + mine
        live = base + [('lifecycle_state', 'in', list(LIVE_LEASES))]

        def window(lo, hi):
            return live + [('end_date', '>=', today + timedelta(days=lo)),
                           ('end_date', '<=', today + timedelta(days=hi))]

        buckets = [
            ('expiring_30', _("Expiring (30 days)"), 'warning', window(0, 30)),
            ('expiring_60', _("Expiring (60 days)"), 'warning', window(31, 60)),
            ('expiring_90', _("Expiring (90 days)"), 'danger', window(61, 90)),
        ]
        counts = {key: Contract.search_count(domain)
                  for key, _label, _tone, domain in buckets}
        expiring_total = sum(counts.values())
        live_total = Contract.search_count(live)

        segments = [{
            'key': 'active_not_expiring',
            'label': _("Active"),
            'value': max(live_total - expiring_total, 0),
            'tone': 'success',
        }]
        segments += [{'key': key, 'label': label, 'value': counts[key], 'tone': tone}
                     for key, label, tone, _domain in buckets]
        segments.append({
            'key': 'leases_on_notice',
            'label': _("Terminated"),
            'value': Contract.search_count(
                base + [('lifecycle_state', 'in', ('ended', 'terminated')),
                        ('end_date', '>=', today - timedelta(days=90))]),
            'tone': 'neutral',
        })
        segments.append({
            'key': 'leases_pending',
            'label': _("Pending"),
            'value': Contract.search_count(
                base + [('lifecycle_state', 'in',
                         ('draft', 'pending_approval', 'pending_signature'))]),
            'tone': 'info',
        })
        total = sum(s['value'] for s in segments)
        for segment in segments:
            segment['percent'] = (round(segment['value'] / total * 100.0)
                                  if total else 0)
        return {'segments': segments, 'total': live_total,
                'total_label': _("Active Leases")}

    # ==================================================================
    # Collection panel
    # ==================================================================
    @api.model
    def _collection_panel(self, collection):
        if not collection:
            return None
        billed = sum(collection['billed'])
        collected = sum(collection['collected'])
        return {
            'labels': [label.split(' ')[0] for label in collection['labels']][-6:],
            'billed': collection['billed'][-6:],
            'collected': collection['collected'][-6:],
            'rate': round(collected / billed * 100.0, 1) if billed else 0.0,
        }

    # ==================================================================
    # Portfolio analysis
    # ==================================================================
    @api.model
    def _unit_mix(self, today, filters):
        """Where every leasable unit currently stands.

        One unit falls in exactly one bucket, in priority order, so the
        segments sum to the portfolio instead of double-counting the unit that
        is both let and out of service.
        """
        if not self._can_read('realestate.property'):
            return {'segments': [], 'total': 0}
        Property = self.env['realestate.property']
        base = ([('is_leasable', '=', True),
                 ('company_id', 'in', self.env.companies.ids)]
                + self._filter_property_domain(filters))
        total = Property.search_count(base)
        occupied = Property.search_count(base + self._occupied_on(today))
        out_of_service = Property.search_count(
            base + [('maintenance_status', '!=', 'normal')]
            + self._occupied_on(today, negate=True))
        turnaround = 0
        if self._can_read('realestate.unit.turn'):
            turnaround = Property.search_count(
                base + self._occupied_on(today, negate=True)
                + [('maintenance_status', '=', 'normal'),
                   ('unit_turn_ids.state', 'not in', ('ready', 'cancelled'))])
        available = Property.search_count(
            base + [('is_available_for_lease', '=', True)])
        # Whatever is left is vacant but not offerable -- unreleased, blocked,
        # or still being built. Naming it beats letting the segments not add up.
        other = max(total - occupied - out_of_service - turnaround - available, 0)
        segments = [
            {'key': 'occupied_units', 'label': _("Occupied"), 'value': occupied,
             'tone': 'primary'},
            {'key': 'available_to_lease', 'label': _("Available"),
             'value': available, 'tone': 'success'},
            {'key': 'units_in_turnaround', 'label': _("In turnaround"),
             'value': turnaround, 'tone': 'warning'},
            {'key': 'out_of_service', 'label': _("Out of service"),
             'value': out_of_service, 'tone': 'danger'},
            {'key': 'units_not_offerable', 'label': _("Not yet offerable"),
             'value': other, 'tone': 'neutral'},
        ]
        return {'segments': [seg for seg in segments if seg['value']],
                'total': total, 'total_label': _("Leasable Units")}

    @api.model
    def _occupancy_by_type(self, today, filters):
        """Occupancy per unit type -- where the vacancy actually is.

        A single portfolio occupancy figure hides the thing worth acting on:
        92% overall can be 99% on apartments and 40% on retail, and those are
        two different problems.
        """
        if not self._can_read('realestate.property'):
            return []
        Property = self.env['realestate.property']
        base = ([('is_leasable', '=', True),
                 ('company_id', 'in', self.env.companies.ids)]
                + self._filter_property_domain(filters))
        field = Property._fields.get('usage_category')
        labels = dict(field.selection) if field and not callable(
            field.selection) else {}

        leasable = dict(Property._read_group(
            base, groupby=['usage_category'], aggregates=['__count']))
        occupied = dict(Property._read_group(
            base + self._occupied_on(today),
            groupby=['usage_category'], aggregates=['__count']))

        rows = []
        for category, total in sorted(leasable.items(),
                                      key=lambda item: -item[1]):
            taken = occupied.get(category, 0)
            rows.append({
                'id': False,
                'type': labels.get(category, category or _("Unclassified")),
                'available': total - taken,
                'occupied': taken,
                'occupancy': round(taken / total * 100.0, 1) if total else 0.0,
            })
        return rows

    @api.model
    def _top_properties(self, today, filters):
        """The buildings, ranked by what they are worth and what they owe.

        Occupancy on its own ranks a fully let studio block above a
        half-empty tower that earns five times as much, so the monthly rent
        and the arrears are on the same row.
        """
        if not (self._can_read('realestate.property')
                and self._can_read('realestate.contract')):
            return []
        Property = self.env['realestate.property']
        buildings = Property.search(
            [('hierarchy_level', 'in', ('building', 'compound')),
             ('company_id', 'in', self.env.companies.ids)], limit=12)
        Obligation = self.env['realestate.contract.payment']
        can_read_money = self._can_read('realestate.contract.payment')

        rows = []
        for building in buildings:
            units = Property.search(
                [('id', 'child_of', building.id),
                 ('hierarchy_level', '=', 'unit'),
                 ('is_leasable', '=', True)])
            if not units:
                continue
            taken = Property.search_count(
                [('id', 'in', units.ids)] + self._occupied_on(today))
            leases = self.env['realestate.contract'].search(
                [('property_line_ids.property_id', 'in', units.ids),
                 ('lifecycle_state', 'in', list(LIVE_LEASES))])
            arrears = 0.0
            if can_read_money and leases:
                groups = Obligation._read_group(
                    self._arrears_domain(self.env.companies.ids)
                    + [('contract_id', 'in', leases.ids)],
                    aggregates=['amount_residual:sum'])
                arrears = (groups[0][0] if groups else 0.0) or 0.0
            rows.append({
                'id': building.id,
                'property': building.display_name,
                'units': len(units),
                'occupancy': round(taken / len(units) * 100.0, 1),
                'rent': sum(leases.mapped('price')),
                'arrears': arrears,
            })
        rows.sort(key=lambda row: -row['rent'])
        return rows[:8]

    # ==================================================================
    # Panels
    # ==================================================================
    @api.model
    def _expirations(self, today, scope):
        if not self._can_read('realestate.contract'):
            return []
        Contract = self.env['realestate.contract']
        company = [('company_id', 'in', self.env.companies.ids)]
        mine = [('user_id', '=', self.env.uid)] if scope == 'mine' else []
        live = company + mine + [('lifecycle_state', 'in', list(LIVE_LEASES))]

        def expiring(days):
            return Contract.search_count(
                live + [('end_date', '>=', today),
                        ('end_date', '<=', today + timedelta(days=days))])

        rows = [
            {'key': 'expiring_30', 'label': _("Expiring in 30 days"),
             'value': expiring(30), 'severity': 'warning', 'icon': 'fa-calendar'},
            {'key': 'expiring_60', 'label': _("Expiring in 60 days"),
             'value': expiring(60), 'icon': 'fa-calendar-o'},
            {'key': 'expiring_90', 'label': _("Expiring in 90 days"),
             'value': expiring(90), 'icon': 'fa-calendar-o'},
        ]
        if self._can_read('realestate.contract.renewal'):
            rows.append({
                'key': 'renewals_in_progress',
                'label': _("Renewals in progress"),
                'value': self.env['realestate.contract.renewal'].search_count(
                    company + mine + [('state', 'in', ('draft', 'proposed',
                                                       'negotiating', 'approved',
                                                       'accepted'))]),
                'icon': 'fa-refresh',
            })
        return rows

    @api.model
    def _attention(self, today, scope):
        """What is wrong, worst first.

        Severity is on the row, but the component only paints it when the
        count is non-zero: "0 overdue payments" in red trains people to ignore
        red, which is the one colour that has to keep working.
        """
        rows = []
        company = [('company_id', 'in', self.env.companies.ids)]
        if self._can_read('realestate.property'):
            rows.append({
                'key': 'out_of_service',
                'label': _("Units out of service"),
                'sublabel': _("Not available to let"),
                'value': self.env['realestate.property'].search_count(
                    company + [('is_leasable', '=', True),
                               ('maintenance_status', '!=', 'normal')]),
                'severity': 'warning',
                'icon': 'fa-wrench',
            })
        if self._can_read('realestate.contract.payment'):
            rows.append({
                'key': 'overdue_obligations',
                'label': _("Overdue obligations"),
                'sublabel': _("Past their due date and unpaid"),
                'value': self.env['realestate.contract.payment'].search_count(
                    self._arrears_domain(self.env.companies.ids)
                    + [('date_due', '<', today)]),
                'severity': 'critical',
                'icon': 'fa-exclamation-circle',
            })
        if self._can_read('realestate.contract'):
            rows.append({
                'key': 'expiring_30_no_renewal',
                'label': _("Expiring soon with no renewal"),
                'value': self.env['realestate.contract'].search_count(
                    company + [('lifecycle_state', 'in', list(LIVE_LEASES)),
                               ('end_date', '>=', today),
                               ('end_date', '<=', today + timedelta(days=30)),
                               ('renewal_state', '=', False)]),
                'severity': 'warning',
                'icon': 'fa-hourglass-half',
            })
        if self._can_read('realestate.contract.deposit'):
            rows.append({
                'key': 'deposits_to_settle',
                'label': _("Deposits to settle"),
                'value': self.env['realestate.contract.deposit'].search_count(
                    company + ['|', ('state', '=', 'requested'),
                               '&', ('state', 'in', ('received', 'held',
                                                     'partially_refunded')),
                               ('contract_id.lifecycle_state', 'in',
                                ('ended', 'terminated'))]),
                'icon': 'fa-shield',
            })
        return rows

    @api.model
    def _work_queue(self, today, scope):
        """This user's queue, as counted rows rather than a wall of cards."""
        definitions = self._visible_tiles(today, 'mine')
        work = [tile for tile in definitions if tile.get('section') == 'work']
        if not work:
            return []
        values = self._tile_values(work)
        icons = {
            'leases_to_approve': 'fa-file-text-o',
            'amendments_to_approve': 'fa-pencil',
            'terminations_to_approve': 'fa-times-circle-o',
            'signatures_waiting': 'fa-pencil-square-o',
            'move_ins_next_7_days': 'fa-sign-in',
            'move_outs_next_7_days': 'fa-sign-out',
            'overdue_obligations': 'fa-exclamation-circle',
            'expiring_30_no_renewal': 'fa-hourglass-half',
            'renewals_in_progress': 'fa-refresh',
            'notices_to_process': 'fa-bell-o',
            'deposits_to_settle': 'fa-shield',
            'my_activities': 'fa-calendar-check-o',
        }
        return [{
            'key': tile['key'],
            'label': tile['label'],
            'value': values.get(tile['key'], 0),
            'icon': icons.get(tile['key'], 'fa-angle-right'),
            'severity': 'warning' if tile.get('warn') else None,
        } for tile in work]
