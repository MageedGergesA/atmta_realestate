"""Property and Rental Managers can open the records they work with.

Like Leasing Agents, the Rental manager roles grant no accounting rights. A
form that makes the web client read a payment, journal entry, journal or
account without a ``groups`` restriction fails for them with an access error.
Each form is read field by field as the role that uses it, on records carrying
real receipts, refunds, settlements, readings and turns.
"""

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase
from .test_agent_reads_forms import LEASE_FORM, _spec


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestManagersReadForms(LeaseCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.property_manager = new_test_user(
            cls.env, login='pm_reads_forms', company_id=cls.company.id,
            groups='base.group_user,atmta_real_estate.group_property_manager')
        cls.rental_manager = new_test_user(
            cls.env, login='rm_reads_forms', company_id=cls.company.id,
            groups='base.group_user,atmta_real_estate.group_rental_manager')

    def _unreadable(self, record, user, view=None):
        record = record.with_user(user)
        view_id = self.env.ref(view).id if view else False
        root = etree.fromstring(record.get_view(view_id, 'form')['arch'])
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
                failures.append('%s %s.%s: %s' % (user.login, record._name, node.get('name'),
                                                  str(exc).splitlines()[0]))
            self.env.invalidate_all()
        return failures

    def test_a_property_manager_reads_the_occupancy_forms(self):
        end = self.today + relativedelta(days=20)
        lease = self.activate(self.make_lease(
            prop=self.unit_a, start=self.today - relativedelta(months=11), end=end))
        move_in = self.env['realestate.move.in'].create({
            'contract_id': lease.id, 'property_id': self.unit_a.id,
            'scheduled_date': self.today, 'tenant_acknowledged': True,
        })
        move_in.action_complete()
        termination = self.env['realestate.contract.termination'].create({
            'contract_id': lease.id, 'requested_end_date': end,
            'effective_date': end, 'reason': 'expiry',
        })
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()
        move_out = self.env['realestate.move.out'].create({
            'contract_id': lease.id, 'property_id': self.unit_a.id,
            'scheduled_date': self.today, 'tenant_acknowledged': True,
        })
        move_out.action_start_inspection()
        move_out.action_complete()
        self.assertTrue(move_out.unit_turn_id)

        meter = self.env['realestate.property.meter'].create({
            'name': 'EL-READS-001', 'property_id': self.unit_b.id,
        })
        self.env['realestate.property.meter.reading'].create({
            'meter_id': meter.id, 'current_reading': 120.0,
        })
        request = self.env['realestate.maintenance.request'].create({
            'name': 'Leaking tap', 'property_id': self.unit_b.id,
        })
        request.action_start_progress()

        failures = []
        for record in (move_in, move_out, move_out.unit_turn_id, meter, request):
            failures += self._unreadable(record, self.property_manager)
        self.assertFalse(failures, '\n'.join(failures))

    def test_a_rental_manager_reads_the_money_forms(self):
        start = self.today.replace(day=1) - relativedelta(months=3)
        lease = self.activate(self.make_lease(
            prop=self.unit_b, start=start, end=start + relativedelta(years=1, days=-1),
            rent=1000.0, use_billing_engine=True))
        lease.action_generate_billing_schedule()
        lease.action_invoice_due_obligations()
        obligation = lease.contract_payment_ids.filtered('move_id')[:1]
        self.assertTrue(obligation)

        deposit = self.env['realestate.contract.deposit'].create({
            'contract_id': lease.id, 'partner_id': self.tenant.id,
            'requested_amount': 2000.0,
        })
        deposit.action_register_receipt()
        deposit.action_refund(amount=500.0, reason='Cleaning deducted.')

        cutoff = self.today.replace(day=1) + relativedelta(months=1)
        termination = self.env['realestate.contract.termination'].create({
            'contract_id': lease.id, 'requested_end_date': cutoff,
            'effective_date': cutoff, 'reason': 'tenant_notice', 'requested_by': 'tenant',
        })
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()

        failures = []
        for record, view in ((lease, LEASE_FORM), (obligation, None), (deposit, None),
                             (termination, None)):
            failures += self._unreadable(record, self.rental_manager, view)
        self.assertFalse(failures, '\n'.join(failures))
