"""Rental dashboard (Phase 4; RENTAL_UX_SPEC.md §5).

The dashboard answers, in order: what needs my attention, how is the portfolio
doing, and how are things trending.

**One definition per tile.** ``_tile_definitions`` is the single source of what
every number means: its model, its domain, how it is measured and who may see
it. ``get_work`` counts from those definitions and ``action_drill`` opens the
records from the same definitions, so a tile and the list it opens cannot
disagree.

**Today, not a stored counter.** Every date test compares the authoritative
date (end date, due date, scheduled date) with the user's today. Move-in and
move-out day ranges are computed in the user's timezone.

**Mine or team.** In *Mine*, lease-based tiles count only leases the user is
responsible for; *Team* counts every lease the user may see. Record rules and
company isolation apply in both.

**Roles.** A tile is sent only to the roles that act on it. Hiding a tile is
not security: the drilldown opens an ordinary list, so ACLs and record rules
still decide what the user sees.

**What cannot be read is dropped.** On top of the role filter, a tile, a chart
or a quick action whose model the user may not read is left out of the payload
-- the convention ``atmta_dashboard/models/dashboard_provider.py`` sets for
every ATMTA dashboard. Never an ``AccessError`` in the user's face, and never a
zero that would read as "nothing to do".

Occupancy is measured on leasable units only: compounds, buildings and floors
are containers, not units that can be let.
"""

import logging
from datetime import datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression

from .lease_states import OCCUPYING_LIFECYCLE, OVERDUE_BUCKETS

_logger = logging.getLogger(__name__)

#: Lease lifecycle states that count as a running lease on the dashboard.
LIVE_LEASES = ('active', 'notice')

#: How many months of history the trend charts show.
TREND_MONTHS = 12

SCOPES = ('mine', 'team')

AGENT = 'atmta_real_estate.group_rental_agent'
PROPERTY_MANAGER = 'atmta_real_estate.group_property_manager'
RENTAL_MANAGER = 'atmta_real_estate.group_rental_manager'

#: Models whose activities count as rental work.
RENTAL_ACTIVITY_MODELS = (
    'realestate.contract', 'realestate.contract.renewal',
    'realestate.contract.amendment', 'realestate.contract.termination',
    'realestate.contract.deposit', 'realestate.move.in', 'realestate.move.out',
    'realestate.unit.turn', 'realestate.maintenance.request',
)

MOVE_IN_OPEN = ('schedule', 'inspection')
MOVE_OUT_OPEN = ('schedule', 'inspection', 'assessment')
OPEN_RENEWALS = ('draft', 'proposed', 'negotiating', 'approved', 'accepted')


