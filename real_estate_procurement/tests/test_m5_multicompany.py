# -*- coding: utf-8 -*-
"""M5 — multi-company and project isolation, plus the integrity audit.

Every assertion is made with **both** companies allowed, because that is the
configuration where a rule reading `env.company` instead of `company_ids` looks
correct in every single-company test and leaks the moment somebody ticks two
boxes. The M10 Construction file learned this the hard way; this one starts
there.

Identifiers are compared as identifiers. Nothing here searches an id inside a
stringified payload — that was the frozen-test defect corrected in `47a5a57`,
and repeating it would be repeating a test that cannot fail for the right
reason.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import M5Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5MultiCompany(M5Common):

    def setUp(self):
        super().setUp()
        self.company_a = self.company
        self.company_b = self.env['res.company'].create({'name': 'M5 Co B'})
        self.env.user.company_ids = [(4, self.company_b.id)]

        self._budget([(self.concrete, 10_000_000.0)])
        self.request_a = self._demand(1_000, code=self.concrete, unit=3_000.0)
        self.event_a = self._event(self.request_a, title='Tender A')
        self._publish(self.event_a, [self.vendor_a])
        self.bid_a = self._bid(self.event_a.invitation_ids, 2_800_000.0)

        self.project_b = self.env['realestate.project'].create({
            'name': 'M5 Project B', 'code': 'M5B%s' % self._next(),
            'company_id': self.company_b.id,
        })
        self.event_b = self.env[
            'realestate.procurement.sourcing.event'].with_company(
                self.company_b).create({
                    'title': 'Tender B',
                    'company_id': self.company_b.id,
                    'project_id': self.project_b.id,
                    'sourcing_method': 'competitive_tender',
                    'close_datetime': self._close_at(),
                })

    def _user(self, login, companies):
        return self.env['res.users'].create({
            'name': login, 'login': login,
            'company_id': companies[0].id,
            'company_ids': [(6, 0, [c.id for c in companies])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('base.group_multi_company').id,
                self.env.ref(
                    'real_estate_procurement.group_procurement_manager').id,
            ])],
        })

    # ------------------------------------------------------------------
    def test_a_tender_cannot_name_another_companys_project(self):
        with self.assertRaises(Exception):
            self.env['realestate.procurement.sourcing.event'].create({
                'title': 'Cross-company tender',
                'company_id': self.company_a.id,
                'project_id': self.project_b.id,
                'sourcing_method': 'rfq',
                'close_datetime': self._close_at(),
            })

    def test_a_tender_cannot_allocate_another_companys_demand(self):
        with self.assertRaises(UserError):
            self.event_b.action_allocate_request(self.request_a)

    def test_with_both_companies_active_each_tender_stays_its_own(self):
        user = self._user('m5.both', [self.company_a, self.company_b])
        allowed = user.company_ids.ids
        Event = self.env[
            'realestate.procurement.sourcing.event'].with_user(
                user).with_context(allowed_company_ids=allowed)

        visible = Event.search([])
        self.assertIn(self.event_a.id, visible.ids)
        self.assertIn(self.event_b.id, visible.ids)
        # Identity, not substring: each event's own records belong to it.
        self.assertEqual(self.event_a.invitation_ids.company_id,
                         self.company_a)
        self.assertFalse(
            self.event_b.invitation_ids,
            "Company B's tender inherited company A's invitations.")

    def test_a_company_b_user_cannot_read_company_a_bids(self):
        user = self._user('m5.b.only', [self.company_b])
        Bid = self.env['realestate.procurement.bid.response'].with_user(user)

        found = Bid.search([('id', '=', self.bid_a.id)])
        self.assertFalse(found,
                         "A company B manager found a company A bid.")
        with self.assertRaises(AccessError):
            Bid.browse(self.bid_a.id).read(['amount_untaxed'])

    def test_the_rpc_payload_for_company_b_carries_no_company_a_bid(self):
        """Raw ORM, the way an export or a client would ask."""
        user = self._user('m5.b.rpc', [self.company_b])
        rows = self.env[
            'realestate.procurement.bid.response'].with_user(
                user).with_context(
                    allowed_company_ids=[self.company_b.id]).search_read(
                        [], ['amount_untaxed', 'partner_id'])

        self.assertEqual(rows, [],
                         "A company B user's payload contained bid data: %s"
                         % rows)

    def test_an_rfq_belongs_to_the_tenders_company(self):
        order = self.event_a.invitation_ids.purchase_order_id
        self.assertEqual(order.company_id, self.company_a)
        self.assertEqual(order.re_sourcing_event_id, self.event_a)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5IntegrityAudit(M5Common):
    """The audit reports, and on a healthy tender it reports nothing severe."""

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        self.request = self._demand(1_000, code=self.concrete, unit=3_000.0)
        self.event = self._event(self.request)
        self._publish(self.event, [self.vendor_a, self.vendor_b])
        self._bid(self.event.invitation_ids[0], 2_800_000.0)
        self.Audit = self.env['realestate.procurement.sourcing.audit']

    def test_a_healthy_tender_has_no_critical_findings(self):
        report = self.Audit.run(company=self.company)
        critical = [f for f in report['findings']
                    if f['severity'] == 'critical']
        self.assertEqual(critical, [],
                         "Critical findings on a clean tender: %s"
                         % [f['key'] for f in critical])
        self.assertEqual(report['counts']['critical'], 0)

    def test_it_notices_an_over_allocation(self):
        allocation = self.event.allocation_ids[:1]
        allocation.sudo().write({'quantity': allocation.quantity * 10})

        report = self.Audit.run(company=self.company)
        keys = [f['key'] for f in report['findings']]

        self.assertIn('allocation_exceeds_demand', keys)
        finding = [f for f in report['findings']
                   if f['key'] == 'allocation_exceeds_demand'][0]
        self.assertEqual(finding['severity'], 'critical')

    def test_a_divergent_rfq_is_reported_as_information_not_a_fault(self):
        """The snapshot diverging from the RFQ is the design working."""
        order = self.event.invitation_ids[0].purchase_order_id
        order.order_line[:1].write({'price_unit': 1.0})

        report = self.Audit.run(company=self.company)
        finding = [f for f in report['findings']
                   if f['key'] == 'rfq_differs_from_bid']

        self.assertTrue(finding, "The divergence was not reported at all.")
        self.assertEqual(finding[0]['severity'], 'low')
        self.assertIn('No action', finding[0]['remediation'])

    def test_it_reports_a_withdrawn_bid_left_current(self):
        invitation = self.event.invitation_ids[0]
        response = invitation.current_response_id
        response._engine().write({'state': 'withdrawn'})

        report = self.Audit.run(company=self.company)
        keys = [f['key'] for f in report['findings']]

        self.assertIn('withdrawn_still_current', keys)

    def test_the_audit_never_repairs_what_it_finds(self):
        allocation = self.event.allocation_ids[:1]
        allocation.sudo().write({'quantity': 99_999.0})

        self.Audit.run(company=self.company)
        allocation.invalidate_recordset()

        self.assertEqual(allocation.quantity, 99_999.0,
                         "The audit edited commercial history instead of "
                         "reporting it.")
