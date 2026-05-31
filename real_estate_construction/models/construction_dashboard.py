from datetime import timedelta
from odoo import api, fields, models


class ConstructionDashboard(models.AbstractModel):
    _name = 'realestate.construction.dashboard'
    _description = 'Construction Dashboard data provider'

    @api.model
    def get_data(self):
        Milestone = self.env['realestate.construction.milestone']
        CostLine = self.env['realestate.construction.cost.line']
        Contractor = self.env['realestate.contractor']
        Project = self.env['realestate.project']

        today = fields.Date.today()
        month_start = today.replace(day=1)

        # ---- KPIs ----
        total_milestones = Milestone.search_count([])
        not_started = Milestone.search_count([('state', '=', 'not_started')])
        in_progress = Milestone.search_count([('state', '=', 'in_progress')])
        completed = Milestone.search_count([('state', '=', 'completed')])
        delayed = Milestone.search_count([('state', '=', 'delayed')])

        avg_completion = 0.0
        all_active = Milestone.search([('state', 'in', ('not_started', 'in_progress', 'delayed'))])
        if all_active:
            avg_completion = round(sum(all_active.mapped('completion_percentage')) / len(all_active), 1)

        # Cost vs budget
        total_budget = sum(Milestone.search([]).mapped('budget_amount'))
        total_actual = sum(CostLine.search([]).mapped('amount'))
        cost_variance = total_actual - total_budget
        cost_mtd = sum(CostLine.search([
            ('date', '>=', month_start), ('date', '<=', today),
        ]).mapped('amount'))

        active_contractors = Contractor.search_count([('active', '=', True)])

        kpis = {
            'total_milestones': total_milestones,
            'in_progress': in_progress,
            'completed': completed,
            'delayed': delayed,
            'on_schedule_pct': round(((total_milestones - delayed) / total_milestones * 100.0), 1) if total_milestones else 0.0,
            'avg_completion': avg_completion,
            'total_budget': total_budget,
            'total_actual': total_actual,
            'cost_variance': cost_variance,
            'budget_used_pct': round((total_actual / total_budget * 100.0), 1) if total_budget else 0.0,
            'cost_mtd': cost_mtd,
            'active_contractors': active_contractors,
        }

        # ---- Milestone states (donut) ----
        ms_states = {}
        for st in ('not_started', 'in_progress', 'completed', 'delayed', 'cancelled'):
            ms_states[st] = Milestone.search_count([('state', '=', st)])

        # ---- Cost vs Budget per project (bar) ----
        proj_budget_actual = {}
        for ms in Milestone.search([]):
            proj = ms.project_id
            if not proj: continue
            d = proj_budget_actual.setdefault(proj.name, {'budget': 0.0, 'actual': 0.0})
            d['budget'] += ms.budget_amount
            d['actual'] += ms.actual_cost
        # Top 5 by budget
        proj_sorted = sorted(proj_budget_actual.items(), key=lambda x: x[1]['budget'], reverse=True)[:8]
        cost_proj_labels = [n for n, _ in proj_sorted]
        cost_proj_budget = [d['budget'] for _, d in proj_sorted]
        cost_proj_actual = [d['actual'] for _, d in proj_sorted]

        # ---- Contractor workload ----
        contractor_load = {}
        for c in Contractor.search([('active', '=', True)]):
            active_ms = c.milestone_ids.filtered(lambda m: m.state in ('not_started', 'in_progress', 'delayed'))
            if active_ms:
                contractor_load[c.name] = len(active_ms)
        top_contractors = sorted(contractor_load.items(), key=lambda x: x[1], reverse=True)[:6]

        # ---- Spend trend last 6 months ----
        from dateutil.relativedelta import relativedelta
        trend_labels = []
        trend_amount = []
        for i in range(5, -1, -1):
            m_start = today.replace(day=1) - relativedelta(months=i)
            m_end = m_start + relativedelta(months=1) - timedelta(days=1)
            trend_labels.append(m_start.strftime('%b %Y'))
            month_total = sum(CostLine.search([
                ('date', '>=', m_start), ('date', '<=', m_end),
            ]).mapped('amount'))
            trend_amount.append(round(month_total, 2))

        # ---- Map: projects with construction milestones ----
        proj_with_ms = Project.search([('milestone_ids', '!=', False)])
        map_projs = []
        for p in proj_with_ms:
            if not (p.latitude or p.longitude): continue
            map_projs.append({
                'id': p.id, 'name': p.name, 'code': p.code,
                'latitude': p.latitude, 'longitude': p.longitude,
                'city': p.city, 'state': p.state,
                'construction_progress': p.construction_progress,
            })

        # ---- Delayed milestones list ----
        delayed_recs = Milestone.search([('state', '=', 'delayed')],
                                         order='expected_end_date asc', limit=10)
        delayed_list = [{
            'id': m.id, 'name': m.name,
            'project': m.project_id.name or '',
            'contractor': m.contractor_id.name or '',
            'completion': m.completion_percentage,
            'expected_end': m.expected_end_date.isoformat() if m.expected_end_date else None,
            'days_late': (today - m.expected_end_date).days if m.expected_end_date else 0,
        } for m in delayed_recs]

        # ---- Top cost overruns ----
        overruns = []
        for m in Milestone.search([]):
            if m.budget_amount and m.actual_cost > m.budget_amount:
                overruns.append({
                    'id': m.id, 'name': m.name,
                    'project': m.project_id.name or '',
                    'budget': m.budget_amount, 'actual': m.actual_cost,
                    'variance': m.cost_variance,
                })
        overruns.sort(key=lambda x: x['variance'], reverse=True)
        overruns_list = overruns[:10]

        # ---- Recent cost lines ----
        recent_costs = CostLine.search([], order='date desc', limit=15)
        recent_costs_list = [{
            'id': c.id, 'name': c.name,
            'project': c.project_id.name or '',
            'milestone': c.milestone_id.name or '',
            'contractor': c.contractor_id.name or '',
            'amount': c.amount, 'date': c.date.isoformat() if c.date else None,
            'category': c.category or '',
        } for c in recent_costs]

        return {
            'kpis': kpis,
            'ms_states': ms_states,
            'cost_proj': {
                'labels': cost_proj_labels,
                'budget': cost_proj_budget,
                'actual': cost_proj_actual,
            },
            'top_contractors': top_contractors,
            'spend_trend': {'labels': trend_labels, 'amounts': trend_amount},
            'map_projs': map_projs,
            'delayed_list': delayed_list,
            'overruns_list': overruns_list,
            'recent_costs': recent_costs_list,
            'currency': self.env.company.currency_id.symbol or '',
        }
