"""A tenant's balance due is in the company currency, whatever their leases use.

``rental_balance_due`` added up each invoiced obligation's outstanding amount
in its lease's own currency and showed the total in the company currency, so a
tenant with one lease in the company currency and another in a foreign one saw
a meaningless mixed figure on the Tenants list and the contact's Rental tab.
Each outstanding amount is now converted at today's rate before it is added,
as Odoo converts a partner's receivable.
"""

from dateutil.relativedelta import relativedelta

from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestTenantBalanceCurrency(LeaseCase):

    def _foreign_currency(self):
        foreign = self.env.ref('base.EUR')
        if foreign == self.currency:
            foreign = self.env.ref('base.USD')
        foreign.active = True
        self.env['res.currency.rate'].create({
            'currency_id': foreign.id, 'company_id': self.company.id,
            'name': self.today - relativedelta(days=1), 'rate': 0.5,
        })
        return foreign

    def _invoiced_lease(self, unit, currency, rent):
        lease = self.activate(self.make_lease(
            prop=unit, start=self.today, end=self.today + relativedelta(years=1),
            rent=rent, use_billing_engine=True, currency_id=currency.id))
        lease.action_generate_billing_schedule()
        first = lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        return first

    def test_balance_converts_foreign_leases_to_the_company_currency(self):
        foreign = self._foreign_currency()
        local = self._invoiced_lease(self.unit_a, self.currency, 500.0)
        abroad = self._invoiced_lease(self.parking, foreign, 300.0)
        self.env.invalidate_all()
        self.assertEqual(abroad.currency_id, foreign)
        self.assertTrue(local.amount_residual and abroad.amount_residual)

        expected = local.amount_residual + foreign._convert(
            abroad.amount_residual, self.currency, self.company, self.today)
        self.assertEqual(self.tenant.rental_currency_id, self.currency)
        self.assertAlmostEqual(self.tenant.rental_balance_due, expected, places=2)
        self.assertNotAlmostEqual(
            self.tenant.rental_balance_due, local.amount_residual + abroad.amount_residual,
            places=2, msg="the foreign amount must not be added as if it were local")
