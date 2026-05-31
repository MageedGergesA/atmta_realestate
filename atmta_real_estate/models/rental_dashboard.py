from datetime import timedelta

from odoo import api, fields, models


class RentalDashboard(models.AbstractModel):
    _name = 'realestate.rental.dashboard'
    _description = 'Rental Dashboard data provider'

    @api.model
    def get_data(self):
        Contract = self.env['realestate.contract']
        Payment = self.env['realestate.contract.payment']
        Property = self.env['realestate.property']
        Maintenance = self.env['realestate.maintenance.request']

        today = fields.Date.today()
        month_start = today.replace(day=1)
        next_30 = today + timedelta(days=30)
        next_60 = today + timedelta(days=60)
        next_90 = today + timedelta(days=90)

        # ---- Module is standalone — every property is rental-relevant ----
        rental_props_set = Property.search([])
        rental_prop_ids = rental_props_set.ids
        prop_filter = []  # no extra filter

        # ---- KPI ----
        all_props = len(rental_prop_ids)
        rented_props = Property.search_count([('state', '=', 'rented')])
        available_props = Property.search_count([('state', '=', 'available')])
        maintenance_props = Property.search_count([('state', '=', 'maintenance')])

        active_contracts = Contract.search_count([('state', 'in', ('confirmed', 'invoiced', 'active'))])
        draft_contracts = Contract.search_count([('state', '=', 'draft')])

        # "Collected" = the payment's invoice is actually reconciled
        # (state is computed from the move's payment_state).
        paid_domain = [('state', '=', 'paid')]

        paid_payments = Payment.search(paid_domain + [
            ('date_due', '>=', month_start),
            ('date_due', '<=', today),
        ])
        revenue_collected_mtd = sum(paid_payments.mapped('amount'))

        expected_mtd = Payment.search([
            ('date_due', '>=', month_start),
            ('date_due', '<=', today),
        ])
        revenue_expected_mtd = sum(expected_mtd.mapped('amount'))

        overdue_payments = Payment.search([
            ('date_due', '<', today),
            ('state', 'not in', ('paid', 'cancelled')),
        ])
        overdue_amount = sum(overdue_payments.mapped('amount'))
        overdue_count = len(overdue_payments)

        kpis = {
            'occupancy_rate': round((rented_props / all_props * 100.0), 1) if all_props else 0.0,
            'rented_count': rented_props,
            'available_count': available_props,
            'maintenance_count': maintenance_props,
            'total_properties': all_props,
            'active_contracts': active_contracts,
            'draft_contracts': draft_contracts,
            'revenue_collected_mtd': revenue_collected_mtd,
            'revenue_expected_mtd': revenue_expected_mtd,
            'collection_rate': round((revenue_collected_mtd / revenue_expected_mtd * 100.0), 1) if revenue_expected_mtd else 0.0,
            'overdue_amount': overdue_amount,
            'overdue_count': overdue_count,
        }

        # ---- Property state distribution (donut) ----
        prop_state_data = {
            'available': available_props,
            'reserved': Property.search_count([('state', '=', 'reserved')]),
            'rented': rented_props,
            'maintenance': maintenance_props,
            'inactive': Property.search_count([('state', '=', 'inactive')]),
        }

        # ---- Contract state distribution (donut) ----
        contract_state_data = {}
        for st in ('draft', 'confirmed', 'invoiced', 'active', 'expired', 'terminated'):
            contract_state_data[st] = Contract.search_count([('state', '=', st)])

        # ---- Revenue trend (last 6 months) ----
        revenue_trend_labels = []
        revenue_trend_collected = []
        revenue_trend_expected = []
        from dateutil.relativedelta import relativedelta
        for i in range(5, -1, -1):
            m_start = (today.replace(day=1) - relativedelta(months=i))
            m_end = (m_start + relativedelta(months=1)) - timedelta(days=1)
            revenue_trend_labels.append(m_start.strftime('%b %Y'))
            month_payments = Payment.search([('date_due', '>=', m_start), ('date_due', '<=', m_end)])
            collected = sum(p.amount for p in month_payments if p.state == 'paid')
            expected = sum(month_payments.mapped('amount'))
            revenue_trend_collected.append(round(collected, 2))
            revenue_trend_expected.append(round(expected, 2))

        # ---- Top tenants by total contracted value (any payment state) ----
        tenant_data = {}
        for p in Payment.search([]):
            partner = p.contract_id.partner_id
            if not partner:
                continue
            tenant_data[partner.name] = tenant_data.get(partner.name, 0.0) + p.amount
        top_tenants = sorted(tenant_data.items(), key=lambda x: x[1], reverse=True)[:5]

        # ---- Properties by city — rental-scoped ----
        properties_by_city = {}
        for prop in rental_props_set:
            city = prop.city or 'Unspecified'
            properties_by_city[city] = properties_by_city.get(city, 0) + 1
        properties_by_city_sorted = sorted(properties_by_city.items(), key=lambda x: x[1], reverse=True)[:10]

        # ---- Map data ----
        map_props = Property.search_read(
            ['|', ('latitude', '!=', 0), ('longitude', '!=', 0)],
            ['id', 'name', 'property_code', 'latitude', 'longitude', 'state', 'city', 'rental_status'],
        )

        # ---- Hierarchy — include any top-level property that has rental descendants ----
        # Roots whose subtree contains at least one rental-scoped unit.
        rental_root_ids = set()
        for p in rental_props_set:
            if p.parent_path:
                rental_root_ids.add(int(p.parent_path.split('/')[0]))
            else:
                rental_root_ids.add(p.id)
        roots = Property.browse(list(rental_root_ids))
        hierarchy = []
        for root in roots:
            # Only count descendants that are rental-scoped
            children = root.child_ids.filtered(lambda c: c.id in rental_prop_ids) if root.child_ids else root.child_ids
            hierarchy.append({
                'id': root.id,
                'name': root.name,
                'property_code': root.property_code,
                'state': root.state,
                'rental_status': root.rental_status if 'rental_status' in root._fields else 'not_rented',
                'hierarchy_level': root.hierarchy_level,
                'child_count': len(children),
                'occupied_count': len(children.filtered(lambda c: c.state == 'rented')),
                'available_count': len(children.filtered(lambda c: c.state == 'available')),
                'city': root.city,
            })

        # ---- Upcoming expirations ----
        expiring_30 = Contract.search([
            ('state', 'in', ('active', 'invoiced', 'confirmed')),
            ('end_date', '>=', today),
            ('end_date', '<=', next_30),
        ], order='end_date asc', limit=20)
        expiring_60 = Contract.search([
            ('state', 'in', ('active', 'invoiced', 'confirmed')),
            ('end_date', '>=', today),
            ('end_date', '<=', next_60),
        ])
        expiring_90 = Contract.search([
            ('state', 'in', ('active', 'invoiced', 'confirmed')),
            ('end_date', '>=', today),
            ('end_date', '<=', next_90),
        ])
        expirations = [{
            'id': c.id,
            'name': c.name,
            'partner_name': c.partner_id.name or '',
            'end_date': c.end_date.isoformat() if c.end_date else None,
            'days_left': (c.end_date - today).days if c.end_date else 0,
        } for c in expiring_30]

        # ---- Overdue payments list ----
        overdue_list = [{
            'id': p.id,
            'name': p.name,
            'contract_name': p.contract_id.name or '',
            'partner_name': p.contract_id.partner_id.name or '',
            'date_due': p.date_due.isoformat() if p.date_due else None,
            'days_overdue': (today - p.date_due).days if p.date_due else 0,
            'amount': p.amount,
        } for p in overdue_payments.sorted('date_due')[:20]]

        # ---- Open maintenance ----
        open_maintenance = Maintenance.search([
            ('state', 'in', ('draft', 'scheduled', 'in_progress')),
        ], order='request_date desc', limit=20)
        maintenance_list = [{
            'id': m.id,
            'name': m.name,
            'property_name': m.property_id.display_name if m.property_id else '',
            'request_date': m.request_date.isoformat() if m.request_date else None,
            'scheduled_date': m.scheduled_date.isoformat() if m.scheduled_date else None,
            'state': m.state,
        } for m in open_maintenance]

        return {
            'kpis': kpis,
            'property_states': prop_state_data,
            'contract_states': contract_state_data,
            'revenue_trend': {
                'labels': revenue_trend_labels,
                'collected': revenue_trend_collected,
                'expected': revenue_trend_expected,
            },
            'top_tenants': top_tenants,
            'properties_by_city': properties_by_city_sorted,
            'map_props': map_props,
            'hierarchy': hierarchy,
            'expirations': {
                'list': expirations,
                'count_30': len(expirations),
                'count_60': len(expiring_60),
                'count_90': len(expiring_90),
            },
            'overdue': overdue_list,
            'maintenance': maintenance_list,
            'currency': self.env.company.currency_id.symbol or '',
            'company_id': self.env.company.id,
        }
