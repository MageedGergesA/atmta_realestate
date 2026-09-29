# -*- coding: utf-8 -*-
"""Regressions for the public API lifecycle run (Sept 2026).

Every test goes through a real HTTP request against the real route: what was
wrong was what a website or an integration actually received, not what a
Python method returned.
"""

import base64
import json

from odoo import fields
from odoo.tests.common import tagged

from odoo.addons.real_estate_maquette.tests.common import _ONE_PIXEL_PNG

from .common import ApiCommon


def _json(response):
    return json.loads(response.content.decode())


@tagged('post_install', '-at_install')
class TestInternalPriceNotPublished(ApiCommon):
    """`base_price` is the internal number; an unreleased unit has no price."""

    def test_unreleased_unit_detail_does_not_carry_the_internal_price(self):
        project = self._public_project()
        unit = self._unit(project, released=False, price=1500000.0)
        self.assertFalse(unit.is_available_for_sale)

        response = self._get('/api/v1/units/%s' % unit.id)

        self.assertEqual(response.status_code, 200)
        data = _json(response)
        self.assertEqual(data['status'], 'hidden')
        self.assertEqual(data['price'], 0.0)

    def test_released_unit_detail_carries_its_public_price(self):
        project = self._public_project()
        unit = self._unit(project, price=1000000.0)

        data = _json(self._get('/api/v1/units/%s' % unit.id))

        self.assertEqual(data['price'], unit._public_price())
        self.assertEqual(data['price'], 1000000.0)

    def test_project_price_range_ignores_units_not_for_sale(self):
        project = self._public_project()
        self._unit(project, price=1000000.0)
        self._unit(project, released=False, price=1500000.0)

        data = _json(self._get('/api/v1/projects/%s' % project.id))

        self.assertEqual(data['starting_price'], 1000000.0)
        self.assertEqual(data['price_max'], 1000000.0)

    def test_map_does_not_offer_an_unreleased_unit_at_its_internal_price(self):
        project = self._public_project()
        released = self._unit(project, price=1000000.0,
                              latitude=30.1, longitude=31.2)
        unreleased = self._unit(project, released=False, price=1500000.0,
                                latitude=30.1, longitude=31.2)

        data = _json(self._get(
            '/api/v1/map/properties?hierarchy_level=unit&project_id=%s'
            % project.id))

        rows = {r['id']: r for r in data['results']}
        self.assertNotIn(unreleased.id, rows)
        self.assertEqual(rows[released.id]['base_price'], 1000000.0)


@tagged('post_install', '-at_install')
class TestApiGroupsEnforced(ApiCommon):
    """A key only works for a user the API groups say may use it."""

    def test_a_key_without_the_api_group_cannot_mint_embed_tokens(self):
        project = self._public_project()
        self._attach_master_plan(project)
        _user, key = self._api_key_for()

        response = self._post_json('/api/v1/embed-tokens', {
            'kind': 'plan-2d', 'resource_model': 'realestate.project',
            'resource_id': project.id,
            'allowed_origins': 'https://partner.test',
        }, key=key)

        self.assertEqual(response.status_code, 403)

    def test_an_api_user_key_can_mint_embed_tokens(self):
        project = self._public_project()
        self._attach_master_plan(project)
        _user, key = self._api_key_for(
            'real_estate_api.group_realestate_api_user')

        response = self._post_json('/api/v1/embed-tokens', {
            'kind': 'plan-2d', 'resource_model': 'realestate.project',
            'resource_id': project.id,
            'allowed_origins': 'https://partner.test',
        }, key=key)

        self.assertEqual(response.status_code, 201)

    def test_a_key_without_the_api_group_cannot_read_customer_data(self):
        self._api_partner('Group Buyer', 'grp-1')
        _user, key = self._api_key_for()

        contracts = self._get(
            '/api/v1/partners/by-ref/grp-1/contracts', key=key)
        statement = self._get(
            '/api/v1/partners/by-ref/grp-1/statement.pdf', key=key)

        self.assertEqual(contracts.status_code, 403)
        self.assertEqual(statement.status_code, 403)


