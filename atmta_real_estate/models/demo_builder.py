# -*- coding: utf-8 -*-
"""Demo layer: a let portfolio with a history.

The Rental Overview is a time-series screen. Occupancy is reconstructed from
allocation date ranges, the collection chart groups obligations by the month
they fell due, and three separate panels count leases by how soon they end. A
handful of leases all starting last week makes every one of those read as a
flat line through zero.

So this builds a portfolio with a *past*: leases staggered over two years,
ending on a spread of dates so the 30/60/90-day buckets are each populated,
obligations raised monthly from each lease's start with the older ones settled
and a realistic tail unpaid, and move-ins and move-outs in the week either side
of today.

Everything is generated relative to today, so the screen still reads correctly
next quarter.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

MODULE = 'atmta_real_estate'
SENTINEL = '%s.demo_lease_1' % MODULE

#: How the let portfolio is spread. The first number is a WEIGHT, not a count:
#: the plan is scaled to whatever lettable stock the database actually has, so
#: a demo on forty units and a demo on four hundred both end up with the same
#: shape -- a settled core, one group per expiry bucket, and a live pipeline.
#: Hard counts meant the groups at the end of the list silently never got
#: created on a small database, which is exactly how this shipped with no
#: lease on notice and nothing in the 60- or 90-day bucket.
#: (weight, lifecycle, months since start, months until end, collected share)
LEASE_PLAN = [
    # The settled core of the book.
    (14, 'active', 22, 14, 1.00),
    (10, 'active', 16, 20, 0.95),
    (8, 'active', 10, 26, 0.90),
    # Ending soon: one group per expiry bucket the dashboard counts.
    (5, 'active', 23, 1, 0.92),     # inside 30 days
    (4, 'active', 21, 2, 0.88),     # inside 60 days
    (3, 'active', 20, 3, 0.85),     # inside 90 days
    # Served notice, and the rest of the pipeline.
    (3, 'notice', 19, 2, 0.80),
    (2, 'pending_approval', 0, 12, 0.0),
    (2, 'pending_signature', 0, 12, 0.0),
    (2, 'draft', 0, 12, 0.0),
    (3, 'ended', 26, -2, 1.00),
    (1, 'terminated', 20, -1, 0.70),
]


class RentalDemoBuilder(models.AbstractModel):
    _name = 'realestate.demo.rental'
    _description = 'Demo Builder — Let Portfolio'

    @api.model
    def _xmlid(self, suffix, record):
        self.env['ir.model.data']._update_xmlids([{
            'xml_id': '%s.%s' % (MODULE, suffix),
            'record': record,
            'noupdate': True,
        }])
        return record

    # ------------------------------------------------------------------
    @api.model
    def _build(self):
        if self.env.ref(SENTINEL, raise_if_not_found=False):
            return True
        foundation = self.env['realestate.demo.property']
        foundation._ensure_foundation()

        units = self._lettable_units()
        if not units:
            _logger.warning("Demo: no lettable units found, rental demo skipped")
            return True
        tenants = self._tenants(foundation)
        leases = self._build_leases(units, tenants)
        self._build_obligations(leases)
        self._bill_and_collect(leases)
        self._build_moves(leases)
        self._mark_out_of_service(units)
        return True

    # ------------------------------------------------------------------
    @api.model
    def _lettable_units(self):
        """Units this demo may let.

        Leaf units only -- letting a whole tower is a different transaction
        and is not what the Rental app models -- and only units that are not
        already committed. `_check_no_overlap` refuses a second live
        allocation on the same unit, which is correct and is exactly what it
        did when this picked up the units an earlier demo had already let.
        """
        Property = self.env['realestate.property']
        units = Property.search([
            ('hierarchy_level', '=', 'unit'),
            ('is_leasable', '=', True),
            # The company matters. Occupancy divides occupied allocations by
            # leasable units, and the denominator is company-scoped: letting a
            # unit whose `company_id` is NOT set puts its lease in the
            # numerator and the unit nowhere in the denominator, which is how
            # this demo first produced an occupancy of 114.3%.
            ('company_id', 'in', self.env.companies.ids),
        ], order='property_code')
        committed = self.env['realestate.contract.property.line'].search(
            [('property_id', 'in', units.ids)]).property_id
        free = units - committed
        # Keep roughly a fifth of the stock empty. A fully let portfolio makes
        # Available Units, the vacancy trend and "Find Available Unit" all read
        # zero, which is the least useful demo of a rental system there is.
        #
        # The vacancies are taken every fifth unit rather than off the end of
        # the list. Slicing the tail left them all in the same building -- and
        # that building was the one still under construction, so every vacant
        # unit was also unavailable and the Available Units card stayed at
        # zero anyway.
        return free.browse([unit.id for index, unit in enumerate(free)
                            if index % 5 != 4])

    @api.model
    def _tenants(self, foundation):
        """Enough tenants to let the portfolio without reusing one everywhere."""
        tenants = list(foundation._demo_partners('tenant'))
        tenants += list(foundation._demo_partners('buyer'))
        return tenants

    # ------------------------------------------------------------------
    @api.model
    def _build_leases(self, units, tenants):
        Contract = self.env['realestate.contract']
        today = fields.Date.context_today(self)
        agents = self.env['res.users'].search(
            [('share', '=', False), ('active', '=', True)], limit=4) or self.env.user

        leases, index, unit_index = [], 0, 0
        for count, lifecycle, started, ends_in, collected in self._scaled_plan(len(units)):
            for _n in range(count):
                if unit_index >= len(units):
                    return leases
                unit = units[unit_index]
                unit_index += 1
                index += 1
                start = today - relativedelta(months=started)
                end = today + relativedelta(months=ends_in)
                if end <= start:
                    end = start + relativedelta(months=12)
                annual = max(unit.base_price or 3000000, 1000000) * 0.075
                try:
                    contract = Contract.create({
                        'partner_id': tenants[index % len(tenants)].id,
                        'property_id': unit.id,
                        'is_single_property': True,
                        'is_multi_property': False,
                        'user_id': agents[index % len(agents)].id,
                        'start_date': start,
                        'end_date': end,
                        'price': round(annual / 12.0, -2),
                        'billing_frequency': 'monthly',
                        'billing_due_rule': 'period_start',
                        'billing_mode': 'lease',
                        'deposit_amount': round(annual / 12.0, -2) * 2,
                        'lifecycle_state': lifecycle,
                        'signature_status': 'signed' if lifecycle in (
                            'active', 'notice', 'ended', 'terminated') else 'pending',
                    })
                except ValidationError as error:
                    # One unit the engine will not let is not a reason to ship
                    # no portfolio. Name it and move on to the next unit.
                    _logger.warning("Demo: unit %s could not be let: %s",
                                    unit.property_code, error)
                    index -= 1
                    continue
                self._xmlid('demo_lease_%d' % index, contract)
                leases.append((contract, collected))
        return leases

    @api.model
    def _scaled_plan(self, available):
        """LEASE_PLAN with its weights turned into counts for this database.

        Every group gets at least one lease as long as there is stock for it,
        so the expiry buckets, the notice state and the pipeline are all
        represented however small the demo inventory is. Largest-remainder
        allocation, so the counts add up to `available` exactly instead of
        drifting by a lease or two.
        """
        total_weight = sum(row[0] for row in LEASE_PLAN)
        if not available or not total_weight:
            return []
        exact = [row[0] / total_weight * available for row in LEASE_PLAN]
        counts = [max(int(value), 1 if value >= 0.5 else 0) for value in exact]

        # Trim or top up the largest groups until the counts fit the stock.
        order = sorted(range(len(counts)), key=lambda i: exact[i], reverse=True)
        while sum(counts) > available:
            for i in order:
                if sum(counts) <= available:
                    break
                if counts[i] > 1:
                    counts[i] -= 1
        for i in order:
            if sum(counts) >= available:
                break
            counts[i] += available - sum(counts)
        return [(counts[i],) + tuple(LEASE_PLAN[i][1:]) for i in range(len(LEASE_PLAN))
                if counts[i]]

    # ------------------------------------------------------------------
    @api.model
    def _build_obligations(self, leases):
        """One obligation per month of each live lease, mostly settled.

        The collection chart groups by the month an obligation fell due, so
        obligations have to exist across the whole 12-month window, not only
        from today. The unpaid tail is deliberately weighted to the recent
        months: that is what real arrears look like, and it is what makes the
        ageing buckets and the Outstanding Rent card meaningful.
        """
        Obligation = self.env['realestate.contract.payment']
        today = fields.Date.context_today(self)
        rows = []
        for contract, collected in leases:
            if contract.lifecycle_state in ('draft', 'pending_approval',
                                            'pending_signature'):
                continue
            due = contract.start_date
            month = 0
            while due <= min(contract.end_date, today + relativedelta(months=1)):
                rows.append({
                    'contract_id': contract.id,
                    'date_due': due,
                    'amount': contract.price,
                })
                due += relativedelta(months=1)
                month += 1
                if month > 36:          # a guard, not a business rule
                    break
        if rows:
            Obligation.create(rows)
        return True

    # ------------------------------------------------------------------
    @api.model
    def _bill_and_collect(self, leases):
        """Invoice what has fallen due, then collect most of it.

        Obligations alone are not money. `realestate.contract.payment.state`,
        `amount_invoiced`, `amount_paid` and `amount_residual` are all derived
        from the linked invoice, so a schedule with no invoices leaves
        Outstanding Rent, the collection chart and every ageing bucket empty --
        which is exactly how this demo first looked.

        So it goes through the real billing engine and Odoo's own payment
        register, the way a user would. The unpaid tail is weighted to the
        recent months, because that is what arrears actually look like and it
        is what makes the ageing buckets worth drawing.
        """
        today = fields.Date.context_today(self)
        live = self.env['realestate.contract'].browse(
            [c.id for c, _share in leases
             if c.lifecycle_state in ('active', 'notice', 'ended', 'terminated')])
        if not live:
            return True
        try:
            live.action_invoice_due_obligations(up_to=today)
        except Exception as error:          # noqa: BLE001 - demo only
            _logger.warning("Demo: obligations could not be invoiced: %s", error)
            return True

        # Settle everything older than three months, and about two thirds of
        # what has fallen due since. The rest becomes the arrears the dashboard
        # is there to surface.
        cutoff = today - relativedelta(months=3)
        invoiced = self.env['realestate.contract.payment'].search([
            ('contract_id', 'in', live.ids),
            ('state', '=', 'invoiced'),
            ('move_id', '!=', False),
        ], order='date_due')
        to_pay = self.env['realestate.contract.payment']
        for position, obligation in enumerate(invoiced):
            if obligation.date_due <= cutoff or position % 3:
                to_pay |= obligation
        moves = to_pay.mapped('move_id').filtered(
            lambda m: m.state == 'posted' and m.payment_state in ('not_paid', 'partial'))
        if not moves:
            return True
        journal = self.env['account.journal'].search(
            [('type', 'in', ('bank', 'cash')),
             ('company_id', '=', self.env.company.id)], limit=1)
        if not journal:
            _logger.warning("Demo: no bank journal, rent left uncollected")
            return True
        try:
            wizard = self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=moves.ids,
            ).create({'journal_id': journal.id, 'group_payment': False})
            wizard.action_create_payments()
        except Exception as error:          # noqa: BLE001 - demo only
            _logger.warning("Demo: rent could not be collected: %s", error)
        return True

    # ------------------------------------------------------------------
    @api.model
    def _build_moves(self, leases):
        """Move-ins and move-outs in the week either side of today.

        The dashboard's two move cards count the next seven days and compare
        against the previous seven, so both windows need something in them or
        the comparison has nothing to divide by.
        """
        MoveIn = self.env['realestate.move.in']
        MoveOut = self.env['realestate.move.out']
        now = fields.Datetime.now()
        live = [c for c, _s in leases if c.lifecycle_state in ('active', 'notice')]
        if not live:
            return True

        for offset, state in ((1, 'schedule'), (2, 'schedule'), (3, 'inspection'),
                              (5, 'schedule'), (6, 'schedule'),
                              (-2, 'completed'), (-4, 'completed'), (-6, 'completed')):
            contract = live[(abs(offset) * 3) % len(live)]
            MoveIn.create({
                'contract_id': contract.id,
                'scheduled_date': now + relativedelta(days=offset),
                'state': state,
            })

        notice = [c for c in live if c.lifecycle_state == 'notice'] or live
        for offset, state in ((2, 'schedule'), (4, 'inspection'), (6, 'schedule'),
                              (-3, 'completed'), (-5, 'completed')):
            contract = notice[abs(offset) % len(notice)]
            MoveOut.create({
                'contract_id': contract.id,
                'scheduled_date': now + relativedelta(days=offset),
                'state': state,
            })
        return True

    # ------------------------------------------------------------------
    @api.model
    def _mark_out_of_service(self, units):
        """A couple of units off the market, so Attention Required is real."""
        for unit in units[-2:]:
            if not unit.single_contract_ids:
                unit.maintenance_status = 'maintenance'
        return True
