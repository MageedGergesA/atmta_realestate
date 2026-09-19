"""Money buttons are shown only to users who may post accounting entries.

Rental roles grant no accounting rights. A Rental Manager without Odoo's
Invoicing group clicked Register Receipt in the browser and got "You are not
allowed to create 'Payments' (account.payment) records". Every button that
posts an invoice, credit note or payment now requires ``can_post_accounting``;
the server still refuses such a user if the method is called anyway.
"""

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase

MONEY_BUTTONS = {
    ('realestate.contract', 'atmta_real_estate.view_realestate_contract_form'):
        ('action_invoice_due_obligations', 'action_generate_invoices'),
    ('realestate.contract.deposit', None):
        ('action_register_receipt', 'action_refund', 'action_forfeit', 'action_apply_to_arrears'),
    ('realestate.contract.termination', None): ('action_settle',),
    ('realestate.contract.utility.line', None):
        ('action_confirm_utility', 'action_register_payment_utility'),
}


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestMoneyButtons(LeaseCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(
            cls.env, login='money_btn_manager', company_id=cls.company.id,
            groups='base.group_user,atmta_real_estate.group_rental_manager')
        cls.finance_manager = new_test_user(
            cls.env, login='money_btn_finance', company_id=cls.company.id,
            groups='base.group_user,atmta_real_estate.group_rental_manager,'
                   'account.group_account_invoice')

    def test_every_money_button_requires_invoicing_rights(self):
        for (model, view), buttons in MONEY_BUTTONS.items():
            view_id = self.env.ref(view).id if view else False
            arch = etree.fromstring(self.env[model].with_user(self.finance_manager).get_view(
                view_id, 'form')['arch'])
            self.assertTrue(arch.xpath("//field[@name='can_post_accounting']"), model)
            for name in buttons:
                nodes = arch.xpath("//button[@name='%s']" % name)
                self.assertTrue(nodes, '%s.%s is on the form' % (model, name))
                for node in nodes:
                    self.assertIn('not can_post_accounting', node.get('invisible', ''),
                                  '%s.%s' % (model, name))

    def test_the_flag_follows_invoicing_rights(self):
        lease = self.make_lease()
        self.assertFalse(lease.with_user(self.manager).can_post_accounting)
        self.assertTrue(lease.with_user(self.finance_manager).can_post_accounting)

    def test_the_server_still_refuses_a_user_without_invoicing_rights(self):
        lease = self.activate(self.make_lease(
            start=self.today - relativedelta(months=1), rent=1000.0))
        deposit = self.env['realestate.contract.deposit'].create({
            'contract_id': lease.id, 'partner_id': self.tenant.id, 'requested_amount': 1500.0,
        })
        deposit.action_request()
        with self.assertRaises(AccessError):
            deposit.with_user(self.manager).action_register_receipt()
        deposit.with_user(self.finance_manager).action_register_receipt()
        self.assertEqual(deposit.state, 'held')
