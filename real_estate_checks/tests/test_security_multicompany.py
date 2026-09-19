# -*- coding: utf-8 -*-
"""M29 / M31 / M32 — isolation, permissions and privacy.

0.1 shipped three groups and **zero record rules**, so anyone who could read a
cheque read every cheque in every company, bank account numbers included. Every
sensitive operation was gated by `groups=` on an XML button and by nothing at
all on the server, which is the definition of a permission that is not enforced.
"""

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import ChecksCommon


class SecurityCommon(ChecksCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.group_user = cls.env.ref('real_estate_checks.group_checks_user')
        cls.group_officer = cls.env.ref(
            'real_estate_checks.group_checks_treasurer')
        cls.group_manager = cls.env.ref(
            'real_estate_checks.group_checks_manager')
        cls.base_user = cls.env.ref('base.group_user')
        cls.account_user = cls.env.ref('account.group_account_user')

    def _user(self, login, groups, company=None):
        company = company or self.company
        return self.env['res.users'].with_context(
            no_reset_password=True, mail_create_nosubscribe=True).create({
            'name': login,
            'login': '%s@test.example' % login,
            'email': '%s@test.example' % login,
            'company_id': company.id,
            'company_ids': [(6, 0, [company.id])],
            'groups_id': [(6, 0, [self.base_user.id] + [g.id for g in groups])],
        })


@tagged('post_install', '-at_install', 'atmta_checks')
class TestServerSideEnforcement(SecurityCommon):
    """M31 — buttons disappearing is not security."""

    def setUp(self):
        super().setUp()
        self.clerk = self._user('clerk', [self.group_user, self.account_user])

    def test_a_clerk_can_register_a_cheque(self):
        check = self.Check.with_user(self.clerk).create({
            'partner_id': self.buyer.id,
            'company_id': self.company.id,
            'currency_id': self.company.currency_id.id,
            'bank_id': self.bank.id,
            'check_number': 'CLERK-1',
            'amount': 1000.0,
            'due_date': self.env.cr.now().date(),
        })
        self.assertTrue(check.id)

    @mute_logger('odoo.addons.base.models.ir_rule', 'odoo.models')
    def test_a_clerk_cannot_confirm_a_deposit(self):
        """0.1 let any Checks User do this over RPC."""
        check = self._check()
        deposit = self._deposit(check, confirm=False)
        # Odoo's `assertRaises` override does not accept a tuple.
        with self.assertRaises(AccessError):
            deposit.with_user(self.clerk).action_confirm()

    def test_a_clerk_cannot_clear_a_cheque(self):
        check = self._check()
        self._deposit(check)
        with self.assertRaises(AccessError) as err:
            check.with_user(self.clerk).action_mark_cleared()
        self.assertIn('not allowed', str(err.exception))

    def test_a_clerk_cannot_cancel_a_cheque(self):
        check = self._check(state='draft')
        with self.assertRaises(AccessError):
            check.with_user(self.clerk).action_cancel()

    def test_a_clerk_cannot_return_a_cheque_to_the_customer(self):
        check = self._check()
        with self.assertRaises(AccessError):
            check.with_user(self.clerk).action_return_to_customer()

    @mute_logger('odoo.addons.base.models.ir_model', 'odoo.models')
    def test_a_clerk_cannot_create_a_bounce_row(self):
        """0.1 gave `group_checks_user` create+write on the bounce model, and
        creating a bounce row is what bounces a cheque."""
        check = self._check()
        self._deposit(check)
        with self.assertRaises(AccessError):
            self.Bounce.with_user(self.clerk).create({
                'check_id': check.id, 'reason': 'insufficient'})

    def test_a_clerk_cannot_transfer_custody(self):
        check = self._check()
        wizard = self.env['realestate.check.custody.wizard'].sudo().create({
            'check_ids': [(6, 0, check.ids)],
            'to_location_id': self.bank_location.id,
        })
        with self.assertRaises(AccessError):
            wizard.with_user(self.clerk).action_transfer()

    def test_an_officer_can_do_all_of_it(self):
        officer = self._user('officer', [self.group_officer, self.account_user])
        check = self._check()
        deposit = self._deposit(check, confirm=False)
        deposit.with_user(officer).action_confirm()
        self.assertEqual(deposit.state, 'confirmed')

    def test_the_role_hierarchy_is_intact(self):
        approver = self.env.ref('real_estate_checks.group_checks_approver')
        self.assertIn(self.group_user, self.group_officer.implied_ids)
        self.assertIn(self.group_officer, approver.implied_ids)
        self.assertIn(approver, self.group_manager.implied_ids)

    def test_the_0_1_group_xml_ids_still_exist(self):
        """Renaming one would silently un-assign every production user."""
        for xmlid in ('group_checks_user', 'group_checks_treasurer',
                      'group_checks_manager'):
            self.assertTrue(self.env.ref('real_estate_checks.%s' % xmlid,
                                         raise_if_not_found=False), xmlid)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestMultiCompanyIsolation(SecurityCommon):
    """M29 — 0.1 had no record rules at all."""

    def setUp(self):
        super().setUp()
        self.other_company = self.env['res.company'].create({
            'name': 'Rival Developer'})
        self.our_user = self._user('ours', [self.group_officer,
                                            self.account_user])
        self.their_user = self._user('theirs', [self.group_officer,
                                                self.account_user],
                                     company=self.other_company)

    def test_every_model_carries_a_record_rule(self):
        for model in ('realestate.check', 'realestate.check.deposit',
                      'realestate.check.bounce', 'realestate.check.allocation',
                      'realestate.check.custody',
                      'realestate.check.presentation',
                      'realestate.check.location'):
            rules = self.env['ir.rule'].search([
                ('model_id.model', '=', model)])
            self.assertTrue(rules, 'no record rule on %s' % model)
            self.assertTrue(any('company_id' in (r.domain_force or '')
                                for r in rules),
                            'no company rule on %s' % model)

    def test_a_rival_cannot_see_our_cheques(self):
        check = self._check()
        visible = self.Check.with_user(self.their_user).search(
            [('id', '=', check.id)])
        self.assertFalse(visible)

    def test_we_can_see_our_own(self):
        check = self._check()
        visible = self.Check.with_user(self.our_user).search(
            [('id', '=', check.id)])
        self.assertEqual(visible, check)

    def test_the_rules_do_not_admit_a_missing_company(self):
        """A cheque with no company is a data error, and making it visible to
        everyone would be the wrong way to surface it."""
        for xmlid in ('rule_check_company', 'rule_check_deposit_company'):
            rule = self.env.ref('real_estate_checks.%s' % xmlid)
            self.assertNotIn('company_id', rule.domain_force.split('|')[0]
                             if '|' in rule.domain_force else '')
            self.assertNotIn("'=', False", rule.domain_force)

    def test_a_cheque_cannot_be_moved_to_another_company_s_journal(self):
        foreign_journal = self.env['account.journal'].create({
            'name': 'Rival Bank', 'code': 'RVB', 'type': 'bank',
            'company_id': self.other_company.id,
        })
        check = self._check()
        with self.assertRaises(ValidationError) as err:
            check.write({'journal_id': foreign_journal.id})
        self.assertIn('cannot cross companies', str(err.exception))

    def test_an_allocation_cannot_straddle_companies(self):
        contract = self._signed_contract()
        installment = contract.installment_ids[0]
        foreign = self._check()
        foreign.sudo().write({'company_id': self.other_company.id,
                              'journal_id': False, 'sale_contract_id': False,
                              'location_id': False})
        with self.assertRaises(ValidationError):
            self.Allocation.create({
                'check_id': foreign.id,
                'sale_installment_id': installment.id,
                'allocated_amount': 1.0,
            })

    def test_the_rules_are_global_so_they_bind_administrators_too(self):
        rule = self.env.ref('real_estate_checks.rule_check_company')
        self.assertTrue(rule['global'])
        self.assertFalse(rule.groups)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestDataPrivacy(SecurityCommon):
    """M32 — cheque data is sensitive and the views say who sees what."""

    def test_bank_details_are_behind_a_group(self):
        view = self.env.ref('real_estate_checks.view_realestate_check_form')
        arch = view.arch_db
        for sensitive in ('account_number', 'partner_bank_id', 'branch'):
            self.assertIn(sensitive, arch)
        self.assertIn('group_checks_see_bank_details', arch)

    def test_treasury_officers_get_that_group(self):
        detail = self.env.ref(
            'real_estate_checks.group_checks_see_bank_details')
        self.assertIn(detail, self.group_officer.implied_ids)

    def test_an_ordinary_clerk_does_not(self):
        detail = self.env.ref(
            'real_estate_checks.group_checks_see_bank_details')
        self.assertNotIn(detail, self.group_user.implied_ids)

    def test_the_account_number_is_not_tracked_into_chatter(self):
        """Tracking it would copy a bank account number into a permanent,
        widely readable message for every edit."""
        for sensitive in ('account_number', 'branch', 'notes'):
            field = self.Check._fields[sensitive]
            self.assertFalse(getattr(field, 'tracking', False),
                             '%s is tracked into chatter' % sensitive)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestPortalExposure(SecurityCommon):
    """M26 — the portal was audited and deliberately left alone."""

    def test_no_portal_route_exposes_cheques(self):
        """Phase 0 found the portal exposes no PDC data at all. Adding it would
        mean publishing bank details and treasury notes to customers, so this
        release does not — and asserts that it has not started to."""
        views = self.env['ir.ui.view'].search([
            ('type', '=', 'qweb'),
            ('arch_db', 'like', 'realestate.check'),
        ])
        portal_views = views.filtered(
            lambda v: 'portal' in (v.key or '') or 'website' in (v.key or ''))
        self.assertFalse(
            portal_views,
            'a portal template now renders cheque data: %s'
            % portal_views.mapped('key'))
