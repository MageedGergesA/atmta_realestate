# -*- coding: utf-8 -*-
"""The Treasury Overview payload.

One idea runs through this screen: **paper is not cash.** A cheque on hand,
deposited or in clearing is an instrument somebody might honour; only a
cleared one is money. Every figure below is therefore labelled as paper or as
cash, and the two are never added together -- which is the single most
common way a PDC-heavy business overstates its position.

The cards carry a `measure` of paper or cash so the front end can say which
is which, and the hints say it in words as well, because a colour-only
distinction is not a distinction.
"""

import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ON_HAND = ('draft', 'registered')
AT_BANK = ('deposited', 'in_clearing')
TREND_MONTHS = 6


class TreasuryOverview(models.AbstractModel):
    _inherit = 'realestate.check.dashboard'

    @api.model
    def _can_read(self, model_name):
        return model_name in self.env and self.env[model_name].has_access('read')

    @api.model
    def _today(self):
        return fields.Date.context_today(self)

    @api.model
    def _sum(self, domain):
        groups = self.env['realestate.check']._read_group(
            domain, aggregates=['amount:sum'])
        return (groups[0][0] if groups else 0.0) or 0.0

    # ==================================================================
    @api.model
    def get_overview(self, scope='team', filters=None):
        filters = dict(filters or {})
        today = self._today()
        company = self.env.company
        return {
            'title': _("Treasury"),
            'company_name': company.display_name,
            'currency_id': company.currency_id.id,
            'as_of': today.isoformat(),
            'scope': scope,
            'supports_scope': False,
            'quick_actions': self._quick_actions(),
            'filters': self._filters(),
            'kpis': self._kpis(today, filters),
            'lifecycle': self._lifecycle(filters),
            'maturity': self._maturity(today, filters),
            'alerts': self._attention(today, filters),
            'work_queue': self._work_queue(today),
            'banks_table': self._banks_table(filters),
            'upcoming_table': self._upcoming_table(today, filters),
        }

    # ------------------------------------------------------------------
    @api.model
    def _filters(self):
        filters = []
        if self._can_read('res.bank'):
            banks = self.env['res.bank'].search([], limit=30)
            if banks:
                filters.append({
                    'key': 'bank_id', 'label': _("Bank"), 'icon': 'fa-bank',
                    'all_label': _("All Banks"),
                    'options': [{'key': b.id, 'label': b.display_name} for b in banks],
                })
        if self._can_read('realestate.check.location'):
            locations = self.env['realestate.check.location'].search([], limit=30)
            if locations:
                filters.append({
                    'key': 'location_id', 'label': _("Held At"), 'icon': 'fa-archive',
                    'all_label': _("Anywhere"),
                    'options': [{'key': l.id, 'label': l.display_name}
                                for l in locations],
                })
        return filters

    @api.model
    def _base(self, filters):
        domain = [('company_id', 'in', self.env.companies.ids)]
        if filters.get('bank_id'):
            domain.append(('bank_id', '=', int(filters['bank_id'])))
        if filters.get('location_id') and 'location_id' in self.env['realestate.check']._fields:
            domain.append(('location_id', '=', int(filters['location_id'])))
        return domain

    # ==================================================================
    @api.model
    def _kpis(self, today, filters):
        if not self._can_read('realestate.check'):
            return []
        Check = self.env['realestate.check']
        base = self._base(filters)
        paper = _("Face value of paper held or presented, not money received.")
        cash = _("Bank-confirmed and reconciled. This is money.")

        on_hand = base + [('state', 'in', list(ON_HAND))]
        at_bank = base + [('state', 'in', list(AT_BANK))]
        matured = on_hand + [('due_date', '<', today)]
        due_30 = on_hand + [('due_date', '>=', today),
                            ('due_date', '<=', today + timedelta(days=30))]
        cleared = base + [('state', '=', 'cleared')]
        bounced = base + [('state', '=', 'bounced')]

        return [
            {'key': 'on_hand', 'label': _("Cheques On Hand"),
             'icon': 'fa-archive', 'tone': 'info',
             'value': self._sum(on_hand), 'format': 'monetary',
             'higher_is_better': True, 'qualifier': 'paper',
             'hint': paper + ' ' + _("%s instrument(s).",
                                     Check.search_count(on_hand)),
             'drill': True},
            {'key': 'due_30', 'label': _("Maturing in 30 Days"),
             'icon': 'fa-calendar', 'tone': 'warning',
             'value': self._sum(due_30), 'format': 'monetary',
             'higher_is_better': True, 'qualifier': 'paper',
             'hint': _("Paper that has to be banked soon.") + ' ' + paper,
             'drill': True},
            {'key': 'matured', 'label': _("Matured, Not Presented"),
             'icon': 'fa-exclamation-circle', 'tone': 'danger',
             'value': self._sum(matured), 'format': 'monetary',
             # Paper sitting past its due date in the safe is money nobody is
             # collecting, which is why rising is bad here.
             'higher_is_better': False,
             'warning': bool(Check.search_count(matured)),
             'warning_label': _("Bank these"),
             'qualifier': 'paper',
             'hint': _("Due dates have passed and the cheque is still in the "
                       "safe."), 'drill': True},
            {'key': 'at_bank', 'label': _("At Bank / In Clearing"),
             'icon': 'fa-bank', 'tone': 'primary',
             'value': self._sum(at_bank), 'format': 'monetary',
             'higher_is_better': True, 'qualifier': 'paper',
             'hint': _("Presented, not yet honoured.") + ' ' + paper,
             'drill': True},
            {'key': 'cleared', 'label': _("Cleared To Date"),
             'icon': 'fa-check-circle', 'tone': 'success',
             'value': self._sum(cleared), 'format': 'monetary',
             'higher_is_better': True, 'qualifier': 'cash',
             'hint': cash, 'drill': True},
            {'key': 'bounced', 'label': _("Bounced"),
             'icon': 'fa-times-circle', 'tone': 'danger',
             'value': self._sum(bounced), 'format': 'monetary',
             'higher_is_better': False,
             'warning': bool(Check.search_count(bounced)),
             'warning_label': _("Represent or replace"),
             'qualifier': 'paper',
             'hint': _("Returned unpaid. The underlying instalment is still "
                       "owed."), 'drill': True},
        ]

    # ==================================================================
    @api.model
    def _lifecycle(self, filters):
        if not self._can_read('realestate.check'):
            return {'segments': [], 'total': 0}
        Check = self.env['realestate.check']
        base = self._base(filters)
        states = [('registered', _("On Hand"), 'info'),
                  ('deposited', _("Deposited"), 'primary'),
                  ('in_clearing', _("In Clearing"), 'warning'),
                  ('cleared', _("Cleared"), 'success'),
                  ('bounced', _("Bounced"), 'danger'),
                  ('draft', _("Draft"), 'neutral')]
        segments = [{'key': 'state_%s' % key, 'label': label, 'tone': tone,
                     'value': Check.search_count(base + [('state', '=', key)])}
                    for key, label, tone in states]
        segments = [s for s in segments if s['value']]
        return {'segments': segments, 'total': sum(s['value'] for s in segments),
                'total_label': _("Cheques")}

    @api.model
    def _maturity(self, today, filters):
        """Face value maturing in each of the next six months.

        Expected paper, not guaranteed cash: a cheque can still bounce, and
        the subtitle on the chart says so.
        """
        if not self._can_read('realestate.check'):
            return None
        base = self._base(filters) + [('state', 'in', list(ON_HAND))]
        labels, amounts = [], []
        for offset in range(TREND_MONTHS):
            start = (today.replace(day=1) + relativedelta(months=offset))
            end = start + relativedelta(months=1) - timedelta(days=1)
            labels.append(start.strftime('%b'))
            amounts.append(round(self._sum(
                base + [('due_date', '>=', start), ('due_date', '<=', end)]), 2))
        return {'labels': labels, 'amounts': amounts}

    # ==================================================================
    @api.model
    def _attention(self, today, filters):
        if not self._can_read('realestate.check'):
            return []
        Check = self.env['realestate.check']
        base = self._base(filters)
        rows = [
            {'key': 'bounced', 'label': _("Bounced cheques"),
             'sublabel': _("The instalment behind them is still owed"),
             'value': Check.search_count(base + [('state', '=', 'bounced')]),
             'severity': 'critical', 'icon': 'fa-times-circle'},
            {'key': 'matured', 'label': _("Matured but still in the safe"),
             'sublabel': _("Money nobody is collecting"),
             'value': Check.search_count(
                 base + [('state', 'in', list(ON_HAND)), ('due_date', '<', today)]),
             'severity': 'critical', 'icon': 'fa-exclamation-circle'},
            {'key': 'due_today', 'label': _("Maturing today"),
             'value': Check.search_count(
                 base + [('state', 'in', list(ON_HAND)), ('due_date', '=', today)]),
             'severity': 'warning', 'icon': 'fa-calendar'},
            {'key': 'due_7', 'label': _("Maturing in 7 days"),
             'value': Check.search_count(
                 base + [('state', 'in', list(ON_HAND)),
                         ('due_date', '>', today),
                         ('due_date', '<=', today + timedelta(days=7))]),
             'severity': 'warning', 'icon': 'fa-calendar-o'},
            {'key': 'in_clearing', 'label': _("In clearing over 7 days"),
             'sublabel': _("Presented but not yet honoured"),
             'value': Check.search_count(
                 base + [('state', '=', 'in_clearing'),
                         ('deposit_date', '<=', today - timedelta(days=7))]),
             'icon': 'fa-hourglass-half'},
        ]
        return rows

    @api.model
    def _work_queue(self, today):
        rows = []
        if self._can_read('realestate.check'):
            Check = self.env['realestate.check']
            rows.append({'key': 'state_draft', 'label': _("Cheques to Register"),
                         'value': Check.search_count([('state', '=', 'draft')]),
                         'severity': 'warning', 'icon': 'fa-pencil'})
            rows.append({'key': 'ready_to_bank', 'label': _("Ready to Bank"),
                         'value': Check.search_count(
                             [('state', '=', 'registered'),
                              ('due_date', '<=', today)]),
                         'severity': 'warning', 'icon': 'fa-bank'})
        if self._can_read('realestate.check.deposit'):
            rows.append({'key': 'deposits_draft', 'label': _("Deposit Slips in Draft"),
                         'value': self.env['realestate.check.deposit'].search_count(
                             [('state', '=', 'draft')]),
                         'icon': 'fa-file-text-o'})
        if self._can_read('realestate.check.bounce'):
            rows.append({'key': 'bounces_pending', 'label': _("Bounces to Resolve"),
                         'value': self.env['realestate.check.bounce'].search_count(
                             [('resolution', '=', 'pending')]),
                         'severity': 'critical', 'icon': 'fa-times-circle'})
        return rows

    # ==================================================================
    @api.model
    def _banks_table(self, filters):
        """Concentration by drawee bank.

        One bank holding most of the book is a risk nobody sees in a single
        total.
        """
        if not self._can_read('realestate.check'):
            return []
        Check = self.env['realestate.check']
        base = self._base(filters)
        rows = []
        for bank, count, amount in Check._read_group(
                base + [('state', 'not in', ('cancelled', 'returned'))],
                groupby=['bank_id'], aggregates=['__count', 'amount:sum']):
            if not bank:
                continue
            cleared = self._sum(base + [('bank_id', '=', bank.id),
                                        ('state', '=', 'cleared')])
            bounced = Check.search_count(base + [('bank_id', '=', bank.id),
                                                 ('state', '=', 'bounced')])
            rows.append({
                'id': bank.id, 'bank': bank.display_name,
                'cheques': count, 'face': amount or 0.0, 'cleared': cleared,
                'status': (_("%s bounced", bounced) if bounced else _("Clean")),
                'status_tone': 'danger' if bounced else 'success',
            })
        rows.sort(key=lambda row: -row['face'])
        return rows

    @api.model
    def _upcoming_table(self, today, filters):
        if not self._can_read('realestate.check'):
            return []
        cheques = self.env['realestate.check'].search(
            self._base(filters) + [('state', 'in', list(ON_HAND))],
            order='due_date', limit=12)
        rows = []
        for cheque in cheques:
            days = (cheque.due_date - today).days if cheque.due_date else 0
            rows.append({
                'id': cheque.id,
                'cheque': cheque.name or cheque.check_number,
                'drawer': cheque.partner_id.display_name or '—',
                'bank': cheque.bank_id.display_name or '—',
                'amount': cheque.amount,
                'due': fields.Date.to_string(cheque.due_date) if cheque.due_date else '—',
                'status': (_("Overdue") if days < 0
                           else (_("Today") if days == 0 else _("%s days", days))),
                'status_tone': ('danger' if days < 0
                                else ('warning' if days <= 7 else 'success')),
            })
        return rows

    # ==================================================================
    @api.model
    def _quick_actions(self):
        actions = []
        if self._can_read('realestate.check'):
            actions.append({'key': 'new_check', 'label': _("Register Cheque"),
                            'icon': 'fa-plus'})
        if self._can_read('realestate.check.deposit'):
            actions.append({'key': 'deposits', 'label': _("Deposit Slips"),
                            'icon': 'fa-bank'})
        return actions

    @api.model
    def _targets(self, today):
        base = [('company_id', 'in', self.env.companies.ids)]
        return {
            'on_hand': (_("Cheques On Hand"), 'realestate.check',
                        base + [('state', 'in', list(ON_HAND))]),
            'due_30': (_("Maturing in 30 Days"), 'realestate.check',
                       base + [('state', 'in', list(ON_HAND)),
                               ('due_date', '>=', today),
                               ('due_date', '<=', today + timedelta(days=30))]),
            'due_7': (_("Maturing in 7 Days"), 'realestate.check',
                      base + [('state', 'in', list(ON_HAND)),
                              ('due_date', '>', today),
                              ('due_date', '<=', today + timedelta(days=7))]),
            'due_today': (_("Maturing Today"), 'realestate.check',
                          base + [('state', 'in', list(ON_HAND)),
                                  ('due_date', '=', today)]),
            'matured': (_("Matured, Not Presented"), 'realestate.check',
                        base + [('state', 'in', list(ON_HAND)),
                                ('due_date', '<', today)]),
            'at_bank': (_("At Bank or In Clearing"), 'realestate.check',
                        base + [('state', 'in', list(AT_BANK))]),
            'in_clearing': (_("In Clearing"), 'realestate.check',
                            base + [('state', '=', 'in_clearing')]),
            'cleared': (_("Cleared Cheques"), 'realestate.check',
                        base + [('state', '=', 'cleared')]),
            'bounced': (_("Bounced Cheques"), 'realestate.check',
                        base + [('state', '=', 'bounced')]),
            'ready_to_bank': (_("Ready to Bank"), 'realestate.check',
                              base + [('state', '=', 'registered'),
                                      ('due_date', '<=', today)]),
            'deposits_draft': (_("Deposit Slips in Draft"),
                               'realestate.check.deposit', [('state', '=', 'draft')]),
            'bounces_pending': (_("Bounces to Resolve"),
                                'realestate.check.bounce',
                                [('resolution', '=', 'pending')]),
        }

    @api.model
    def action_overview_drill(self, key):
        today = self._today()
        target = self._targets(today).get(key)
        if not target and key.startswith('state_'):
            target = (_("Cheques"), 'realestate.check',
                      [('state', '=', key[len('state_'):])])
        if not target:
            raise UserError(_("Unknown dashboard figure '%s'.", key))
        name, model, domain = target
        if not self._can_read(model):
            raise UserError(_("These records are not available to you."))
        return {
            'type': 'ir.actions.act_window', 'name': name, 'res_model': model,
            'views': [[False, 'list'], [False, 'form']], 'view_mode': 'list,form',
            'domain': domain, 'context': {'create': False}, 'target': 'current',
        }

    @api.model
    def action_overview_quick(self, key):
        targets = {'new_check': ('realestate.check', 'form'),
                   'deposits': ('realestate.check.deposit', 'list')}
        target = targets.get(key)
        if not target or not self._can_read(target[0]):
            raise UserError(_("Unknown dashboard action '%s'.", key))
        model, mode = target
        return {
            'type': 'ir.actions.act_window', 'name': _("Treasury"),
            'res_model': model,
            'views': ([[False, 'form']] if mode == 'form'
                      else [[False, 'list'], [False, 'form']]),
            'view_mode': 'form' if mode == 'form' else 'list,form',
            'target': 'current',
        }