@tagged('post_install', '-at_install')
class TestByRefDownloadsRender(ApiCommon):
    """An API Manager's by-ref download must render, not 500."""

    def test_contract_pdf_and_installment_invoice_render_for_api_manager(self):
        project = self._public_project()
        unit = self._unit(project, price=1000000.0)
        buyer = self._api_partner('Download Buyer', 'dl-1')
        contract = self._signed_contract(unit, buyer)
        first = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        first.action_generate_invoice()
        if first.move_id.state == 'draft':
            first.move_id.action_post()
        _user, key = self._api_key_for(
            'real_estate_api.group_realestate_api_manager')

        pdf = self._get(
            '/api/v1/partners/by-ref/dl-1/contracts/%s/download?kind=sale'
            % contract.id, key=key)
        invoice = self._get(
            '/api/v1/partners/by-ref/dl-1/installments/%s/invoice'
            % first.id, key=key)

        self.assertEqual(pdf.status_code, 200, pdf.content[:200])
        self.assertEqual(invoice.status_code, 200, invoice.content[:200])

    def test_another_customers_contract_is_still_not_found(self):
        project = self._public_project()
        unit = self._unit(project, price=1000000.0)
        self._api_partner('Caller', 'dl-2')
        other = self._api_partner('Somebody Else', 'dl-3')
        contract = self._signed_contract(unit, other)
        _user, key = self._api_key_for(
            'real_estate_api.group_realestate_api_manager')

        response = self._get(
            '/api/v1/partners/by-ref/dl-2/contracts/%s/download?kind=sale'
            % contract.id, key=key)

        self.assertEqual(response.status_code, 404)


@tagged('post_install', '-at_install')
class TestInterestIdempotency(ApiCommon):

    def test_a_replay_returns_the_original_lead(self):
        project = self._public_project()
        body = {'name': 'Web Wendy', 'email': 'wendy@web.test',
                'project_id': project.id, 'message': 'Call me'}
        headers = {'Idempotency-Key': 'abc-123'}

        first = self._post_json('/api/v1/interests', body, headers=headers)
        replay = self._post_json('/api/v1/interests', body, headers=headers)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(_json(replay)['id'], _json(first)['id'])
        self.assertTrue(_json(replay)['idempotent_replay'])
        self.assertEqual(self.env['crm.lead'].search_count(
            [('contact_name', '=', 'Web Wendy')]), 1)


@tagged('post_install', '-at_install')
class TestEmbedOriginFromReferer(ApiCommon):

    def _token(self, allowed):
        project = self._public_project()
        self._attach_master_plan(project)
        return self.env['realestate.embed.token']._mint(
            kind='plan-2d', resource_model='realestate.project',
            resource_id=project.id, allowed_origins=allowed).token

    def test_an_iframe_sending_only_a_full_referer_is_allowed(self):
        token = self._token('https://partner.test')

        response = self._get('/embed/v1/plan-2d/%s' % token, headers={
            'Referer': 'https://partner.test/listings/visual-bay?x=1'})

        self.assertEqual(response.status_code, 200)

    def test_a_trailing_slash_in_the_allow_list_does_not_refuse(self):
        token = self._token('https://partner.test/')

        response = self._get('/embed/v1/plan-2d/%s' % token, headers={
            'Origin': 'https://partner.test'})

        self.assertEqual(response.status_code, 200)

    def test_a_referer_on_another_host_is_still_refused(self):
        token = self._token('https://partner.test')

        response = self._get('/embed/v1/plan-2d/%s' % token, headers={
            'Referer': 'https://partner.test.evil.test/partner.test'})

        self.assertEqual(response.status_code, 403)


