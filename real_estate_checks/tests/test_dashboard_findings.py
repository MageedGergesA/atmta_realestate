# -*- coding: utf-8 -*-
"""Regressions for what driving the Treasury dashboard by hand found.

The dashboard is now the Treasury application's own home screen
(`atmta_treasury_app.menu_dashboard` opens `action_check_dashboard`), so a
figure that reads wrong here is the first thing a treasurer sees every morning.

Each test states the manager's reading of the label and asserts the domain
answers that question — the discipline `check_dashboard.py` already applies to
paper versus cash, extended to the three confusions the payload still allowed.
"""

from odoo import fields
from odoo.tests.common import tagged

from .common import ChecksCommon


class DashboardFindingsCommon(ChecksCommon):

    def setUp(self):
        super().setUp()
        self.today = fields.Date.context_today(self.env['res.partner'])
        self.Dashboard = self.env['realestate.check.dashboard']

    def _kpi(self, data, section, key):
        for kpi in data[section]:
            if kpi['key'] == key:
                return kpi
        self.fail("No %r KPI in the %r section" % (key, section))

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


@tagged('post_install', '-at_install', 'atmta_checks')
class TestDepositsTodayIsPaperThatLeftTheBuilding(DashboardFindingsCommon):
    """#1 — "Deposits Today" counted slips still being typed up.

    A deposit slip is created in `draft` with `deposit_date` defaulting to
    today; the cheques on it stay `registered`, in the safe, and Odoo knows
    nothing about them. Counting that slip under "At the Bank" reported the
    same paper twice: once as held and once as deposited.
    """

    def test_a_draft_slip_is_not_a_deposit(self):
        held = self._check()
        draft = self._deposit(held, confirm=False)
        self.assertEqual(draft.state, 'draft')
        self.assertEqual(held.state, 'registered')

        data = self.Dashboard.get_dashboard_data()
        kpi = self._kpi(data, 'deposit', 'deposits_today')
        rows = self.env['realestate.check.deposit'].search(kpi['domain'])
        self.assertNotIn(
            draft, rows,
            "'%s' counts %s, which is still in the safe" % (kpi['label'],
                                                            draft.name))
        self.assertEqual(kpi['count'], 0)
        self.assertEqual(kpi['amount'], 0.0)

    def test_the_same_paper_is_not_reported_twice(self):
        held = self._check(amount=4000.0)
        self._deposit(held, confirm=False)
        data = self.Dashboard.get_dashboard_data()
        on_hand = self._kpi(data, 'on_hand', 'on_hand')
        self.assertIn(held, self.Check.search(on_hand['domain']))
        self.assertEqual(
            self._kpi(data, 'deposit', 'deposits_today')['amount'], 0.0,
            "the cheque is counted both On Hand and At the Bank")

    def test_a_confirmed_slip_is_counted(self):
        presented = self._check(amount=5000.0, journal=self.journal_outstanding)
        slip = self._deposit(presented, journal=self.journal_outstanding)
        self.assertEqual(slip.state, 'confirmed')

        data = self.Dashboard.get_dashboard_data()
        kpi = self._kpi(data, 'deposit', 'deposits_today')
        rows = self.env['realestate.check.deposit'].search(kpi['domain'])
        self.assertEqual(rows, slip)
        self.assertEqual(kpi['count'], 1)
        self.assertEqual(kpi['amount'], 5000.0)

    def test_a_cancelled_slip_is_still_excluded(self):
        held = self._check()
        draft = self._deposit(held, confirm=False)
        draft.action_cancel()
        data = self.Dashboard.get_dashboard_data()
        self.assertEqual(
            self._kpi(data, 'deposit', 'deposits_today')['count'], 0)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestBouncesAwaitingManualAccounting(DashboardFindingsCommon):
    """#2 — the Risk block lost sight of a receivable never put back.

    When the bank had already matched the receipt, `_restore_receivable`
    refuses to unwind it and leaves `requires_manual_accounting`: the cheque is
    bounced but the ledger still shows the customer as having paid. Recording a
    commercial resolution (a replacement cheque, say) takes the bounce out of
    "Unresolved Bounces" and the cheque out of "Bounced" — and nothing on the
    dashboard then says the ledger is still wrong. The bounce list already has
    this exact queue as its "Needs Manual Accounting" filter.
    """

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

    def test_the_ledger_backlog_has_its_own_figure(self):
        data = self.Dashboard.get_dashboard_data()
        kpi = data['risk']['manual_accounting']
        self.assertEqual(kpi['count'], 1)
        self.assertEqual(kpi['amount'], self.check.amount)
        self.assertEqual(kpi['kind'], 'paper')

    def test_it_survives_a_commercial_resolution(self):
        """The receivable is still overstated after the paper is replaced."""
        self.bounce.resolution = 'replaced'
        data = self.Dashboard.get_dashboard_data()
        self.assertEqual(data['risk']['unresolved_bounces']['count'], 0)
        self.assertEqual(
            data['risk']['manual_accounting']['count'], 1,
            "the ledger is still wrong and no figure says so")

    def test_it_clears_when_accounting_is_done(self):
        self.bounce.payment_id.move_id._reverse_moves(cancel=True)
        self.bounce.action_mark_accounting_done()
        data = self.Dashboard.get_dashboard_data()
        self.assertEqual(data['risk']['manual_accounting']['count'], 0)

    def test_the_figure_equals_the_records_it_opens(self):
        data = self.Dashboard.get_dashboard_data()
        kpi = data['risk']['manual_accounting']
        rows = self.env[kpi['model']].search(kpi['domain'])
        self.assertEqual(len(rows), kpi['count'])
        self.assertIn(self.bounce, rows)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestCoverageCardsDoNotOfferAnAccessError(DashboardFindingsCommon):
    """#3 — the Coverage cards drilled into a model Treasury cannot read.

    `_coverage_block` reads obligations with `sudo` on purpose, so a Treasury
    Officer who holds no Developer group still sees the coverage figures. It
    also published Developer's model and domain, and the template makes any
    card carrying them clickable — so the one role the block exists for got an
    AccessError for clicking it. A figure computed with elevated rights must
    not advertise a drill the user cannot take.
    """

    def setUp(self):
        super().setUp()
        self._signed_contract()
        self.officer = self._user('cov_officer', [
            'real_estate_checks.group_checks_treasurer'])

    def test_a_treasury_officer_cannot_read_obligations(self):
        self.assertFalse(
            self.env['realestate.sale.installment'].with_user(
                self.officer).has_access('read'))

    def test_the_card_is_not_clickable_for_them(self):
        data = self.Dashboard.with_user(self.officer).get_dashboard_data()
        card = data['coverage']['future_obligations']
        self.assertGreater(card['amount'], 0.0,
                           "the figure itself must survive")
        self.assertFalse(
            card.get('model'),
            "the card offers a drill into %s, which this user may not read"
            % card.get('model'))
        self.assertFalse(card.get('domain'))

    def test_a_user_who_may_read_them_keeps_the_drill(self):
        reader = self._user('cov_reader', [
            'real_estate_checks.group_checks_treasurer',
            'real_estate_developer.group_dev_readonly'])
        data = self.Dashboard.with_user(reader).get_dashboard_data()
        card = data['coverage']['future_obligations']
        self.assertEqual(card['model'], 'realestate.sale.installment')
        rows = self.env['realestate.sale.installment'].with_user(
            reader).search(card['domain'])
        self.assertEqual(len(rows), card['count'])
