"""Every Rental screen loads, lists, filters and groups for every role.

For each menu screen a role can see, this does what the web client does when
the user opens it and uses its search panel: load the views, read the first
page with the list view's own columns, and apply every filter and group-by of
the search view. A list column the role cannot read, a filter naming a field
that no longer exists or cannot be searched, or a broken default filter fails
here with the screen, the role and the error.
"""

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo import fields
from odoo.tests.common import new_test_user, tagged
from odoo.tools.safe_eval import datetime, safe_eval, time

import types

from .common import LeaseCase, shown_menu_ids

class _ClientDatetime(datetime.datetime):
    """The web client's ``context_today()``: a datetime that knows ``to_utc()``.

    Search view domains are evaluated in the browser, where ``context_today()``
    returns a date object with ``to_utc()``; Python's ``datetime`` has none.
    Arithmetic keeps the type, so ``(context_today() + relativedelta(days=7)).to_utc()``
    evaluates here as it does in the client.
    """

    def to_utc(self):
        return self

    def _wrap(self, value):
        if isinstance(value, datetime.datetime) and not isinstance(value, _ClientDatetime):
            return _ClientDatetime.combine(value.date(), value.time())
        return value

    def __add__(self, other):
        return self._wrap(super().__add__(other))

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        return self._wrap(super().__sub__(other))


#: The ``datetime`` module as the web client exposes it to domains:
#: ``datetime.datetime.combine(...)`` returns a value that knows ``to_utc()``.
_CLIENT_DATETIME = types.SimpleNamespace(
    datetime=_ClientDatetime, date=datetime.date, time=datetime.time,
    timedelta=datetime.timedelta)


