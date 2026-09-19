# -*- coding: utf-8 -*-
"""Findings of the visual/portal lifecycle run, each reproduced before the fix.

* A buyer whose contract bills per instalment saw no instalments anywhere: the
  portal only read the V1 whole-price invoice (`contract.invoice_id`), which
  signing no longer raises.
* The visit form's own `datetime-local` value crashed the request (500).
* The public page counted units from the legacy `property.state`.
* `/projects` listed every project although only published ones have a page.
"""

import base64
from datetime import datetime

from lxml import html as lxml_html

from odoo import http
from odoo.tests import tagged
from odoo.tests.common import HttpCase

from odoo.addons.real_estate_developer.tests.test_contract import ContractCommon
from odoo.addons.real_estate_maquette.tests.common import VisualCommon

ONE_PIXEL_PNG = base64.b64decode(
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
    b'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')


@tagged('post_install', '-at_install')
class TestPortalInstallments(ContractCommon, HttpCase):

    def setUp(self):
        super().setUp()
        self.portal_user = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Portal Buyer', 'login': 'portal_buyer_findings',
                'password': 'portal_buyer_findings',
                'partner_id': self.buyer.id,
                'groups_id': [(6, 0, [self.env.ref('base.group_portal').id])],
            })
        self.contract = self._contract(payment_plan_id=self._plan().id)
        self.contract.action_sign()
        self.assertFalse(self.contract.invoice_id)
        self.schedule = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))
        self.assertEqual(len(self.schedule), 6)
        self.first = self.schedule[0]
        self.first.action_mark_paid()
        self.assertEqual(self.first.state, 'paid')
        self.authenticate('portal_buyer_findings', 'portal_buyer_findings')

    def _page(self, url):
        response = self.url_open(url)
        self.assertEqual(response.status_code, 200, url)
        return lxml_html.fromstring(response.content)

    def test_home_counter_counts_unpaid_instalments(self):
        counters = self.make_jsonrpc_request(
            '/my/counters', {'counters': ['installment_count']})

        self.assertEqual(counters['installment_count'], 5)

    def test_contract_page_shows_the_schedule(self):
        doc = self._page('/my/sale-contracts/%s' % self.contract.id)

        rows = doc.xpath('//table[.//th[text()="Due Date"]]/tbody/tr')
        self.assertEqual(len(rows), 6)
        self.assertEqual(
            len(doc.xpath('//table/tbody/tr//span[text()="Paid"]')), 1)

    def test_installments_page_lists_them_with_totals(self):
        doc = self._page('/my/installments')

        rows = doc.xpath('//table/tbody/tr')
        self.assertEqual(len(rows), 6)

        def figure(label):
            text = doc.xpath('//p[strong[text()="%s"]]' % label)[0].text_content()
            return float(text.split(':', 1)[1].strip())

        paid = self.first.paid_amount
        self.assertEqual(figure('Total:'), 1000000.0)
        self.assertEqual(figure('Paid:'), paid)
        self.assertEqual(figure('Balance:'),
                         sum(self.schedule.mapped('residual_amount')))


class PublishedProjectMixin:

    def _published_project(self, **kwargs):
        project = self._project(visual_public_enabled=True, **kwargs)
        project.master_plan_2d = base64.b64encode(ONE_PIXEL_PNG)
        project.visual_3d_enabled = False
        project.action_visual_validate()
        project.action_visual_publish()
        self.assertTrue(project.visual_is_live)
        return project


@tagged('post_install', '-at_install')
class TestPublicFindings(PublishedProjectMixin, VisualCommon, HttpCase):

    def setUp(self):
        super().setUp()
        self.project = self._published_project()
        self.unit = self._unit(self.project)
        self.unreleased = self._unit(self.project, released=False)
        self.assertEqual(self.unreleased.visual_state, 'unreleased')
        self.authenticate(None, None)

    def _visit(self, **extra):
        payload = {
            'name': 'Visiting Vera', 'email': 'vera@example.com',
            'csrf_token': http.Request.csrf_token(self),
        }
        payload.update(extra)
        return self.url_open('/projects/%s/visit' % self.project.id,
                             data=payload)

    def _leads(self):
        return self.env['crm.lead'].search(
            [('re_project_id', '=', self.project.id)])

    def test_visit_date_from_the_form_input_is_accepted(self):
        response = self._visit(visit_date='2026-09-20T10:30')

        self.assertEqual(response.status_code, 200)
        lead = self._leads()
        self.assertEqual(len(lead), 1)
        self.assertEqual(lead.re_visit_date, datetime(2026, 9, 20, 10, 30))

    def test_an_invalid_visit_date_is_a_form_error_not_a_crash(self):
        response = self._visit(visit_date='next tuesday')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'couldn', response.content)
        self.assertFalse(self._leads())

    def test_project_page_counts_units_from_the_gallery(self):
        response = self.url_open('/projects/%s' % self.project.id)
        self.assertEqual(response.status_code, 200)
        doc = lxml_html.fromstring(response.content)
        stats = {
            stat.xpath('.//div[@class="re-stat-label"]')[0].text_content().strip():
            int(stat.xpath('.//div[@class="re-stat-value"]')[0].text_content())
            for stat in doc.xpath('//div[@class="re-stat"]')
        }

        self.assertEqual(stats['Total units'], 2)
        self.assertEqual(stats['Available'], 1)

    def test_index_lists_only_projects_that_have_a_page(self):
        hidden = self._project(name='Unpublished Findings Project')

        response = self.url_open('/projects')

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.project.name.encode(), response.content)
        self.assertNotIn(hidden.name.encode(), response.content)
        self.assertEqual(
            self.url_open('/projects/%s' % hidden.id).status_code, 404)
