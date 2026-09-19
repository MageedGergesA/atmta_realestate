"""The money side of a live lease, in a real browser.

A Leasing Agent requests a security deposit from the lease; a Rental Manager
registers its receipt and refunds it, and amends the rent through the gear
menu's Amend Lease, applying it with the confirmation a user sees. The server is
checked between tours.
"""

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests.common import HttpCase, new_test_user, tagged

LEASES = '/odoo/action-atmta_real_estate.action_realestate_contract/%s'
DEPOSITS = '/odoo/action-atmta_real_estate.action_deposits/%s'


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestMoneyCycleBrowser(HttpCase):

    def _user(self, login, *groups):
        return new_test_user(
            self.env, login=login, company_id=self.env.company.id,
            groups=','.join(('base.group_user',) + groups))

    def _tour(self, url, tour, user):
        self.start_tour(url, tour, login=user.login, timeout=300)
        self.env.invalidate_all()

    def _configure_deposits(self, company):
        Account = self.env['account.account']
        deposit_account = Account.create({
            'name': 'Browser Tenant Deposits', 'code': 'BRDEP01',
            'account_type': 'liability_current', 'reconcile': True,
            'company_ids': [Command.link(company.id)],
        })
        forfeit_account = Account.create({
            'name': 'Browser Forfeited Deposits', 'code': 'BRFRF01',
            'account_type': 'income_other', 'company_ids': [Command.link(company.id)],
        })
        journal = self.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1) \
            or self.env['account.journal'].create({
                'name': 'Browser Bank', 'type': 'bank', 'code': 'BRBK', 'company_id': company.id})
        company.write({
            're_deposit_account_id': deposit_account.id,
            're_deposit_journal_id': journal.id,
            're_deposit_forfeit_income_account_id': forfeit_account.id,
        })

    def test_deposit_and_amendment_in_the_browser(self):
        env = self.env
        company = env.company
        today = fields.Date.context_today(env['res.partner'])
        self._configure_deposits(company)
        agent = self._user('money_agent', 'atmta_real_estate.group_rental_agent',
                           'atmta_real_estate.group_rental_all_portfolios')
        manager = self._user('money_manager', 'atmta_real_estate.group_rental_manager')
        # Receipts and refunds post payments. Rental roles grant no accounting
        # rights, so the person handling the money also holds Invoicing.
        cashier = self._user('money_cashier', 'atmta_real_estate.group_rental_manager',
                             'account.group_account_invoice')

        tenant = env['res.partner'].create({'name': 'Money Tenant'})
        unit = env['realestate.property'].create({
            'name': 'Money Unit', 'property_code': 'MNY-U-001', 'hierarchy_level': 'unit',
            'usage_category': 'apartment', 'area_sqm': 80.0, 'company_id': company.id,
        })
        lease = env['realestate.contract'].create({
            'partner_id': tenant.id, 'property_id': unit.id,
            'is_single_property': True, 'is_multi_property': False,
            'start_date': today - relativedelta(months=3),
            'end_date': today + relativedelta(months=9),
            'price': 1000.0, 'company_id': company.id, 'currency_id': company.currency_id.id,
        })
        lease.action_to_proposal()
        lease.action_submit_for_approval()
        lease.action_approve_lease()
        lease.action_mark_signed()
        lease.action_activate_lease()
        lease.action_generate_billing_schedule()

        # ------------------------------------------------------------ deposit
        self._tour(LEASES % lease.id, 'atmta_money_1_agent_request_deposit', agent)
        deposit = env['realestate.contract.deposit'].search([('contract_id', '=', lease.id)])
        self.assertEqual(len(deposit), 1)
        self.assertEqual(deposit.state, 'requested')
        self.assertEqual(deposit.requested_amount, 2000.0)

        self._tour(DEPOSITS % deposit.id, 'atmta_money_2_manager_receive_refund', cashier)
        self.assertEqual(deposit.received_amount, 2000.0)
        self.assertEqual(deposit.refunded_amount, 2000.0)
        self.assertEqual(deposit.state, 'refunded')
        self.assertEqual(len(deposit.payment_ids), 2, "an inbound receipt and an outbound refund")

        # ---------------------------------------------------------- amendment
        self._tour(LEASES % lease.id, 'atmta_money_3_manager_amend_rent', manager)
        amendment = env['realestate.contract.amendment'].search([('contract_id', '=', lease.id)])
        self.assertEqual(len(amendment), 1)
        self.assertEqual(amendment.state, 'applied')
        self.assertEqual(amendment.new_rent, 1100.0)
        self.assertAlmostEqual(lease._rent_on(amendment.effective_date + relativedelta(days=1)),
                               1100.0, places=2)
