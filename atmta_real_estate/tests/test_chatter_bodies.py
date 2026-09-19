"""Chatter notes render as HTML, and the values they quote stay escaped.

``mail.thread.message_post`` escapes a plain ``str`` body, so a note written
as ``_("... <b>%s</b> ...")`` showed its tags as text. The notes now build
their markup with ``Markup``, which also escapes every value put into it.
"""

from dateutil.relativedelta import relativedelta

from odoo.tests.common import tagged

from .common import LeaseCase

HOSTILE = '<script>alert(1)</script>'


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestChatterBodies(LeaseCase):

    def _body(self, record, needle):
        messages = self.env['mail.message'].search(
            [('model', '=', record._name), ('res_id', '=', record.id)], order='id desc')
        for message in messages:
            if needle in str(message.body):
                return str(message.body)
        self.fail('No note containing %r on %s' % (needle, record.display_name))

    def assertRendered(self, body, *tags):
        for tag in tags:
            self.assertIn(tag, body)
        for escaped in ('&lt;b&gt;', '&lt;ul&gt;', '&lt;li&gt;', '&lt;br'):
            self.assertNotIn(escaped, body)
        self.assertNotIn('<script', body)

    def test_lease_status_note(self):
        lease = self.make_lease(prop=self.unit_b)
        lease.action_submit_for_approval()
        self.assertRendered(self._body(lease, 'Lease status'), '<b>')

    def test_primary_tenant_note_escapes_the_name(self):
        lease = self.make_lease(prop=self.unit_b)
        partner = self.env['res.partner'].create({'name': 'Tenant ' + HOSTILE})
        party = self.env['realestate.contract.party'].create({
            'contract_id': lease.id,
            'partner_id': partner.id,
            'role': 'co_tenant',
        })
        party.action_make_primary()
        body = self._body(lease, 'Primary tenant')
        self.assertRendered(body, '<b>')
        self.assertIn('&lt;script&gt;', body)

    def test_availability_override_note_escapes_the_reason(self):
        self.unit_a.write({
            'availability_override': 'force_available',
            'availability_override_reason': 'Held ' + HOSTILE,
        })
        body = self._body(self.unit_a, 'Availability override')
        self.assertRendered(body, '<b>', '<br')
        self.assertIn('&lt;script&gt;', body)

    def test_incentive_approval_note_escapes_the_reason(self):
        lease = self.make_lease(prop=self.unit_b)
        incentive = self.env['realestate.contract.incentive'].create({
            'contract_id': lease.id,
            'name': 'Launch discount',
            'incentive_type': 'percent_discount',
            'percentage': 10.0,
            'start_date': lease.start_date,
            'end_date': lease.start_date + relativedelta(months=2, days=-1),
            'reason': 'Promotion ' + HOSTILE,
        })
        incentive.action_approve()
        body = self._body(lease, 'approved')
        self.assertRendered(body, '<b>')
        self.assertIn('&lt;script&gt;', body)

    def test_amendment_applied_note(self):
        start = self.today.replace(day=1)
        lease = self.make_lease(prop=self.unit_b, start=start,
                                end=start + relativedelta(years=1, days=-1),
                                rent=1000.0, use_billing_engine=True)
        self.activate(lease)
        lease.action_generate_billing_schedule()
        amendment = self.env['realestate.contract.amendment'].create({
            'contract_id': lease.id,
            'amendment_type': 'rent_change',
            'effective_date': start + relativedelta(months=6),
            'new_rent': 1200.0,
            'reason': 'Review ' + HOSTILE,
        })
        amendment.action_propose()
        amendment.action_approve()
        amendment.action_sign()
        amendment.action_apply()
        body = self._body(lease, 'applied, effective')
        self.assertRendered(body, '<b>', '<br')
        self.assertIn(amendment.name, body)

    def test_price_rule_conversion_note_is_a_list(self):
        start = self.today.replace(day=1)
        lease = self.make_lease(start=start, end=start + relativedelta(years=3, days=-1),
                                rent=1000.0)
        permanent = self.env['realestate.contract.increment.rule'].create({
            'start_month': 12, 'increase_type': 'percent', 'increase_value': 5.0,
            'priority': 10, 'duration_months': 0, 'discount': False,
        })
        temporary = self.env['realestate.contract.increment.rule'].create({
            'start_month': 6, 'increase_type': 'fixed', 'increase_value': 200.0,
            'priority': 5, 'duration_months': 6, 'discount': False,
        })
        lease.increment_rule_ids = permanent | temporary
        lease._convert_legacy_price_rules()
        body = self._body(lease, 'Legacy price rules carried over')
        self.assertRendered(body, '<ul>', '<li>')

    def test_renewal_note_links_the_new_lease(self):
        """The link showed as '<a href=...>' text on the renewed lease."""
        start = self.today.replace(day=1)
        end = start + relativedelta(years=1, days=-1)
        lease = self.activate(self.make_lease(start=start, end=end, rent=1000.0))
        renewal = self.env['realestate.contract.renewal'].create({
            'contract_id': lease.id,
            'proposed_start_date': end + relativedelta(days=1),
            'proposed_end_date': end + relativedelta(years=1),
            'proposed_rent': 1100.0,
        })
        renewal.action_propose()
        renewal.action_approve()
        renewal.action_accept()
        renewal.action_create_renewal_lease()
        body = self._body(lease, 'Renewed by')
        self.assertIn('<a ', body)
        self.assertIn("data-oe-id='%s'" % renewal.new_contract_id.id, body.replace('"', "'"))
        self.assertNotIn('&lt;a', body)