ROLES = {
    'agent': 'atmta_real_estate.group_rental_agent',
    'property_manager': 'atmta_real_estate.group_property_manager',
    'rental_manager': 'atmta_real_estate.group_rental_manager',
}


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestScreenSweep(LeaseCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.users = {'admin': cls.env.ref('base.user_admin')}
        for label, group in ROLES.items():
            cls.users[label] = new_test_user(
                cls.env, login='sweep_%s' % label, company_id=cls.company.id,
                groups='base.group_user,%s' % group)
        # Some data for the lists and groupings to chew on.
        lease = cls.env['realestate.contract'].create({
            'partner_id': cls.tenant.id, 'property_id': cls.unit_a.id,
            'is_single_property': True, 'is_multi_property': False,
            'start_date': cls.today, 'end_date': cls.today + relativedelta(years=1, days=-1),
            'price': 1000.0, 'company_id': cls.company.id, 'currency_id': cls.currency.id,
        })
        cls.lease = lease

    # ------------------------------------------------------------------
    def _eval_context(self, user):
        today = fields.Date.context_today(self.env['res.partner'])
        return {
            'uid': user.id, 'user': user, 'active_id': False, 'active_ids': [],
            'context_today': lambda: _ClientDatetime.combine(today, datetime.time()),
            'relativedelta': relativedelta, 'datetime': _CLIENT_DATETIME, 'time': time,
            'current_date': today.strftime('%Y-%m-%d'),
            'company_id': self.company.id, 'company_ids': [self.company.id],
            'allowed_company_ids': [self.company.id],
        }

    def _screens(self, user):
        root = self.env.ref('atmta_real_estate.real_estate_menu_root')
        menus = self.env['ir.ui.menu'].with_context(**{'ir.ui.menu.full_list': True}).search(
            [('id', 'child_of', root.id)])
        visible = shown_menu_ids(self.env, user)
        for menu in menus:
            action = menu.action
            if (menu.id in visible and action and action._name == 'ir.actions.act_window'
                    and action.res_model and action.res_model != 'res.config.settings'):
                yield menu, action

    def _sweep(self, label):
        user = self.users[label]
        ctx_vars = self._eval_context(user)
        failures = []

        def fail(menu, step, exc):
            failures.append('[%s] %s — %s: %s: %s' % (
                label, menu.complete_name, step, type(exc).__name__,
                (str(exc).splitlines() or [''])[0][:200]))

        for menu, action in self._screens(user):
            Model = self.env[action.res_model].with_user(user)
            try:
                action_context = safe_eval(action.context or '{}', ctx_vars)
                action_domain = safe_eval(action.domain or '[]', ctx_vars)
            except Exception as exc:  # noqa: BLE001
                fail(menu, 'action domain/context', exc)
                continue
            Model = Model.with_context(**{k: v for k, v in action_context.items()
                                           if not k.startswith('search_default_')})
            view_types = [v for v in (action.view_mode or 'list').split(',') if v]
            try:
                views = Model.get_views([(False, t) for t in view_types] + [(action.search_view_id.id or False, 'search')])
            except Exception as exc:  # noqa: BLE001
                fail(menu, 'get_views', exc)
                continue

            # First page of the list, with the list's own columns.
            list_view = views['views'].get('list')
            if list_view:
                arch = etree.fromstring(list_view['arch'])
                spec = {}
                for node in arch.iter('field'):
                    if any(p.tag == 'field' for p in node.iterancestors()):
                        continue
                    field = Model._fields.get(node.get('name'))
                    if field is None:
                        continue
                    spec[node.get('name')] = ({'fields': {'display_name': {}}}
                                              if field.type == 'many2one' else {})
                try:
                    Model.web_search_read(action_domain, spec, limit=80)
                except Exception as exc:  # noqa: BLE001
                    fail(menu, 'list page', exc)

            # Every other view the action offers, the way the client loads it.
            today = fields.Date.context_today(self.env['res.partner'])
            for view_type in ('kanban', 'pivot', 'graph', 'calendar'):
                view = views['views'].get(view_type)
                if not view:
                    continue
                arch = etree.fromstring(view['arch'])
                try:
                    if view_type == 'kanban':
                        group_by = arch.get('default_group_by')
                        if group_by:
                            Model.web_read_group(action_domain, ['__count'], [group_by], limit=10)
                        else:
                            Model.web_search_read(action_domain, {'display_name': {}}, limit=10)
                    elif view_type in ('pivot', 'graph'):
                        nodes = list(arch.iter('field'))
                        groupbys = [
                            node.get('name') + (':%s' % node.get('interval') if node.get('interval') else '')
                            for node in nodes if node.get('type') in ('row', 'col')
                        ]
                        if view_type == 'graph' and not groupbys:
                            groupbys = [node.get('name') for node in nodes
                                        if node.get('type') not in ('measure',)][:1]
                        measures = [node.get('name') for node in nodes
                                    if node.get('type') == 'measure' and node.get('name') != '__count']
                        aggregates = ['__count'] + ['%s:sum' % name for name in measures]
                        Model.read_group(action_domain, aggregates, groupbys[:1], lazy=True)
                    elif view_type == 'calendar':
                        start = arch.get('date_start')
                        Model.search_count(action_domain + [
                            (start, '>=', today - relativedelta(days=31)),
                            (start, '<=', today + relativedelta(days=31)),
                        ])
                except Exception as exc:  # noqa: BLE001
                    fail(menu, '%s view' % view_type, exc)

            # Every filter and group-by of the search view, and the defaults.
            search_arch = etree.fromstring(views['views']['search']['arch'])
            for node in search_arch.iter('filter'):
                name = node.get('name') or node.get('string')
                try:
                    domain = safe_eval(node.get('domain') or '[]', ctx_vars)
                    context = safe_eval(node.get('context') or '{}', ctx_vars)
                    group_by = context.get('group_by')
                    if node.get('date'):
                        continue  # date filters are built client-side from the field
                    if domain:
                        Model.search_count(action_domain + domain, limit=1)
                    if group_by:
                        groupbys = group_by if isinstance(group_by, list) else [group_by]
                        Model.web_read_group(action_domain, ['__count'], groupbys[:1], limit=10)
                except Exception as exc:  # noqa: BLE001
                    fail(menu, 'filter %r' % name, exc)
            for node in search_arch.iter('field'):
                field = Model._fields.get(node.get('name'))
                if field is None:
                    fail(menu, 'search field %r' % node.get('name'),
                         ValueError('not a field of %s' % action.res_model))
                    continue
                if node.get('filter_domain'):
                    try:
                        domain = safe_eval(node.get('filter_domain'), dict(ctx_vars, self='x'))
                        Model.search_count(action_domain + domain, limit=1)
                    except Exception as exc:  # noqa: BLE001
                        fail(menu, 'search field %r' % node.get('name'), exc)
            defaults = [key[len('search_default_'):] for key in action_context
                        if key.startswith('search_default_')]
            names = {n.get('name') for n in search_arch.iter('filter')} | {
                n.get('name') for n in search_arch.iter('field')}
            for default in defaults:
                if default not in names:
                    fail(menu, 'default filter %r' % default,
                         ValueError('not in the search view'))
        return failures

    def test_admin(self):
        failures = self._sweep('admin')
        self.assertFalse(failures, '\n'.join(failures))

    def test_leasing_agent(self):
        failures = self._sweep('agent')
        self.assertFalse(failures, '\n'.join(failures))

    def test_property_manager(self):
        failures = self._sweep('property_manager')
        self.assertFalse(failures, '\n'.join(failures))

    def test_rental_manager(self):
        failures = self._sweep('rental_manager')
        self.assertFalse(failures, '\n'.join(failures))
