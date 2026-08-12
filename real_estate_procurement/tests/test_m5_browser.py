# -*- coding: utf-8 -*-
"""M5 — the real-browser gate for sourcing, plus the parts a browser cannot say.

The tour drives what a buyer actually sees. Two of the milestone's most
important guarantees cannot be expressed through the UI at all — the native
compare mutation and the `skip_alternative_check` bypass — so they are asserted
here in Python around the tour, against the same seeded tender the browser
opened. Splitting them that way is deliberate: a gate that only clicked buttons
would miss both.
"""

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import HttpCase, tagged

from .common import M5Common


class M5BrowserCommon(M5Common, HttpCase):
    """One seeded tender, published, bid on, amended — the browser's subject."""

    def _seed(self):
        self._budget([(self.concrete, 10_000_000.0)])
        # Two requisition lines, so the tour can prove that consolidating
        # 50 + 50 into one 100-unit tender line does not lose either source.
        self.request = self._request(
            self.project,
            [(self.product, 50.0), (self.product, 50.0)],
            line_defaults={'cost_code_id': self.concrete.id,
                           'wbs_id': self.wbs.id,
                           'estimated_unit_cost': 30_000.0})
        self.request.action_submit()
        self.request.action_approve()

        self.vendor_a.name = 'Gate Vendor A'
        self.vendor_b.name = 'Gate Vendor B'
        self.vendor_c.name = 'Gate Vendor C'

        event = self._event(self.request, title='Browser Gate Tender')
        # The tour logs in as `admin`, and the sourcing action opens with
        # "My Sourcing" applied — which is the right default for a buyer and
        # the reason an event owned by the fixture user was invisible on the
        # first run of this gate. The buyer opening their own tender is also
        # the realistic case.
        event.buyer_id = self.env.ref('base.user_admin')
        self._publish(event, [self.vendor_a, self.vendor_b, self.vendor_c])

        self.bid_a = self._bid(event.invitation_ids[0], 3_200_000.0)
        self.bid_b = self._bid(event.invitation_ids[1], 3_050_000.0)

        self.env['realestate.procurement.sourcing.clarification'].create({
            'event_id': event.id,
            'clarification_type': 'vendor_question',
            'partner_id': self.vendor_a.id,
            'visibility': 'vendor_only',
            'question': 'Is the rate inclusive of fixing?',
        })
        event.action_issue_addendum(reason='Programme extended by two weeks')
        return event

    def _browser_user(self, *groups):
        user = self.env.ref('base.user_admin')
        for xmlid in groups:
            user.groups_id |= self.env.ref(xmlid)
        self.env.flush_all()
        return user


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5SourcingBrowser(M5BrowserCommon):

    def test_the_sourcing_screens_open_and_tell_the_truth(self):
        event = self._seed()
        self._browser_user(
            'real_estate_procurement.group_procurement_manager',
            'atmta_real_estate.group_realestate_user')
        self.env.flush_all()

        self.start_tour('/odoo', 'procurement_sourcing_tour', login='admin',
                        timeout=300)

        # The browser closed with the evidence intact.
        self.assertEqual(self.bid_a.amount_untaxed, 3_200_000.0)
        self.assertEqual(event.current_version_id.revision, 1)

    def test_native_compare_mutation_leaves_the_evidence_alone(self):
        """M5's central regression, exercised the way Odoo exercises it.

        `action_choose` is what the native compare view calls, and it clears
        quantities on every competing alternative line. The browser cannot
        express this against a specific competitor, so it is driven here.
        """
        event = self._seed()
        orders = event.invitation_ids.purchase_order_id
        chosen = orders[0].order_line[:1]
        competitor_line = orders[1].order_line[:1]
        before = (self.bid_b.amount_untaxed,
                  self.bid_b.line_ids[:1].quantity,
                  self.bid_b.line_ids[:1].price_unit)

        chosen.action_choose()
        self.bid_b.invalidate_recordset()
        competitor_line.invalidate_recordset()

        self.assertEqual(competitor_line.product_qty, 0.0,
                         "The native mutation did not happen, so this test "
                         "is not exercising what it claims.")
        self.assertEqual((self.bid_b.amount_untaxed,
                          self.bid_b.line_ids[:1].quantity,
                          self.bid_b.line_ids[:1].price_unit), before,
                         "A buyer using the compare view rewrote what a "
                         "vendor submitted.")

    def test_the_tender_rfq_refuses_to_confirm_by_any_route(self):
        event = self._seed()
        order = event.invitation_ids[0].purchase_order_id
        reserved = self._reserved(self.project, self.concrete)

        with self.assertRaises(UserError):
            order.button_confirm()
        with self.assertRaises(UserError):
            order.with_context(skip_alternative_check=True).button_confirm()

        self.assertEqual(order.state, 'draft')
        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._actual(self.project), 0.0)
        self.assertEqual(self._reserved(self.project, self.concrete), reserved)

    def test_the_whole_flow_moved_no_money(self):
        event = self._seed()
        event.action_close()

        self.assertEqual(self._reserved(self.project, self.concrete),
                         3_000_000.0)
        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._actual(self.project), 0.0)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5BrowserRoles(M5BrowserCommon):
    """A restricted requester cannot reach bid evidence through the client."""

    def test_a_requester_is_refused_the_bid_action_over_the_web(self):
        event = self._seed()
        requester = self.env['res.users'].create({
            'name': 'Gate Requester', 'login': 'gate.requester',
            'password': 'gate.requester.pw',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_procurement.group_procurement_requester').id,
            ])],
        })
        self.env.flush_all()
        self.authenticate('gate.requester', 'gate.requester.pw')

        # The same call the web client makes when an action opens a list.
        response = self.url_open(
            '/web/dataset/call_kw',
            data=('{"jsonrpc":"2.0","method":"call","params":{'
                  '"model":"realestate.procurement.bid.response",'
                  '"method":"web_search_read","args":[],'
                  '"kwargs":{"domain":[],"specification":{"amount_untaxed":{}}}'
                  '}}'),
            headers={'Content-Type': 'application/json'})
        payload = response.json()

        self.assertIn('error', payload,
                      "A requester read bid amounts over RPC.")
        self.assertNotIn('3200000', response.text.replace(',', ''),
                         "A competitor's price reached a requester in an RPC "
                         "payload.")
        # And the record rules agree with the RPC layer.
        with self.assertRaises(AccessError):
            self.env['realestate.procurement.bid.response'].with_user(
                requester).search([('event_id', '=', event.id)])


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5BrowserRTL(M5BrowserCommon):
    """RTL, proved through Odoo's own mechanism before anything is asserted.

    Odoo 18's backend does **not** stamp `html[dir]`: the only `t-att-dir` in
    the source is in report templates. Backend right-to-left is delivered by
    serving the rtlcss-generated `.rtl` asset bundle, so that is what the tour
    checks. Asserting `body direction` instead reports `ltr` on a working RTL
    session — which is the root cause of the RTL failures in
    `atmta_real_estate`, `real_estate_brokerage` and `real_estate_checks`, and
    is recorded here for the sessions that own them rather than fixed from M5.
    """

    def test_the_sourcing_screens_render_right_to_left(self):
        self._seed()
        self._browser_user(
            'real_estate_procurement.group_procurement_manager',
            'atmta_real_estate.group_realestate_user')

        # `_activate_lang` is Odoo's own activation path — it installs the
        # language properly rather than flipping `active` on a row, which
        # leaves the session resolving to English. Getting this wrong is what
        # produced `html[dir]=null session.lang=undefined` on the first run of
        # this gate, and it is the same signature the RTL tests in three other
        # modules currently fail with.
        self.env['res.lang']._activate_lang('ar_001')
        arabic = self.env['res.lang'].with_context(active_test=False).search(
            [('code', '=', 'ar_001')], limit=1)
        if not arabic or not arabic.active:
            self.skipTest("Arabic could not be activated in this database.")
        # Direction comes from the language, so proving it here means a tour
        # failure afterwards is a layout finding rather than a session that
        # never went RTL in the first place.
        self.assertEqual(arabic.direction, 'rtl')
        self.env.ref('base.user_admin').lang = arabic.code
        self.env.flush_all()

        self.start_tour('/odoo', 'procurement_sourcing_rtl_tour',
                        login='admin', timeout=300)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM5BrowserTablet(M5BrowserCommon):

    def test_the_sourcing_screens_fit_a_tablet(self):
        self._seed()
        self._browser_user(
            'real_estate_procurement.group_procurement_manager',
            'atmta_real_estate.group_realestate_user')
        self.env.flush_all()

        self.browser_size = '1024x768'
        self.start_tour('/odoo', 'procurement_sourcing_tablet_tour',
                        login='admin', timeout=300)
