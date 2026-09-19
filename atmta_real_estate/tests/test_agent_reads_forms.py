"""A Leasing Agent can open every Rental record they work with.

Rental roles deliberately grant no accounting rights. A form that shows an
invoice, a credit note or a journal entry to such a user without a ``groups``
restriction makes the web client read that record, and the whole form fails
with an access error -- opening any lease raised "You are not allowed to access
'Journal Entry' (account.move) records" from its Invoices button.

Each form here is read field by field as a Leasing Agent, with the
specification the web client sends, on records that carry real invoices,
obligations and settlements, so an empty link cannot hide a failure.
"""

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase

LEASE_FORM = 'atmta_real_estate.view_realestate_contract_form'


def _spec(env, model, node):
    field = env[model]._fields.get(node.get('name'))
    if field is None:
        return None
    if field.type in ('one2many', 'many2many'):
        sub = {}
        for child in node.iter('field'):
            if child is node:
                continue
            child_field = env[field.comodel_name]._fields.get(child.get('name'))
            if child_field is not None:
                sub[child.get('name')] = ({'fields': {'display_name': {}}}
                                          if child_field.type == 'many2one' else {})
        return {'fields': sub or {'display_name': {}}}
    if field.type == 'many2one':
        return {'fields': {'display_name': {}}}
    return {}


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestAgentReadsForms(LeaseCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = new_test_user(
            cls.env, login='agent_reads_forms', company_id=cls.company.id,
            groups='base.group_user,atmta_real_estate.group_rental_agent,'
                   'atmta_real_estate.group_rental_all_portfolios')

    def _unreadable(self, record, view=None):
        """Every field on the record's form that the agent cannot read."""
        record = record.with_user(self.agent)
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
                failures.append('%s.%s: %s' % (record._name, node.get('name'),
                                               str(exc).splitlines()[0]))
            self.env.invalidate_all()
        return failures

    def test_a_leasing_agent_can_read_every_form_they_open(self):
        start = self.today.replace(day=1) - relativedelta(months=3)
        lease = self.activate(self.make_lease(
            prop=self.unit_a, start=start, end=start + relativedelta(years=1, days=-1),
            rent=1000.0, use_billing_engine=True, user_id=self.agent.id))
        lease.action_generate_billing_schedule()
        lease.action_invoice_due_obligations()
        obligation = lease.contract_payment_ids.filtered('move_id')[:1]
        self.assertTrue(obligation, "the fixture needs an invoiced obligation")

        deposit = self.env['realestate.contract.deposit'].create({
            'contract_id': lease.id, 'partner_id': self.tenant.id,
            'requested_amount': 2000.0,
        })
        renewal = self.env['realestate.contract.renewal'].create({
            'contract_id': lease.id,
            'proposed_start_date': lease.end_date + relativedelta(days=1),
            'proposed_end_date': lease.end_date + relativedelta(years=1),
            'proposed_rent': 1050.0,
        })
        amendment = self.env['realestate.contract.amendment'].create({
            'contract_id': lease.id, 'amendment_type': 'rent_change',
            'effective_date': self.today + relativedelta(months=1),
            'new_rent': 1100.0, 'reason': 'Review',
        })
        move_in = self.env['realestate.move.in'].create({
            'contract_id': lease.id, 'property_id': self.unit_a.id,
            'scheduled_date': self.today, 'tenant_acknowledged': True,
        })
        cutoff = self.today.replace(day=1) + relativedelta(months=1)
        termination = self.env['realestate.contract.termination'].create({
            'contract_id': lease.id, 'requested_end_date': cutoff,
            'effective_date': cutoff, 'reason': 'tenant_notice', 'requested_by': 'tenant',
        })
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()

        failures = []
        for record, view in (
                (lease, LEASE_FORM), (obligation, None), (deposit, None),
                (renewal, None), (amendment, None), (move_in, None),
                (termination, None), (self.unit_a, None), (self.tenant, None)):
            failures += self._unreadable(record, view)
        self.assertFalse(failures, '\n'.join(failures))
