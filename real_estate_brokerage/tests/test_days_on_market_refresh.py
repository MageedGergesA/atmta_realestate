# -*- coding: utf-8 -*-
"""Days on market keeps counting after the day it was last written.

``days_on_market`` is stored, because the listing list, kanban and the
dashboard's average read it, but it is computed from today's date, and a stored
compute only reruns when the listing changes. Nothing refreshed it, so a
listing activated three weeks ago and not touched since still showed the days
it had when it was last saved.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import tagged

from .common import BrokerageCommon


@tagged('post_install', '-at_install')
class TestDaysOnMarketRefresh(BrokerageCommon):

    def _listed_days_ago(self, listing, days):
        """Move the list date in the database only, as time passing would."""
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE realestate_listing SET list_date = %s WHERE id = %s",
            (fields.Date.today() - timedelta(days=days), listing.id))
        self.env.invalidate_all()

    def test_an_active_listing_keeps_counting(self):
        listing = self._listing(activate=True)
        self.assertEqual(listing.days_on_market, 0)
        self._listed_days_ago(listing, 21)
        self.assertEqual(listing.days_on_market, 0,
                         "a stored compute does not notice the days passing by itself")

        self.env['realestate.listing'].cron_expire_listings()
        self.env.invalidate_all()
        self.assertEqual(listing.days_on_market, 21)

    def test_a_closed_listing_stays_at_its_final_count(self):
        listing = self._listing(activate=True)
        listing.action_withdraw()
        final = listing.days_on_market
        self._listed_days_ago(listing, 0)
        self.env.cr.execute(
            "UPDATE realestate_listing SET days_on_market = %s WHERE id = %s",
            (final + 7, listing.id))
        self.env.invalidate_all()

        self.env['realestate.listing'].cron_expire_listings()
        self.env.invalidate_all()
        self.assertEqual(listing.days_on_market, final + 7,
                         "a listing whose end date is set is not recomputed daily")
