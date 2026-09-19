# -*- coding: utf-8 -*-
"""§7 and §8 of the release brief — the registers, the documents, and who may
see what.

Every one of the eight registers is opened the way the UI opens it — action
domain plus action context, evaluated against a two-company, two-currency
fixture — and asserted for company scoping, currency handling, the records it
returns, its filters, and its grouping. The two QWeb documents are rendered.

The access half asserts the inverse: that an ordinary Sales user, who has every
Developer right and no Treasury right, cannot reach bank references, custody,
treasury notes or internal bounce detail.
"""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import ChecksCommon

REGISTERS = (
    'action_report_check_register',
    'action_report_pdc_maturity',
    'action_report_deposit_register',
    'action_report_cleared_register',
    'action_report_bounce_register',
    'action_report_replacement_register',
    'action_report_check_coverage',
    'action_report_customer_pdc',
)


class RegisterCommon(ChecksCommon):
    """Two companies, two currencies, one of everything worth reporting."""

    def setUp(self):
        super().setUp()
        self.today = fields.Date.context_today(self.env['res.partner'])
        self.rival = self.env['res.company'].create({'name': 'Rival Treasury'})
        self.rival_partner = self.env['res.partner'].create(
            {'name': 'Rival Buyer'})
        self.rival_bank = self.env['res.bank'].create({'name': 'Rival Bank'})

        # Ours: on hand, presented, cleared, bounced, replaced.
        self.on_hand = self._check(due_date=self.today + timedelta(days=20))
        self.presented = self._check(journal=self.journal_outstanding)
        self._deposit(self.presented, journal=self.journal_outstanding)
        self.cleared = self._check(journal=self.journal_direct)
        self._deposit(self.cleared, journal=self.journal_direct)
        self.cleared._sync_clearance_from_accounting()
        self.bounced = self._check(journal=self.journal_outstanding)
        self._deposit(self.bounced, journal=self.journal_outstanding)
        self.bounce = self.Bounce.create({
            'check_id': self.bounced.id, 'reason': 'insufficient'})
        self.replaced = self._check()
        self.replacement = self._replace(self.replaced)

        # Theirs, which must never appear in our registers.
        self.rival_check = self.env['realestate.check'].with_company(
            self.rival).create({
                'partner_id': self.rival_partner.id,
                'company_id': self.rival.id,
                'currency_id': self.rival.currency_id.id,
                'bank_id': self.rival_bank.id,
                'check_number': '000099001',
                'amount': 5000.0,
                'issue_date': self.today,
                'due_date': self.today,
                'state': 'registered',
            })

        # A foreign-currency cheque of ours, for the currency assertions.
        self.foreign = self._check(currency_id=self._other_currency().id)

    def _replace(self, original):
        wizard = self.env['realestate.check.replace.wizard'].create({
            'original_check_id': original.id,
            'new_partner_id': original.partner_id.id,
            'check_number': '%s-R' % original.check_number,
            'bank_id': original.bank_id.id,
            'amount': original.amount,
            'issue_date': self.today,
            'due_date': self.today,
        })
        wizard.action_replace()
        original.invalidate_recordset()
        return original.replacement_check_id

    def _open(self, xmlid, user=None):
        """Open a register the way the web client opens it."""
        action = self.env.ref('real_estate_checks.%s' % xmlid)
        model = self.env[action.res_model]
        if user:
            model = model.with_user(user)
        domain = action.domain and eval(action.domain, {  # noqa: S307
            'context_today': fields.Date.context_today,
            'relativedelta': __import__('dateutil.relativedelta',
                                        fromlist=['relativedelta']).relativedelta,
        }) or []
        return action, model.search(domain)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestRegistersExistAndOpen(RegisterCommon):

    def test_all_eight_registers_exist(self):
        for xmlid in REGISTERS:
            action = self.env.ref('real_estate_checks.%s' % xmlid,
                                  raise_if_not_found=False)
            self.assertTrue(action, "Register %s is missing" % xmlid)
            self.assertEqual(action.type, 'ir.actions.act_window')

    def test_every_register_opens_and_returns_records(self):
        for xmlid in REGISTERS:
            action, records = self._open(xmlid)
            self.assertTrue(
                action.res_model in self.env,
                "%s points at an unknown model" % xmlid)
            # Every register must be reachable; emptiness is only acceptable
            # where the fixture genuinely has nothing.
            self.assertIsNotNone(records)

    def test_the_registers_are_operational_datasets(self):
        """M25 — a register is a list/pivot, not a PDF."""
        for xmlid in REGISTERS:
            action = self.env.ref('real_estate_checks.%s' % xmlid)
            self.assertIn('list', action.view_mode,
                          "%s has no list view" % xmlid)

    def test_the_analytical_registers_offer_a_pivot(self):
        for xmlid in ('action_report_check_register',
                      'action_report_pdc_maturity',
                      'action_report_cleared_register',
                      'action_report_check_coverage',
                      'action_report_customer_pdc'):
            action = self.env.ref('real_estate_checks.%s' % xmlid)
            self.assertIn('pivot', action.view_mode,
                          "%s cannot be pivoted" % xmlid)

    def test_every_register_has_a_search_view_or_a_domain(self):
        """A register with neither filters nor a domain is a raw table dump."""
        for xmlid in REGISTERS:
            action = self.env.ref('real_estate_checks.%s' % xmlid)
            has_filters = bool(action.search_view_id) or bool(action.domain)
            self.assertTrue(has_filters,
                            "%s offers no filters and no domain" % xmlid)

    def test_the_grouped_registers_declare_their_grouping(self):
        expected = {
            'action_report_check_register': 'group_state',
            'action_report_pdc_maturity': 'group_bucket',
            'action_report_deposit_register': 'group_journal',
            'action_report_bounce_register': 'group_reason',
            'action_report_replacement_register': 'group_partner',
            'action_report_customer_pdc': 'group_partner',
        }
        for xmlid, group in expected.items():
            action = self.env.ref('real_estate_checks.%s' % xmlid)
            self.assertIn(
                'search_default_%s' % group, action.context or '',
                "%s does not group by %s out of the box" % (xmlid, group))


