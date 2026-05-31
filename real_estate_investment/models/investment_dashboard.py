from odoo import api, fields, models


class InvestmentDashboard(models.AbstractModel):
    _name = 'realestate.investment.dashboard'
    _description = 'Investment Dashboard data provider'

    @api.model
    def get_data(self):
        Feasibility = self.env['realestate.investment.feasibility']
        Scenario = self.env['realestate.investment.scenario']
        Project = self.env['realestate.project']

        # ---- KPIs ----
        total_studies = Feasibility.search_count([])
        approved = Feasibility.search_count([('state', '=', 'approved')])
        positive = Feasibility.search_count([('is_positive', '=', True)])
        rejected = Feasibility.search_count([('state', '=', 'rejected')])

        studies = Feasibility.search([])
        avg_npv = round(sum(studies.mapped('npv')) / len(studies), 2) if studies else 0.0
        avg_irr = round(sum(studies.mapped('irr')) / len(studies), 2) if studies else 0.0
        avg_payback = 0.0
        with_payback = studies.filtered(lambda s: s.payback_period > 0)
        if with_payback:
            avg_payback = round(sum(with_payback.mapped('payback_period')) / len(with_payback), 2)

        total_inflow = sum(studies.mapped('total_inflow'))
        total_outflow = sum(studies.mapped('total_outflow'))

        kpis = {
            'total_studies': total_studies,
            'approved': approved,
            'positive': positive,
            'rejected': rejected,
            'positive_rate': round((positive / total_studies * 100.0), 1) if total_studies else 0.0,
            'avg_npv': avg_npv,
            'avg_irr': avg_irr,
            'avg_payback': avg_payback,
            'total_inflow': total_inflow,
            'total_outflow': total_outflow,
        }

        # ---- Feasibility states (donut) ----
        feas_states = {}
        for st in ('draft', 'approved', 'rejected', 'archived'):
            feas_states[st] = Feasibility.search_count([('state', '=', st)])

        # ---- NPV / IRR distribution per study (top 10 by NPV) ----
        ranked = studies.sorted(key=lambda s: s.npv, reverse=True)[:10]
        npv_labels = [(s.project_id.name or s.name) for s in ranked]
        npv_values = [round(s.npv, 2) for s in ranked]
        irr_values = [round(s.irr, 2) for s in ranked]

        # ---- IRR distribution histogram ----
        irr_buckets = {'<0%': 0, '0-10%': 0, '10-15%': 0, '15-20%': 0, '20%+': 0}
        for s in studies:
            r = s.irr
            if r < 0: irr_buckets['<0%'] += 1
            elif r < 10: irr_buckets['0-10%'] += 1
            elif r < 15: irr_buckets['10-15%'] += 1
            elif r < 20: irr_buckets['15-20%'] += 1
            else: irr_buckets['20%+'] += 1

        # ---- Payback distribution histogram ----
        pb_buckets = {'<2 yrs': 0, '2-3 yrs': 0, '3-5 yrs': 0, '5-7 yrs': 0, '7+ yrs': 0, 'never': 0}
        for s in studies:
            p = s.payback_period or 0
            if p == 0: pb_buckets['never'] += 1
            elif p < 2: pb_buckets['<2 yrs'] += 1
            elif p < 3: pb_buckets['2-3 yrs'] += 1
            elif p < 5: pb_buckets['3-5 yrs'] += 1
            elif p < 7: pb_buckets['5-7 yrs'] += 1
            else: pb_buckets['7+ yrs'] += 1

        # ---- Scenario sensitivity (avg NPV per scenario type) ----
        scenarios = Scenario.search([])
        sens_data = {'best': [], 'expected': [], 'worst': [], 'custom': []}
        for sc in scenarios:
            t = sc.scenario_type
            if t in sens_data:
                sens_data[t].append(sc.npv)
        sens_avg = {
            'best': round(sum(sens_data['best']) / len(sens_data['best']), 2) if sens_data['best'] else 0,
            'expected': round(sum(sens_data['expected']) / len(sens_data['expected']), 2) if sens_data['expected'] else 0,
            'worst': round(sum(sens_data['worst']) / len(sens_data['worst']), 2) if sens_data['worst'] else 0,
            'custom': round(sum(sens_data['custom']) / len(sens_data['custom']), 2) if sens_data['custom'] else 0,
        }

        # ---- Map: project locations with their study's NPV color ----
        map_projs = []
        for p in Project.search([]):
            if not (p.latitude or p.longitude): continue
            study = Feasibility.search([('project_id', '=', p.id)], limit=1)
            map_projs.append({
                'id': p.id, 'name': p.name, 'code': p.code,
                'latitude': p.latitude, 'longitude': p.longitude,
                'city': p.city,
                'npv': study.npv if study else 0,
                'irr': study.irr if study else 0,
                'has_study': bool(study),
            })

        # ---- Top studies list ----
        top_recs = studies.sorted(key=lambda s: s.npv, reverse=True)[:10]
        top_list = [{
            'id': s.id, 'name': s.name,
            'project': s.project_id.name or '',
            'npv': s.npv, 'irr': s.irr,
            'payback': s.payback_period,
            'state': s.state, 'is_positive': s.is_positive,
        } for s in top_recs]

        # ---- Bottom studies (negative NPV) ----
        bottom_recs = studies.filtered(lambda s: s.npv < 0).sorted(key=lambda s: s.npv)[:10]
        bottom_list = [{
            'id': s.id, 'name': s.name,
            'project': s.project_id.name or '',
            'npv': s.npv, 'irr': s.irr,
            'state': s.state,
        } for s in bottom_recs]

        # ---- Recent scenarios ----
        recent_scen = scenarios.sorted(key=lambda s: s.create_date or fields.Datetime.now(), reverse=True)[:10]
        scen_list = [{
            'id': s.id, 'name': s.name,
            'study': s.feasibility_id.name or '',
            'project': s.feasibility_id.project_id.name or '',
            'type': s.scenario_type,
            'npv': s.npv, 'irr': s.irr, 'is_positive': s.is_positive,
        } for s in recent_scen]

        return {
            'kpis': kpis,
            'feas_states': feas_states,
            'npv_chart': {'labels': npv_labels, 'npv': npv_values, 'irr': irr_values},
            'irr_buckets': irr_buckets,
            'pb_buckets': pb_buckets,
            'sens_avg': sens_avg,
            'map_projs': map_projs,
            'top_list': top_list,
            'bottom_list': bottom_list,
            'scen_list': scen_list,
            'currency': self.env.company.currency_id.symbol or '',
        }
