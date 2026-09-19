# -*- coding: utf-8 -*-
"""Accountants can still open payment terms.

Read access to the real-estate schedule segments was narrowed from every
internal user to Developer read-only, but this module adds the segment list and
its total to Odoo's own payment-term form with no group. An Invoicing user
without a Developer role opening any payment term read those fields and got an
access error. The builder block is now shown to Developer users only.
"""

from lxml import etree

from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPaymentTermFormAccess(TransactionCase):

    def _form_fields(self, user):
        Term = self.env['account.payment.term'].with_user(user)
        arch = etree.fromstring(Term.get_view(
            self.env.ref('account.view_payment_term_form').id, 'form')['arch'])
        return {node.get('name') for node in arch.iter('field')
                if node.getparent() is not None
                and not any(a.tag == 'field' for a in node.iterancestors())}

    def test_an_accountant_opens_a_payment_term(self):
        accountant = new_test_user(self.env, login='pt_accountant',
                                   groups='base.group_user,account.group_account_invoice')
        term = self.env.ref('account.account_payment_term_30days')
        names = self._form_fields(accountant)
        self.assertNotIn('re_segment_ids', names)
        self.assertNotIn('re_total_pct', names)
        spec = {name: {} for name in names
                if self.env['account.payment.term']._fields[name].type
                not in ('one2many', 'many2many')}
        term.with_user(accountant).web_read(spec)

    def test_a_developer_still_sees_the_schedule_builder(self):
        developer = new_test_user(self.env, login='pt_developer',
                                  groups='base.group_user,account.group_account_invoice,'
                                         'real_estate_developer.group_dev_readonly')
        names = self._form_fields(developer)
        self.assertIn('re_segment_ids', names)
        self.assertIn('re_total_pct', names)
        self.env.ref('account.account_payment_term_30days').with_user(developer).web_read(
            {'re_segment_ids': {'fields': {'name': {}}}, 're_total_pct': {}})
