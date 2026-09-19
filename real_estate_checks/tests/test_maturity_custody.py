# -*- coding: utf-8 -*-
"""M4 / M5 — maturity, and physical custody."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import tagged

from .common import ChecksCommon
from ..models.check_states import maturity_bucket_for


@tagged('post_install', '-at_install', 'atmta_checks')
class TestMaturity(ChecksCommon):

    def setUp(self):
        super().setUp()
        self.today = fields.Date.context_today(self.env['res.partner'])

    def test_a_future_cheque_is_not_due(self):
        check = self._check(due_date=self.today + timedelta(days=45))
        self.assertEqual(check.days_to_due, 45)
        self.assertFalse(check.is_due)
        self.assertEqual(check.maturity_bucket, 'd60')

    def test_a_cheque_due_today(self):
        check = self._check(due_date=self.today)
        self.assertEqual(check.days_to_due, 0)
        self.assertTrue(check.is_due)
        self.assertEqual(check.maturity_bucket, 'today')

    def test_a_matured_cheque(self):
        check = self._check(due_date=self.today - timedelta(days=10),
                            issue_date=self.today - timedelta(days=60))
        self.assertEqual(check.days_to_due, -10)
        self.assertTrue(check.is_due)
        self.assertEqual(check.maturity_bucket, 'past_due')

    def test_bucket_boundaries(self):
        """The bucket function is shared by the field and the forecast, so the
        two can never disagree. Its edges are asserted directly."""
        self.assertEqual(maturity_bucket_for(-1), 'past_due')
        self.assertEqual(maturity_bucket_for(0), 'today')
        self.assertEqual(maturity_bucket_for(7), 'd7')
        self.assertEqual(maturity_bucket_for(8), 'd30')
        self.assertEqual(maturity_bucket_for(30), 'd30')
        self.assertEqual(maturity_bucket_for(31), 'd60')
        self.assertEqual(maturity_bucket_for(365), 'd365')
        self.assertEqual(maturity_bucket_for(366), 'beyond')

    def test_overdue_for_deposit_respects_the_company_tolerance(self):
        self.company.check_overdue_presentation_days = 5
        just_late = self._check(due_date=self.today - timedelta(days=3),
                                issue_date=self.today - timedelta(days=60))
        properly_late = self._check(due_date=self.today - timedelta(days=30),
                                    issue_date=self.today - timedelta(days=90))
        self.assertFalse(just_late.is_overdue_for_deposit)
        self.assertTrue(properly_late.is_overdue_for_deposit)

    def test_staleness_is_off_unless_configured(self):
        """M5 — no universal legal duration is invented."""
        self.company.check_stale_days = 0
        ancient = self._check(due_date=self.today - timedelta(days=3650),
                              issue_date=self.today - timedelta(days=3700))
        self.assertFalse(ancient.is_stale)

    def test_staleness_applies_once_configured(self):
        self.company.check_stale_days = 180
        stale = self._check(due_date=self.today - timedelta(days=200),
                            issue_date=self.today - timedelta(days=260))
        fresh = self._check(due_date=self.today - timedelta(days=10),
                            issue_date=self.today - timedelta(days=60))
        self.assertTrue(stale.is_stale)
        self.assertFalse(fresh.is_stale)

    def test_is_due_is_searchable(self):
        self._check(due_date=self.today - timedelta(days=1),
                    issue_date=self.today - timedelta(days=30))
        self._check(due_date=self.today + timedelta(days=30))
        due = self.Check.search([('is_due', '=', True),
                                 ('company_id', '=', self.company.id)])
        not_due = self.Check.search([('is_due', '=', False),
                                     ('company_id', '=', self.company.id)])
        self.assertTrue(due)
        self.assertTrue(not_due)
        self.assertFalse(due & not_due)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestEarlyPresentationPolicy(ChecksCommon):
    """M5 — presenting a PDC early is a policy decision, defaulted safe."""

    def setUp(self):
        super().setUp()
        self.today = fields.Date.context_today(self.env['res.partner'])
        self.future = self._check(due_date=self.today + timedelta(days=30))

    def test_early_presentation_is_refused_by_default(self):
        self.assertFalse(self.company.check_allow_early_deposit)
        with self.assertRaises(UserError) as err:
            self._deposit(self.future)
        self.assertIn('does not allow presenting a post-dated cheque early',
                      str(err.exception))

    def test_early_presentation_within_the_window_is_allowed(self):
        self.company.check_allow_early_deposit = True
        self.company.check_early_deposit_days = 45
        deposit = self._deposit(self.future)
        self.assertEqual(deposit.state, 'confirmed')

    def test_early_presentation_beyond_the_window_is_refused(self):
        self.company.check_allow_early_deposit = True
        self.company.check_early_deposit_days = 7
        with self.assertRaises(UserError) as err:
            self._deposit(self.future)
        self.assertIn('days early', str(err.exception))

    def test_a_stale_cheque_is_not_presented_automatically(self):
        self.company.check_stale_days = 90
        stale = self._check(due_date=self.today - timedelta(days=200),
                            issue_date=self.today - timedelta(days=260))
        with self.assertRaises(UserError) as err:
            self._deposit(stale)
        self.assertIn('validity period', str(err.exception))


@tagged('post_install', '-at_install', 'atmta_checks')
class TestCustody(ChecksCommon):
    """M4 — custody is a history, not a field."""

    def test_a_new_cheque_opens_a_custody_record(self):
        check = self._check()
        self.assertTrue(check.custody_ids)
        self.assertEqual(check.custody_ids[0].reason, 'receipt')
        self.assertEqual(check.custodian_id, self.env.user)
        self.assertEqual(check.location_id, self.safe)

    def test_transfer_records_where_it_came_from(self):
        check = self._check()
        other_user = self.env['res.users'].create({
            'name': 'Second Treasurer', 'login': 'treasurer2@test.example',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('real_estate_checks.group_checks_treasurer').id,
            ])],
        })
        vault = self.Location.create({
            'name': 'Head Office Vault', 'code': 'HOV', 'kind': 'safe',
            'company_id': self.company.id,
        })
        self.Custody.transfer(check, to_custodian=other_user,
                              to_location=vault, reason='transfer',
                              note='Moved to head office')
        check.invalidate_recordset()
        self.assertEqual(check.custodian_id, other_user)
        self.assertEqual(check.location_id, vault)
        latest = check.custody_ids.sorted('id')[-1]
        self.assertEqual(latest.from_location_id, self.safe)
        self.assertEqual(latest.to_location_id, vault)

    def test_custody_history_cannot_be_edited(self):
        check = self._check()
        movement = check.custody_ids[0]
        with self.assertRaises(UserError) as err:
            movement.write({'to_location_id': self.bank_location.id})
        self.assertIn('historical record', str(err.exception))

    def test_the_note_may_still_be_corrected(self):
        check = self._check()
        movement = check.custody_ids[0]
        movement.write({'note': 'Corrected wording'})
        self.assertEqual(movement.note, 'Corrected wording')

    def test_custody_history_cannot_be_deleted(self):
        check = self._check()
        with self.assertRaises(UserError) as err:
            check.custody_ids.unlink()
        self.assertIn('cannot be deleted', str(err.exception))

    def test_the_current_custodian_is_not_directly_writable(self):
        """M4 — custody that can be typed over is not custody."""
        self.assertTrue(self.Check._fields['custodian_id'].readonly)
        self.assertTrue(self.Check._fields['location_id'].readonly)

    def test_a_movement_that_moves_nothing_is_refused(self):
        check = self._check()
        with self.assertRaises(ValidationError):
            self.Custody.create({
                'check_id': check.id,
                'from_location_id': self.safe.id,
                'to_location_id': self.safe.id,
                'from_custodian_id': self.env.user.id,
                'to_custodian_id': self.env.user.id,
                'reason': 'transfer',
            })

    def test_depositing_moves_custody_to_the_bank(self):
        check = self._check()
        self._deposit(check)
        check.invalidate_recordset()
        self.assertEqual(check.location_id.kind, 'bank')
        self.assertTrue(check.custody_ids.filtered(
            lambda c: c.reason == 'deposit'))

    def test_the_wizard_refuses_to_move_paper_that_is_at_the_bank(self):
        check = self._check()
        self._deposit(check)
        wizard = self.env['realestate.check.custody.wizard'].create({
            'check_ids': [(6, 0, check.ids)],
            'to_location_id': self.safe.id,
        })
        with self.assertRaises(UserError) as err:
            wizard.action_transfer()
        self.assertIn('not in your custody', str(err.exception))
