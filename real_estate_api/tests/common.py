# -*- coding: utf-8 -*-
"""Shared fixtures for the public API suite.

The API sits on top of the sales gallery and the developer engine, so the
inventory comes from the gallery's own fixtures (a selling project, released or
unreleased units) rather than a second copy of them. What is added here is what
only the API needs: users holding API keys, a contract a customer can download,
and a thin HTTP client that goes through the real routes.
"""

import json
from datetime import timedelta

from odoo import fields
from odoo.tests.common import HttpCase

from odoo.addons.real_estate_maquette.tests.common import VisualCommon


class ApiCommon(VisualCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ICP = cls.env['ir.config_parameter'].sudo()
        # Every request of the suite comes from 127.0.0.1; the throttles are
        # tested nowhere here and must not turn a finding into a 429.
        for key in ('real_estate_api.rate_limit.public.per_minute',
                    'real_estate_api.rate_limit.authed.per_minute',
                    'real_estate_api.rate_limit.interest.per_hour'):
            ICP.set_param(key, '100000')

    # ------------------------------------------------------------------
    # Users and keys
    # ------------------------------------------------------------------
    def _api_key_for(self, *groups):
        user = self._visual_user(*groups)
        key = self.env['res.users.apikeys'].with_user(user)._generate(
            'real_estate_api', 'test key',
            fields.Datetime.now() + timedelta(days=1))
        return user, key

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _get(self, url, key=None, headers=None):
        h = dict(headers or {})
        if key:
            h['Authorization'] = 'Bearer %s' % key
        return self.url_open(url, headers=h)

    def _post_json(self, url, body, key=None, headers=None):
        h = dict(headers or {}, **{'Content-Type': 'application/json'})
        if key:
            h['Authorization'] = 'Bearer %s' % key
        return self.url_open(url, data=json.dumps(body), headers=h)

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------
    def _public_project(self, **kwargs):
        vals = {'state': 'marketing'}
        vals.update(kwargs)
        return self._project(**vals)

    def _signed_contract(self, unit, partner):
        plan = self.env['realestate.payment.plan'].create({
            'name': 'API Plan', 'company_id': self.company.id,
            'project_id': unit.project_id.id,
        })
        self.env['realestate.payment.plan.line'].create([
            {'plan_id': plan.id, 'sequence': 10, 'kind': 'down_payment',
             'calculation_type': 'percent', 'value': 10.0,
             'date_rule': 'on_booking'},
            {'plan_id': plan.id, 'sequence': 20, 'kind': 'handover',
             'calculation_type': 'residual',
             'date_rule': 'months_after_booking', 'offset_value': 24},
        ])
        plan.action_activate()
        unit.product_variant_id.taxes_id = [(5, 0, 0)]
        contract = self.env['realestate.sale.contract'].create({
            'partner_id': partner.id,
            'property_id': unit.id,
            'sale_price': unit.base_price,
            'payment_plan_id': plan.id,
            'contract_date': fields.Date.context_today(partner),
        })
        contract.action_sign()
        return contract

    def _api_partner(self, name, ref):
        return self.env['res.partner'].create({
            'name': name,
            'email': '%s@api.test' % ref,
            'realestate_api_source': True,
            'realestate_api_external_ref': ref,
        })
