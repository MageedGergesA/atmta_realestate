# -*- coding: utf-8 -*-
"""Shared fixtures for the handover suite.

A handover is the last step of a sale, so the smallest believable setup is a
released unit sold on a signed contract. Everything here builds exactly that
and nothing more, so individual tests can say what they are about.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class HandoverCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Handover = cls.env['realestate.handover']
        cls.Snag = cls.env['realestate.snagging.issue']
        cls.Contract = cls.env['realestate.sale.contract']

        cls.project = cls.env['realestate.project'].create({
            'name': 'Handover Heights', 'code': 'HOH', 'company_id': cls.company.id})
        cls.phase = cls.env['realestate.phase'].create({
            'name': 'HOH P1', 'project_id': cls.project.id})
        cls.units = cls.env['realestate.property'].create([{
            'name': 'HOH-U-%03d' % i, 'property_code': 'HOH-U-%03d' % i,
            'hierarchy_level': 'unit', 'usage_category': 'apartment',
            'area_sqm': 100.0 + i, 'company_id': cls.company.id,
            'project_id': cls.project.id, 'phase_id': cls.phase.id,
        } for i in range(1, 5)])
        cls.project.commercial_state = 'selling'
        cls.phase.commercial_state = 'selling'
        batch = cls.env['realestate.unit.release.batch'].create({
            'project_id': cls.project.id, 'phase_id': cls.phase.id,
            'property_ids': [(6, 0, cls.units.ids)]})
        batch.action_approve()
        batch.action_release()
        cls.units.write({'base_price': 1000000.0})

        cls.plan = cls.env['realestate.payment.plan'].create({
            'name': 'HOH plan', 'company_id': cls.company.id, 'project_id': cls.project.id})
        cls.env['realestate.payment.plan.line'].create([
            {'plan_id': cls.plan.id, 'sequence': 10, 'kind': 'down_payment',
             'calculation_type': 'percent', 'value': 20.0, 'date_rule': 'on_booking'},
            {'plan_id': cls.plan.id, 'sequence': 20, 'kind': 'handover',
             'calculation_type': 'residual', 'date_rule': 'months_after_booking',
             'offset_value': 24},
        ])
        cls.plan.action_activate()
        cls.buyer = cls.env['res.partner'].create({'name': 'Handover Buyer'})

    @classmethod
    def _contract(cls, unit, sign=True):
        contract = cls.Contract.create({
            'partner_id': cls.buyer.id, 'property_id': unit.id,
            'sale_price': 1000000.0, 'payment_plan_id': cls.plan.id,
            'contract_date': fields.Date.context_today(cls.env['res.partner']),
        })
        if sign:
            contract.action_sign()
        return contract

    def _handover(self, contract, **vals):
        values = {
            'sale_contract_id': contract.id,
            'scheduled_date': fields.Datetime.now() + timedelta(days=1),
            'warranty_period_months': 12,
        }
        values.update(vals)
        return self.Handover.create(values)

    def _snag(self, handover, severity='minor', **vals):
        values = {'handover_id': handover.id, 'description': 'Snag %s' % severity,
                  'severity': severity}
        values.update(vals)
        return self.Snag.create(values)