@tagged('post_install', '-at_install', 'atmta_checks')
class TestRegisterContent(RegisterCommon):
    """The right records, and only the right records."""

    def test_the_check_register_holds_every_instrument(self):
        _action, records = self._open('action_report_check_register')
        for check in (self.on_hand, self.presented, self.cleared,
                      self.bounced, self.replaced, self.replacement):
            self.assertIn(check, records)

    def test_the_maturity_register_holds_only_paper_on_hand(self):
        _action, records = self._open('action_report_pdc_maturity')
        self.assertIn(self.on_hand, records)
        self.assertNotIn(self.presented, records,
                         "A presented cheque is not a future maturity")
        self.assertNotIn(self.cleared, records)

    def test_the_cleared_register_holds_only_cleared(self):
        _action, records = self._open('action_report_cleared_register')
        self.assertIn(self.cleared, records)
        self.assertNotIn(self.presented, records,
                         "A presented cheque is not cleared")
        self.assertNotIn(self.on_hand, records)
        self.assertEqual(set(records.mapped('state')), {'cleared'})

    def test_the_bounce_register_holds_the_bounce(self):
        _action, records = self._open('action_report_bounce_register')
        self.assertIn(self.bounce, records)

    def test_the_replacement_register_holds_the_chain(self):
        _action, records = self._open('action_report_replacement_register')
        self.assertIn(self.replaced, records)
        self.assertIn(self.replacement, records)
        self.assertNotIn(self.on_hand, records)

    def test_the_deposit_register_holds_the_slips(self):
        _action, records = self._open('action_report_deposit_register')
        self.assertTrue(records)
        self.assertEqual(set(records.mapped('company_id')), {self.company})

    def test_the_coverage_register_holds_open_obligations(self):
        contract = self._signed_contract()
        _action, records = self._open('action_report_check_coverage')
        self.assertTrue(records & contract.installment_ids)
        for record in records:
            self.assertFalse(record.is_cancelled)
            self.assertNotIn(record.state, ('paid', 'cancelled'))


