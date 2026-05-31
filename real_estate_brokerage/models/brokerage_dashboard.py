from datetime import timedelta
from odoo import api, fields, models


class BrokerageDashboard(models.AbstractModel):
    _name = 'realestate.brokerage.dashboard'
    _description = 'Brokerage Dashboard data provider'

    @api.model
    def get_data(self):
        Listing = self.env['realestate.listing']
        Lead = self.env['realestate.lead']
        Viewing = self.env['realestate.viewing']
        Offer = self.env['realestate.offer']
        Transaction = self.env['realestate.transaction']
        Commission = self.env['realestate.commission']
        Property = self.env['realestate.property']

        today = fields.Date.today()
        month_start = today.replace(day=1)
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        next_30 = today + timedelta(days=30)

        # ---- KPIs ----
        active_listings = Listing.search_count([('state', '=', 'active')])
        under_offer = Listing.search_count([('state', '=', 'under_offer')])
        sold_mtd = Listing.search_count([
            ('state', '=', 'sold'), ('sold_date', '>=', month_start),
        ])
        open_leads = Lead.search_count([('state', 'not in', ('converted', 'lost'))])
        hot_leads = Lead.search_count([
            ('state', 'not in', ('converted', 'lost')),
            ('priority', 'in', ('3', '4', '5')),
        ])
        viewings_this_week = Viewing.search_count([
            ('scheduled_at', '>=', week_start),
            ('scheduled_at', '<=', week_end),
            ('state', 'in', ('scheduled', 'completed')),
        ])
        pending_offers = Offer.search_count([('state', 'in', ('submitted', 'countered'))])

        # Conversion: closed leads / total touched
        total_leads = Lead.search_count([])
        converted_leads = Lead.search_count([('state', '=', 'converted')])
        conversion_rate = round((converted_leads / total_leads * 100.0), 1) if total_leads else 0.0

        # Revenue (commissions paid)
        commissions_mtd = sum(Commission.search([
            ('payment_date', '>=', month_start), ('paid', '=', True),
        ]).mapped('amount'))
        total_commission_pipeline = sum(Commission.search([
            ('transaction_id.state', 'in', ('contract_signed', 'closed')),
        ]).mapped('amount'))

        kpis = {
            'active_listings': active_listings,
            'under_offer': under_offer,
            'sold_mtd': sold_mtd,
            'open_leads': open_leads,
            'hot_leads': hot_leads,
            'viewings_this_week': viewings_this_week,
            'pending_offers': pending_offers,
            'conversion_rate': conversion_rate,
            'commissions_mtd': commissions_mtd,
            'total_pipeline': total_commission_pipeline,
        }

        # ---- Listing states (donut) ----
        listing_states = {}
        for st in ('draft', 'active', 'under_offer', 'sold', 'withdrawn', 'expired'):
            listing_states[st] = Listing.search_count([('state', '=', st)])

        # ---- Lead funnel (bar) ----
        lead_funnel = {}
        for st in ('new', 'qualified', 'matched', 'viewing_scheduled', 'offer', 'converted'):
            lead_funnel[st] = Lead.search_count([('state', '=', st)])

        # ---- Days on market histogram ----
        active_with_dom = Listing.search_read([('state', '=', 'active')], ['days_on_market'])
        dom_buckets = {'0-30': 0, '31-60': 0, '61-90': 0, '90+': 0}
        for r in active_with_dom:
            d = r.get('days_on_market') or 0
            if d <= 30: dom_buckets['0-30'] += 1
            elif d <= 60: dom_buckets['31-60'] += 1
            elif d <= 90: dom_buckets['61-90'] += 1
            else: dom_buckets['90+'] += 1

        # ---- Top recipients (commission earned, paid only) ----
        recipient_totals = {}
        for c in Commission.search([('paid', '=', True)]):
            recipient = c.partner_id
            if not recipient:
                continue
            recipient_totals[recipient.name] = recipient_totals.get(recipient.name, 0.0) + c.amount
        top_agents = sorted(recipient_totals.items(), key=lambda x: x[1], reverse=True)[:5]

        # ---- Sales velocity: closed transactions per month, last 6 months ----
        from dateutil.relativedelta import relativedelta
        velocity_labels = []
        velocity_count = []
        velocity_revenue = []
        for i in range(5, -1, -1):
            m_start = today.replace(day=1) - relativedelta(months=i)
            m_end = m_start + relativedelta(months=1) - timedelta(days=1)
            velocity_labels.append(m_start.strftime('%b %Y'))
            txns = Transaction.search([
                ('state', '=', 'closed'),
                ('closing_date', '>=', m_start), ('closing_date', '<=', m_end),
            ])
            velocity_count.append(len(txns))
            velocity_revenue.append(round(sum(txns.mapped('sale_price')), 2))

        # ---- Map: active + under_offer listings only ----
        active_listings_recs = Listing.search([('state', 'in', ('active', 'under_offer'))])
        prop_ids = active_listings_recs.mapped('property_id').ids
        map_props = Property.search_read(
            [('id', 'in', prop_ids),
             '|', ('latitude', '!=', 0), ('longitude', '!=', 0)],
            ['id', 'name', 'property_code', 'latitude', 'longitude', 'city'],
        )
        # Attach the listing state to each property dict
        prop_to_listing_state = {}
        for l in active_listings_recs:
            if l.property_id:
                prop_to_listing_state[l.property_id.id] = l.state
        for p in map_props:
            p['listing_state'] = prop_to_listing_state.get(p['id'], 'active')

        # ---- Recent leads list ----
        recent_leads = Lead.search([], order='create_date desc', limit=10)
        recent_leads_list = [{
            'id': l.id, 'name': l.name,
            'partner_name': l.partner_id.name or '',
            'source': l.source or '',
            'state': l.state,
            'agent': l.agent_id.name or '',
            'priority': l.priority,
            'create_date': l.create_date.isoformat() if l.create_date else None,
        } for l in recent_leads]

        # ---- Upcoming viewings ----
        upcoming = Viewing.search([
            ('scheduled_at', '>=', today),
            ('state', '=', 'scheduled'),
        ], order='scheduled_at asc', limit=10)
        viewings_list = [{
            'id': v.id, 'name': v.name,
            'listing': v.listing_id.name or '',
            'property': v.property_id.display_name if v.property_id else '',
            'partner': v.partner_id.name or '',
            'agent': v.agent_id.name or '',
            'scheduled_at': v.scheduled_at.isoformat() if v.scheduled_at else None,
        } for v in upcoming]

        # ---- Pending offers (recent) ----
        offers_list_recs = Offer.search([
            ('state', 'in', ('submitted', 'countered')),
        ], order='offer_date desc', limit=10)
        offers_list = [{
            'id': o.id, 'name': o.name,
            'listing': o.listing_id.name or '',
            'buyer': o.partner_id.name or '',
            'amount': o.amount,
            'offer_date': o.offer_date.isoformat() if o.offer_date else None,
            'expiry_date': o.expiry_date.isoformat() if o.expiry_date else None,
        } for o in offers_list_recs]

        return {
            'kpis': kpis,
            'listing_states': listing_states,
            'lead_funnel': lead_funnel,
            'dom_buckets': dom_buckets,
            'top_agents': top_agents,
            'velocity': {
                'labels': velocity_labels,
                'count': velocity_count,
                'revenue': velocity_revenue,
            },
            'map_props': map_props,
            'recent_leads': recent_leads_list,
            'upcoming_viewings': viewings_list,
            'pending_offers': offers_list,
            'currency': self.env.company.currency_id.symbol or '',
        }
