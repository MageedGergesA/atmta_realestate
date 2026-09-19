# -*- coding: utf-8 -*-
"""Regressions for what the treasury lifecycle run found.

Each test reproduces a reported defect through the same entry point a user
reaches it from — the workbench, the cheque form, the bounce form, the cron —
rather than by poking the model underneath.
"""

from datetime import timedelta

from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form
from odoo.tests.common import tagged
from odoo.tools.safe_eval import safe_eval

from .common import ChecksCommon


class FindingsCommon(ChecksCommon):

    def setUp(self):
        super().setUp()
        self.today = fields.Date.context_today(self.env['res.partner'])

    def _user(self, login, groups):
        return self.env['res.users'].with_context(
            no_reset_password=True, mail_create_nosubscribe=True).create({
            'name': login, 'login': '%s@test.example' % login,
            'email': '%s@test.example' % login,
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id] + [
                self.env.ref(xmlid).id for xmlid in groups])],
        })

    def _workbench(self, checks):
        return self.env['realestate.check.deposit.wizard'].create({
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'currency_id': self.company.currency_id.id,
            'payment_method_line_id': self._method_line(self.journal).id,
            'check_ids': [(6, 0, checks.ids)],
        })

    def _bounced(self):
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        bounce = self.Bounce.create({
            'check_id': check.id, 'reason': 'insufficient'})
        return check, bounce

    def _button_invisible(self, view_xmlid, button, values):
        arch = etree.fromstring(self.env.ref(view_xmlid).arch)
        node = arch.xpath("//button[@name='%s']" % button)[0]
        return bool(safe_eval(node.get('invisible') or 'False', values))


@tagged('post_install', '-at_install', 'atmta_checks')
class TestWorkbenchMakerChecker(FindingsCommon):
    """#1 — with maker/checker on, nobody could deposit through the workbench."""

    def setUp(self):
        super().setUp()
        self.company.check_maker_checker = True
        self.company.check_self_approve_limit = 0.0

    def test_the_workbench_leaves_the_slip_for_another_user(self):
        check = self._check()
        action = self._workbench(check).action_create_deposit()
        deposit = self.Deposit.browse(action['res_id'])
        self.assertEqual(action['res_model'], 'realestate.check.deposit')
        self.assertEqual(deposit.state, 'draft')
        self.assertEqual(deposit.prepared_by_id, self.env.user)
        self.assertEqual(deposit.check_ids, check)

        approver = self._user('checker', [
            'real_estate_checks.group_checks_approver',
            'account.group_account_user'])
        deposit.with_user(approver).with_context(
            tracking_disable=True, mail_notrack=True).action_confirm()
        self.assertEqual(deposit.state, 'confirmed')
        self.assertEqual(deposit.confirmed_by_id, approver)

    def test_under_the_limit_the_workbench_still_confirms(self):
        self.company.check_self_approve_limit = 500000.0
        check = self._check(amount=1000.0)
        action = self._workbench(check).action_create_deposit()
        self.assertEqual(self.Deposit.browse(action['res_id']).state,
                         'confirmed')


@tagged('post_install', '-at_install', 'atmta_checks')
class TestCancelledSlipReleasesCheques(FindingsCommon):
    """#3 — cancelling a draft slip stranded its cheques."""

    def test_cancelling_a_draft_slip_frees_its_cheques(self):
        checks = self._check() | self._check()
        deposit = self._deposit(checks, confirm=False)
        deposit.action_cancel()
        for check in checks:
            self.assertFalse(check.deposit_id)
            self.assertEqual(check.state, 'registered')
        wizard = self._workbench(checks)
        self.assertEqual(wizard.eligible_check_ids & checks, checks)
        checks[0].action_back_to_draft()
        self.assertEqual(checks[0].state, 'draft')

    def test_the_cancel_button_is_hidden_where_it_always_refuses(self):
        """U1 — the method keeps its guard; the button stops offering it."""
        view = 'real_estate_checks.view_check_deposit_form'
        for state, live, hidden in (('draft', False, False),
                                    ('confirmed', True, True),
                                    ('confirmed', False, False),
                                    ('partial', False, True),
                                    ('partial', True, True)):
            self.assertEqual(
                self._button_invisible(view, 'action_cancel', {
                    'state': state, 'has_live_payments': live}),
                hidden, 'state=%s live payments=%s' % (state, live))
        check = self._check()
        deposit = self._deposit(check)
        self.assertTrue(deposit.has_live_payments)
        with self.assertRaises(UserError):
            deposit.action_cancel()