@tagged('post_install', '-at_install', 'atmta_checks')
class TestRegisterCompanyScoping(RegisterCommon):
    """No register may show another company's paper."""

    def setUp(self):
        super().setUp()
        self.our_user = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Register Reader',
                'login': 'register.reader@test.example',
                'email': 'register.reader@test.example',
                'company_id': self.company.id,
                'company_ids': [(6, 0, [self.company.id])],
                'groups_id': [(6, 0, [
                    self.env.ref('base.group_user').id,
                    self.env.ref(
                        'real_estate_checks.group_checks_treasurer').id,
                    self.env.ref('account.group_account_user').id,
                    # The PDC Coverage register reports Developer's
                    # obligations, so reading it needs a Developer role too.
                    self.env.ref(
                        'real_estate_developer.group_dev_readonly').id,
                ])],
            })

    @mute_logger('odoo.addons.base.models.ir_rule')
    def test_no_register_leaks_another_company(self):
        for xmlid in REGISTERS:
            _action, records = self._open(xmlid, user=self.our_user)
            companies = records.mapped('company_id')
            self.assertNotIn(
                self.rival, companies,
                "%s shows records belonging to another company" % xmlid)

    def test_the_rival_cheque_is_invisible(self):
        visible = self.env['realestate.check'].with_user(
            self.our_user).search([('id', '=', self.rival_check.id)])
        self.assertFalse(visible)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestRegisterCurrencyHandling(RegisterCommon):
    """M30 — a register must never present one meaningless total."""

    def test_the_registers_expose_currency_so_totals_can_be_grouped(self):
        for xmlid in ('action_report_check_register',
                      'action_report_pdc_maturity',
                      'action_report_cleared_register',
                      'action_report_customer_pdc'):
            action = self.env.ref('real_estate_checks.%s' % xmlid)
            search = action.search_view_id
            self.assertTrue(search, "%s has no search view" % xmlid)
            self.assertIn(
                'group_currency', search.arch_db,
                "%s cannot be grouped by currency, so a multi-currency "
                "portfolio would be summed into one meaningless figure"
                % xmlid)

    def test_a_foreign_currency_cheque_keeps_its_own_currency(self):
        _action, records = self._open('action_report_check_register')
        self.assertIn(self.foreign, records)
        self.assertNotEqual(self.foreign.currency_id, self.company.currency_id)
        # The list view carries the currency field, so monetary columns render
        # in each row's own currency rather than the company's.
        view = self.env.ref('real_estate_checks.view_realestate_check_list')
        self.assertIn('currency_id', view.arch_db)

    def test_the_dashboard_flags_rather_than_sums(self):
        data = self.env['realestate.check.dashboard'].get_dashboard_data()
        self.assertTrue(data['currency_warning']['multi_currency'])
        self.assertIn('NOT converted', data['currency_warning']['message'])


