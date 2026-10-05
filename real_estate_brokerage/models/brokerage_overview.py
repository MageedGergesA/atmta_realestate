# -*- coding: utf-8 -*-
"""The Brokerage Overview payload.

Same shape, same reading order and the same components as the Rental
Overview: KPIs with their trend, then the analysis, then what is wrong, then
what this user has to do. One call, because the sparkline on a card and the
chart below it must be the same series or they will eventually disagree.

Trends are only drawn where an honest series exists. Listings created, deals
closed and commission paid are **flows** and have real monthly histories.
"Active listings right now" is a **stock** and cannot be reconstructed from a
date field, so that card carries no sparkline rather than a made-up one.
"""

import logging
from datetime import datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TREND_MONTHS = 6
LIVE_COMMISSION = [('state', 'not in', ('clawed_back', 'cancelled'))]


def _delta(series):
    if len(series) < 2 or not series[-2]:
        return None
    return (series[-1] - series[-2]) / abs(series[-2]) * 100.0


class BrokerageOverview(models.AbstractModel):
    _inherit = 'realestate.brokerage.dashboard'

    # ------------------------------------------------------------------
    @api.model
    def _can_read(self, model_name):
        """Whether this user may read a model at all.

        Defined here rather than inherited: `realestate.brokerage.dashboard`
        is a standalone AbstractModel, not an `atmta.dashboard.provider`
        subclass, so it does not get the provider's helper. A figure the
        reader cannot open is omitted rather than shown as a zero -- "not
        yours to see" and "there is nothing here" are different answers.
        """
        return model_name in self.env and self.env[model_name].has_access('read')

    # ==================================================================
    @api.model
    def get_overview(self, scope='team', filters=None):
        filters = dict(filters or {})
        today = fields.Date.context_today(self)
        company = self.env.company
        months = self._month_labels(today)

        return {
            'title': _("Brokerage"),
            'company_name': company.display_name,
            'currency_id': company.currency_id.id,
            'as_of': today.isoformat(),
            'scope': scope,
            'supports_scope': False,
            'quick_actions': self._overview_quick_actions(),
            'filters': self._overview_filters(),
            'kpis': self._overview_kpis(today, filters, months),
            'listing_status': self._listing_status(filters),
            'funnel': self._funnel(),
            'velocity': self._velocity(today, months),
            'alerts': self._attention(today),
            'work_queue': self._work_queue(today),
            'top_agents': self._top_agents(today),
            'listings_table': self._listings_table(today, filters),
        }

    # ------------------------------------------------------------------
    @api.model
    def _month_labels(self, today):
        return [(today.replace(day=1) - relativedelta(months=offset))
                for offset in range(TREND_MONTHS - 1, -1, -1)]

    @api.model
    def _monthly_counts(self, model, domain, field, months):
        """How many records fell in each month, zero-filled.

        `_read_group` returns only the months that have rows; a missing month
        is a real zero, and dropping it shifts every later point left and
        draws a trend that never happened.
        """
        if not self._can_read(model):
            return []
        start = months[0]
        groups = self.env[model]._read_group(
            domain + [(field, '>=', start)],
            groupby=['%s:month' % field], aggregates=['__count'])
        found = {}
        for bucket, count in groups:
            key = bucket.date() if hasattr(bucket, 'date') else bucket
            found['%d-%02d' % (key.year, key.month)] = count
        return [found.get('%d-%02d' % (m.year, m.month), 0) for m in months]

    @api.model
    def _monthly_sum(self, model, domain, field, measure, months):
        if not self._can_read(model):
            return []
        groups = self.env[model]._read_group(
            domain + [(field, '>=', months[0])],
            groupby=['%s:month' % field], aggregates=['%s:sum' % measure])
        found = {}
        for bucket, total in groups:
            key = bucket.date() if hasattr(bucket, 'date') else bucket
            found['%d-%02d' % (key.year, key.month)] = total or 0.0
        return [round(found.get('%d-%02d' % (m.year, m.month), 0.0), 2)
                for m in months]

    # ------------------------------------------------------------------
    @api.model
    def _overview_filters(self):
        filters = []
        if self._can_read('realestate.listing'):
            field = self.env['realestate.listing']._fields.get('deal_type')
            options = field.selection if field and not callable(
                field.selection) else []
            filters.append({
                'key': 'deal_type', 'label': _("Deal Type"),
                'icon': 'fa-handshake-o', 'all_label': _("All Deal Types"),
                'options': [{'key': k, 'label': v} for k, v in options],
            })
        agents = self.env['res.users'].search(
            [('share', '=', False), ('active', '=', True)], limit=30)
        filters.append({
            'key': 'agent_id', 'label': _("Agent"), 'icon': 'fa-user-o',
            'all_label': _("All Agents"),
            'options': [{'key': a.id, 'label': a.display_name} for a in agents],
        })
        return filters

    @api.model
    def _listing_domain(self, filters):
        domain = []
        if filters.get('deal_type'):
            domain.append(('deal_type', '=', filters['deal_type']))
        if filters.get('agent_id'):
            domain.append(('lister_agent_id', '=', int(filters['agent_id'])))
        return domain

    # ------------------------------------------------------------------
    @api.model
    def _overview_kpis(self, today, filters, months):
        Listing = self.env['realestate.listing']
        base = self._listing_domain(filters)
        month_start = today.replace(day=1)
        week_start = today - timedelta(days=today.weekday())
        kpis = []

        if self._can_read('realestate.listing'):
            kpis.append({
                'key': 'active_listings', 'label': _("Active Listings"),
                'icon': 'fa-building-o', 'tone': 'primary',
                'value': Listing.search_count(base + [('state', '=', 'active')]),
                'format': 'integer', 'higher_is_better': True,
                # A stock, not a flow: no honest monthly history exists, so
                # the card shows the figure and no invented trend.
                'hint': _("Listings currently on the market."),
                'drill': True,
            })
            sold = self._monthly_counts(
                'realestate.listing', base + [('state', '=', 'sold')],
                'sold_date', months)
            kpis.append({
                'key': 'sold_mtd', 'label': _("Sold This Month"),
                'icon': 'fa-check-circle', 'tone': 'success',
                'value': Listing.search_count(
                    base + [('state', '=', 'sold'),
                            ('sold_date', '>=', month_start),
                            ('sold_date', '<=', today)]),
                'format': 'integer', 'spark': sold,
                'delta_percent': _delta(sold),
                'comparison_label': _("vs last month"),
                'higher_is_better': True,
                'hint': _("Listings marked sold between the 1st and today."),
                'drill': True,
            })

        if self._can_read('realestate.viewing'):
            week_end = datetime.combine(week_start + timedelta(days=7), time.min)
            viewings = self._monthly_counts(
                'realestate.viewing',
                [('state', 'in', ('scheduled', 'confirmed', 'completed'))],
                'scheduled_at', months)
            kpis.append({
                'key': 'viewings_week', 'label': _("Viewings This Week"),
                'icon': 'fa-eye', 'tone': 'info',
                'value': self.env['realestate.viewing'].search_count([
                    ('scheduled_at', '>=', datetime.combine(week_start, time.min)),
                    ('scheduled_at', '<', week_end),
                    ('state', 'in', ('scheduled', 'confirmed', 'completed'))]),
                'format': 'integer', 'spark': viewings,
                'delta_percent': _delta(viewings),
                'comparison_label': _("vs last month"),
                'higher_is_better': True, 'drill': True,
            })

        if self._can_read('realestate.offer'):
            kpis.append({
                'key': 'pending_offers', 'label': _("Offers Awaiting Decision"),
                'icon': 'fa-gavel', 'tone': 'warning',
                'value': self.env['realestate.offer'].search_count(
                    [('state', 'in', ('submitted', 'countered'))]),
                'format': 'integer', 'higher_is_better': True,
                'hint': _("Submitted or countered, not yet accepted or rejected."),
                'drill': True,
            })

        if self._can_read('realestate.commission'):
            Commission = self.env['realestate.commission']
            paid = self._monthly_sum(
                'realestate.commission',
                LIVE_COMMISSION + [('paid', '=', True)],
                'payment_date', 'amount', months)
            kpis.append({
                'key': 'commission_mtd', 'label': _("Commission Paid (MTD)"),
                'icon': 'fa-money', 'tone': 'success',
                'value': sum(Commission.search(
                    LIVE_COMMISSION + [('paid', '=', True),
                                       ('payment_date', '>=', month_start),
                                       ('payment_date', '<=', today)]
                ).mapped('amount')),
                'format': 'monetary', 'spark': paid,
                'delta_percent': _delta(paid),
                'comparison_label': _("vs last month"),
                'higher_is_better': True,
                'hint': _("Commission on a vendor bill that has actually been "
                          "paid, by payment date."),
                'drill': True,
            })
            kpis.append({
                'key': 'commission_pipeline', 'label': _("Commission Pipeline"),
                'icon': 'fa-line-chart', 'tone': 'primary',
                'value': sum(Commission.search(
                    LIVE_COMMISSION
                    + [('transaction_id.state', 'in',
                        ('contract_signed', 'closed'))]).mapped('amount')),
                'format': 'monetary', 'higher_is_better': True,
                'hint': _("Commission on signed or closed deals, paid or not."),
                'drill': True,
            })
        return kpis

    # ------------------------------------------------------------------
    @api.model
    def _listing_status(self, filters):
        if not self._can_read('realestate.listing'):
            return {'segments': [], 'total': 0}
        Listing = self.env['realestate.listing']
        base = self._listing_domain(filters)
        states = [('active', _("Active"), 'success'),
                  ('under_offer', _("Under Offer"), 'warning'),
                  ('sold', _("Sold"), 'primary'),
                  ('draft', _("Draft"), 'info'),
                  ('withdrawn', _("Withdrawn"), 'neutral'),
                  ('expired', _("Expired"), 'danger')]
        segments = [{'key': 'listings_%s' % key, 'label': label, 'tone': tone,
                     'value': Listing.search_count(base + [('state', '=', key)])}
                    for key, label, tone in states]
        live = sum(s['value'] for s in segments
                   if s['key'] in ('listings_active', 'listings_under_offer'))
        return {'segments': [s for s in segments if s['value']],
                'total': live, 'total_label': _("On the Market")}

    @api.model
    def _funnel(self):
        """The CRM pipeline, stage by stage.

        Omitted entirely rather than zeroed for a user who may not read
        `crm.lead`: "not yours to see" and "there is nothing here" are
        different answers and a dashboard must not confuse them.
        """
        if not self._can_read_leads():
            return None
        stages = self.env['crm.stage'].search([], order='sequence')
        rows = []
        for stage in stages:
            count = self.env['crm.lead'].search_count([
                ('re_is_realestate', '=', True), ('type', '=', 'opportunity'),
                ('stage_id', '=', stage.id)])
            if count:
                rows.append({'key': 'stage_%d' % stage.id,
                             'label': stage.name, 'value': count,
                             'tone': 'success' if stage.is_won else 'primary'})
        return {'segments': rows, 'total': sum(r['value'] for r in rows),
                'total_label': _("Opportunities")}

    @api.model
    def _velocity(self, today, months):
        """Deals closed and commission earned, month by month."""
        if not self._can_read('realestate.transaction'):
            return None
        closed = self._monthly_counts(
            'realestate.transaction', [('state', '=', 'closed')],
            'closing_date', months)
        value = self._monthly_sum(
            'realestate.transaction', [('state', '=', 'closed')],
            'closing_date', 'sale_price', months)
        return {'labels': [m.strftime('%b') for m in months],
                'deals': closed, 'value': value}

    # ------------------------------------------------------------------
    @api.model
    def _attention(self, today):
        rows = []
        if self._can_read('realestate.offer'):
            rows.append({
                'key': 'offers_expiring', 'label': _("Offers expiring in 7 days"),
                'sublabel': _("Decide or they lapse"),
                'value': self.env['realestate.offer'].search_count([
                    ('state', 'in', ('submitted', 'countered')),
                    ('expiry_date', '>=', today),
                    ('expiry_date', '<=', today + timedelta(days=7))]),
                'severity': 'critical', 'icon': 'fa-hourglass-end',
            })
        if self._can_read('realestate.listing'):
            rows.append({
                'key': 'listings_expiring', 'label': _("Mandates expiring in 30 days"),
                'value': self.env['realestate.listing'].search_count([
                    ('state', 'in', ('active', 'under_offer')),
                    ('expiry_date', '>=', today),
                    ('expiry_date', '<=', today + timedelta(days=30))]),
                'severity': 'warning', 'icon': 'fa-file-text-o',
            })
            rows.append({
                'key': 'listings_stale', 'label': _("On the market over 90 days"),
                'sublabel': _("Review the asking price"),
                'value': self.env['realestate.listing'].search_count([
                    ('state', '=', 'active'),
                    ('list_date', '<=', today - timedelta(days=90))]),
                'severity': 'warning', 'icon': 'fa-clock-o',
            })
        if self._can_read('realestate.viewing'):
            rows.append({
                'key': 'viewings_no_outcome',
                'label': _("Completed viewings with no outcome"),
                'sublabel': _("Feedback never recorded"),
                'value': self.env['realestate.viewing'].search_count([
                    ('state', '=', 'completed'), ('outcome', '=', False)]),
                'icon': 'fa-comment-o',
            })
        if self._can_read('realestate.commission'):
            rows.append({
                'key': 'commission_unpaid', 'label': _("Commission approved, not paid"),
                'value': self.env['realestate.commission'].search_count(
                    LIVE_COMMISSION + [('state', '=', 'approved')]),
                'icon': 'fa-money',
            })
        return rows

    @api.model
    def _work_queue(self, today):
        uid = self.env.uid
        rows = []
        if self._can_read('realestate.viewing'):
            rows.append({
                'key': 'my_viewings', 'label': _("My Viewings, Next 7 Days"),
                'value': self.env['realestate.viewing'].search_count([
                    ('agent_id', '=', uid),
                    ('state', 'in', ('scheduled', 'confirmed')),
                    ('scheduled_at', '>=', datetime.combine(today, time.min)),
                    ('scheduled_at', '<', datetime.combine(
                        today + timedelta(days=7), time.min))]),
                'icon': 'fa-eye',
            })
        if self._can_read('realestate.offer'):
            rows.append({
                'key': 'my_offers', 'label': _("My Offers Awaiting Decision"),
                'value': self.env['realestate.offer'].search_count([
                    ('agent_id', '=', uid),
                    ('state', 'in', ('submitted', 'countered'))]),
                'icon': 'fa-gavel',
            })
            rows.append({
                'key': 'offers_need_approval', 'label': _("Offers Awaiting Approval"),
                'value': self.env['realestate.offer'].search_count(
                    [('approval_state', '=', 'pending')]),
                'severity': 'warning', 'icon': 'fa-check-square-o',
            })
        if self._can_read('realestate.listing'):
            rows.append({
                'key': 'my_listings', 'label': _("My Active Listings"),
                'value': self.env['realestate.listing'].search_count([
                    ('lister_agent_id', '=', uid), ('state', '=', 'active')]),
                'icon': 'fa-building-o',
            })
            rows.append({
                'key': 'listings_draft', 'label': _("Listings Not Published"),
                'value': self.env['realestate.listing'].search_count(
                    [('state', '=', 'draft')]),
                'icon': 'fa-pencil',
            })
        if self._can_read_leads():
            rows.append({
                'key': 'my_leads', 'label': _("My Open Opportunities"),
                'value': self.env['crm.lead'].search_count([
                    ('re_is_realestate', '=', True), ('user_id', '=', uid),
                    ('type', '=', 'opportunity'), ('stage_id.is_won', '=', False)]),
                'icon': 'fa-user-plus',
            })
        return rows

    # ------------------------------------------------------------------
    @api.model
    def _top_agents(self, today):
        """Who is actually closing, by commission actually paid."""
        if not self._can_read('realestate.commission'):
            return []
        since = today - relativedelta(months=12)
        commissions = self.env['realestate.commission'].search(
            LIVE_COMMISSION + [('paid', '=', True), ('payment_date', '>=', since)])
        by_partner = {}
        for commission in commissions:
            partner = commission.partner_id
            entry = by_partner.setdefault(
                partner.id, {'id': partner.id, 'agent': partner.display_name,
                             'commission': 0.0, 'deals': set()})
            entry['commission'] += commission.amount
            entry['deals'].add(commission.transaction_id.id)
        rows = sorted(by_partner.values(), key=lambda r: -r['commission'])[:8]
        for row in rows:
            row['deals'] = len(row['deals'])
        return rows

    @api.model
    def _listings_table(self, today, filters):
        """What is on the market and how long it has been there."""
        if not self._can_read('realestate.listing'):
            return []
        listings = self.env['realestate.listing'].search(
            self._listing_domain(filters)
            + [('state', 'in', ('active', 'under_offer'))],
            order='list_date asc', limit=10)
        rows = []
        for listing in listings:
            days = (today - listing.list_date).days if listing.list_date else 0
            rows.append({
                'id': listing.id,
                'listing': listing.property_id.display_name or listing.name,
                'agent': listing.lister_agent_id.name or '—',
                'price': listing.list_price,
                'days': days,
                'status': dict(listing._fields['state'].selection).get(
                    listing.state, listing.state),
                # Over ninety days on the market is where a price review
                # belongs, so the badge says so rather than leaving the
                # reader to subtract dates.
                'status_tone': 'danger' if days > 90 else (
                    'warning' if days > 45 else 'success'),
            })
        return rows

    @api.model
    def _overview_quick_actions(self):
        actions = []
        if self._can_read('realestate.listing'):
            actions.append({'key': 'new_listing', 'label': _("New Listing"),
                            'icon': 'fa-plus'})
        if self._can_read('realestate.viewing'):
            actions.append({'key': 'schedule_viewing',
                            'label': _("Schedule Viewing"), 'icon': 'fa-calendar'})
        if self._can_read_leads():
            actions.append({'key': 'new_lead', 'label': _("New Opportunity"),
                            'icon': 'fa-user-plus'})
        return actions

    # ==================================================================
    # Drill-through
    #
    # The backend owns every domain. A tile and the list it opens are built
    # from the same expression here, so the figure and the records behind it
    # cannot disagree -- which they will the moment the domain is duplicated
    # in JavaScript.
    # ==================================================================
    @api.model
    def _drill_targets(self, today):
        month_start = today.replace(day=1)
        week_start = today - timedelta(days=today.weekday())
        week_end = datetime.combine(week_start + timedelta(days=7), time.min)
        live_viewing = ('scheduled', 'confirmed', 'completed')
        live_offer = ('submitted', 'countered')
        uid = self.env.uid
        return {
            'active_listings': (_("Active Listings"), 'realestate.listing',
                                [('state', '=', 'active')]),
            'sold_mtd': (_("Sold This Month"), 'realestate.listing',
                         [('state', '=', 'sold'), ('sold_date', '>=', month_start),
                          ('sold_date', '<=', today)]),
            'viewings_week': (_("Viewings This Week"), 'realestate.viewing',
                              [('scheduled_at', '>=', datetime.combine(week_start, time.min)),
                               ('scheduled_at', '<', week_end),
                               ('state', 'in', list(live_viewing))]),
            'pending_offers': (_("Offers Awaiting Decision"), 'realestate.offer',
                               [('state', 'in', list(live_offer))]),
            'commission_mtd': (_("Commission Paid This Month"), 'realestate.commission',
                               LIVE_COMMISSION + [('paid', '=', True),
                                                  ('payment_date', '>=', month_start),
                                                  ('payment_date', '<=', today)]),
            'commission_pipeline': (_("Commission Pipeline"), 'realestate.commission',
                                    LIVE_COMMISSION
                                    + [('transaction_id.state', 'in',
                                        ('contract_signed', 'closed'))]),
            'offers_expiring': (_("Offers Expiring"), 'realestate.offer',
                                [('state', 'in', list(live_offer)),
                                 ('expiry_date', '>=', today),
                                 ('expiry_date', '<=', today + timedelta(days=7))]),
            'listings_expiring': (_("Mandates Expiring"), 'realestate.listing',
                                  [('state', 'in', ('active', 'under_offer')),
                                   ('expiry_date', '>=', today),
                                   ('expiry_date', '<=', today + timedelta(days=30))]),
            'listings_stale': (_("Listings Over 90 Days"), 'realestate.listing',
                               [('state', '=', 'active'),
                                ('list_date', '<=', today - timedelta(days=90))]),
            'viewings_no_outcome': (_("Viewings Without Feedback"), 'realestate.viewing',
                                    [('state', '=', 'completed'),
                                     ('outcome', '=', False)]),
            'commission_unpaid': (_("Commission Approved, Not Paid"),
                                  'realestate.commission',
                                  LIVE_COMMISSION + [('state', '=', 'approved')]),
            'my_viewings': (_("My Viewings"), 'realestate.viewing',
                            [('agent_id', '=', uid),
                             ('state', 'in', ('scheduled', 'confirmed')),
                             ('scheduled_at', '>=', datetime.combine(today, time.min)),
                             ('scheduled_at', '<', datetime.combine(
                                 today + timedelta(days=7), time.min))]),
            'my_offers': (_("My Offers"), 'realestate.offer',
                          [('agent_id', '=', uid), ('state', 'in', list(live_offer))]),
            'offers_need_approval': (_("Offers Awaiting Approval"), 'realestate.offer',
                                     [('approval_state', '=', 'pending')]),
            'my_listings': (_("My Active Listings"), 'realestate.listing',
                            [('lister_agent_id', '=', uid), ('state', '=', 'active')]),
            'listings_draft': (_("Unpublished Listings"), 'realestate.listing',
                               [('state', '=', 'draft')]),
            'my_leads': (_("My Open Opportunities"), 'crm.lead',
                         [('re_is_realestate', '=', True), ('user_id', '=', uid),
                          ('type', '=', 'opportunity'), ('stage_id.is_won', '=', False)]),
        }

    @api.model
    def action_drill(self, key, scope='team'):
        today = fields.Date.context_today(self)
        target = self._drill_targets(today).get(key)
        if not target and key.startswith('listings_'):
            state = key[len('listings_'):]
            target = (_("Listings"), 'realestate.listing', [('state', '=', state)])
        if not target and key.startswith('stage_'):
            target = (_("Opportunities"), 'crm.lead',
                      [('re_is_realestate', '=', True), ('type', '=', 'opportunity'),
                       ('stage_id', '=', int(key[len('stage_'):]))])
        if not target:
            raise UserError(_("Unknown dashboard figure '%s'.", key))
        name, model, domain = target
        if not self._can_read(model):
            raise UserError(_("These records are not available to you."))
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': model,
            'views': [[False, 'list'], [False, 'form']],
            'view_mode': 'list,form',
            'domain': domain,
            'target': 'current',
        }

    @api.model
    def action_quick(self, key):
        models_by_key = {
            'new_listing': 'realestate.listing',
            'schedule_viewing': 'realestate.viewing',
            'new_lead': 'crm.lead',
        }
        model = models_by_key.get(key)
        if not model or not self._can_read(model):
            raise UserError(_("Unknown dashboard action '%s'.", key))
        return {
            'type': 'ir.actions.act_window',
            'name': _("New"),
            'res_model': model,
            'views': [[False, 'form']],
            'view_mode': 'form',
            'target': 'current',
        }
