from datetime import timedelta
from odoo import api, fields, models


class HandoverDashboard(models.AbstractModel):
    _name = 'realestate.handover.dashboard'
    _description = 'Handover Dashboard data provider'

    @api.model
    def get_data(self):
        Handover = self.env['realestate.handover']
        Snagging = self.env['realestate.snagging.issue']
        Warranty = self.env['realestate.warranty']
        Property = self.env['realestate.property']

        today = fields.Date.today()
        now = fields.Datetime.now()
        month_start = today.replace(day=1)
        next_7 = today + timedelta(days=7)
        next_30 = today + timedelta(days=30)

        # ---- KPIs ----
        scheduled = Handover.search_count([('state', '=', 'scheduled')])
        in_inspection = Handover.search_count([('state', 'in', ('inspection', 'snagging'))])
        completed_mtd = Handover.search_count([
            ('state', '=', 'completed'),
            ('actual_date', '>=', fields.Datetime.to_datetime(month_start)),
        ])
        scheduled_this_week = Handover.search_count([
            ('state', '=', 'scheduled'),
            ('scheduled_date', '>=', now),
            ('scheduled_date', '<=', fields.Datetime.to_datetime(next_7)),
        ])

        open_snagging = Snagging.search_count([('state', 'in', ('open', 'assigned', 'in_progress'))])
        critical_snagging = Snagging.search_count([
            ('state', 'in', ('open', 'assigned', 'in_progress')),
            ('severity', '=', 'critical'),
        ])
        active_warranties = Warranty.search_count([('state', '=', 'active')])
        expiring_warranties = Warranty.search_count([
            ('state', '=', 'active'),
            ('end_date', '>=', today), ('end_date', '<=', next_30),
        ])

        # Readiness — avg across properties that have at least one readiness value set
        properties_with_readiness = Property.search([
            '|', '|', '|',
            ('readiness_structural', '>', 0),
            ('readiness_finishing', '>', 0),
            ('readiness_utilities', '>', 0),
            ('readiness_inspection', '>', 0),
        ])
        avg_readiness = 0.0
        if properties_with_readiness:
            avg_readiness = round(
                sum(p.readiness_overall for p in properties_with_readiness) / len(properties_with_readiness), 1)
        ready_to_deliver = Property.search_count([('ready_to_deliver', '=', True)])
        ready_to_move = Property.search_count([('ready_to_move', '=', True)])

        kpis = {
            'scheduled': scheduled,
            'scheduled_this_week': scheduled_this_week,
            'in_inspection': in_inspection,
            'completed_mtd': completed_mtd,
            'open_snagging': open_snagging,
            'critical_snagging': critical_snagging,
            'active_warranties': active_warranties,
            'expiring_warranties': expiring_warranties,
            'avg_readiness': avg_readiness,
            'ready_to_deliver': ready_to_deliver,
            'ready_to_move': ready_to_move,
        }

        # ---- Handover states (donut) ----
        ho_states = {}
        for st in ('scheduled', 'inspection', 'snagging', 'completed', 'cancelled'):
            ho_states[st] = Handover.search_count([('state', '=', st)])

        # ---- Snagging by severity (donut) ----
        sn_severity = {
            'minor': Snagging.search_count([('severity', '=', 'minor'),
                                            ('state', 'in', ('open', 'assigned', 'in_progress'))]),
            'major': Snagging.search_count([('severity', '=', 'major'),
                                            ('state', 'in', ('open', 'assigned', 'in_progress'))]),
            'critical': Snagging.search_count([('severity', '=', 'critical'),
                                               ('state', 'in', ('open', 'assigned', 'in_progress'))]),
        }

        # ---- Snagging states (donut) ----
        sn_states = {}
        for st in ('open', 'assigned', 'in_progress', 'resolved', 'verified', 'rejected'):
            sn_states[st] = Snagging.search_count([('state', '=', st)])

        # ---- Readiness distribution (histogram) ----
        readiness_buckets = {'0-25%': 0, '26-50%': 0, '51-75%': 0, '76-99%': 0, '100%': 0}
        for p in properties_with_readiness:
            r = p.readiness_overall or 0
            if r >= 100: readiness_buckets['100%'] += 1
            elif r >= 76: readiness_buckets['76-99%'] += 1
            elif r >= 51: readiness_buckets['51-75%'] += 1
            elif r >= 26: readiness_buckets['26-50%'] += 1
            else: readiness_buckets['0-25%'] += 1

        # ---- Handovers per month (6 months) ----
        from dateutil.relativedelta import relativedelta
        trend_labels = []
        trend_scheduled = []
        trend_completed = []
        for i in range(5, -1, -1):
            m_start_d = today.replace(day=1) - relativedelta(months=i)
            m_end_d = m_start_d + relativedelta(months=1) - timedelta(days=1)
            m_start_dt = fields.Datetime.to_datetime(m_start_d)
            m_end_dt = fields.Datetime.to_datetime(m_end_d) + timedelta(days=1) - timedelta(seconds=1)
            trend_labels.append(m_start_d.strftime('%b %Y'))
            trend_scheduled.append(Handover.search_count([
                ('scheduled_date', '>=', m_start_dt), ('scheduled_date', '<=', m_end_dt),
            ]))
            trend_completed.append(Handover.search_count([
                ('actual_date', '>=', m_start_dt), ('actual_date', '<=', m_end_dt),
                ('state', '=', 'completed'),
            ]))

        # ---- Map: properties with handover events ----
        ho_property_ids = list(set(Handover.search([]).mapped('property_id').ids))
        map_props = Property.search_read([
            ('id', 'in', ho_property_ids),
            '|', ('latitude', '!=', 0), ('longitude', '!=', 0),
        ], ['id', 'name', 'property_code', 'latitude', 'longitude', 'city',
            'readiness_overall', 'ready_to_deliver', 'ready_to_move'])

        # ---- Upcoming handovers ----
        upcoming = Handover.search([
            ('state', '=', 'scheduled'),
            ('scheduled_date', '>=', now),
        ], order='scheduled_date asc', limit=10)
        upcoming_list = [{
            'id': h.id, 'name': h.name,
            'property': h.property_id.display_name if h.property_id else '',
            'buyer': h.partner_id.name or '',
            'scheduled_date': h.scheduled_date.isoformat() if h.scheduled_date else None,
            'checklist_progress': h.checklist_progress,
        } for h in upcoming]

        # ---- Critical / open snagging issues ----
        snag_recs = Snagging.search([
            ('state', 'in', ('open', 'assigned', 'in_progress')),
        ], order='severity desc, reported_date desc', limit=10)
        snag_list = [{
            'id': s.id, 'name': s.name,
            'property': s.property_id.display_name if s.property_id else '',
            'description': s.description or '',
            'severity': s.severity,
            'state': s.state,
            'contractor': s.contractor_id.name or '',
            'days_open': (today - s.reported_date).days if s.reported_date else 0,
        } for s in snag_recs]

        # ---- Expiring warranties ----
        exp_recs = Warranty.search([
            ('state', '=', 'active'),
            ('end_date', '>=', today), ('end_date', '<=', next_30),
        ], order='end_date asc', limit=10)
        warranty_list = [{
            'id': w.id, 'name': w.name,
            'property': w.property_id.display_name if w.property_id else '',
            'buyer': w.partner_id.name or '',
            'end_date': w.end_date.isoformat() if w.end_date else None,
            'days_left': (w.end_date - today).days if w.end_date else 0,
        } for w in exp_recs]

        return {
            'kpis': kpis,
            'ho_states': ho_states,
            'sn_severity': sn_severity,
            'sn_states': sn_states,
            'readiness_buckets': readiness_buckets,
            'trend': {'labels': trend_labels, 'scheduled': trend_scheduled, 'completed': trend_completed},
            'map_props': map_props,
            'upcoming': upcoming_list,
            'snag_list': snag_list,
            'warranty_list': warranty_list,
        }
