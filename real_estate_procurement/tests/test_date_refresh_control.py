# -*- coding: utf-8 -*-
"""Waiting days and expiry anomalies keep moving after the day they were saved.

A requisition's days waiting, an approval step's waiting days and a
reservation's anomaly flag are stored, so registers can sort and filter on
them, but they are computed from the current date, and a stored compute only
reruns when its record changes. Nothing refreshed them: a requisition waiting a
week for approval still showed 0 days, and a reservation past its expiry date
was not flagged. Control now has a daily refresh (decision 15 Sep 2026).
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import M3Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestControlDateRefresh(M3Common):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])

    def _age(self, record, **columns):
        """Set columns in the database only, then forget the cache."""
        self.env.flush_all()
        assignments = ', '.join('%s = %%s' % column for column in columns)
        self.env.cr.execute(
            'UPDATE %s SET %s WHERE id = %%s' % (record._table, assignments),
            list(columns.values()) + [record.id])
        self.env.invalidate_all()

    def _refresh(self):
        self.env['realestate.procurement.control.refresh']._cron_refresh_dates()
        self.env.flush_all()
        self.env.invalidate_all()

    def test_a_pending_approval_keeps_counting_days(self):
        self.env['realestate.procurement.approval.rule'].create({
            'name': 'Any amount',
            'group_id': self.env.ref('real_estate_procurement.group_procurement_approver').id,
            'company_id': self.company.id,
        })
        request = self._demand(1_000.0, approve=False)
        step = request.approval_step_ids
        self.assertEqual(request.state, 'submitted')
        self.assertEqual(step.decision, 'pending')
        self.assertEqual((step.waiting_days, request.days_waiting), (0, 0))

        week_ago = fields.Datetime.now() - timedelta(days=7)
        self._age(step, requested_on=week_ago)
        self._age(request, submitted_on=week_ago, waiting_since=week_ago)
        self.assertEqual((step.waiting_days, request.days_waiting), (0, 0),
                         "a stored compute does not notice the days passing")

        self._refresh()
        self.assertEqual(step.waiting_days, 7)
        self.assertEqual(request.days_waiting, 7)

    def test_a_reservation_past_its_expiry_is_flagged(self):
        request = self._demand(1_000.0)
        reservation = self._reservation(request)
        self.assertTrue(reservation)
        reservation.expiry_date = self.today + timedelta(days=5)
        self.assertFalse(reservation.has_anomaly)

        self._age(reservation, expiry_date=self.today - timedelta(days=1))
        self.assertFalse(reservation.has_anomaly)

        self._refresh()
        self.assertTrue(reservation.has_anomaly)
        self.assertIn(str(self.today - timedelta(days=1)), reservation.anomaly_note)