@tagged('post_install', '-at_install', 'atmta_checks')
class TestManualAccountingCanBeClosed(FindingsCommon):
    """#4 — a bounce left for manual accounting could never be closed."""

    def setUp(self):
        super().setUp()
        contract = self._signed_contract()
        target = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        self.invoice = self._invoice_installment(target)
        self.check = self._check(
            amount=self.invoice.amount_total, sale_contract_id=contract.id,
            sale_installment_id=target.id, journal=self.journal_outstanding)
        self._deposit(self.check, journal=self.journal_outstanding)
        self._reconcile_with_bank(self.check.payment_id)
        self.check._sync_clearance_from_accounting()
        self.check.write({'state': 'deposited'})
        self.bounce = self.Bounce.create({
            'check_id': self.check.id, 'reason': 'technical'})
        self.assertTrue(self.bounce.requires_manual_accounting)
        self.assertFalse(self.bounce.accounting_handled)

    def test_refused_while_the_receipt_still_settles_the_invoice(self):
        with self.assertRaises(UserError) as err:
            self.bounce.action_mark_accounting_done()
        self.assertIn('still reconciled', str(err.exception))
        self.assertFalse(self.bounce.accounting_handled)

    def test_closed_once_accounting_reversed_the_payment(self):
        self.bounce.payment_id.move_id._reverse_moves(cancel=True)
        self.invoice.invalidate_recordset()
        self.assertTrue(self.invoice.amount_residual > 0)
        self.bounce.action_mark_accounting_done()
        self.assertTrue(self.bounce.accounting_handled)
        # The re-presentation that was blocked is now possible.
        self.bounce.action_authorize_representation()
        self.assertEqual(self.check.state, 'registered')

    def test_only_treasury_may_close_it(self):
        self.bounce.payment_id.move_id._reverse_moves(cancel=True)
        clerk = self._user('bounce_clerk', [
            'real_estate_checks.group_checks_user',
            'account.group_account_user'])
        with self.assertRaises(AccessError):
            self.bounce.with_user(clerk).action_mark_accounting_done()

    def test_the_buttons_follow_the_manual_accounting_state(self):
        """#4 — the button is offered exactly while the work is pending."""
        view = 'real_estate_checks.view_check_bounce_form'
        pending = {'resolution': 'pending', 'requires_manual_accounting': True,
                   'accounting_handled': False}
        done = dict(pending, accounting_handled=True)
        self.assertFalse(self._button_invisible(
            view, 'action_mark_accounting_done', pending))
        self.assertTrue(self._button_invisible(
            view, 'action_mark_accounting_done', done))


@tagged('post_install', '-at_install', 'atmta_checks')
class TestBounceResolvedFromTheChequeForm(FindingsCommon):
    """#5 — acting on a bounced cheque left its bounce pending."""

    def test_replacing_from_the_cheque_form(self):
        check, bounce = self._bounced()
        action = check.action_open_replacement_wizard()
        wizard = self.env['realestate.check.replace.wizard'].with_context(
            **action['context']).create({
                'new_partner_id': check.partner_id.id,
                'check_number': '%s-R' % check.check_number,
                'bank_id': check.bank_id.id,
                'amount': check.amount,
                'issue_date': self.today,
                'due_date': self.today,
            })
        self.assertFalse(wizard.bounce_id)
        wizard.action_replace()
        self.assertEqual(bounce.resolution, 'replaced')
        self.assertEqual(bounce.replacement_check_id,
                         check.replacement_check_id)
        self.assertTrue(bounce.resolved_date)

    def test_returning_a_bounced_cheque(self):
        check, bounce = self._bounced()
        check.action_return_to_customer()
        self.assertEqual(bounce.resolution, 'cancelled')

    def test_cancelling_a_bounced_cheque(self):
        check, bounce = self._bounced()
        check.action_cancel()
        self.assertEqual(bounce.resolution, 'cancelled')


