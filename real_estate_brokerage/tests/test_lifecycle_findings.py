# -*- coding: utf-8 -*-
"""Regressions for the brokerage lifecycle run (Sept 2026).

Each test reproduces one defect the end-to-end cycle found: a deal walked from
mandate to commission the way an office actually does it, through the forms.
"""

from datetime import datetime, time, timedelta

from lxml import etree

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import Form, tagged
from odoo.tools.safe_eval import safe_eval

from .common import BrokerageCommon, module_installed


@tagged('post_install', '-at_install')
class TestLifecycleFindings(BrokerageCommon):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _accepted_offer(self, **offer_vals):
        listing = self._mandated_listing(minimum_price=500000.0)
        listing.action_activate()
        offer = self._offer(listing, amount=950000.0, **offer_vals)
        offer.action_accept()
        return listing, offer

    def _header_button_visible(self, record, method):
        """Whether the form's header shows `method` for this record's state."""
        arch = record.get_views([(None, 'form')])['views']['form']['arch']
        doc = etree.fromstring(arch)
        buttons = doc.xpath("//header//button[@name='%s']" % method)
        self.assertTrue(buttons, "%s has no %s button" % (record._name, method))
        return any(
            not btn.get('invisible')
            or not safe_eval(btn.get('invisible'), {'state': record.state})
            for btn in buttons)

    # ------------------------------------------------------------------
    # 1. Acceptance on a listing whose mandate has lapsed
    # ------------------------------------------------------------------
    def test_offer_on_external_listing_without_active_mandate_is_refused(self):
        listing = self._mandated_listing(minimum_price=500000.0)
        listing.action_activate()
        mandate = listing.active_mandate_id
        offer = self._offer(listing, amount=950000.0)
        today = fields.Date.today()
        self.env.cr.execute(
            "UPDATE realestate_listing_mandate SET date_start=%s, date_end=%s "
            "WHERE id=%s", (today - timedelta(days=40),
                            today - timedelta(days=1), mandate.id))
        mandate.invalidate_recordset()
        self.env['realestate.listing.mandate']._cron_expire_mandates()
        listing.invalidate_recordset()
        self.assertEqual(mandate.state, 'expired')
        self.assertFalse(listing.active_mandate_id)

        with self.assertRaises(UserError):
            offer.action_accept()
        self.assertNotEqual(offer.state, 'accepted')

    # ------------------------------------------------------------------
    # 2. Dashboard
    # ------------------------------------------------------------------
    def _dashboard(self):
        return self.env['realestate.brokerage.dashboard'].get_data()

    def test_dashboard_counts_crm_opportunities(self):
        before = self._dashboard()['kpis']
        opp = self._opportunity(name='Dashboard Hot Opportunity',
                                priority='3')
        won = self._opportunity(name='Dashboard Won Opportunity')
        won.action_set_won()

        data = self._dashboard()
        self.assertEqual(data['kpis']['open_leads'],
                         before['open_leads'] + 1)
        self.assertEqual(data['kpis']['hot_leads'], before['hot_leads'] + 1)
        self.assertGreater(data['kpis']['conversion_rate'], 0.0)
        self.assertIn(opp.id, [lead['id'] for lead in data['recent_leads']])

    def test_dashboard_ignores_clawed_back_commissions(self):
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)
        line.action_approve()
        line.action_create_vendor_bill()
        line.action_mark_paid()
        self.assertTrue(line.paid)
        before = self._dashboard()

        line.action_clawback('deal_collapsed')

        after = self._dashboard()
        self.assertAlmostEqual(after['kpis']['commissions_mtd'],
                               before['kpis']['commissions_mtd'] - line.amount)
        recipient = line.partner_id.name
        self.assertNotIn(recipient, dict(after['top_agents']))

    def test_dashboard_counts_confirmed_viewings_through_sunday(self):
        today = fields.Date.today()
        sunday = today - timedelta(days=today.weekday()) + timedelta(days=6)
        before = self._dashboard()['kpis']['viewings_this_week']
        self._viewing(scheduled_at=datetime.combine(sunday, time(15, 0)),
                      state='confirmed')

        self.assertEqual(self._dashboard()['kpis']['viewings_this_week'],
                         before + 1)

    # ------------------------------------------------------------------
    # 3. Matching on any channel
    # ------------------------------------------------------------------
    def test_any_channel_matches_available_resale_units(self):
        resale = self._unit(released=False, project_id=False, phase_id=False,
                            price=1000000.0)
        self.assertEqual(resale.state, 'available')
        self.assertFalse(resale.is_available_for_sale)

        any_lead = self._opportunity(re_market='any')
        self.assertIn(resale, any_lead._re_candidate_properties())

        developer_lead = self._opportunity(re_market='developer')
        self.assertNotIn(resale, developer_lead._re_candidate_properties())

    # ------------------------------------------------------------------
    # 4. Cancelling a transaction releases its offer
    # ------------------------------------------------------------------
    def test_cancelling_a_transaction_closes_its_accepted_offer(self):
        listing, offer = self._accepted_offer()
        offer.action_create_transaction()
        txn = listing.transaction_id

        txn.action_cancel('buyer_withdrew')

        self.assertNotEqual(offer.state, 'accepted')
        self.assertTrue(offer.rejection_reason)

    # ------------------------------------------------------------------
    # 5. Commission to an ineligible broker
    # ------------------------------------------------------------------
    def test_commission_cannot_be_billed_to_a_suspended_broker(self):
        txn = self._closed_deal(gross_percentage=2.0)
        broker = self._broker()
        line = self._commission(txn, partner_id=broker.id,
                                calculation_method='share',
                                share_percentage=50.0)
        line.action_approve()
        broker.action_suspend_broker()

        with self.assertRaises(UserError):
            line.action_create_vendor_bill()
        self.assertFalse(line.bill_id)

    # ------------------------------------------------------------------
    # 6. Create Offer from a viewing on a CRM opportunity
    # ------------------------------------------------------------------
    def test_create_offer_from_viewing_uses_the_opportunity(self):
        viewing = self._viewing()
        self.assertFalse(viewing.lead_id)

        action = viewing.action_create_offer()

        self.assertEqual(action['context']['default_crm_lead_id'],
                         viewing.crm_lead_id.id)

    # ------------------------------------------------------------------
    # 7. A sold external listing stops being advertised
    # ------------------------------------------------------------------
    def test_closing_an_external_deal_unpublishes_the_listing(self):
        txn = self._open_deal(gross_percentage=2.0)
        txn.listing_id.publication_state = 'published'
        txn.action_sign_contract()

        txn.action_close()

        self.assertEqual(txn.listing_id.state, 'sold')
        self.assertEqual(txn.listing_id.publication_state, 'unpublished')

    # ------------------------------------------------------------------
    # 9. Seller on a transaction promoted from an offer
    # ------------------------------------------------------------------
    def test_transaction_from_offer_carries_the_seller(self):
        listing, offer = self._accepted_offer()

        offer.action_create_transaction()

        self.assertEqual(listing.transaction_id.seller_id, self.seller)

    # ------------------------------------------------------------------
    # 13. Closing today records today
    # ------------------------------------------------------------------
    def test_closing_today_does_not_stamp_the_proposed_future_date(self):
        today = fields.Date.today()
        planned = today + timedelta(days=30)
        listing, offer = self._accepted_offer(proposed_closing_date=planned)
        offer.action_create_transaction()
        txn = listing.transaction_id
        txn.transaction_type = 'in_house'
        txn.action_sign_contract()

        txn.action_close()

        self.assertEqual(txn.closing_date, today)
        self.assertEqual(listing.sold_date, today)
        self.assertEqual(offer.proposed_closing_date, planned)

    # ------------------------------------------------------------------
    # 14. A second clawback
    # ------------------------------------------------------------------
    def test_second_clawback_says_already_clawed_back(self):
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)
        line.action_approve()
        line.action_create_vendor_bill()
        line.action_clawback('deal_collapsed')

        with self.assertRaises(UserError) as caught:
            line.action_clawback('overpaid')
        self.assertIn('already been clawed back', str(caught.exception))

    # ------------------------------------------------------------------
    # UX
    # ------------------------------------------------------------------
    def test_u1_mandate_can_be_activated_from_pending_signature(self):
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.seller.id)
        mandate = self._mandate(listing)
        mandate.action_submit_for_signature()

        self.assertTrue(self._header_button_visible(mandate,
                                                    'action_activate'))

    def test_u2_confirmed_viewing_can_be_completed_or_cancelled(self):
        viewing = self._viewing()
        viewing.action_confirm()

        for method in ('action_mark_completed', 'action_mark_no_show',
                       'action_cancel'):
            with self.subTest(method=method):
                self.assertTrue(self._header_button_visible(viewing, method))

    def test_u3_outcome_can_be_recorded_on_a_scheduled_viewing(self):
        viewing = self._viewing()
        self.assertEqual(viewing.state, 'scheduled')
        self.assertTrue(self._header_button_visible(viewing,
                                                    'action_mark_completed'))

        with Form(viewing) as form:
            form.outcome = 'interested'
        viewing.action_mark_completed()

        self.assertEqual(viewing.state, 'completed')

    def test_u4_cancellation_reason_can_be_given_before_cancelling(self):
        txn = self._open_deal(gross_percentage=2.0)

        with Form(txn) as form:
            form.cancellation_reason_code = 'buyer_withdrew'
        txn.action_cancel()

        self.assertEqual(txn.state, 'cancelled')

    def test_u5_app_lost_reasons_menu_edits_crm_lost_reasons(self):
        if not module_installed(self.env, 'atmta_brokerage_app'):
            self.skipTest('atmta_brokerage_app is not installed')
        menu = self.env.ref('atmta_brokerage_app.menu_cfg_lost_reasons')
        self.assertEqual(menu.action.res_model, 'crm.lost.reason')
