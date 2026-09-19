# -*- coding: utf-8 -*-
"""A Leasing Agent can open a unit that the Developer app also uses.

This module adds reservation and sale-contract buttons and a Development tab to
the unit form. A Rental user without a Developer role may not read those
models, so without a ``groups`` restriction the whole unit form failed for
them with an access error.
"""

from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .common import _spec, module_installed

from lxml import etree

from .test_contract import ContractCommon


@tagged('post_install', '-at_install', 'atmta_developer')
class TestRentalAgentReadsUnit(ContractCommon):

    def setUp(self):
        super().setUp()
        if not module_installed(self.env, 'atmta_real_estate'):
            self.skipTest('atmta_real_estate is not installed: these tests are '
                          'about a Rental leasing agent reading a unit')

    def _unreadable(self, record, user):
        record = record.with_user(user)
        root = etree.fromstring(record.get_view(False, 'form')['arch'])
        failures = []
        for node in root.iter('field'):
            if any(parent.tag == 'field' for parent in node.iterancestors()):
                continue
            spec = _spec(self.env, record._name, node)
            if spec is None:
                continue
            try:
                with self.env.cr.savepoint():
                    record.web_read({node.get('name'): spec})
            except AccessError as exc:
                failures.append('%s.%s: %s' % (record._name, node.get('name'),
                                               str(exc).splitlines()[0]))
            self.env.invalidate_all()
        return failures

    def test_a_leasing_agent_opens_a_unit_with_sales_history(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        held = self.units[1]
        self._release(held)
        held.base_price = 900000.0
        self.env['realestate.unit.reservation'].create({
            'property_id': held.id, 'partner_id': self.co_buyer.id,
        })
        agent = new_test_user(
            self.env, login='dev_rental_agent', company_id=self.company.id,
            groups='base.group_user,atmta_real_estate.group_rental_agent')

        failures = self._unreadable(self.unit, agent) + self._unreadable(held, agent)
        self.assertFalse(failures, '\n'.join(failures))

    def test_a_developer_readonly_user_still_sees_the_sales_history(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        reader = new_test_user(
            self.env, login='dev_readonly_reader', company_id=self.company.id,
            groups='base.group_user,atmta_real_estate.group_rental_user,'
                   'real_estate_developer.group_dev_readonly')
        arch = self.unit.with_user(reader).get_view(False, 'form')['arch']
        for name in ('sale_contract_count', 'sale_contract_ids', 'reservation_ids'):
            self.assertIn('name="%s"' % name, arch, name)
        self.assertFalse(self._unreadable(self.unit, reader))
