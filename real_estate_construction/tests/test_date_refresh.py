# -*- coding: utf-8 -*-
"""Overdue flags and day counts keep moving after the day they were saved.

These values are stored, because registers filter, group and sort on them, but
they are computed from today's date, and a stored compute only reruns when its
record changes. Nothing refreshed them, so an RFI whose response date passed
last week still read "not overdue" until somebody edited it, a delay kept the
duration it had when it was logged, and a milestone past its end date stayed
"in progress". Each module now has a daily refresh (decision 15 Sep 2026).

Each test moves a date into the past in the database only, as time passing
would, shows the stored value is stale, then runs the module's refresh.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestDateRefresh(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.project = self._project()

    def _age(self, record, **columns):
        """Set date columns in the database only, then forget the cache."""
        self.env.flush_all()
        assignments = ', '.join('%s = %%s' % column for column in columns)
        self.env.cr.execute(
            'UPDATE %s SET %s WHERE id = %%s' % (record._table, assignments),
            list(columns.values()) + [record.id])
        self.env.invalidate_all()

    def _refresh(self, model):
        self.env[model]._cron_refresh_dates()
        self.env.flush_all()
        self.env.invalidate_all()

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    def test_an_rfi_becomes_overdue(self):
        rfi = self._rfi(self.project, required_response_date=self.today + timedelta(days=5))
        self.assertFalse(rfi.is_overdue)
        self._age(rfi, required_response_date=self.today - timedelta(days=3))
        self.assertFalse(rfi.is_overdue, "a stored compute does not notice the date passing")
        self._refresh('realestate.construction.documents.refresh')
        self.assertTrue(rfi.is_overdue)
        self.assertEqual(rfi.overdue_days, 3)

    def test_a_submittal_becomes_overdue(self):
        submittal = self._submittal(
            self.project, required_submission_date=self.today + timedelta(days=5))
        self.assertFalse(submittal.is_overdue)
        self._age(submittal, required_submission_date=self.today - timedelta(days=4))
        self.assertFalse(submittal.is_overdue)
        self._refresh('realestate.construction.documents.refresh')
        self.assertTrue(submittal.is_overdue)
        self.assertEqual(submittal.submission_overdue_days, 4)

    def test_a_transmittal_acknowledgement_becomes_overdue(self):
        transmittal = self._transmittal(
            self.project, acknowledgement_required=True,
            response_due_date=self.today + timedelta(days=5))
        transmittal.write({'state': 'sent', 'sent_date': self.today})
        self.assertFalse(transmittal.is_acknowledgement_overdue)
        self._age(transmittal, response_due_date=self.today - timedelta(days=2))
        self.assertFalse(transmittal.is_acknowledgement_overdue)
        self._refresh('realestate.construction.documents.refresh')
        self.assertTrue(transmittal.is_acknowledgement_overdue)
        self.assertEqual(transmittal.acknowledgement_overdue_days, 2)

    def test_closed_documents_are_left_alone(self):
        rfi = self._rfi(self.project, required_response_date=self.today + timedelta(days=5))
        rfi.write({'state': 'void'})
        self._age(rfi, required_response_date=self.today - timedelta(days=3))
        self._refresh('realestate.construction.documents.refresh')
        self.assertFalse(rfi.is_overdue)

    # ------------------------------------------------------------------
    # Quality
    # ------------------------------------------------------------------
    def test_an_observation_becomes_overdue(self):
        observation = self._observation(self.project, due_date=self.today + timedelta(days=5))
        self.assertFalse(observation.is_overdue)
        self._age(observation, due_date=self.today - timedelta(days=1))
        self.assertFalse(observation.is_overdue)
        self._refresh('realestate.construction.quality.refresh')
        self.assertTrue(observation.is_overdue)

    def test_an_ncr_becomes_overdue(self):
        ncr = self._ncr(self.project, target_completion_date=self.today + timedelta(days=5))
        self.assertFalse(ncr.is_overdue)
        self._age(ncr, target_completion_date=self.today - timedelta(days=1))
        self.assertFalse(ncr.is_overdue)
        self._refresh('realestate.construction.quality.refresh')
        self.assertTrue(ncr.is_overdue)

    # ------------------------------------------------------------------
    # Cost: issues and risks
    # ------------------------------------------------------------------
    def test_an_issue_becomes_overdue(self):
        issue = self._issue(self.project, due_date=self.today + timedelta(days=5))
        self.assertFalse(issue.is_overdue)
        self._age(issue, due_date=self.today - timedelta(days=1))
        self.assertFalse(issue.is_overdue)
        self._refresh('realestate.construction.cost.refresh')
        self.assertTrue(issue.is_overdue)

    def test_a_risk_counts_its_overdue_actions(self):
        risk = self._risk(self.project)
        action = self.env['realestate.construction.risk.action'].create({
            'risk_id': risk.id, 'name': 'Qualify a second supplier',
            'due_date': self.today + timedelta(days=5),
        })
        self.assertEqual(risk.overdue_action_count, 0)
        self._age(action, due_date=self.today - timedelta(days=1))
        self.assertEqual(risk.overdue_action_count, 0)
        self._refresh('realestate.construction.cost.refresh')
        self.assertEqual(risk.overdue_action_count, 1)

    # ------------------------------------------------------------------
    # Claims: notices and delays
    # ------------------------------------------------------------------
    def test_an_unissued_notice_becomes_overdue(self):
        package = self._package(self.project, notice_required=True, notice_period_days=10)
        notice = self._notice(self.project, package=package)
        self.assertTrue(notice.has_deadline)
        self.assertNotEqual(notice.status, 'overdue')
        self._age(notice, awareness_date=self.today - timedelta(days=15),
                  deadline_date=self.today - timedelta(days=5))
        self.assertNotEqual(notice.status, 'overdue')
        self._refresh('realestate.construction.claims.refresh')
        self.assertEqual(notice.status, 'overdue')

    def test_an_ongoing_delay_keeps_counting(self):
        event = self._delay_event(self.project)
        self.assertLess(event.duration_days, 1)
        self._age(event, start_date=fields.Datetime.now() - timedelta(days=10))
        self.assertLess(event.duration_days, 1)
        self._refresh('realestate.construction.claims.refresh')
        self.assertAlmostEqual(event.duration_days, 10, delta=0.1)

    # ------------------------------------------------------------------
    # Site: milestones
    # ------------------------------------------------------------------
    def test_a_milestone_past_its_end_date_becomes_delayed(self):
        # Not started or in progress: both become delayed once the expected end
        # date has passed (completion is derived from the milestone's work, so
        # a fresh milestone is "not started").
        milestone = self._milestone(
            self.project, expected_end_date=self.today + timedelta(days=5))
        self.assertIn(milestone.state, ('not_started', 'in_progress'))
        self._age(milestone, expected_end_date=self.today - timedelta(days=1))
        self.assertNotEqual(milestone.state, 'delayed')
        self._refresh('realestate.construction.site.refresh')
        self.assertEqual(milestone.state, 'delayed')
