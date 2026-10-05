# -*- coding: utf-8 -*-
"""Base of every ATMTA application dashboard.

A provider describes; this class counts. A subclass returns *specs* -- sections
of tiles, charts, quick actions -- and ``get_dashboard`` turns them into the
payload the ``atmta_dashboard`` client action renders:

* A tile names a model and a domain. Its value is counted (or summed) here with
  the user's environment, so access rights and record rules apply exactly as
  they do in the list the tile opens. A tile on a model the user cannot read is
  dropped, never shown as a zero that would read as "nothing to do".
* ``action_drill`` rebuilds the same specs and opens the tile's own model and
  domain, so a figure and the records behind it cannot disagree.
* Scope: ``mine`` narrows every tile that declares a ``user_field`` to the
  current user; ``team`` shows everything the user may see.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression

_logger = logging.getLogger(__name__)

FORMATS = ('integer', 'monetary', 'percent', 'decimal')


class AtmtaDashboardProvider(models.AbstractModel):
    _name = 'atmta.dashboard.provider'
    _description = 'ATMTA Dashboard Provider'

    # ------------------------------------------------------------------
    # To implement
    # ------------------------------------------------------------------
    def _dashboard_title(self):
        return _("Dashboard")

    def _dashboard_sections(self, scope):
        """Return ``[{'id', 'title', 'icon', 'tiles': [tile spec, ...]}]``.

        Tile spec keys: ``key`` (unique), ``label``, ``model``, ``domain``;
        optional ``measure`` (field summed instead of counted), ``format``,
        ``hint``, ``user_field`` (narrowed in *mine* scope), ``warning_above``
        (flag when the value exceeds it), ``groups`` (comma-separated group
        XML IDs; the tile is shown to members of any of them), ``value`` (a
        figure computed by the provider; the tile still opens ``model`` /
        ``domain``), ``action_name``, ``context``.
        """
        return []

    def _dashboard_charts(self, scope):
        """Return ``[{'key', 'title', 'subtitle', 'type', 'labels', 'series': [
        {'name', 'label', 'data', 'format'}], 'drill': [tile spec or None, ...]}]``.
        """
        return []

    def _dashboard_quick_actions(self):
        """Return ``[{'key', 'label', 'icon', 'action': xmlid}]``, or with
        ``'model'`` (and optional ``'context'``) instead of ``'action'`` to open
        a new record's form."""
        return []

    def _dashboard_supports_scope(self):
        return False

    def _dashboard_filters(self):
        """Return ``[{'key', 'label', 'icon', 'all_label', 'options': [
        {'key', 'label'}]}]`` for the global filter bar.

        A dashboard shows only the selectors it actually honours. Offering a
        Building filter that nothing reads is worse than offering none: the
        reader changes it, the figures do not move, and they stop trusting
        the screen.
        """
        return []

    def _dashboard_alerts(self, scope):
        """Return ``[{'key', 'label', 'value', 'severity', 'icon'}]``.

        Severity is ``critical``, ``warning`` or omitted. Rows render through
        the same row list as the work queue and open records by ``key`` through
        ``action_drill``, so an alert must have a matching tile spec.
        """
        return []

    def _dashboard_work_queue(self, scope):
        """What *this user* has to do, in the same row format as the alerts."""
        return []

    def _dashboard_tables(self, scope):
        """Return ``[{'key', 'title', 'icon', 'span', 'columns', 'rows'}]``."""
        return []

    # ------------------------------------------------------------------
    # Helpers for providers
    # ------------------------------------------------------------------
    @api.model
    def _can_read(self, model_name):
        if model_name not in self.env:
            return False
        return self.env[model_name].has_access('read')

    @api.model
    def _in_groups(self, groups):
        if not groups:
            return True
        return any(self.env.user.has_group(xmlid.strip()) for xmlid in groups.split(','))

    @api.model
    def _quick_available(self, quick):
        if quick.get('model'):
            return self._can_read(quick['model']) and self.env[quick['model']].has_access('create')
        action = self.env.ref(quick['action'], raise_if_not_found=False)
        if not action:
            return False
        # A button that only ends in an access error is not offered.
        res_model = getattr(action.sudo(), 'res_model', False)
        return self._can_read(res_model) if res_model else True

    @api.model
    def _today(self):
        return fields.Date.context_today(self)

    @api.model
    def _company_domain(self, model_name):
        Model = self.env[model_name]
        if 'company_id' in Model._fields:
            return [('company_id', 'in', self.env.companies.ids + [False])]
        return []

    # ------------------------------------------------------------------
    # Payload
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard(self, scope='team', filters=None):
        """The whole payload for one dashboard screen.

        ``filters`` is whatever the filter bar currently has selected. It is
        passed down to every hook rather than applied here, because only the
        provider knows which of its models the Building selector is even a
        field on. ``action_drill`` is given the same dict, so a tile and the
        records it opens can never disagree about what is selected.
        """
        scope = scope if scope in ('mine', 'team') else 'team'
        self = self.with_context(dashboard_filters=dict(filters or {}))
        sections = []
        for section in self._dashboard_sections(scope):
            tiles = [t for t in (self._resolve_tile(spec, scope) for spec in section.get('tiles', ())) if t]
            if tiles:
                sections.append({
                    'id': section['id'],
                    'title': section['title'],
                    'subtitle': section.get('subtitle', ''),
                    'icon': section.get('icon') or 'fa-th-large',
                    'tiles': tiles,
                })
        charts = []
        for chart in self._dashboard_charts(scope):
            drill = chart.get('drill') or []
            charts.append({
                'key': chart['key'],
                'title': chart['title'],
                'subtitle': chart.get('subtitle', ''),
                'icon': chart.get('icon', ''),
                'span': chart.get('span', 'o_ad_col_4'),
                'type': chart.get('type', 'bar'),
                'labels': chart.get('labels', []),
                'series': [{
                    'name': s['name'], 'label': s['label'], 'data': s['data'],
                    'format': s.get('format', 'integer'),
                    # A series may override the chart's type so a running
                    # total can ride as a line over the bars it totals.
                    # Whitelisted here, which is why it has to be listed.
                    'type': s.get('type'),
                } for s in chart.get('series', ())],
                'drillable': [bool(spec) for spec in drill],
            })
        quick = [
            {'key': q['key'], 'label': q['label'], 'icon': q.get('icon', 'fa-plus')}
            for q in self._dashboard_quick_actions()
            if self._in_groups(q.get('groups')) and self._quick_available(q)
        ]
        company = self.env.company
        return {
            'title': self._dashboard_title(),
            'company_name': company.display_name,
            'as_of': fields.Date.to_string(self._today()),
            'currency_id': company.currency_id.id,
            'supports_scope': self._dashboard_supports_scope(),
            'scope': scope,
            'sections': sections,
            'charts': charts,
            'quick_actions': quick,
            'filters': self._dashboard_filters(),
            'alerts': self._dashboard_alerts(scope),
            'work_queue': self._dashboard_work_queue(scope),
            'tables': self._dashboard_tables(scope),
        }

    def _tile_domain(self, spec, scope):
        domain = list(spec.get('domain') or [])
        domain = expression.AND([domain, self._company_domain(spec['model'])])
        if scope == 'mine' and spec.get('user_field'):
            domain = expression.AND([domain, [(spec['user_field'], '=', self.env.uid)]])
        return domain

    def _resolve_tile(self, spec, scope):
        if not self._in_groups(spec.get('groups')):
            return None
        # A tile may carry a pre-computed `value` instead of a model to count.
        # Investment needs that: a portfolio NPV is the sum of a computed
        # field across filtered records, not a search_count, and forcing it
        # through a domain would mean re-deriving in SQL what the model
        # already knows how to work out.
        model_name = spec.get('model')
        if not model_name:
            if 'value' not in spec:
                _logger.warning("Dashboard tile %s has neither model nor value",
                                spec.get('key'))
                return None
            return self._value_tile(spec)
        if not self._can_read(model_name):
            return None
        Model = self.env[model_name]
        domain = self._tile_domain(spec, scope)
        fmt = spec.get('format', 'monetary' if spec.get('measure') else 'integer')
        if 'value' in spec:
            value = spec['value']
        elif spec.get('measure'):
            groups = Model._read_group(domain, aggregates=[f"{spec['measure']}:sum"])
            value = (groups[0][0] if groups else 0.0) or 0.0
        else:
            value = Model.search_count(domain)
        warning_above = spec.get('warning_above')
        tile = {
            'key': spec['key'],
            'label': spec['label'],
            'value': value,
            'format': fmt if fmt in FORMATS else 'integer',
            'hint': spec.get('hint', ''),
            'icon': spec.get('icon') or 'fa-th-large',
            'tone': spec.get('tone') or 'primary',
            # Whether a rise is good news. Occupancy up is green; arrears up is
            # red. The front end cannot work this out from the sign, and
            # guessing gets every risk metric exactly backwards.
            'higher_is_better': spec.get('higher_is_better', True),
            'warning': warning_above is not None and value > warning_above,
            'warning_label': spec.get('warning_label') or _("Needs attention"),
            'drill': spec.get('drill', True),
            # A word saying what KIND of number this is -- Treasury's paper
            # versus cash, Investment's modelled versus booked. Distinct from
            # `measure` above, which names a field to sum.
            'qualifier': spec.get('qualifier'),
            'suffix': spec.get('suffix'),
            # A second fact: what the number is out of, or what it is made of.
            'context': spec.get('context') or '',
        }
        tile.update(self._tile_trend(spec, domain))
        return tile

    def _value_tile(self, spec):
        """A tile whose value the provider worked out for itself."""
        value = spec['value']
        warning_above = spec.get('warning_above')
        fmt = spec.get('format', 'integer')
        return {
            'key': spec['key'],
            'label': spec['label'],
            'value': value,
            'format': fmt if fmt in FORMATS else 'integer',
            'hint': spec.get('hint', ''),
            'icon': spec.get('icon') or 'fa-th-large',
            'tone': spec.get('tone') or 'primary',
            'higher_is_better': spec.get('higher_is_better', True),
            'warning': warning_above is not None and value > warning_above,
            'warning_label': spec.get('warning_label') or _("Needs attention"),
            # Nothing to open: the figure was computed, not counted, so there
            # is no domain that reproduces it.
            'drill': spec.get('drill', False),
            'qualifier': spec.get('qualifier'),
            'suffix': spec.get('suffix'),
            # A second fact: what the number is out of, or what it is made of.
            'context': spec.get('context') or '',
        }

    # ------------------------------------------------------------------
    # Trend and comparison
    # ------------------------------------------------------------------
    @api.model
    def _tile_trend(self, spec, domain):
        """The tile's recent history and its change against the last period.

        A tile only gets a trend if it declares ``trend_field`` -- a date or
        datetime field on its own model that says *when the thing happened*.

        That restriction is the whole point. A sparkline is only honest for a
        **flow**: leases signed, rent collected, tickets raised. A **stock**
        -- "active leases *right now*", "units currently available" -- cannot
        be reconstructed from a date field, because the historical value
        depends on states that have since changed. Plotting `create_date` for
        one of those draws a confident line that is simply wrong, so a tile
        with no ``trend_field`` gets no sparkline and no delta rather than an
        invented one.
        """
        # A stock CAN be plotted honestly when the record stores both when it
        # opened and when it closed: at any past date, it was open if it had
        # been opened by then and had not yet been closed. That is measured
        # history, not a reconstruction from a single date, so it is offered
        # as a separate, explicit pair of fields.
        if spec.get('trend_open_field') and spec.get('trend_close_field'):
            return self._tile_stock_trend(spec)

        field = spec.get('trend_field')
        if not field:
            return {}
        Model = self.env[spec['model']]
        if field not in Model._fields:
            _logger.warning("Dashboard tile %s declares unknown trend_field %s",
                            spec.get('key'), field)
            return {}

        buckets = spec.get('trend_buckets', 6)
        granularity = spec.get('trend_granularity', 'month')
        today = self._today()
        start = self._period_start(today, granularity, buckets - 1)

        aggregate = f"{spec['measure']}:sum" if spec.get('measure') else '__count'
        try:
            groups = Model._read_group(
                expression.AND([domain, [(field, '>=', start)]]),
                groupby=[f'{field}:{granularity}'],
                aggregates=[aggregate],
            )
        except (ValueError, KeyError) as error:
            _logger.warning("Dashboard tile %s could not read its trend: %s",
                            spec.get('key'), error)
            return {}

        # `_read_group` returns only the buckets that have rows. A missing
        # month is a real zero, and dropping it would shift every later point
        # left and draw a trend that never happened.
        found = {}
        for bucket, measure in groups:
            key = bucket[0] if isinstance(bucket, tuple) else bucket
            if hasattr(key, 'date'):
                key = key.date()
            found[self._bucket_key(key, granularity)] = measure or 0.0

        series = []
        for offset in range(buckets - 1, -1, -1):
            moment = self._period_start(today, granularity, offset)
            series.append(found.get(self._bucket_key(moment, granularity), 0.0))

        trend = {'spark': series,
                 'comparison_label': spec.get('comparison_label')
                 or self._comparison_label(granularity)}
        if len(series) >= 2 and series[-2]:
            trend['delta_percent'] = (series[-1] - series[-2]) / abs(series[-2]) * 100.0
        return trend

    @api.model
    def _tile_stock_trend(self, spec):
        """History of a stock, counted from its own opening and closing dates.

        At the end of each bucket, a record counted if it had been opened by
        then and had not been closed yet. Nothing is inferred: both dates are
        on the record, so this is what the number actually was.

        The tile's own domain is deliberately NOT reused. That domain carries
        the CURRENT state ("state in open"), and applying it to history would
        ask "how many records that are open today existed last month", which
        is a different and far less useful question. `trend_domain` carries
        only the filters that identify the *kind* of record -- a priority, a
        severity -- which do not change as the record moves through its life.
        """
        model_name = spec['model']
        Model = self.env[model_name]
        open_field = spec['trend_open_field']
        close_field = spec['trend_close_field']
        for field in (open_field, close_field):
            if field not in Model._fields:
                _logger.warning("Dashboard tile %s declares unknown trend field %s",
                                spec.get('key'), field)
                return {}

        buckets = spec.get('trend_buckets', 6)
        granularity = spec.get('trend_granularity', 'month')
        today = self._today()
        base = expression.AND([
            list(spec.get('trend_domain') or []),
            self._company_domain(model_name),
        ])

        series = []
        for offset in range(buckets - 1, -1, -1):
            # The END of the bucket: the stock as it stood when that period
            # closed. Using the start would report every period one period late.
            edge = self._period_start(today, granularity, offset - 1) if offset else today
            domain = expression.AND([base, [
                (open_field, '<=', edge),
                '|', (close_field, '=', False), (close_field, '>', edge),
            ]])
            try:
                series.append(float(Model.search_count(domain)))
            except (ValueError, KeyError) as error:
                _logger.warning("Dashboard tile %s could not read its stock trend: %s",
                                spec.get('key'), error)
                return {}

        trend = {'spark': series,
                 'comparison_label': spec.get('comparison_label')
                 or self._comparison_label(granularity)}
        if len(series) >= 2 and series[-2]:
            trend['delta_percent'] = (series[-1] - series[-2]) / abs(series[-2]) * 100.0
        return trend

    @api.model
    def _comparison_label(self, granularity):
        """Translated at call time, not at import time.

        A module-level ``_()`` is evaluated once when the registry loads, in
        whatever language happened to be active then, and every user sees that
        one. This suite is bilingual, so that is a visible bug, not a nicety.
        """
        return {
            'day': _("vs yesterday"),
            'week': _("vs last week"),
            'month': _("vs last month"),
            'quarter': _("vs last quarter"),
            'year': _("vs last year"),
        }.get(granularity, _("vs previous period"))

    @staticmethod
    def _period_start(today, granularity, offset):
        """Start of the bucket ``offset`` periods before the one holding today."""
        if granularity == 'day':
            return today - relativedelta(days=offset)
        if granularity == 'week':
            return today - relativedelta(days=today.weekday(), weeks=offset)
        if granularity == 'quarter':
            first = today.replace(day=1, month=((today.month - 1) // 3) * 3 + 1)
            return first - relativedelta(months=3 * offset)
        if granularity == 'year':
            return today.replace(day=1, month=1) - relativedelta(years=offset)
        return today.replace(day=1) - relativedelta(months=offset)

    @staticmethod
    def _bucket_key(moment, granularity):
        if granularity == 'day':
            return moment.isoformat()
        if granularity == 'week':
            return (moment - relativedelta(days=moment.weekday())).isoformat()
        if granularity == 'quarter':
            return '%d-Q%d' % (moment.year, (moment.month - 1) // 3 + 1)
        if granularity == 'year':
            return str(moment.year)
        return '%d-%02d' % (moment.year, moment.month)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _find_tile_spec(self, key, scope):
        for section in self._dashboard_sections(scope):
            for spec in section.get('tiles', ()):
                if spec['key'] == key:
                    return spec
        return None

    def _open_spec(self, spec, scope):
        if not spec or not self._in_groups(spec.get('groups')) or not self._can_read(spec['model']):
            raise UserError(_("These records are not available to you."))
        return {
            'type': 'ir.actions.act_window',
            'name': spec.get('action_name') or spec['label'],
            'res_model': spec['model'],
            'views': [[False, 'list'], [False, 'form']],
            'view_mode': 'list,form',
            'domain': self._tile_domain(spec, scope),
            'context': dict(spec.get('context') or {}, create=False),
            'target': 'current',
        }

    @api.model
    def action_drill(self, key, scope='team'):
        spec = self._find_tile_spec(key, scope)
        if not spec or spec.get('drill') is False:
            raise UserError(_("Unknown dashboard figure '%s'.", key))
        return self._open_spec(spec, scope)

    @api.model
    def action_drill_chart(self, chart_key, index, scope='team'):
        for chart in self._dashboard_charts(scope):
            if chart['key'] == chart_key:
                drill = chart.get('drill') or []
                if 0 <= index < len(drill) and drill[index]:
                    return self._open_spec(drill[index], scope)
        raise UserError(_("This part of the chart does not open any records."))

    @api.model
    def action_quick(self, key):
        for quick in self._dashboard_quick_actions():
            if quick['key'] == key and self._in_groups(quick.get('groups')) and self._quick_available(quick):
                if quick.get('model'):
                    return {
                        'type': 'ir.actions.act_window',
                        'name': quick['label'],
                        'res_model': quick['model'],
                        'views': [[False, 'form']],
                        'view_mode': 'form',
                        'context': dict(quick.get('context') or {}),
                        'target': 'current',
                    }
                return self.env['ir.actions.actions']._for_xml_id(quick['action'])
        raise UserError(_("Unknown dashboard action '%s'.", key))
