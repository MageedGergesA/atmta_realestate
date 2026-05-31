from datetime import timedelta
from odoo import api, fields, models


class DeveloperDashboard(models.AbstractModel):
    _name = 'realestate.developer.dashboard'
    _description = 'Developer Dashboard data provider'

    @api.model
    def get_data(self):
        Project = self.env['realestate.project']
        Phase = self.env['realestate.phase']
        Reservation = self.env['realestate.unit.reservation']
        SaleContract = self.env['realestate.sale.contract']
        Installment = self.env['realestate.sale.installment']
        Property = self.env['realestate.property']

        today = fields.Date.today()
        month_start = today.replace(day=1)
        next_30 = today + timedelta(days=30)

        # ---- KPIs ----
        active_projects = Project.search_count([('state', 'in', ('marketing', 'handover'))])
        construction_projects = Project.search_count([('state', '=', 'construction')])
        total_units = Property.search_count([('project_id', '!=', False)])
        sold_units = Property.search_count([('project_id', '!=', False), ('is_sold', '=', True)])
        available_units = Property.search_count([('project_id', '!=', False), ('state', '=', 'available')])
        active_reservations = Reservation.search_count([('state', 'in', ('hold', 'booked'))])
        signed_contracts = SaleContract.search_count([('state', '=', 'signed')])

        # Revenue MTD: paid installments this month
        paid_mtd = sum(Installment.search([
            ('state', '=', 'paid'),
            ('date_due', '>=', month_start), ('date_due', '<=', today),
        ]).mapped('amount'))

        # Total contracted value
        total_contracted = sum(SaleContract.search([
            ('state', 'in', ('signed', 'handed_over')),
        ]).mapped('sale_price'))

        kpis = {
            'active_projects': active_projects,
            'construction_projects': construction_projects,
            'total_units': total_units,
            'sold_units': sold_units,
            'available_units': available_units,
            'sales_progress': round((sold_units / total_units * 100.0), 1) if total_units else 0.0,
            'active_reservations': active_reservations,
            'signed_contracts': signed_contracts,
            'paid_mtd': paid_mtd,
            'total_contracted': total_contracted,
        }

        # ---- Projects by state ----
        proj_states = {}
        for st in ('planning', 'construction', 'marketing', 'handover', 'completed', 'cancelled'):
            proj_states[st] = Project.search_count([('state', '=', st)])

        # ---- Units by state (within projects) ----
        unit_states = {}
        for st in ('available', 'reserved', 'rented', 'maintenance', 'inactive'):
            unit_states[st] = Property.search_count([('project_id', '!=', False), ('state', '=', st)])

        # ---- Sales velocity: signed contracts per month (6 mo) ----
        from dateutil.relativedelta import relativedelta
        velocity_labels = []
        velocity_count = []
        velocity_revenue = []
        for i in range(5, -1, -1):
            m_start = today.replace(day=1) - relativedelta(months=i)
            m_end = m_start + relativedelta(months=1) - timedelta(days=1)
            velocity_labels.append(m_start.strftime('%b %Y'))
            month_contracts = SaleContract.search([
                ('signing_date', '>=', m_start), ('signing_date', '<=', m_end),
            ])
            velocity_count.append(len(month_contracts))
            velocity_revenue.append(round(sum(month_contracts.mapped('sale_price')), 2))

        # ---- Top projects by total contract value ----
        proj_totals = {}
        for c in SaleContract.search([('state', 'in', ('signed', 'handed_over'))]):
            proj = c.project_id
            if not proj: continue
            proj_totals[proj.name] = proj_totals.get(proj.name, 0.0) + c.sale_price
        top_projects = sorted(proj_totals.items(), key=lambda x: x[1], reverse=True)[:5]

        # ---- Installment collections (next 30 days) ----
        upcoming_collections = sum(Installment.search([
            ('state', 'in', ('pending', 'invoiced')),
            ('date_due', '>=', today), ('date_due', '<=', next_30),
        ]).mapped('amount'))

        # Overdue installments
        overdue_installments = Installment.search([
            ('state', 'in', ('pending', 'invoiced')),
            ('date_due', '<', today),
        ])
        overdue_amount = sum(overdue_installments.mapped('amount'))

        # ---- Map: project locations + plot boundaries ----
        map_projs = Project.search_read(
            [('latitude', '!=', 0), ('longitude', '!=', 0)],
            ['id', 'name', 'code', 'latitude', 'longitude', 'state', 'city',
             'project_type', 'expected_budget', 'expected_revenue'])
        # Attach boundary points (ordered by sequence) for polygon rendering
        proj_ids = [p['id'] for p in map_projs]
        if proj_ids:
            points = self.env['realestate.project.boundary.point'].search_read(
                [('project_id', 'in', proj_ids)],
                ['project_id', 'sequence', 'latitude', 'longitude'],
                order='project_id, sequence',
            )
            by_proj = {}
            for pt in points:
                by_proj.setdefault(pt['project_id'][0], []).append(
                    [pt['latitude'], pt['longitude']]
                )
            for p in map_projs:
                p['boundary'] = by_proj.get(p['id'], [])
        else:
            for p in map_projs:
                p['boundary'] = []

        # ---- Expiring reservations (next 24h) ----
        soon = fields.Datetime.now() + timedelta(hours=24)
        expiring_recs = Reservation.search([
            ('state', '=', 'hold'),
            ('hold_expiry_at', '<=', soon),
        ], order='hold_expiry_at asc', limit=10)
        expiring_list = [{
            'id': r.id, 'name': r.name,
            'property': r.property_id.display_name if r.property_id else '',
            'partner': r.partner_id.name or '',
            'expiry': r.hold_expiry_at.isoformat() if r.hold_expiry_at else None,
        } for r in expiring_recs]

        # ---- Upcoming installments (next 30 days) ----
        upcoming_recs = Installment.search([
            ('state', 'in', ('pending', 'invoiced')),
            ('date_due', '>=', today), ('date_due', '<=', next_30),
        ], order='date_due asc', limit=15)
        upcoming_list = [{
            'id': i.id, 'contract': i.sale_contract_id.name or '',
            'partner': i.partner_id.name or '',
            'amount': i.amount,
            'date_due': i.date_due.isoformat() if i.date_due else None,
            'kind': i.kind,
        } for i in upcoming_recs]

        # ---- Recent signed contracts ----
        recent_contracts = SaleContract.search(
            [('state', 'in', ('signed', 'handed_over'))],
            order='signing_date desc', limit=10)
        contracts_list = [{
            'id': c.id, 'name': c.name,
            'partner': c.partner_id.name or '',
            'property': c.property_id.display_name if c.property_id else '',
            'sale_price': c.sale_price,
            'progress': c.progress,
            'state': c.state,
        } for c in recent_contracts]

        return {
            'kpis': kpis,
            'proj_states': proj_states,
            'unit_states': unit_states,
            'velocity': {
                'labels': velocity_labels,
                'count': velocity_count,
                'revenue': velocity_revenue,
            },
            'top_projects': top_projects,
            'upcoming_collections': upcoming_collections,
            'overdue_amount': overdue_amount,
            'overdue_count': len(overdue_installments),
            'map_projs': map_projs,
            'expiring_reservations': expiring_list,
            'upcoming_installments': upcoming_list,
            'recent_contracts': contracts_list,
            'currency': self.env.company.currency_id.symbol or '',
        }
