"""The lease form's editable lines, in a real browser.

``test_form_cycle.py`` drives the same onchange calls through ``odoo.tests.Form``;
this proves the web client end to end on the exact action a user reported.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLeaseFormBrowser(HttpCase):

    def test_adding_an_escalation_row_on_the_lease_form(self):
        env = self.env
        company = env.company
        today = fields.Date.context_today(env['res.partner'])
        unit = env['realestate.property'].create({
            'name': 'Browser Escalation Unit', 'property_code': 'BRW-ESC-001',
            'hierarchy_level': 'unit', 'usage_category': 'apartment',
            'area_sqm': 80.0, 'company_id': company.id,
        })
        tenant = env['res.partner'].create({'name': 'Browser Escalation Tenant'})
        lease = env['realestate.contract'].create({
            'partner_id': tenant.id,
            'property_id': unit.id,
            'is_single_property': True,
            'is_multi_property': False,
            'start_date': today - relativedelta(months=6),
            'end_date': today + relativedelta(months=18),
            'price': 1000.0,
            'company_id': company.id,
            'currency_id': company.currency_id.id,
        })
        env['realestate.rent.escalation.rule'].create({
            'contract_id': lease.id,
            'effective_date': today,
            'escalation_type': 'percentage',
            'percentage': 5.0,
        })

        self.start_tour(
            '/odoo/action-atmta_real_estate.action_realestate_contract/%s' % lease.id,
            'atmta_rental_escalation_line_tour', login='admin', timeout=180)

        self.assertEqual(len(lease.escalation_rule_ids), 1,
                         "the discarded row was not saved")