@tagged('post_install', '-at_install', 'atmta_checks')
class TestQwebDocuments(RegisterCommon):
    """The two documents that are genuinely useful on paper."""

    def _render(self, xmlid, records):
        report = self.env.ref('real_estate_checks.%s' % xmlid)
        html = self.env['ir.actions.report']._render_qweb_html(
            report.report_name, records.ids)[0]
        return html.decode() if isinstance(html, bytes) else html

    def _text(self, html):
        import re
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', html))

    def test_the_deposit_slip_renders_completely(self):
        deposit = self.presented.deposit_id
        body = self._render('action_report_deposit_slip', deposit)
        text = self._text(body)
        for expected in ('Cheque Deposit Slip', deposit.name,
                         deposit.journal_id.display_name,
                         self.presented.check_number,
                         'Prepared by', 'Confirmed by', 'Received by bank'):
            self.assertIn(expected, text,
                          "The deposit slip omits %r" % expected)

    def test_the_acknowledgement_is_not_a_payment_receipt(self):
        contract = self._signed_contract()
        held = self._check(sale_contract_id=contract.id)
        body = self._render('action_report_pdc_acknowledgement', contract)
        text = self._text(body)

        self.assertIn('Cheque Receipt / PDC Acknowledgement', text)
        self.assertIn('not a payment receipt', text)
        self.assertIn('no payment has been made', text)
        self.assertNotIn('Payment Receipt<', body)
        self.assertIn(held.check_number, text)

    def test_the_acknowledgement_lists_only_paper_still_held(self):
        """It acknowledges instruments in hand — nothing that has left."""
        contract = self._signed_contract()
        held = self._check(sale_contract_id=contract.id)
        banked = self._check(sale_contract_id=contract.id,
                             journal=self.journal_outstanding)
        self._deposit(banked, journal=self.journal_outstanding)

        text = self._text(
            self._render('action_report_pdc_acknowledgement', contract))
        self.assertIn(held.check_number, text)
        self.assertNotIn(banked.check_number, text)

    def test_the_slip_does_not_claim_the_money_arrived(self):
        deposit = self.presented.deposit_id
        text = self._text(
            self._render('action_report_deposit_slip', deposit)).lower()
        for phrase in ('cash collected', 'collected revenue', 'paid in full'):
            self.assertNotIn(phrase, text)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestSensitiveDataAccess(RegisterCommon):
    """§8 — an ordinary Sales user must not reach Treasury's data."""

    def setUp(self):
        super().setUp()
        # Every Developer right, no Treasury right. This is the realistic
        # profile of the person most likely to open a cheque record by
        # accident: a salesperson looking at their own contract.
        self.sales_user = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Sales Person',
                'login': 'sales.person@test.example',
                'email': 'sales.person@test.example',
                'company_id': self.company.id,
                'company_ids': [(6, 0, [self.company.id])],
                'groups_id': [(6, 0, [
                    self.env.ref('base.group_user').id,
                    self.env.ref(
                        'real_estate_developer.group_dev_sales_manager').id,
                ])],
            })

    def test_a_sales_user_has_no_treasury_group(self):
        for group in ('group_checks_user', 'group_checks_treasurer',
                      'group_checks_approver', 'group_checks_manager',
                      'group_checks_see_bank_details'):
            self.assertFalse(
                self.sales_user.has_group('real_estate_checks.%s' % group),
                "A Sales user holds %s" % group)

    @mute_logger('odoo.addons.base.models.ir_model', 'odoo.models')
    def test_a_sales_user_cannot_read_cheques_at_all(self):
        with self.assertRaises(AccessError):
            self.env['realestate.check'].with_user(
                self.sales_user).search([('id', '=', self.on_hand.id)])

    @mute_logger('odoo.addons.base.models.ir_model', 'odoo.models')
    def test_a_sales_user_cannot_read_custody_or_bounces(self):
        for model, record in (('realestate.check.custody',
                               self.on_hand.custody_ids[:1]),
                              ('realestate.check.bounce', self.bounce),
                              ('realestate.check.presentation',
                               self.presented.presentation_ids[:1])):
            with self.assertRaises(AccessError):
                self.env[model].with_user(self.sales_user).search(
                    [('id', 'in', record.ids)])

    def test_a_sales_user_still_sees_the_contract_totals(self):
        """The aggregate is fine; the instrument detail is not.

        This is why the contract's cheque statistics are computed with a
        sudo READ — a salesperson opening their own contract must not get an
        AccessError, but must not thereby gain access to cheque records.
        """
        contract = self._signed_contract()
        self._check(sale_contract_id=contract.id)
        as_sales = contract.with_user(self.sales_user)
        as_sales.invalidate_recordset()
        self.assertEqual(as_sales.check_count, 1)
        self.assertGreater(as_sales.check_amount_total, 0.0)

    def test_a_checks_user_sees_no_bank_details_on_the_form(self):
        """M32 — the fields are stripped from the arch, not merely hidden."""
        clerk = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Checks Clerk',
                'login': 'checks.clerk@test.example',
                'email': 'checks.clerk@test.example',
                'company_id': self.company.id,
                'company_ids': [(6, 0, [self.company.id])],
                'groups_id': [(6, 0, [
                    self.env.ref('base.group_user').id,
                    self.env.ref('real_estate_checks.group_checks_user').id,
                ])],
            })
        arch = self.env['realestate.check'].with_user(clerk).get_view(
            self.env.ref(
                'real_estate_checks.view_realestate_check_form').id,
            'form')['arch']
        for sensitive in ('account_number', 'partner_bank_id', 'branch'):
            self.assertNotIn(
                '<field name="%s"' % sensitive, arch,
                "A plain Checks User is served the %s field" % sensitive)
        # The notebook PAGE is also called "notes"; only the FIELD is gated.
        self.assertNotIn(
            '<field name="notes"', arch,
            "A plain Checks User is served the internal treasury notes")

    def test_a_treasury_officer_does_see_them(self):
        """The inverse, so the gate is not passing by hiding it from everyone."""
        officer = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Officer',
                'login': 'officer.view@test.example',
                'email': 'officer.view@test.example',
                'company_id': self.company.id,
                'company_ids': [(6, 0, [self.company.id])],
                'groups_id': [(6, 0, [
                    self.env.ref('base.group_user').id,
                    self.env.ref(
                        'real_estate_checks.group_checks_treasurer').id,
                ])],
            })
        arch = self.env['realestate.check'].with_user(officer).get_view(
            self.env.ref(
                'real_estate_checks.view_realestate_check_form').id,
            'form')['arch']
        for sensitive in ('account_number', 'partner_bank_id', 'branch'):
            self.assertIn('<field name="%s"' % sensitive, arch)

    def test_internal_bounce_notes_are_treasury_only(self):
        """A Checks User may see that a cheque bounced, not the file on it."""
        access = self.env['ir.model.access'].search([
            ('model_id.model', '=', 'realestate.check.bounce'),
            ('group_id', '=',
             self.env.ref('real_estate_checks.group_checks_user').id),
        ])
        self.assertTrue(access)
        self.assertTrue(access.perm_read)
        self.assertFalse(access.perm_write,
                         "A Checks User can edit bounce records")
        self.assertFalse(access.perm_create,
                         "A Checks User can create bounce records, which is "
                         "how a cheque gets bounced")

    def test_custody_is_read_only_below_treasury(self):
        access = self.env['ir.model.access'].search([
            ('model_id.model', '=', 'realestate.check.custody'),
            ('group_id', '=',
             self.env.ref('real_estate_checks.group_checks_user').id),
        ])
        self.assertTrue(access)
        self.assertFalse(access.perm_write)
        self.assertFalse(access.perm_create)
        self.assertFalse(access.perm_unlink)
