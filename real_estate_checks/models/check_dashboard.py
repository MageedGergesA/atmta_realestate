# -*- coding: utf-8 -*-
"""M20 / M21 — the treasury dashboard, and the maturity forecast.

Two rules govern everything here.

**One: every number is produced by `_read_group`, never by loading records.**
A developer with 100,000 cheques cannot have a KPI that reads them all into
Python (M33).

**Two: nothing is labelled as cash unless it is cash.** "PDC amount received"
is paper. The dashboard says so, in the payload, so a front end cannot
accidentally present it as collection. Every KPI carries an explicit `kind` of
either `cash` or `paper`, and the one figure that is genuinely money — cleared —
is the only one marked `cash`.

Every KPI also carries the `domain` that produced it. The drill-down opens that
exact domain, so a count and its list can never disagree (the discipline the
Rental Dashboard V2 established).
"""

from odoo import _, api, fields, models

from .check_states import (
    CHECK_AT_BANK,
    CHECK_ON_HAND,
    MATURITY_BOUNDS,
    MATURITY_BUCKET,
)


class CheckDashboard(models.AbstractModel):
    _name = 'realestate.check.dashboard'
    _description = 'PDC Treasury Dashboard'

    # ==================================================================
    # Helpers
    # ==================================================================
    @api.model
    def _company_domain(self):
        return [('company_id', 'in', self.env.companies.ids)]

    @api.model
    def _agg(self, domain, model='realestate.check', field='amount'):
        """One grouped query → (count, sum). No records loaded."""
        Model = self.env[model]
        groups = Model._read_group(domain, aggregates=['__count',
                                                       '%s:sum' % field])
        if not groups:
            return 0, 0.0
        count, total = groups[0]
        return count or 0, total or 0.0

    @api.model
    def _kpi(self, key, label, domain, kind='paper', model='realestate.check',
             field='amount', note=None):
        count, amount = self._agg(domain, model=model, field=field)
        return {
            'key': key,
            'label': label,
            'count': count,
            'amount': amount,
            # `paper` = face value of instruments. `cash` = money Odoo confirms.
            # A front end must never sum the two.
            'kind': kind,
            'model': model,
            'domain': domain,
            'note': note,
        }

    # ==================================================================
    # The payload
    # ==================================================================
    @api.model
    def get_dashboard_data(self, company_ids=None):
        companies = (self.env['res.company'].browse(company_ids)
                     if company_ids else self.env.companies)
        base = [('company_id', 'in', companies.ids)]
        today = fields.Date.context_today(self)
        d7 = fields.Date.add(today, days=7)
        d30 = fields.Date.add(today, days=30)
        on_hand = base + [('state', 'in', list(CHECK_ON_HAND))]
        at_bank = base + [('state', 'in', list(CHECK_AT_BANK))]
        month_start = today.replace(day=1)

        currency = companies[:1].currency_id or self.env.company.currency_id
        return {
            'company_ids': companies.ids,
            # The browser formats amounts with this. Without it `formatMonetary`
            # has no currency to render and silently drops the symbol.
            'currency_id': currency.id,
            'currency_name': currency.name,
            'today': today,
            'currency_warning': self._currency_warning(base),
            'on_hand': [
                self._kpi('on_hand', _('Cheques On Hand'), on_hand),
                self._kpi('due_today', _('Due Today'),
                          on_hand + [('due_date', '=', today)]),
                self._kpi('due_7', _('Due in 7 Days'),
                          on_hand + [('due_date', '>=', today),
                                     ('due_date', '<=', d7)]),
                self._kpi('due_30', _('Due in 30 Days'),
                          on_hand + [('due_date', '>=', today),
                                     ('due_date', '<=', d30)]),
                self._kpi('past_due_on_hand', _('Matured, Not Presented'),
                          on_hand + [('due_date', '<', today)],
                          note=_('Matured cheques still in the safe.')),
            ],
            'deposit': [
                self._kpi('in_clearing', _('At Bank / In Clearing'), at_bank),
                # A slip is created in `draft` with today's date and its
                # cheques still `registered` in the safe: nothing has left the
                # building and Odoo has registered no payment. Counting it here
                # put the same paper under "On Hand" and under "At the Bank" at
                # once. Only a slip that was actually presented counts.
                self._kpi('deposits_today', _('Deposits Today'),
                          base + [('deposit_date', '=', today),
                                  ('state', 'not in', ('draft', 'cancelled'))],
                          model='realestate.check.deposit',
                          field='total_amount'),
                self._kpi('awaiting_reconciliation',
                          _('Awaiting Bank Reconciliation'),
                          at_bank + [('accounting_state', 'in',
                                      ('payment_registered', 'in_payment'))],
                          note=_('Payment registered; the bank has not yet '
                                 'confirmed it.')),
            ],
            'cleared': [
                self._kpi('cleared_month', _('Cleared This Month'),
                          base + [('state', '=', 'cleared'),
                                  ('cleared_date', '>=', month_start),
                                  ('cleared_date', '<=', today)],
                          kind='cash',
                          note=_('Bank-confirmed. This is money.')),
                self._kpi('cleared_total', _('Cleared To Date'),
                          base + [('state', '=', 'cleared')], kind='cash'),
            ],
            'risk': self._risk_block(companies),
            'coverage': self._coverage_block(companies),
            'charts': {
                'maturity_forecast': self.get_maturity_forecast(companies.ids),
                'by_state': self._group_chart(base, 'state'),
                'by_bank': self._group_chart(
                    base + [('state', 'in', list(CHECK_ON_HAND + CHECK_AT_BANK))],
                    'bank_id'),
                'by_project': self._group_chart(
                    base + [('state', 'in', list(CHECK_ON_HAND + CHECK_AT_BANK))],
                    'project_id'),
                'bounce_reasons': self._group_chart(
                    base, 'reason', model='realestate.check.bounce'),
            },
        }

    def _risk_block(self, companies):
        base = [('company_id', 'in', companies.ids)]
        bounced_domain = base + [('state', '=', 'bounced')]
        unresolved_domain = base + [('resolution', '=', 'pending')]
        # The ledger backlog, which neither figure above keeps in view. When
        # the bank had already matched the receipt, `_restore_receivable`
        # refuses to unwind it and leaves the work to Accounting: the cheque is
        # bounced but the customer still reads as having paid. Recording a
        # commercial resolution moves the cheque out of `bounced` and the
        # bounce out of `pending`, and the overstated receivable then had
        # nothing reporting it. Same domain as the bounce list's "Needs Manual
        # Accounting" filter.
        manual_domain = base + [('requires_manual_accounting', '=', True),
                                ('accounting_handled', '=', False)]
        outstanding_replacements = base + [
            ('replaces_check_id', '!=', False),
            ('state', 'in', list(CHECK_ON_HAND + CHECK_AT_BANK))]

        bounced, bounced_amount = self._agg(bounced_domain)
        # The rate's denominator is cheques that actually reached a bank —
        # dividing by the whole portfolio would flatter it with paper that has
        # never been presented.
        presented, _presented_amount = self._agg(
            base + [('presented_date', '!=', False)])
        unresolved, unresolved_amount = self._agg(
            unresolved_domain, model='realestate.check.bounce')
        manual, manual_amount = self._agg(
            manual_domain, model='realestate.check.bounce')
        replacements, replacement_amount = self._agg(outstanding_replacements)

        return {
            'bounced': {
                'label': _('Bounced'), 'count': bounced,
                'amount': bounced_amount, 'kind': 'paper',
                'model': 'realestate.check', 'domain': bounced_domain},
            'bounce_rate': {
                'label': _('Bounce Rate'),
                'value': (bounced / presented * 100.0) if presented else 0.0,
                'basis': _('%(bounced)s of %(presented)s presented',
                           bounced=bounced, presented=presented)},
            'unresolved_bounces': {
                'label': _('Unresolved Bounces'), 'count': unresolved,
                'amount': unresolved_amount, 'kind': 'paper',
                'model': 'realestate.check.bounce',
                'domain': unresolved_domain},
            'manual_accounting': {
                'label': _('Bounces Awaiting Manual Accounting'),
                'count': manual, 'amount': manual_amount, 'kind': 'paper',
                'model': 'realestate.check.bounce',
                'domain': manual_domain},
            'replacements_outstanding': {
                'label': _('Replacement Cheques Outstanding'),
                'count': replacements, 'amount': replacement_amount,
                'kind': 'paper', 'model': 'realestate.check',
                'domain': outstanding_replacements},
        }

    def _coverage_block(self, companies):
        """M19 — obligations versus paper. Aggregated, never iterated.

        The labels are the point. `pdc_received` is face value of instruments,
        and calling it collection would be the single most misleading thing
        this dashboard could do.

        Read with `sudo`, deliberately and narrowly. Coverage compares cheques
        against Developer's obligations, and a Treasury Officer is not
        necessarily a Developer user — without this the *entire* dashboard
        raised `AccessError` on `realestate.sale.installment` for exactly the
        people it is built for. (Found in a browser, not in review: the payload
        failed, the component fell back to its error state, and every KPI
        disappeared.)

        Company scoping is not weakened: `companies` comes from the caller's
        own `env.companies`, and the domain below pins it explicitly, so sudo
        buys access to the model and not to another company's data. Only
        aggregate amounts leave this method — no obligation is identified.

        What sudo buys the figure it must not buy the drill-down. A card that
        carries a `model` is clickable, and a Treasury Officer clicking
        "Future Obligations" got an `AccessError` on Developer's model — the
        one role this block exists for. The model and domain are therefore
        published only to a user who may read them; without them the card
        renders its figure and opens nothing, exactly as `unsecured` already
        does.
        """
        Installment = self.env['realestate.sale.installment'].sudo()
        domain = [('company_id', 'in', companies.ids),
                  ('is_cancelled', '=', False),
                  ('state', 'not in', ('paid', 'cancelled'))]
        groups = Installment._read_group(
            domain, aggregates=['current_amount:sum', 'paid_amount:sum',
                                'secured_by_checks_amount:sum', '__count'])
        if groups:
            current, paid, secured, count = groups[0]
        else:
            current = paid = secured = 0.0
            count = 0
        future = max((current or 0.0) - (paid or 0.0), 0.0)
        secured = min(secured or 0.0, future)
        may_open = self.env['realestate.sale.installment'].has_access('read')
        obligations = {
            'label': _('Future Obligations'), 'amount': future,
            'count': count, 'kind': 'obligation'}
        if may_open:
            obligations.update(model='realestate.sale.installment',
                               domain=domain)
        return {
            'future_obligations': obligations,
            'pdc_received': {
                'label': _('PDC Amount Received (Face Value)'),
                'amount': secured, 'kind': 'paper',
                'note': _('Paper held against those obligations. NOT cash '
                          'collected.')},
            'coverage_percent': {
                'label': _('PDC Coverage'),
                'value': (secured / future * 100.0) if future else 0.0},
            'unsecured': {
                'label': _('Unsecured Obligations'),
                'amount': max(future - secured, 0.0), 'kind': 'obligation'},
        }

    def _currency_warning(self, base):
        """M30 — never add EGP to SAR and call it a total.

        The dashboard sums in each cheque's own currency because that is what
        `_read_group` does on a Monetary field. If more than one currency is in
        play, the front end is told so it can refuse to present one number.
        """
        groups = self.env['realestate.check']._read_group(
            base, groupby=['currency_id'], aggregates=['__count'])
        currencies = [(c.id, c.name, n) for c, n in groups if c]
        return {
            'multi_currency': len(currencies) > 1,
            'currencies': currencies,
            'message': _(
                "Cheques in %s currencies are included. Amounts below are NOT "
                "converted — filter by currency for a meaningful total."
            ) % len(currencies) if len(currencies) > 1 else '',
        }

    def _group_chart(self, domain, groupby, model='realestate.check',
                     field='amount'):
        groups = self.env[model]._read_group(
            domain, groupby=[groupby], aggregates=['__count', '%s:sum' % field])
        labels, counts, amounts, keys = [], [], [], []
        selection = self.env[model]._fields[groupby].selection \
            if self.env[model]._fields[groupby].type == 'selection' else None
        for value, count, total in groups:
            if selection:
                label = dict(selection).get(value, value or _('None'))
                key = value
            else:
                label = value.display_name if value else _('None')
                key = value.id if value else False
            labels.append(label)
            keys.append(key)
            counts.append(count or 0)
            amounts.append(total or 0.0)
        return {'groupby': groupby, 'model': model, 'domain': domain,
                'labels': labels, 'keys': keys, 'counts': counts,
                'amounts': amounts}

    # ==================================================================
    # M21 — maturity forecast
    # ==================================================================
    @api.model
    def get_maturity_forecast(self, company_ids=None, extra_domain=None,
                              groupby=None):
        """"What PDC cash should mature next month?" — without calling it cash.

        Grouped in the database on the stored `maturity_bucket`. `groupby` adds
        a second dimension (company, project, partner, bank, currency) so the
        same query answers the operational and the analytical question.
        """
        companies = (self.env['res.company'].browse(company_ids)
                     if company_ids else self.env.companies)
        domain = [('company_id', 'in', companies.ids),
                  ('state', 'in', list(CHECK_ON_HAND))] + (extra_domain or [])
        groups = self.env['realestate.check']._read_group(
            domain, groupby=['maturity_bucket'] + ([groupby] if groupby else []),
            aggregates=['__count', 'amount:sum'])

        labels = dict(MATURITY_BUCKET)
        order = [key for key, _bound in MATURITY_BOUNDS]
        rows = {}
        for group in groups:
            if groupby:
                bucket, dimension, count, total = group
            else:
                bucket, count, total = group
                dimension = None
            entry = rows.setdefault(bucket or 'beyond',
                                    {'count': 0, 'amount': 0.0, 'split': []})
            entry['count'] += count or 0
            entry['amount'] += total or 0.0
            if groupby:
                entry['split'].append({
                    'key': dimension.id if hasattr(dimension, 'id') else dimension,
                    'label': (dimension.display_name
                              if hasattr(dimension, 'display_name')
                              else dimension or _('None')),
                    'count': count or 0,
                    'amount': total or 0.0,
                })

        return {
            'domain': domain,
            'groupby': groupby,
            'note': _("Face value of post-dated cheques maturing in each "
                      "window. This is expected paper, not guaranteed cash — "
                      "a cheque can still bounce."),
            'buckets': [{
                'key': key,
                'label': labels[key],
                'count': rows.get(key, {}).get('count', 0),
                'amount': rows.get(key, {}).get('amount', 0.0),
                'split': rows.get(key, {}).get('split', []),
                'domain': domain + [('maturity_bucket', '=', key)],
            } for key in order],
        }

    # ==================================================================
    # Drill-down (M20) — the server owns the domain
    # ==================================================================
    @api.model
    def action_drilldown(self, model, domain, title=None):
        """Open the exact records behind a KPI.

        The domain comes back from the payload the server produced, so a count
        and its drill-down are guaranteed to agree. It is re-validated against
        the allowed models rather than trusted, because a domain arriving from
        a browser is input.
        """
        allowed = {
            'realestate.check': _('Cheques'),
            'realestate.check.deposit': _('Deposits'),
            'realestate.check.bounce': _('Bounces'),
            'realestate.sale.installment': _('Obligations'),
        }
        if model not in allowed:
            raise ValueError('Unsupported drill-down model: %s' % model)
        views = ([(False, 'list'), (False, 'form')]
                 if model != 'realestate.sale.installment'
                 else [(False, 'list'), (False, 'form')])
        return {
            'type': 'ir.actions.act_window',
            'name': title or allowed[model],
            'res_model': model,
            'views': views,
            'view_mode': 'list,form',
            'domain': domain,
            'target': 'current',
        }