@tagged('post_install', '-at_install')
class TestDescriptorsHideNonPublicProjects(ApiCommon):

    def _cancelled_project(self):
        project = self._public_project()
        building = self._building(project)
        building.plan_image = base64.b64encode(_ONE_PIXEL_PNG)
        self._attach_master_plan(project)
        self._attach_glb(project, ['U1'])
        project.state = 'cancelled'
        return project, building

    def test_plan_2d_of_a_cancelled_project_is_not_found(self):
        project, _building = self._cancelled_project()
        response = self._get('/api/v1/projects/%s/plan-2d' % project.id)
        self.assertEqual(response.status_code, 404)

    def test_maquette_3d_of_a_cancelled_project_is_not_found(self):
        project, _building = self._cancelled_project()
        response = self._get('/api/v1/projects/%s/maquette-3d' % project.id)
        self.assertEqual(response.status_code, 404)

    def test_property_plan_2d_inside_a_cancelled_project_is_not_found(self):
        _project, building = self._cancelled_project()
        response = self._get('/api/v1/properties/%s/plan-2d' % building.id)
        self.assertEqual(response.status_code, 404)

    def test_plan_2d_of_a_public_project_still_serves(self):
        project = self._public_project()
        self._attach_master_plan(project)
        response = self._get('/api/v1/projects/%s/plan-2d' % project.id)
        self.assertEqual(response.status_code, 200)


@tagged('post_install', '-at_install')
class TestInterestOnUnitsNotForSale(ApiCommon):

    def _interest(self, unit):
        return self._post_json('/api/v1/interests', {
            'name': 'Unit Enquirer', 'email': 'enq@web.test',
            'unit_id': unit.id})

    def test_an_unreleased_unit_takes_no_enquiry(self):
        project = self._public_project()
        unit = self._unit(project, released=False)
        self.assertEqual(self._interest(unit).status_code, 404)

    def test_a_contracted_unit_takes_no_enquiry(self):
        project = self._public_project()
        unit = self._unit(project)
        self._signed_contract(unit, self._api_partner('Owner', 'own-1'))
        self.assertEqual(unit.commercial_status, 'contracted')
        self.assertEqual(self._interest(unit).status_code, 404)

    def test_an_available_unit_takes_an_enquiry(self):
        project = self._public_project()
        unit = self._unit(project)
        self.assertEqual(self._interest(unit).status_code, 201)


@tagged('post_install', '-at_install')
class TestRentalPaymentsPayload(ApiCommon):
    """`/payments` serves rental obligations; nothing exercised it before.

    The route builds its rows field by field off ``realestate.contract.payment``,
    so a field removed from that model turns every call into a 500 and no test
    would have noticed. It carried ``hijri_date_due`` until 0.12 withdrew the
    Hijri columns.
    """

    def _lease_with_an_obligation(self, tenant):
        today = fields.Date.today()
        unit = self.env['realestate.property'].create({
            'name': 'Rental Unit A-101',
            'property_code': 'API-RENT-101',
            'hierarchy_level': 'unit',
            'usage_category': 'apartment',
            'area_sqm': 100.0,
        })
        lease = self.env['realestate.contract'].create({
            'partner_id': tenant.id,
            'property_id': unit.id,
            'is_single_property': True,
            'is_multi_property': False,
            'start_date': today,
            'end_date': fields.Date.add(today, years=1, days=-1),
            'price': 5000.0,
        })
        self.env['realestate.contract.payment'].create({
            'contract_id': lease.id,
            'date_due': today,
            'amount': 5000.0,
        })
        return lease

    def test_the_payments_route_answers_and_carries_no_hijri_key(self):
        tenant = self._api_partner('Rental Tenant', 'rp-1')
        self._lease_with_an_obligation(tenant)
        _user, key = self._api_key_for(
            'real_estate_api.group_realestate_api_manager')

        response = self._get('/api/v1/partners/by-ref/rp-1/payments', key=key)

        self.assertEqual(response.status_code, 200, response.content[:400])
        payload = _json(response)
        self.assertEqual(payload['total_count'], 1)
        row = payload['results'][0]
        self.assertEqual(row['amount'], 5000.0)
        self.assertTrue(row['date_due'], "the Gregorian due date still ships")
        self.assertNotIn('hijri_date_due', row)
