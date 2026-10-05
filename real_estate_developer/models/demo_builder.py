# -*- coding: utf-8 -*-
"""Demo layer: one development, sold.

Builds on the property foundation in ``atmta_property_core`` and carries the
flagship project all the way through the commercial lifecycle, because a
dashboard whose every tile reads zero teaches nobody what the suite does:

* a project and two phases, commercially open;
* the forty units linked to them;
* a release batch, without which the availability engine refuses every sale
  (and refusing is correct -- unreleased inventory is not for sale);
* two published payment plans;
* reservations and sale contracts spread across the real workflow states, from
  draft through signed and active, with their instalment schedules raised by
  the engine rather than written by hand.

Instalments are deliberately NOT created directly. ``action_sign`` expands the
payment plan, and letting it do so is the only way the demo data proves the
engine still works -- hand-written instalment rows would keep looking right
long after the generator broke.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MODULE = 'real_estate_developer'
SENTINEL = '%s.demo_tmr_project' % MODULE


class DeveloperDemoBuilder(models.AbstractModel):
    _name = 'realestate.demo.developer'
    _description = 'Demo Builder — Development and Sales'

    @api.model
    def _xmlid(self, suffix, record):
        self.env['ir.model.data']._update_xmlids([{
            'xml_id': '%s.%s' % (MODULE, suffix),
            'record': record,
            'noupdate': True,
        }])
        return record

    @api.model
    def _ref(self, suffix):
        return self.env.ref('%s.%s' % (MODULE, suffix), raise_if_not_found=False)

    # ------------------------------------------------------------------
    @api.model
    def _build(self):
        if self.env.ref(SENTINEL, raise_if_not_found=False):
            return True
        foundation = self.env['realestate.demo.property']
        foundation._ensure_foundation()

        project, phase1, phase2 = self._build_project()
        units = self._link_units(project, phase1, phase2)
        self._release(project, phase1, units)
        plans = self._build_plans(project)
        self._build_deals(project, plans, foundation)
        self._bill_and_collect()
        return True

    # ------------------------------------------------------------------
    @api.model
    def _build_project(self):
        Project = self.env['realestate.project']
        country = self.env.ref('base.eg', raise_if_not_found=False)
        today = fields.Date.context_today(self)

        project = self._xmlid('demo_tmr_project', Project.create({
            'name': 'Teklines Marina Residences',
            'code': 'TMR',
            'project_type': 'mixed',
            # Physically under construction, commercially selling. The two are
            # orthogonal and this project demonstrates exactly that.
            'state': 'construction',
            'commercial_state': 'selling',
            'country_id': country.id if country else False,
            'city': 'Alexandria',
            'district': 'North Coast — Sidi Abdel Rahman',
            'address_line': 'Km 132 Alexandria–Marsa Matrouh Road',
            'latitude': 30.9612,
            'longitude': 28.7644,
            'start_date': today - relativedelta(months=20),
            'expected_completion_date': today + relativedelta(months=15),
            'expected_handover_date': today + relativedelta(months=18),
            'commercial_launch_date': today - relativedelta(months=18),
            'sales_opening_date': today - relativedelta(months=18),
            'total_land_area': 48000,
            'total_built_up_area': 92000,
            'expected_budget': 1250000000,
            'expected_revenue': 1890000000,
            'description': '<p>Flagship mixed-use seafront development: two '
                           'residential towers, a retail podium and a marina '
                           'promenade.</p>',
        }))

        Phase = self.env['realestate.phase']
        phase1 = self._xmlid('demo_tmr_phase_1', Phase.create({
            'name': 'Phase 1 — Seafront Towers',
            'code': 'TMR-P1',
            'project_id': project.id,
            'state': 'construction',
            'commercial_state': 'selling',
            'start_date': today - relativedelta(months=20),
            'expected_completion_date': today + relativedelta(months=9),
            'expected_delivery_date': today + relativedelta(months=12),
            'sales_launch_date': today - relativedelta(months=18),
            'hold_duration_hours': 72,
            'booking_fee': 50000,
            'target_sales_value': 980000000,
        }))
        phase2 = self._xmlid('demo_tmr_phase_2', Phase.create({
            'name': 'Phase 2 — Marina Promenade',
            'code': 'TMR-P2',
            'project_id': project.id,
            'state': 'planning',
            'commercial_state': 'pre_launch',
            'start_date': today + relativedelta(months=2),
            'expected_completion_date': today + relativedelta(months=24),
            'expected_delivery_date': today + relativedelta(months=27),
            'hold_duration_hours': 48,
            'booking_fee': 75000,
            'target_sales_value': 910000000,
        }))
        return project, phase1, phase2

    # ------------------------------------------------------------------
    @api.model
    def _link_units(self, project, phase1, phase2):
        """Attach the whole TMR hierarchy to the project.

        Demo units that belong to no project are why the inventory, pricing
        and availability screens all read empty: every one of them filters by
        project.
        """
        props = self.env['realestate.property'].search(
            [('property_code', '=like', 'TMR-%')])
        # The retail podium is Phase 2; the towers are Phase 1.
        podium_ids = props.filtered(
            lambda p: p.property_code.startswith('TMR-RP'))
        (props - podium_ids).write({'project_id': project.id,
                                    'phase_id': phase1.id})
        podium_ids.write({'project_id': project.id, 'phase_id': phase2.id})
        return props.filtered(lambda p: p.hierarchy_level == 'unit')

    # ------------------------------------------------------------------
    @api.model
    def _release(self, project, phase1, units):
        """Put Phase 1 inventory on the market.

        Without a live release batch ``_check_available_for_sale`` refuses
        every reservation and every signature with `not_released`, which is
        the correct behaviour and the reason this record has to exist.
        """
        today = fields.Date.context_today(self)
        sellable = units.filtered(
            lambda p: not p.property_code.startswith('TMR-RP'))
        batch = self.env['realestate.unit.release.batch'].create({
            'name': 'TMR Phase 1 — Launch Release',
            'project_id': project.id,
            'phase_id': phase1.id,
            'property_ids': [(6, 0, sellable.ids)],
            'state': 'released',
            'release_date': today - relativedelta(months=18),
            'valid_from': today - relativedelta(months=18),
            'notes': '<p>Full Phase 1 inventory released at commercial '
                     'launch.</p>',
        })
        return self._xmlid('demo_tmr_release_batch', batch)

    # ------------------------------------------------------------------
    @api.model
    def _build_plans(self, project):
        Plan = self.env['realestate.payment.plan']
        today = fields.Date.context_today(self)
        common = {
            'project_id': project.id,
            'state': 'active',
            'version': 1,
            'valid_from': today - relativedelta(months=18),
            'booking_handling': 'part_of_price',
        }
        five = self._xmlid('demo_tmr_plan_5y', Plan.create(dict(common, **{
            'name': 'TMR — 5 Year Plan (5 / 15 / 60 / 20)',
            'code': 'TMR-5Y',
            'notes': '<p>Standard launch plan: 5% booking, 15% down payment '
                     'after one month, 60% over twenty quarterly '
                     'instalments, 20% on handover.</p>',
            'line_ids': [
                (0, 0, {'sequence': 10, 'name': 'Booking Deposit',
                        'kind': 'booking', 'calculation_type': 'percent',
                        'value': 5, 'occurrences': 1,
                        'date_rule': 'on_booking'}),
                (0, 0, {'sequence': 20, 'name': 'Down Payment',
                        'kind': 'down_payment', 'calculation_type': 'percent',
                        'value': 15, 'occurrences': 1,
                        'date_rule': 'months_after_booking',
                        'offset_value': 1}),
                (0, 0, {'sequence': 30, 'name': 'Quarterly Instalment',
                        'kind': 'installment', 'calculation_type': 'percent',
                        'value': 3, 'occurrences': 20, 'interval': 'quarterly',
                        'date_rule': 'months_after_contract',
                        'offset_value': 3}),
                (0, 0, {'sequence': 40, 'name': 'Handover Payment',
                        'kind': 'handover', 'calculation_type': 'percent',
                        'value': 20, 'occurrences': 1,
                        'date_rule': 'on_handover'}),
            ],
        })))
        three = self._xmlid('demo_tmr_plan_3y', Plan.create(dict(common, **{
            'name': 'TMR — 3 Year Fast Track (10 / 20 / 60 / 10)',
            'code': 'TMR-3Y',
            'notes': '<p>Shorter plan for buyers who want a lower handover '
                     'balance.</p>',
            'line_ids': [
                (0, 0, {'sequence': 10, 'name': 'Booking Deposit',
                        'kind': 'booking', 'calculation_type': 'percent',
                        'value': 10, 'occurrences': 1,
                        'date_rule': 'on_booking'}),
                (0, 0, {'sequence': 20, 'name': 'Down Payment',
                        'kind': 'down_payment', 'calculation_type': 'percent',
                        'value': 20, 'occurrences': 1,
                        'date_rule': 'months_after_booking',
                        'offset_value': 1}),
                (0, 0, {'sequence': 30, 'name': 'Quarterly Instalment',
                        'kind': 'installment', 'calculation_type': 'percent',
                        'value': 5, 'occurrences': 12, 'interval': 'quarterly',
                        'date_rule': 'months_after_contract',
                        'offset_value': 3}),
                (0, 0, {'sequence': 40, 'name': 'Handover Payment',
                        'kind': 'handover', 'calculation_type': 'percent',
                        'value': 10, 'occurrences': 1,
                        'date_rule': 'on_handover'}),
            ],
        })))
        return {'5y': five, '3y': three}

    # ------------------------------------------------------------------
    #: (unit code, buyer key, plan key, months ago, target state)
    #:
    #: Signings are spread across the last six months on purpose, not bunched
    #: a year back. Sales Velocity and Contracted This Month both bound on
    #: that window, and a demo whose newest signature is eight months old
    #: showed an honest but useless "no contract signed in the last six
    #: months" where the velocity chart should be.
    DEALS = [
        ('TMR-A-101', 'buyer_1', '5y', 17, 'active'),
        ('TMR-A-201', 'buyer_2', '5y', 13, 'active'),
        ('TMR-A-301', 'buyer_5', '3y', 9, 'active'),
        ('TMR-A-401', 'buyer_7', '5y', 5, 'active'),
        ('TMR-A-402', 'buyer_2', '3y', 4, 'signed'),
        ('TMR-A-902', 'buyer_3', '3y', 3, 'active'),
        ('TMR-A-501', 'buyer_6', '5y', 2, 'signed'),
        ('TMR-A-502', 'buyer_8', '5y', 1, 'signed'),
        ('TMR-A-601', 'buyer_4', '3y', 0, 'signed'),
        ('TMR-B-101', 'buyer_4', '5y', 1, 'pending_signature'),
        ('TMR-B-102', 'buyer_6', '3y', 0, 'pending_approval'),
        ('TMR-B-201', 'buyer_6', '3y', 0, 'draft'),
    ]

    @api.model
    def _build_deals(self, project, plans, foundation):
        Contract = self.env['realestate.sale.contract']
        Reservation = self.env['realestate.unit.reservation']
        today = fields.Date.context_today(self)

        for index, (code, buyer_key, plan_key, months, target) in enumerate(
                self.DEALS, start=1):
            unit = foundation._demo_unit(code)
            if not unit:
                _logger.warning("Demo: unit %s missing, deal skipped", code)
                continue
            buyer = foundation._demo_partner(buyer_key)
            signed_on = today - relativedelta(months=months)

            reservation = Reservation.create({
                'property_id': unit.id,
                'partner_id': buyer.id,
                'payment_plan_id': plans[plan_key].id,
                'proposed_price': unit.base_price,
                'booking_fee': 50000,
                'booking_amount_required': 50000,
                'booking_amount_received': 50000,
                'state': 'confirmed',
                'hold_started_at': fields.Datetime.to_datetime(signed_on),
            })
            self._xmlid('demo_tmr_reservation_%d' % index, reservation)
            # The reservation freezes its own schedule when it is created, and
            # it anchors that schedule on TODAY -- which is right for a real
            # booking and wrong for a demo that back-dates deals by up to
            # seventeen months. `_schedule_rows` prefers the reservation's
            # frozen schedule over the plan, so every contract came out with
            # its first instalment due today and nothing ever overdue.
            # Dropping the frozen lines sends the contract back through the
            # payment plan, which expands from `contract_date`.
            reservation.schedule_line_ids.unlink()

            contract = Contract.create({
                'partner_id': buyer.id,
                'property_id': unit.id,
                'payment_plan_id': plans[plan_key].id,
                'reservation_id': reservation.id,
                'sale_price': unit.base_price,
                'contract_date': signed_on,
                # Staggered, not one date for the whole book: the earlier
                # deals are on lower floors that top out first, and a single
                # handover date eighteen months out left the Upcoming
                # Handovers panel permanently empty.
                'expected_handover_date': today + relativedelta(months=3 + index * 2),
                'notes': '<p>Demo contract for unit %s.</p>' % code,
            })
            self._xmlid('demo_tmr_sale_%d' % index, contract)
            self._advance(contract, target, signed_on)

    @api.model
    def _bill_and_collect(self):
        """Invoice the instalments that have fallen due, then collect most.

        A raised schedule is not money. `realestate.sale.installment`'s
        `paid_amount`, `residual_amount` and `state` are all derived from the
        linked invoice, so a contract whose instalments were never invoiced
        leaves Collected This Month, the Scheduled-vs-Collected chart and
        every ageing figure at zero -- which is exactly how this demo first
        looked, with a sales dashboard that showed sales and no cash.

        It goes through the module's own `action_generate_invoice` and
        `action_mark_paid`, the way a collections clerk would, so the demo
        exercises the real posting and reconciliation path rather than
        writing numbers the engine would never produce.
        """
        today = fields.Date.context_today(self)
        Instalment = self.env['realestate.sale.installment']
        due = Instalment.search([
            ('date_due', '<=', today),
            ('is_cancelled', '=', False),
            ('state', '=', 'pending'),
            ('sale_contract_id.state', 'in',
             ('signed', 'active', 'financially_cleared', 'handed_over')),
        ], order='date_due')
        if not due:
            return True

        # Everything older than four months is settled; two in every three of
        # the rest are. The remainder becomes the arrears the dashboard is
        # there to surface -- a demo where everything is paid shows nothing.
        cutoff = today - relativedelta(months=4)
        for position, instalment in enumerate(due):
            try:
                instalment.action_generate_invoice()
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: instalment %s could not be invoiced: %s",
                                instalment.display_name, error)
                continue
            if instalment.date_due > cutoff and position % 3 == 0:
                continue
            try:
                instalment.action_mark_paid()
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: instalment %s could not be settled: %s",
                                instalment.display_name, error)
        return True

    @api.model
    def _advance(self, contract, target, signed_on):
        """Walk the contract to its target state through the real workflow."""
        try:
            if target == 'draft':
                return
            if target == 'pending_approval':
                contract.action_submit_for_approval()
                return
            contract.action_send_for_signature()
            if target == 'pending_signature':
                return
            contract.signing_date = signed_on
            contract.action_sign()
            if target == 'active':
                contract.action_activate()
        except UserError as error:
            # A demo that silently ships half a workflow is worse than one
            # that says which step the engine refused and why.
            _logger.warning("Demo: contract %s could not reach %s: %s",
                            contract.name, target, error)