@tagged('post_install', '-at_install', 'atmta_checks')
class TestReturnMovesCustody(FindingsCommon):
    """#6 — after Return to Customer the cheque kept its old custody."""

    def test_the_return_is_the_latest_movement(self):
        check = self._check()
        vault = self.Location.create({
            'name': 'Branch Vault', 'code': 'BRV', 'kind': 'safe',
            'company_id': self.company.id,
        })
        self.Custody.transfer(check, to_location=vault, reason='transfer')
        self.assertEqual(check.location_id, vault)
        check.action_return_to_customer()
        check.invalidate_recordset()
        self.assertFalse(check.location_id)
        self.assertFalse(check.custodian_id)
        latest = check.custody_ids.sorted(
            lambda m: (m.date, m.id), reverse=True)[0]
        self.assertEqual(latest.reason, 'return_to_customer')
        self.assertEqual(latest.from_location_id, vault)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestAlertsDedupedPerKey(FindingsCommon):
    """#7 — the due-soon reminder blocked the overdue one."""

    def _activities(self, check, key):
        model_id = self.env['ir.model']._get_id('realestate.check')
        return self.env['mail.activity'].search([
            ('res_model_id', '=', model_id), ('res_id', '=', check.id),
            ('summary', '=like', '[%s]%%' % key)])

    def test_overdue_follows_due_soon(self):
        self.company.check_due_soon_days = 7
        self.company.check_overdue_presentation_days = 0
        check = self._check(due_date=self.today + timedelta(days=3))
        self.Check._cron_due_soon()
        self.assertEqual(len(self._activities(check, 'due-soon')), 1)

        # Time passes: the cheque matured and is still in the safe.
        check.write({'issue_date': self.today - timedelta(days=30),
                     'due_date': self.today - timedelta(days=5)})
        self.Check._cron_overdue_presentation()
        self.assertEqual(len(self._activities(check, 'overdue')), 1)
        self.Check._cron_overdue_presentation()
        self.Check._cron_due_soon()
        self.assertEqual(len(self._activities(check, 'overdue')), 1)
        self.assertEqual(len(self._activities(check, 'due-soon')), 1)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestBulkWizardPastDueInstalment(FindingsCommon):
    """#8 — one past-due instalment aborted the whole bulk generation."""

    def test_a_past_due_instalment_gets_a_valid_cheque(self):
        contract = self._signed_contract(
            contract_date=self.today - timedelta(days=60))
        past = contract.installment_ids.filtered(
            lambda i: i.date_due < self.today)
        self.assertTrue(past, 'the fixture has no past-due instalment')
        wizard = self.env['realestate.check.bulk.wizard'].create({
            'sale_contract_id': contract.id,
            'bank_id': self.bank.id,
            'first_check_number': '000000701',
        })
        wizard.action_preview()
        lines = wizard.preview_line_ids.filtered(
            lambda l: l.installment_id in past)
        self.assertTrue(lines)
        for line in lines:
            self.assertEqual(line.due_date, self.today)
            self.assertTrue(line.note)
        future = wizard.preview_line_ids - lines
        self.assertFalse(any(future.mapped('note')))

        wizard.action_generate()
        checks = self.Check.search([('sale_contract_id', '=', contract.id)])
        self.assertEqual(len(checks), len(wizard.preview_line_ids))
        for check in checks.filtered(lambda c: c.sale_installment_id in past):
            self.assertEqual(check.due_date, self.today)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestReinstatedChequeAllocations(FindingsCommon):
    """#13 — reinstating a cancelled cheque left its allocations cancelled."""

    def setUp(self):
        super().setUp()
        self.contract = self._signed_contract()
        self.target = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]

    def _allocated_check(self):
        return self._check(amount=100000.0, state='registered',
                           sale_contract_id=self.contract.id,
                           sale_installment_id=self.target.id)

    def test_allocations_come_back_with_the_cheque(self):
        check = self._allocated_check()
        allocation = check.allocation_ids
        self.assertEqual(allocation.state, 'active')
        check.action_cancel()
        self.assertEqual(allocation.state, 'cancelled')
        check.action_back_to_draft()
        check.action_register()
        self.assertEqual(allocation.state, 'active')
        self.assertEqual(check.sale_installment_id, self.target)
        self.assertEqual(check.unapplied_amount, 0.0)

    def test_an_allocation_no_longer_valid_is_reported(self):
        check = self._allocated_check()
        allocation = check.allocation_ids
        check.action_cancel()
        # Meanwhile the instalment was covered in full by other paper.
        self._check(amount=self.target.current_amount, state='registered',
                    sale_contract_id=self.contract.id,
                    sale_installment_id=self.target.id)
        check.action_back_to_draft()
        self.assertEqual(allocation.state, 'cancelled')
        self.assertEqual(check.state, 'draft')
        self.assertIn('could not be restored', check.message_ids[0].body)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestDeveloperOnlyUserOpensTheContractForm(FindingsCommon):
    """A salesperson holds no Checks group and opens contracts all day.

    The lifecycle run found the contract form itself refusing to open for a
    Sales Agent and a Commercial Manager, on 'Cheque Allocation to an
    Obligation'. The form is opened the way the web client opens it.
    """

    def setUp(self):
        super().setUp()
        self.agent = self._user('chk_find_agent', [
            'real_estate_developer.group_dev_agent'])
        self.commercial = self._user('chk_find_commercial', [
            'real_estate_developer.group_dev_commercial_manager'])
        self.contract = self._signed_contract()
        target = self.contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        self._check(amount=100000.0, state='registered',
                    sale_contract_id=self.contract.id,
                    sale_installment_id=target.id)
        self.env.invalidate_all()

    def test_developer_users_open_the_contract_form(self):
        for user in (self.agent, self.commercial):
            with self.subTest(user=user.login):
                Form(self.Contract.with_user(user))
                # Salespeople read the deals they carry (Developer's rule).
                self.contract.agent_id = user
                form = Form(self.contract.with_user(user))
                # Aggregate amounts only, as the sudo design intends.
                self.assertEqual(form.check_count, 1)
                self.assertEqual(form.pdc_secured_amount, 100000.0)

    def test_developer_users_open_an_instalment(self):
        """Clicking a row of the contract's instalment list, as the client does.

        Instalments have no form view of their own, so the client asks for the
        generated one and reads every field it names.
        """
        installment = self.contract.installment_ids[:1]
        for user in (self.agent, self.commercial):
            with self.subTest(user=user.login):
                Installment = self.Installment.with_user(user)
                views = Installment.get_views([(False, 'form')])
                fnames = views['models'][Installment._name]['fields']
                spec = {fname: {} for fname in fnames}
                [values] = installment.with_user(user).web_read(spec)
                self.assertEqual(values['secured_by_checks_amount'], 100000.0)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestRentalOnlyUserOpensARentalPayment(FindingsCommon):
    """Rental's twin of the Developer finding above.

    A Rental user holds no Checks group. Opening a rental payment asks for
    every field its form names; the cheque links on the payment must not refuse
    the form on 'Cheque Allocation to an Obligation'.
    """

    def setUp(self):
        super().setUp()
        self.rental = self._user('chk_find_rental', [
            'atmta_real_estate.group_rental_user'])
        tenant = self.env['res.partner'].create({'name': 'Tenant Finding'})
        unit = self.units[2]
        contract = self._rental_contract(tenant, unit)
        self.rent_payment = self.env['realestate.contract.payment'].create({
            'contract_id': contract.id,
            'property_id': unit.id,
            'date_due': self.today,
            'amount': 50000.0,
        })
        self._check(amount=50000.0, partner=tenant,
                    rental_contract_id=contract.id,
                    rental_payment_id=self.rent_payment.id)
        self.env.invalidate_all()

    def _read(self, fnames):
        spec = {fname: {} for fname in fnames}
        [values] = self.rent_payment.with_user(self.rental).web_read(spec)
        return values

    def test_the_rental_payment_form_opens(self):
        Payment = self.env['realestate.contract.payment'].with_user(
            self.rental)
        views = Payment.get_views([(False, 'form')])
        self._read(views['models'][Payment._name]['fields'])

    def test_the_generated_rental_payment_form_opens(self):
        """The form the client generates when no form view is named."""
        Payment = self.env['realestate.contract.payment'].with_user(
            self.rental)
        arch = Payment._get_default_form_view()
        # Like the view post-processing, drop the fields this user's groups
        # exclude; a field without `groups` stays in and is read.
        fnames = [node.get('name') for node in arch.iter('field')
                  if Payment._fields[node.get('name')].is_accessible(
                      Payment.env)]
        values = self._read(fnames)
        # Aggregates only, computed with sudo as on the instalment.
        self.assertEqual(values['secured_by_checks_amount'], 50000.0)
        self.assertEqual(values['check_count'], 1)