class RentalDashboard(models.AbstractModel):
    _name = 'realestate.rental.dashboard'
    _description = 'Rental Dashboard data provider'

    # ==================================================================
    # Entry points
    # ==================================================================
    @api.model
    def get_work(self, scope='mine'):
        """The tiles: My Work and Portfolio Health, for this user's roles."""
        scope = self._check_scope(scope)
        today = fields.Date.context_today(self)
        definitions = self._visible_tiles(today, scope)
        values = self._tile_values(definitions)
        sections = []
        for section_id, title in self._sections():
            tiles = [self._tile_payload(tile, values[tile['key']])
                     for tile in definitions if tile['section'] == section_id]
            if tiles:
                sections.append({'id': section_id, 'title': title, 'tiles': tiles})
        company = self.env.company
        return {
            'sections': sections,
            'scope': scope,
            'quick_actions': self._visible_quick_actions(),
            'currency_id': company.currency_id.id,
            'company_id': company.id,
            'company_name': company.display_name,
            'as_of': today.isoformat(),
        }

    @api.model
    def get_trends(self):
        """The charts, loaded after the tiles.

        A chart whose model the user may not read is omitted, like a tile: the
        front end draws only the charts it is sent, so nobody is shown a flat
        zero line for money they are not allowed to see.
        """
        today = fields.Date.context_today(self)
        company_ids = self.env.companies.ids
        charts = {}
        if self._can_read('realestate.contract.payment'):
            charts['billed_vs_collected'] = self._billed_vs_collected(today, company_ids)
            charts['arrears_aging'] = self._arrears_aging(company_ids)
        if self._can_read('realestate.contract'):
            charts['expiries_by_month'] = self._expiries_by_month(today, company_ids)
        if (self._can_read('realestate.contract.property.line')
                and self._can_read('realestate.property')):
            charts['occupancy_trend'] = self._occupancy_trend(today, company_ids)
        return {
            'charts': charts,
            'currency_id': self.env.company.currency_id.id,
        }

    # ==================================================================
    # Map
    # ==================================================================
    #: Most units the Overview's map card draws. Units → Map shows every one.
    MAP_UNIT_LIMIT = 2000

    @api.model
    def get_map(self):
        """Leasable units with coordinates, for the Overview's map card.

        Portfolio-wide, like the charts. Read as the user, so record rules and
        company isolation decide which units appear; units without a company
        are left out, as they are from every tile.
        """
        Property = self.env['realestate.property']
        labels = dict(Property._fields['state']._description_selection(self.env))
        rows = Property.search_read(
            [('is_leasable', '=', True), ('company_id', 'in', self.env.companies.ids)],
            ['name', 'property_code', 'latitude', 'longitude', 'state'],
            order='property_code, id')
        units, without_coordinates = [], 0
        for row in rows:
            if not (row['latitude'] or row['longitude']):
                without_coordinates += 1
                continue
            units.append({
                'id': row['id'],
                'name': row['name'],
                'code': row['property_code'] or '',
                'lat': row['latitude'],
                'lng': row['longitude'],
                'status': row['state'] or '',
                'status_label': labels.get(row['state'], row['state'] or ''),
            })
        return {
            'units': units[:self.MAP_UNIT_LIMIT],
            'truncated': len(units) > self.MAP_UNIT_LIMIT,
            'without_coordinates': without_coordinates,
        }

    @api.model
    def action_open_map(self):
        """Units → Map, the full-screen map with filters."""
        return self.env['ir.actions.actions']._for_xml_id(
            'atmta_real_estate.action_properties_map_dashboard')

    @api.model
    def action_open_unit(self, unit_id):
        """Open a unit picked on the map card."""
        unit = self.env['realestate.property'].browse(int(unit_id)).exists()
        if not unit:
            raise UserError(_("This unit no longer exists."))
        unit.check_access('read')
        return self._form_action(unit.display_name, 'realestate.property', unit.id)

    # ==================================================================
    # Tile definitions -- the single source of what each number means
    # ==================================================================
    @api.model
    def _sections(self):
        return [('work', _('My Work')), ('portfolio', _('Portfolio Health'))]

    @api.model
    def _can_read(self, model_name):
        """May this user read ``model_name`` at all?

        The same helper (and the same purpose) as
        ``atmta.dashboard.provider._can_read``: a figure the user could not
        open is dropped rather than counted.
        """
        if model_name not in self.env:
            return False
        return self.env[model_name].has_access('read')

    @api.model
    def _check_scope(self, scope):
        if scope not in SCOPES:
            raise UserError(_("Unknown dashboard scope '%s'.") % scope)
        return scope

    @api.model
    def _day_start_utc(self, day):
        """Midnight of ``day`` in the user's timezone, as a naive UTC datetime."""
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        local = tz.localize(datetime.combine(day, time.min))
        return local.astimezone(pytz.UTC).replace(tzinfo=None)

    @api.model
    def _occupied_on(self, day, negate=False):
        """Units a live lease physically holds on ``day``, read from the dates.

        The same rule as ``realestate.contract.property.line._compute_occupancy``,
        evaluated for ``day`` when searched, so a lease that starts or ends
        today is counted correctly before the daily refresh has run.
        """
        return [('property_line_ids', 'not any' if negate else 'any', [
            ('lifecycle_state', 'in', list(OCCUPYING_LIFECYCLE)),
            ('start_date', '<=', day),
            '|', ('end_date', '=', False), ('end_date', '>=', day),
            '|', ('move_out_date', '=', False), ('move_out_date', '>', day),
        ])]

    @api.model
    def _arrears_domain(self, company_ids):
        return [('company_id', 'in', company_ids),
                ('amount_residual', '>', 0),
                ('state', '=', 'invoiced')]

    @api.model
    def _tile_definitions(self, today, scope):
        """Every tile, before role filtering.

        Each definition: ``key``, ``section``, ``label``, ``model``, ``domain``,
        ``measure`` (``count``, ``sum:<field>`` or ``ratio:<num>/<den>``),
        ``format``, ``groups`` (an xmlid, or None for everyone), ``warn`` (show
        a warning when non-zero), ``hint`` and ``title`` (the drilldown's name).
        ``reads`` names the model the figure queries when that is not ``model``
        -- a computed ratio has no model to open but still reads one.
        The *Mine* restriction is already applied to ``domain``.
        """
        uid = self.env.uid
        company = [('company_id', 'in', self.env.companies.ids)]
        mine = scope == 'mine'

        def lease_scope(path):
            return [(path, '=', uid)] if mine else []

        leasable = [('is_leasable', '=', True)] + company
        live = [('lifecycle_state', 'in', list(LIVE_LEASES))]
        week_start = self._day_start_utc(today)
        week_end = self._day_start_utc(today + timedelta(days=7))
        currency = self.env.company.currency_id

        def expiring(days):
            return (live + company + lease_scope('user_id')
                    + [('end_date', '>=', today),
                       ('end_date', '<=', today + timedelta(days=days))])

        Deposit = [
            '|', ('state', '=', 'requested'),
            '&', ('state', 'in', ('received', 'held', 'partially_refunded')),
            ('contract_id.lifecycle_state', 'in', ('ended', 'terminated'))]

        return [
            # ---------------- My Work ----------------
            dict(key='leases_to_approve', section='work', label=_('Leases to Approve'),
                 model='realestate.contract', groups=RENTAL_MANAGER, warn=True,
                 domain=company + [('lifecycle_state', '=', 'pending_approval')]),
            dict(key='amendments_to_approve', section='work', label=_('Amendments to Approve'),
                 model='realestate.contract.amendment', groups=RENTAL_MANAGER, warn=True,
                 domain=company + [('state', '=', 'proposed')]),
            dict(key='terminations_to_approve', section='work',
                 label=_('Terminations to Approve'),
                 model='realestate.contract.termination', groups=RENTAL_MANAGER, warn=True,
                 domain=company + [('state', '=', 'notice_given')]),
            dict(key='signatures_waiting', section='work', label=_('Awaiting Signature'),
                 model='realestate.contract', groups=AGENT,
                 domain=company + lease_scope('user_id')
                 + [('lifecycle_state', '=', 'pending_signature'),
                    ('signature_status', '!=', 'signed')]),
            dict(key='move_ins_next_7_days', section='work', label=_('Move-Ins, Next 7 Days'),
                 model='realestate.move.in', groups=PROPERTY_MANAGER,
                 domain=company + lease_scope('contract_id.user_id')
                 + [('state', 'in', MOVE_IN_OPEN),
                    ('scheduled_date', '>=', week_start), ('scheduled_date', '<', week_end)]),
            dict(key='move_outs_next_7_days', section='work', label=_('Move-Outs, Next 7 Days'),
                 model='realestate.move.out', groups=PROPERTY_MANAGER,
                 domain=company + lease_scope('contract_id.user_id')
                 + [('state', 'in', MOVE_OUT_OPEN),
                    ('scheduled_date', '>=', week_start), ('scheduled_date', '<', week_end)]),
            dict(key='overdue_obligations', section='work', label=_('Overdue Obligations'),
                 model='realestate.contract.payment', groups=AGENT, warn=True,
                 domain=self._arrears_domain(self.env.companies.ids)
                 + lease_scope('contract_id.user_id') + [('date_due', '<', today)]),
            dict(key='expiring_30_no_renewal', section='work',
                 label=_('Expiring in 30 Days, No Renewal'),
                 model='realestate.contract', groups=AGENT, warn=True,
                 domain=expiring(30) + [('renewal_state', '=', False)]),
            dict(key='renewals_in_progress', section='work', label=_('Renewals in Progress'),
                 model='realestate.contract.renewal', groups=AGENT,
                 domain=company + lease_scope('user_id') + [('state', 'in', OPEN_RENEWALS)]),
            dict(key='notices_to_process', section='work', label=_('Leases on Notice'),
                 model='realestate.contract', groups=PROPERTY_MANAGER,
                 domain=company + lease_scope('user_id') + [('lifecycle_state', '=', 'notice')]),
            dict(key='deposits_to_settle', section='work', label=_('Deposits to Settle'),
                 model='realestate.contract.deposit', groups=PROPERTY_MANAGER,
                 hint=_("Requested but not received, or still held on a lease that has ended."),
                 domain=company + lease_scope('contract_id.user_id') + Deposit),
            dict(key='my_activities', section='work', label=_('My Activities Due'),
                 model='mail.activity', groups=None,
                 domain=[('user_id', '=', uid), ('date_deadline', '<=', today),
                         ('res_model', 'in', list(RENTAL_ACTIVITY_MODELS))]),
            # ---------------- Portfolio Health ----------------
            dict(key='available_to_lease', section='portfolio', label=_('Available to Lease'),
                 model='realestate.property', groups=None,
                 domain=leasable + [('is_available_for_lease', '=', True)]),
            dict(key='occupied_units', section='portfolio', label=_('Occupied Units'),
                 model='realestate.property', groups=None,
                 domain=leasable + self._occupied_on(today)),
            dict(key='occupancy_rate', section='portfolio', label=_('Occupancy'),
                 model=None, groups=None, measure='ratio:occupied_units/leasable_units',
                 reads='realestate.property',
                 format='percent', hint=_("Occupied units as a share of leasable units.")),
            dict(key='active_leases', section='portfolio', label=_('Active Leases'),
                 model='realestate.contract', groups=None,
                 domain=live + company + lease_scope('user_id')),
            dict(key='outstanding_rent', section='portfolio', label=_('Outstanding Rent'),
                 model='realestate.contract.payment', groups=AGENT, warn=True,
                 measure='sum:amount_residual', format='monetary',
                 hint=_("Unpaid balance of invoiced obligations in %s.", currency.name),
                 domain=self._arrears_domain(self.env.companies.ids)
                 + lease_scope('contract_id.user_id')
                 + [('currency_id', '=', currency.id)]),
            dict(key='expiring_30', section='portfolio', label=_('Expiring in 30 Days'),
                 model='realestate.contract', groups=AGENT, domain=expiring(30)),
            dict(key='expiring_60', section='portfolio', label=_('Expiring in 60 Days'),
                 model='realestate.contract', groups=AGENT, domain=expiring(60)),
            dict(key='expiring_90', section='portfolio', label=_('Expiring in 90 Days'),
                 model='realestate.contract', groups=AGENT, domain=expiring(90)),
            dict(key='units_in_turnaround', section='portfolio', label=_('Units in Turnaround'),
                 model='realestate.unit.turn', groups=PROPERTY_MANAGER,
                 domain=company + [('state', 'not in', ('ready', 'cancelled'))]),
            dict(key='out_of_service', section='portfolio', label=_('Out of Service'),
                 model='realestate.property', groups=PROPERTY_MANAGER, warn=True,
                 domain=leasable + [('maintenance_status', '!=', 'normal')]),
        ]

    @api.model
    def _visible_tiles(self, today, scope):
        user = self.env.user
        tiles = []
        for tile in self._tile_definitions(today, scope):
            tile.setdefault('measure', 'count')
            tile.setdefault('format', 'integer')
            tile.setdefault('warn', False)
            tile.setdefault('hint', '')
            tile.setdefault('domain', [])
            tile.setdefault('reads', tile['model'])
            if tile['groups'] and not user.has_group(tile['groups']):
                continue
            # A figure the user could not open is dropped, never counted and
            # never sent as a zero. Roles and ACLs are maintained separately,
            # so the two can disagree; the ACL is the one that decides.
            if tile['reads'] and not self._can_read(tile['reads']):
                continue
            tiles.append(tile)
        return tiles

    @api.model
    def _tile_values(self, tiles):
        values = {}
        today = fields.Date.context_today(self)
        for tile in tiles:
            measure = tile['measure']
            if measure == 'count':
                values[tile['key']] = self.env[tile['model']].search_count(tile['domain'])
            elif measure.startswith('sum:'):
                (total,) = self.env[tile['model']]._read_group(
                    tile['domain'], aggregates=['%s:sum' % measure[4:]])[0]
                values[tile['key']] = total or 0.0
        for tile in tiles:
            if tile['measure'].startswith('ratio:'):
                occupied = self.env['realestate.property'].search_count(
                    [('is_leasable', '=', True), ('company_id', 'in', self.env.companies.ids)]
                    + self._occupied_on(today))
                leasable = self.env['realestate.property'].search_count(
                    [('is_leasable', '=', True), ('company_id', 'in', self.env.companies.ids)])
                values[tile['key']] = round(occupied / leasable * 100.0, 1) if leasable else 0.0
        return values

    @api.model
    def _tile_payload(self, tile, value):
        return {
            'key': tile['key'],
            'label': tile['label'],
            'value': value,
            'format': tile['format'],
            'hint': tile['hint'],
            'warning': bool(tile['warn'] and value),
            'drill': bool(tile['model']),
        }

    # ==================================================================
    # Quick actions
    # ==================================================================
    @api.model
    def _quick_actions(self):
        """``model`` is what the button opens; ``create`` means it opens a new
        one, which needs more than read."""
        return [
            dict(key='new_lease', label=_('New Lease'), icon='fa-plus', groups=AGENT,
                 model='realestate.contract', create=True),
            dict(key='available_units', label=_('Find Available Unit'), icon='fa-search',
                 groups=None, model='realestate.property', create=False),
            dict(key='move_in', label=_('Move-In'), icon='fa-sign-in', groups=PROPERTY_MANAGER,
                 model='realestate.move.in', create=True),
            dict(key='move_out', label=_('Move-Out'), icon='fa-sign-out',
                 groups=PROPERTY_MANAGER, model='realestate.move.out', create=True),
        ]

    @api.model
    def _quick_available(self, action):
        """A button that could only end in an access error is not offered."""
        if not self._can_read(action['model']):
            return False
        return (not action['create']
                or self.env[action['model']].has_access('create'))

    @api.model
    def _visible_quick_actions(self):
        user = self.env.user
        return [{'key': action['key'], 'label': action['label'], 'icon': action['icon']}
                for action in self._quick_actions()
                if (not action['groups'] or user.has_group(action['groups']))
                and self._quick_available(action)]

    @api.model
    def action_quick(self, key):
        """Open the screen behind a quick-action button."""
        if key not in {action['key'] for action in self._visible_quick_actions()}:
            raise UserError(_("Unknown dashboard action '%s'.") % key)
        if key == 'available_units':
            return self.env['ir.actions.act_window']._for_xml_id(
                'atmta_real_estate.action_available_units')
        model, name = {
            'new_lease': ('realestate.contract', _('New Lease')),
            'move_in': ('realestate.move.in', _('New Move-In')),
            'move_out': ('realestate.move.out', _('New Move-Out')),
        }[key]
        return self._form_action(name, model, False)

    # ==================================================================
    # Charts
    # ==================================================================
    def _occupancy_trend(self, today, company_ids):
        """Units under a live allocation at each month end, for 12 months.

        Derived from the allocation date ranges rather than from a stored
        history table, so it stays correct even for leases entered
        retroactively.
        """
        Allocation = self.env['realestate.contract.property.line']
        labels, values = [], []
        leasable_total = self.env['realestate.property'].search_count([
            ('is_leasable', '=', True), ('company_id', 'in', company_ids)])
        for offset in range(TREND_MONTHS - 1, -1, -1):
            month_start = today.replace(day=1) - relativedelta(months=offset)
            month_end = month_start + relativedelta(months=1) - timedelta(days=1)
            occupied = Allocation.search_count([
                ('company_id', 'in', company_ids),
                ('start_date', '<=', month_end),
                '|', ('end_date', '=', False), ('end_date', '>=', month_end),
                ('lifecycle_state', 'in', LIVE_LEASES + ('ended', 'terminated')),
            ])
            labels.append(month_start.strftime('%b %Y'))
            values.append(round(occupied / leasable_total * 100.0, 1)
                          if leasable_total else 0.0)
        return {'labels': labels, 'occupancy_pct': values}

    def _billed_vs_collected(self, today, company_ids):
        """One grouped query for 12 months instead of 12 searches.

        Grouped by the month an obligation FELL DUE, for all three series. The
        paid figure is therefore "how much of that month's rent has been paid
        so far", not cash banked in that month -- ``amount_paid`` is read from
        the invoice's residual and carries no payment date of its own. The
        chart's labels say which question it answers
        (``static/src/js/dashboard/dashboard_schema.js``). A true cash-basis
        series would have to come from ``account.payment``, which no Rental
        role may read (``security/leasing_groups.xml``).
        """
        start = today.replace(day=1) - relativedelta(months=TREND_MONTHS - 1)
        groups = self.env['realestate.contract.payment']._read_group(
            [('company_id', 'in', company_ids),
             ('date_due', '>=', start),
             ('state', '!=', 'cancelled')],
            groupby=['date_due:month'],
            aggregates=['amount_invoiced:sum', 'amount_paid:sum', 'amount_due:sum'])
        by_month = {}
        for month, invoiced, paid, due in groups:
            if month:
                by_month[month.strftime('%Y-%m')] = (
                    invoiced or 0.0, paid or 0.0, due or 0.0)

        labels, billed, collected, scheduled = [], [], [], []
        for offset in range(TREND_MONTHS - 1, -1, -1):
            month = today.replace(day=1) - relativedelta(months=offset)
            inv, paid, due = by_month.get(month.strftime('%Y-%m'), (0.0, 0.0, 0.0))
            labels.append(month.strftime('%b %Y'))
            billed.append(round(inv, 2))
            collected.append(round(paid, 2))
            scheduled.append(round(due, 2))
        return {'labels': labels, 'billed': billed,
                'collected': collected, 'scheduled': scheduled}

    def _arrears_aging(self, company_ids):
        """Outstanding balance per ageing bucket, from the due dates for today.

        Each bucket uses the domain its chart segment opens
        (``action_drill_arrears_bucket``).
        """
        Obligation = self.env['realestate.contract.payment']
        base = self._arrears_domain(company_ids)
        totals = {}
        for bucket, _label in OVERDUE_BUCKETS:
            groups = Obligation._read_group(
                expression.AND([base, Obligation._overdue_bucket_domain(bucket)]),
                aggregates=['amount_residual:sum', '__count'])
            amount, count = groups[0] if groups else (0.0, 0)
            totals[bucket] = (amount or 0.0, count or 0)
        return {
            'labels': [label for _key, label in OVERDUE_BUCKETS],
            'keys': [key for key, _label in OVERDUE_BUCKETS],
            'amounts': [round(totals[key][0], 2) for key, _label in OVERDUE_BUCKETS],
            'counts': [totals[key][1] for key, _label in OVERDUE_BUCKETS],
        }

    def _expiries_by_month(self, today, company_ids):
        groups = self.env['realestate.contract']._read_group(
            [('company_id', 'in', company_ids),
             ('lifecycle_state', 'in', LIVE_LEASES),
             ('end_date', '>=', today),
             ('end_date', '<=', today + relativedelta(months=12))],
            groupby=['end_date:month'], aggregates=['__count'])
        by_month = {month.strftime('%Y-%m'): count
                    for month, count in groups if month}
        labels, counts = [], []
        for offset in range(12):
            month = today.replace(day=1) + relativedelta(months=offset)
            labels.append(month.strftime('%b %Y'))
            counts.append(by_month.get(month.strftime('%Y-%m'), 0))
        return {'labels': labels, 'counts': counts}

    # ==================================================================
    # Actions
    # ==================================================================
    @api.model
    def _list_action(self, name, res_model, domain, context=None):
        """Build a COMPLETE ``ir.actions.act_window`` for a list drilldown.

        ``views`` is mandatory: the web client maps over it unconditionally,
        and an action assembled in a method does not get it filled in.
        """
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': res_model,
            'views': [(False, 'list'), (False, 'form')],
            'view_mode': 'list,form',
            'domain': list(domain),
            'context': dict(context or {}),
            'target': 'current',
        }

    @api.model
    def _form_action(self, name, res_model, res_id, context=None):
        """Complete act_window for opening one record, or a new one."""
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': res_model,
            'views': [(False, 'form')],
            'view_mode': 'form',
            'res_id': res_id,
            'context': dict(context or {}),
            'target': 'current',
        }

    @api.model
    def action_drill(self, key, scope='mine'):
        """Open the records behind a tile, with the domain the tile counted.

        Raises on an unknown or hidden key rather than opening an unfiltered
        list: a tile that silently opens "everything" is worse than an error.
        """
        scope = self._check_scope(scope)
        today = fields.Date.context_today(self)
        tile = next((t for t in self._visible_tiles(today, scope) if t['key'] == key), None)
        if not tile or not tile['model']:
            raise UserError(_("Unknown dashboard drilldown '%s'.") % key)
        return self._list_action(tile['label'], tile['model'], tile['domain'])

    @api.model
    def action_drill_arrears_bucket(self, bucket):
        """Drilldown for one segment of the arrears-ageing chart."""
        if bucket not in dict(OVERDUE_BUCKETS):
            raise UserError(_("Unknown arrears bucket '%s'.") % bucket)
        Obligation = self.env['realestate.contract.payment']
        return self._list_action(
            _('Arrears — %s') % dict(OVERDUE_BUCKETS)[bucket],
            'realestate.contract.payment',
            expression.AND([self._arrears_domain(self.env.companies.ids),
                            Obligation._overdue_bucket_domain(bucket)]),
        )
