# -*- coding: utf-8 -*-
"""M10J — dates mean what the user's calendar says, not what UTC says.

Every deployment this suite targets is ahead of UTC, so for two to three hours
every evening `fields.Date.today()` and the user's actual date disagree. That
window is where date defects live: a milestone flagged overdue a day early, a
certificate dated into last month, a notice deadline computed against
yesterday.

Time is frozen at 22:00 UTC, which is the next day in Kiritimati (UTC+14) and
the previous day in Honolulu (UTC-10). Nothing here asserts against the wall
clock, so nothing here can be flaky.
"""

from freezegun import freeze_time

from odoo import fields
from odoo.tests import tagged

from .common import ConstructionCommon

#: 22:00 UTC — already tomorrow at UTC+14, still today at UTC-10.
UTC_EVENING = '2026-08-09 22:00:00'


@tagged('post_install', '-at_install', 'atmta_construction')
class TestTimezoneSemantics(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('TZ-CIV', 'Civil', 'subcontract')

    def _as_user_in(self, timezone):
        self.env.user.tz = timezone
        return self.env.user

    @freeze_time(UTC_EVENING)
    def test_the_window_this_file_is_about_actually_exists(self):
        """A guard on the guard: if these never differ, the rest proves nothing."""
        self._as_user_in('Pacific/Kiritimati')
        self.assertEqual(fields.Date.today(),
                         fields.Date.to_date('2026-08-09'))
        self.assertEqual(fields.Date.context_today(self.env.user),
                         fields.Date.to_date('2026-08-10'),
                         "At UTC+14 it is already tomorrow.")

        self._as_user_in('Pacific/Honolulu')
        self.assertEqual(fields.Date.context_today(self.env.user),
                         fields.Date.to_date('2026-08-09'))

    @freeze_time(UTC_EVENING)
    def test_a_certificate_is_dated_in_the_users_period(self):
        """An accounting document dated a day early lands in last month."""
        self._configure_construction_accounts()
        self._as_user_in('Pacific/Kiritimati')

        certificate = self.env[
            'realestate.construction.payment.certificate'].create({
                'title': 'Timezone certificate',
                'project_id': self.project.id,
                'contractor_id': self.contractor.id,
                'cost_code_id': self.civil.id,
                'applied_amount': 100_000.0,
                'retention_pct': 0.0,
            }) if 'title' in self.env[
                'realestate.construction.payment.certificate']._fields else \
            self._certificate(self.project, self.contractor,
                              amount=100_000.0, retention_pct=0.0,
                              cost_code=self.civil,
                              period_end=fields.Date.context_today(
                                  self.env.user))

        self.assertEqual(certificate.period_end,
                         fields.Date.to_date('2026-08-10'),
                         "The period defaults to the user's own day.")
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        self.assertEqual(
            certificate.vendor_bill_id.invoice_date,
            fields.Date.to_date('2026-08-10'),
            "The bill must carry the date the person raising it sees.")

    @freeze_time(UTC_EVENING)
    def test_a_milestone_is_not_overdue_on_its_own_due_date(self):
        self._as_user_in('Pacific/Kiritimati')
        milestone = self._milestone(self.project)
        # Written after creation: the state default means the compute runs on
        # a dependency write rather than at create. See the note on the field.
        milestone.expected_end_date = fields.Date.to_date('2026-08-10')

        self.assertNotEqual(
            milestone.state, 'delayed',
            "It is due today in the user's calendar, so it is not late.")

    @freeze_time(UTC_EVENING)
    def test_a_milestone_due_yesterday_is_overdue(self):
        self._as_user_in('Pacific/Kiritimati')
        milestone = self._milestone(self.project)
        milestone.expected_end_date = fields.Date.to_date('2026-08-09')
        self.assertEqual(milestone.state, 'delayed',
                         "Due yesterday in the user's calendar is late, even "
                         "though UTC has not turned over yet.")

    @freeze_time(UTC_EVENING)
    def test_a_daily_report_defaults_to_the_users_day(self):
        self._as_user_in('Pacific/Kiritimati')
        report = self.env['realestate.construction.daily.report'].create({
            'project_id': self.project.id})
        self.assertEqual(report.report_date,
                         fields.Date.to_date('2026-08-10'),
                         "A site report is filed for the day on site.")

    @freeze_time(UTC_EVENING)
    def test_a_notice_deadline_runs_from_the_users_day(self):
        self._as_user_in('Pacific/Kiritimati')
        package = self._package(self.project, self.contractor,
                                value=1_000_000.0, award=True)
        package.write({'notice_required': True, 'notice_period_days': 28})

        notice = self._notice(self.project, package,
                              awareness_date=fields.Date.context_today(
                                  self.env.user))
        notice.action_issue()

        self.assertEqual(notice.notice_date,
                         fields.Date.to_date('2026-08-10'))
        self.assertFalse(notice.is_late)

    @freeze_time(UTC_EVENING)
    def test_an_issue_is_not_overdue_on_its_due_date(self):
        self._as_user_in('Pacific/Kiritimati')
        issue = self._issue(self.project,
                            due_date=fields.Date.to_date('2026-08-10'))
        issue.invalidate_recordset()
        self.assertFalse(issue.is_overdue)

    @freeze_time(UTC_EVENING)
    def test_claim_age_is_measured_in_the_users_calendar(self):
        self._as_user_in('Pacific/Kiritimati')
        package = self._package(self.project, self.contractor,
                                value=1_000_000.0, award=True)
        claim = self._claim(self.project, package, claimed_cost=100_000.0,
                            submission_date=fields.Date.to_date('2026-08-10'))
        claim.action_submit()
        self.assertEqual(claim.age_days, 0,
                         "Submitted today is nought days old.")

    @freeze_time(UTC_EVENING)
    def test_the_control_tower_dates_itself_in_the_users_calendar(self):
        self._as_user_in('Pacific/Kiritimati')
        payload = self.env[
            'realestate.construction.control.tower'].payload(self.project)
        self.assertEqual(payload['as_of'], fields.Date.to_date('2026-08-10'))
        self.assertEqual(payload['header']['data_as_of'],
                         fields.Date.to_date('2026-08-10'))

    @freeze_time(UTC_EVENING)
    def test_a_user_behind_utc_sees_their_own_day(self):
        self._as_user_in('Pacific/Honolulu')
        report = self.env['realestate.construction.daily.report'].create({
            'project_id': self.project.id})
        self.assertEqual(report.report_date,
                         fields.Date.to_date('2026-08-09'))
