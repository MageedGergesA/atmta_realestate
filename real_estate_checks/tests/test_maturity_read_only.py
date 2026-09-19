# -*- coding: utf-8 -*-
"""Reading a cheque does not write its maturity bucket.

The stored ``maturity_bucket`` shared a compute with four live, unstored
figures (days to due, due, overdue for deposit, stale). The cheque list shows
those figures, so opening it recomputed the bucket too and wrote it inside a
read-only request: Odoo logged "cannot execute UPDATE in a read-only
transaction" and ran the request again with write access. The bucket is kept
current by the nightly maturity cron instead.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import tagged

from .common import ChecksCommon


@tagged('post_install', '-at_install')
class TestMaturityReadOnly(ChecksCommon):

    def _mature_by_days(self, check, days):
        """Move the due date in the database only, as time passing would."""
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE realestate_check SET due_date = %s WHERE id = %s",
            (fields.Date.context_today(check) - timedelta(days=days), check.id))
        self.env.invalidate_all()

    def test_reading_the_live_figures_writes_nothing(self):
        check = self._check(due_date=fields.Date.context_today(self.env['res.partner'])
                            + timedelta(days=30))
        self.assertEqual(check.maturity_bucket, 'd30')
        self._mature_by_days(check, 3)

        self.assertEqual(check.days_to_due, -3)
        self.assertTrue(check.is_due)
        self.assertFalse(self.env.cache.has_dirty_fields(check),
                         "reading a cheque's live figures must not queue a write")
        self.assertEqual(check.maturity_bucket, 'd30',
                         "the stored bucket waits for the nightly refresh")

    def test_the_nightly_refresh_still_moves_the_bucket(self):
        check = self._check(due_date=fields.Date.context_today(self.env['res.partner'])
                            + timedelta(days=30))
        self._mature_by_days(check, 3)
        self.env['realestate.check']._cron_refresh_maturity()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(check.maturity_bucket, 'past_due')
