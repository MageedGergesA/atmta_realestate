# -*- coding: utf-8 -*-
"""M7 — the award screens in a real browser, at three viewports.

The tour walks an approved award through the actual menu. Two guarantees a
browser cannot express are asserted in Python around it: that an approved award
authorises exactly its own orders and nothing else, and that the position has
not moved — approval authorises, confirming commits.

Desktop, an Arabic RTL session and a 768x1024 tablet with touch, and nothing is
relaxed for the harder two. `rtlcss` is installed on this host, so the RTL run
is against genuinely mirrored stylesheets rather than a bundle that merely
carries the name.
"""

from odoo.exceptions import UserError
from odoo.tests.common import HttpCase, tagged

from .test_m7_award import M7Common


class M7BrowserCommon(M7Common, HttpCase):

    def _approved_award(self):
        self.vendor_a.name = 'Gulf Ready-Mix LLC'
        round_ = self._finalised_round()
        winner = round_.candidate_ids.filtered(
            lambda c: c.partner_id == self.vendor_a)[:1]
        award = self._award(round_, candidates=winner,
                            justification='Awarded on the evaluated outcome')
        award.action_submit()
        award.with_user(self._second_manager('m7.brw.approver')).action_approve()
        return award

    def _browser_admin(self):
        admin = self.env.ref('base.user_admin')
        admin.groups_id |= (
            self.env.ref('real_estate_procurement.group_procurement_manager')
            | self.env.ref('real_estate_procurement.group_evaluation_manager')
            | self.env.ref('atmta_real_estate.group_realestate_user'))
        self.env.flush_all()
        return admin


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7AwardBrowser(M7BrowserCommon):

    def test_the_award_screens_open_and_tell_the_truth(self):
        award = self._approved_award()
        self._browser_admin()

        self.start_tour('/odoo', 'procurement_award_tour', login='admin',
                        timeout=300)

        self.assertEqual(award.state, 'approved')

    def test_an_approved_award_authorises_only_its_own_orders(self):
        award = self._approved_award()
        awarded = award.line_ids.purchase_order_id
        others = self.event.invitation_ids.purchase_order_id - awarded

        self.assertTrue(awarded._award_authorisation())
        for order in others:
            self.assertFalse(order._award_authorisation())
            with self.assertRaises(UserError):
                order.button_confirm()

    def test_approving_moved_no_money(self):
        self._approved_award()

        self.assertEqual(self._position(), (3_000_000.0, 0.0, 0.0),
                         "Approval committed the budget. Approval "
                         "authorises; confirming commits.")


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7AwardBrowserRtl(M7BrowserCommon):

    def _arabic_admin(self):
        self.env['res.lang']._activate_lang('ar_001')
        admin = self._browser_admin()
        admin.lang = 'ar_001'
        self.env.flush_all()
        return admin

    def test_the_award_screens_work_in_an_rtl_session(self):
        award = self._approved_award()
        self._arabic_admin()

        self.start_tour('/odoo', 'procurement_award_tour', login='admin',
                        timeout=300)

        self.assertEqual(award.state, 'approved')


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7AwardBrowserTablet(M7BrowserCommon):
    """The same walk at tablet width, with touch on.

    Nothing is relaxed. The award form is notebook-heavy and the tour still has
    to reach the Approval tab — a notebook that collapsed its tabs off-screen
    at 768px would put the authorisation trail out of reach on the device a
    site manager actually carries.
    """

    browser_size = '768x1024'
    touch_enabled = True

    def test_the_award_screens_work_on_a_tablet(self):
        award = self._approved_award()
        self._browser_admin()

        self.start_tour('/odoo', 'procurement_award_tour', login='admin',
                        timeout=300)

        self.assertEqual(award.state, 'approved')
