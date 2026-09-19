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

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression

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
    def get_dashboard(self, scope='team'):
        scope = scope if scope in ('mine', 'team') else 'team'
        sections = []
        for section in self._dashboard_sections(scope):
            tiles = [t for t in (self._resolve_tile(spec, scope) for spec in section.get('tiles', ())) if t]
            if tiles:
                sections.append({
                    'id': section['id'],
                    'title': section['title'],
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
                'type': chart.get('type', 'bar'),
                'labels': chart.get('labels', []),
                'series': [{
                    'name': s['name'], 'label': s['label'], 'data': s['data'],
                    'format': s.get('format', 'integer'),
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
        }

    def _tile_domain(self, spec, scope):
        domain = list(spec.get('domain') or [])
        domain = expression.AND([domain, self._company_domain(spec['model'])])
        if scope == 'mine' and spec.get('user_field'):
            domain = expression.AND([domain, [(spec['user_field'], '=', self.env.uid)]])
        return domain

    def _resolve_tile(self, spec, scope):
        if not self._in_groups(spec.get('groups')) or not self._can_read(spec['model']):
            return None
        Model = self.env[spec['model']]
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
        return {
            'key': spec['key'],
            'label': spec['label'],
            'value': value,
            'format': fmt if fmt in FORMATS else 'integer',
            'hint': spec.get('hint', ''),
            'warning': warning_above is not None and value > warning_above,
            'drill': spec.get('drill', True),
        }

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
