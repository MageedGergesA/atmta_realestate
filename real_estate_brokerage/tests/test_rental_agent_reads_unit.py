# -*- coding: utf-8 -*-
"""A Leasing Agent can open units and contacts that Brokerage also uses.

This module adds listing and sale buttons and a Sales tab to the unit form, and
a Broker page to the contact form. A Rental user without a Brokerage role may
not read listings, transactions or broker agreements, so without a ``groups``
restriction those forms failed for them with an access error.
"""

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .common import BrokerageCommon, _spec, module_installed


@tagged('post_install', '-at_install')
class TestRentalAgentReadsBrokerageRecords(BrokerageCommon):

    def setUp(self):
        super().setUp()
        if not module_installed(self.env, 'atmta_real_estate'):
            self.skipTest('atmta_real_estate is not installed: these tests are '
                          'about a Rental leasing agent reading brokerage records')

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

    def _rental_agent(self):
        return new_test_user(
            self.env, login='brokerage_rental_agent', company_id=self.company.id,
            groups='base.group_user,atmta_real_estate.group_rental_agent')

    def test_a_leasing_agent_opens_a_listed_unit_and_a_broker_contact(self):
        listing = self._listing(activate=True)
        broker = self._broker()
        self._agreement(broker)
        agent = self._rental_agent()

        failures = (self._unreadable(listing.property_id, agent)
                    + self._unreadable(broker, agent))
        self.assertFalse(failures, '\n'.join(failures))

    def test_a_sales_reader_still_sees_listings_and_agreements(self):
        listing = self._listing(activate=True)
        reader = new_test_user(
            self.env, login='brokerage_sales_reader', company_id=self.company.id,
            groups='base.group_user,atmta_real_estate.group_rental_user,'
                   'real_estate_brokerage.group_realestate_sales_readonly')
        arch = listing.property_id.with_user(reader).get_view(False, 'form')['arch']
        for name in ('listing_count', 'listing_ids', 'transaction_ids'):
            self.assertIn('name="%s"' % name, arch, name)
