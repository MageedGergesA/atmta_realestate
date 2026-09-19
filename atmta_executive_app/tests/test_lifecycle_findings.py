# -*- coding: utf-8 -*-
"""Findings of the operations lifecycle run, each pinned by a test.

Every test here failed before its fix, for the reason its docstring gives.

The Suite Overview owns no model, so the only way to move one of its figures is
to move the records it counts: a released unit sold on a signed contract, with
the schedule that contract raises. The fixture is built here rather than
imported from the Developer suite, which this application must not depend on
for its own tests to run.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestExecutiveLifecycleFindings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dashboard = cls.env['realestate.executive.dashboard']
        cls.Contract = cls.env['realestate.sale.contract']
        company = cls.env.company
        cls.project = cls.env['realestate.project'].create({
            'name': 'Executive Heights', 'code': 'EXH', 'company_id': company.id})
        cls.phase = cls.env['realestate.phase'].create({
            'name': 'EXH P1', 'project_id': cls.project.id})
        cls.units = cls.env['realestate.property'].create([{
            'name': 'EXH-U-%03d' % i, 'property_code': 'EXH-U-%03d' % i,
            'hierarchy_level': 'unit', 'usage_category': 'apartment',
            'area_sqm': 120.0, 'company_id': company.id,
            'project_id': cls.project.id, 'phase_id': cls.phase.id,
        } for i in range(1, 3)])
        cls.project.commercial_state = 'selling'
        cls.phase.commercial_state = 'selling'
        batch = cls.env['realestate.unit.release.batch'].create({
            'project_id': cls.project.id, 'phase_id': cls.phase.id,
            'property_ids': [(6, 0, cls.units.ids)]})
        batch.action_approve()
        batch.action_release()
        cls.units.write({'base_price': 1000000.0})

        # A down payment due on booking and a balance due two years out: the
        # second is the instalment nobody owes yet and everybody can pay early.
        cls.plan = cls.env['realestate.payment.plan'].create({
            'name': 'EXH plan', 'company_id': company.id, 'project_id': cls.project.id})
        cls.env['realestate.payment.plan.line'].create([
            {'plan_id': cls.plan.id, 'sequence': 10, 'kind': 'down_payment',
             'calculation_type': 'percent', 'value': 20.0, 'date_rule': 'on_booking'},
            {'plan_id': cls.plan.id, 'sequence': 20, 'kind': 'handover',
             'calculation_type': 'residual', 'date_rule': 'months_after_booking',
             'offset_value': 24},
        ])
        cls.plan.action_activate()
        cls.buyer = cls.env['res.partner'].create({'name': 'Executive Buyer'})

    def _tiles(self):
        return {t['key']: t for s in self.Dashboard.get_dashboard()['sections']
                for t in s['tiles']}

    def _collected(self):
        tiles = self._tiles()
        self.assertIn('collected_month', tiles)
        return tiles['collected_month']['value']

    def _signed_contract(self, unit):
        contract = self.Contract.create({
            'partner_id': self.buyer.id, 'property_id': unit.id,
            'sale_price': 1000000.0, 'payment_plan_id': self.plan.id,
            'contract_date': fields.Date.context_today(self.env['res.partner']),
        })
        contract.action_sign()
        return contract

    def _pay_the_latest_instalment(self, contract):
        latest = max(contract.installment_ids, key=lambda i: i.date_due)
        self.assertGreater(latest.date_due,
                           fields.Date.context_today(contract) + timedelta(days=400))
        latest.action_mark_paid()
        self.env.invalidate_all()
        self.assertGreater(latest.paid_amount, 0.0)
        return latest

    def test_money_received_this_month_is_collected_this_month(self):
        """The figure was filtered by the instalment's DUE date, so money that
        arrived today against an instalment due in two years moved it by
        nothing -- while the label promised a month's collections."""
        contract = self._signed_contract(self.units[0])
        before = self._collected()
        paid = self._pay_the_latest_instalment(contract)
        self.assertAlmostEqual(self._collected() - before, paid.paid_amount, 2)

    def test_the_figure_is_the_records_it_opens(self):
        """A figure and the list behind it must not disagree."""
        self._pay_the_latest_instalment(self._signed_contract(self.units[1]))
        action = self.Dashboard.action_drill('collected_month')
        spec = self.Dashboard._find_tile_spec('collected_month', 'team')
        records = self.env[action['res_model']].search(action['domain'])
        self.assertTrue(records)
        self.assertAlmostEqual(
            self._collected(), sum(records.mapped(spec['measure'])), 2)
