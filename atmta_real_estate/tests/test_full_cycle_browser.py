"""The whole rental cycle, in a real browser, by the role that owns each step.

Every step is a click in the web client (``static/tests/tours/
rental_full_cycle_tours.js``), logged in as the Leasing Agent, Rental Manager,
Property Manager or billing user who does that job. Between steps the server
state is checked, so a step that "clicks" but does nothing fails here.

Lease A: created on the form, submitted, approved, signed, activated, moved in,
billed and invoiced, then renewed into a new lease.
Lease B: terminated early with notice, approved and settled, moved out, and
the termination completed.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import HttpCase, new_test_user, tagged

LEASES = '/odoo/action-atmta_real_estate.action_realestate_contract/%s'
RENEWALS = '/odoo/action-atmta_real_estate.action_renewals/%s'
TERMINATIONS = '/odoo/action-atmta_real_estate.action_terminations/%s'


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestFullCycleBrowser(HttpCase):

    def _user(self, login, *groups):
        return new_test_user(
            self.env, login=login, company_id=self.env.company.id,
            groups=','.join(('base.group_user',) + groups))

    def _tour(self, url, tour, user):
        self.start_tour(url, tour, login=user.login, timeout=300)
        self.env.invalidate_all()

    def test_the_whole_rental_cycle_in_the_browser(self):
        env = self.env
        company = env.company
        today = fields.Date.context_today(env['res.partner'])
        agent = self._user('cycle_agent', 'atmta_real_estate.group_rental_agent',
                           'atmta_real_estate.group_rental_all_portfolios')
        manager = self._user('cycle_manager', 'atmta_real_estate.group_rental_manager')
        property_manager = self._user('cycle_pm', 'atmta_real_estate.group_property_manager')
        # Settling posts credit notes and a final invoice, which Odoo reserves
        # for its Invoicing group; Rental roles grant no accounting rights.
        finance_manager = self._user('cycle_finance', 'atmta_real_estate.group_rental_manager',
                                     'account.group_account_invoice')
        billing = env.ref('base.user_admin')

        tenant = env['res.partner'].create({'name': 'Cycle Tenant'})
        Property = env['realestate.property']
        unit_a = Property.create({
            'name': 'Cycle Unit A', 'property_code': 'CYC-A-001', 'hierarchy_level': 'unit',
            'usage_category': 'apartment', 'area_sqm': 90.0, 'company_id': company.id,
        })
        unit_b = Property.create({
            'name': 'Cycle Unit B', 'property_code': 'CYC-B-001', 'hierarchy_level': 'unit',
            'usage_category': 'apartment', 'area_sqm': 75.0, 'company_id': company.id,
        })
        # Dates are typed in the user's language format in the browser; set them
        # as the agent's form defaults instead.
        start_a = today - relativedelta(months=11)
        end_a = today + relativedelta(days=20)
        env['ir.default'].set('realestate.contract', 'start_date', str(start_a), user_id=agent.id)
        env['ir.default'].set('realestate.contract', 'end_date', str(end_a), user_id=agent.id)

        # ------------------------------------------------------------ Lease A
        self._tour(LEASES % 'new', 'atmta_cycle_1_agent_create_submit', agent)
        lease = env['realestate.contract'].search(
            [('partner_id', '=', tenant.id), ('property_id', '=', unit_a.id)])
        self.assertEqual(len(lease), 1, "the agent created exactly one lease")
        self.assertEqual(lease.lifecycle_state, 'pending_approval')
        self.assertEqual((lease.start_date, lease.end_date, lease.price), (start_a, end_a, 1000.0))

        self._tour(LEASES % lease.id, 'atmta_cycle_2_manager_approve', manager)
        self.assertEqual(lease.lifecycle_state, 'pending_signature')

        self._tour(LEASES % lease.id, 'atmta_cycle_3_agent_mark_signed', agent)
        self.assertEqual(lease.signature_status, 'signed')

        self._tour(LEASES % lease.id, 'atmta_cycle_4_pm_activate_move_in', property_manager)
        self.assertEqual(lease.lifecycle_state, 'active')
        move_in = env['realestate.move.in'].search([('contract_id', '=', lease.id)])
        self.assertEqual(move_in.state, 'completed')

        self._tour(LEASES % lease.id, 'atmta_cycle_5_billing', billing)
        self.assertTrue(lease.contract_payment_ids, "a billing schedule was generated")
        invoiced = lease.contract_payment_ids.filtered('move_id')
        self.assertTrue(invoiced, "the due obligations were invoiced")
        self.assertEqual(set(invoiced.move_id.mapped('state')), {'posted'})

        self._tour(LEASES % lease.id, 'atmta_cycle_6_agent_renew_propose', agent)
        renewal = env['realestate.contract.renewal'].search([('contract_id', '=', lease.id)])
        self.assertEqual(renewal.state, 'proposed')
        self.assertEqual(renewal.proposed_start_date, end_a + relativedelta(days=1))

        self._tour(RENEWALS % renewal.id, 'atmta_cycle_7_manager_approve_renewal', manager)
        self.assertEqual(renewal.state, 'approved')
        self._tour(RENEWALS % renewal.id, 'atmta_cycle_8_agent_accept_renewal', agent)
        self.assertEqual(renewal.state, 'accepted')
        self._tour(RENEWALS % renewal.id, 'atmta_cycle_9_manager_create_renewal_lease', manager)
        self.assertEqual(renewal.state, 'renewed')
        self.assertEqual(renewal.new_contract_id.old_contract_id, lease)
        self.assertEqual(renewal.new_contract_id.start_date, end_a + relativedelta(days=1))

        # ------------------------------------------------------------ Lease B
        lease_b = env['realestate.contract'].create({
            'partner_id': tenant.id, 'property_id': unit_b.id,
            'is_single_property': True, 'is_multi_property': False,
            'start_date': today - relativedelta(months=6),
            'end_date': today + relativedelta(months=6),
            'price': 800.0, 'company_id': company.id, 'currency_id': company.currency_id.id,
        })
        lease_b.action_to_proposal()
        lease_b.action_submit_for_approval()
        lease_b.action_approve_lease()
        lease_b.action_mark_signed()
        lease_b.action_activate_lease()
        self.assertEqual(lease_b.lifecycle_state, 'active')

        self._tour(LEASES % lease_b.id, 'atmta_cycle_10_pm_terminate_notice', property_manager)
        termination = env['realestate.contract.termination'].search([('contract_id', '=', lease_b.id)])
        self.assertEqual(termination.state, 'notice_given')
        self.assertEqual(lease_b.lifecycle_state, 'notice')

        self._tour(TERMINATIONS % termination.id, 'atmta_cycle_11_manager_approve_settle',
                   finance_manager)
        self.assertEqual(termination.state, 'settled')

        self._tour(LEASES % lease_b.id, 'atmta_cycle_12_pm_move_out', property_manager)
        move_out = env['realestate.move.out'].search([('contract_id', '=', lease_b.id)])
        self.assertEqual(move_out.state, 'completed')
        self.assertTrue(move_out.unit_turn_id, "the unit went to a turn")

        self._tour(TERMINATIONS % termination.id, 'atmta_cycle_13_pm_complete_termination',
                   property_manager)
        self.assertEqual(termination.state, 'completed')
        self.assertIn(lease_b.lifecycle_state, ('terminated', 'ended'))
