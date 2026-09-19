# -*- coding: utf-8 -*-
"""Rule 5 — nothing downstream may break.

These are the contract tests. They assert the surface that other modules and
existing databases depend on, so that a future refactor cannot quietly remove
a field that `real_estate_developer`, `atmta_real_estate` or a production
script is reading.
"""

from odoo.tests.common import tagged

from .common import ChecksCommon


@tagged('post_install', '-at_install', 'atmta_checks')
class TestModelSurvival(ChecksCommon):
    """The three 0.1 models still exist under their original names."""

    def test_the_models_were_extended_not_replaced(self):
        for model in ('realestate.check', 'realestate.check.deposit',
                      'realestate.check.bounce'):
            self.assertIn(model, self.env, '%s was replaced' % model)

    def test_the_wizards_survive(self):
        for model in ('realestate.check.bulk.wizard',
                      'realestate.check.deposit.wizard',
                      'realestate.check.bounce.wizard'):
            self.assertIn(model, self.env)

    def test_the_sequences_survive_with_their_prefixes(self):
        for code, prefix in (('realestate.check', 'CHK-'),
                             ('realestate.check.deposit', 'DEP-'),
                             ('realestate.check.bounce', 'BNC-')):
            sequence = self.env['ir.sequence'].search(
                [('code', '=', code)], limit=1)
            self.assertTrue(sequence, code)
            self.assertEqual(sequence.prefix, prefix)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestFieldSurvival(ChecksCommon):

    def test_every_0_1_check_field_still_exists(self):
        for name in ('name', 'check_number', 'bank_id', 'branch',
                     'account_number', 'partner_id', 'company_id',
                     'currency_id', 'amount', 'issue_date', 'due_date',
                     'sale_contract_id', 'sale_installment_id',
                     'rental_contract_id', 'rental_payment_id', 'project_id',
                     'property_id', 'state', 'deposit_id', 'deposit_date',
                     'payment_id', 'bounce_id', 'replacement_check_id',
                     'replaces_check_id', 'notes', 'journal_id'):
            self.assertIn(name, self.Check._fields,
                          '0.1 field %r was removed' % name)

    def test_every_0_1_deposit_field_still_exists(self):
        for name in ('name', 'deposit_date', 'journal_id', 'bank_slip_ref',
                     'company_id', 'currency_id', 'check_ids', 'check_count',
                     'total_amount', 'total_cleared', 'total_bounced',
                     'state', 'notes'):
            self.assertIn(name, self.Deposit._fields,
                          '0.1 field %r was removed' % name)

    def test_every_0_1_bounce_field_still_exists(self):
        for name in ('name', 'check_id', 'partner_id', 'amount', 'currency_id',
                     'bounce_date', 'reason', 'penalty_amount',
                     'penalty_invoice_id', 'resolved', 'notes'):
            self.assertIn(name, self.Bounce._fields,
                          '0.1 field %r was removed' % name)

    def test_the_contract_statistics_survive(self):
        contract = self._signed_contract()
        for name in ('check_ids', 'check_count', 'check_amount_total',
                     'check_amount_cleared', 'check_amount_bounced'):
            self.assertIn(name, contract._fields, name)

    def test_the_module_1_compatibility_assertion_still_holds(self):
        """`atmta_real_estate.tests.test_compatibility` asserts exactly this."""
        field = self.Check._fields.get('rental_payment_id')
        self.assertTrue(field)
        self.assertEqual(field.comodel_name, 'realestate.contract.payment')


@tagged('post_install', '-at_install', 'atmta_checks')
class TestMethodSurvival(ChecksCommon):
    """0.1's public methods still exist and still do something sensible."""

    def test_the_0_1_actions_survive(self):
        for method in ('action_register', 'action_back_to_draft',
                       'action_cancel', 'action_open_bounce_wizard',
                       'action_mark_cleared'):
            self.assertTrue(hasattr(self.Check, method), method)
        for method in ('action_confirm', 'action_clear_all', 'action_cancel'):
            self.assertTrue(hasattr(self.Deposit, method), method)
        for method in ('action_issue_penalty_invoice', 'action_mark_resolved'):
            self.assertTrue(hasattr(self.Bounce, method), method)

    def test_the_module_level_constants_survive(self):
        """0.1 exported these and something may import them."""
        from ..models.check import CHECK_STATES
        from ..models.deposit import DEPOSIT_STATES
        from ..models.bounce import BOUNCE_REASONS
        self.assertTrue(CHECK_STATES)
        self.assertTrue(DEPOSIT_STATES)
        self.assertTrue(BOUNCE_REASONS)

    def test_action_clear_all_still_runs(self):
        """Its behaviour changed — it reads clearance instead of asserting it —
        but it must not have vanished from under an existing button."""
        check = self._check(journal=self.journal_direct)
        deposit = self._deposit(check, journal=self.journal_direct)
        deposit.action_clear_all()
        check.invalidate_recordset()
        self.assertEqual(check.state, 'cleared')

    def test_the_developer_late_bound_hooks_are_answered(self):
        contract = self._signed_contract()
        self.assertTrue(hasattr(contract, '_blocking_checks'))
        self.assertTrue(hasattr(contract, '_assert_no_blocking_checks'))
        # Returns an empty recordset rather than raising when nothing is at
        # the bank, which is what Developer's own tests assert.
        self.assertFalse(contract._blocking_checks())
        contract._assert_no_blocking_checks()


@tagged('post_install', '-at_install', 'atmta_checks')
class TestMigrationShape(ChecksCommon):
    """M35 — the upgrade scripts exist and are shaped correctly."""

    def test_the_migration_scripts_exist(self):
        import os
        base = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            'migrations', '0.2')
        self.assertTrue(os.path.exists(os.path.join(base, 'pre-migrate.py')))
        self.assertTrue(os.path.exists(os.path.join(base, 'post-migrate.py')))

    def test_the_module_version_was_bumped(self):
        module = self.env['ir.module.module'].search(
            [('name', '=', 'real_estate_checks')])
        self.assertTrue(module.latest_version.endswith('0.2'),
                        'version is %s' % module.latest_version)

    def test_locations_were_seeded(self):
        locations = self.env['realestate.check.location'].search(
            [('company_id', '=', self.company.id)])
        self.assertTrue(locations)
        self.assertIn('bank', locations.mapped('kind'))
