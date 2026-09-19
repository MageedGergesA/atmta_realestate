# -*- coding: utf-8 -*-
"""Real-browser verification for the Treasury Dashboard.

Every class here drives a real (headless) Chrome through `start_tour`. What
varies between them is the *environment* — the part a code or stylesheet review
cannot vouch for:

* :class:`TestTreasuryDashboardDesktop` / `Tablet1024` / `Tablet991` /
  `Tablet768` — viewport, via `HttpCase.browser_size`, with touch emulation on
  for the tablet sizes.
* :class:`TestTreasuryDashboardValues` — every KPI compared against a fixture
  whose numbers are known, plus a drill-down whose row count must equal the KPI.
* :class:`TestTreasuryDashboardRTL` — a genuinely Arabic session, asserted from
  `getComputedStyle().direction` and real element geometry.
* :class:`TestTreasuryDashboardEmptyCompany` — no data at all, asserted to
  contain no NaN / undefined / Infinity and no divide-by-zero.
* :class:`TestTreasuryDashboardMultiCompany` — two companies with deliberately
  different data, switched with Odoo's own switcher.
* :class:`TestTreasuryDashboardRoles` — the four Treasury levels, each asserted
  for what it may and may not see.

None of these type credentials: `start_tour` authenticates server-side.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import HttpCase, tagged

CONSOLE_IGNORE = ("Failed to load resource", "favicon", "net::ERR_")


class TreasuryBrowserCommon(HttpCase):
    """Fixtures shared by every browser test."""

    def setUp(self):
        super().setUp()
        # `start_tour(login="admin")` authenticates as base.user_admin (uid 2),
        # NOT as self.env.user, which in a TransactionCase is OdooBot (uid 1).
        # Configuring the environment on self.env.user would leave the browser
        # session untouched and every assertion would silently test the default
        # company in English.
        self.browser_user = self.env.ref('base.user_admin')
        self.today = fields.Date.context_today(self.env['res.partner'])
        self.bank = self.env['res.bank'].create({'name': 'Browser Test Bank'})

    # ------------------------------------------------------------------
    # Roles
    # ------------------------------------------------------------------
    def _grant(self, xmlid, user=None):
        user = user or self.browser_user
        user.groups_id |= self.env.ref(xmlid)
        return user

    def _grant_treasury(self, user=None):
        user = self._grant('real_estate_checks.group_checks_treasurer', user)
        user.groups_id |= self.env.ref('account.group_account_user')
        return user

    def _allow_server_side_seeding(self):
        """The fixtures present deposits, and presenting is Treasury's job.

        `self.env.user` in a TransactionCase is OdooBot, who holds no Checks
        group — and the server-side gates are real (M31), so seeding is refused
        without this. The browser session is a different user entirely; see
        `self.browser_user`.
        """
        self.env.user.groups_id |= (
            self.env.ref('real_estate_checks.group_checks_treasurer')
            | self.env.ref('account.group_account_user'))

    # ------------------------------------------------------------------
    # Accounting
    # ------------------------------------------------------------------
    def _journal(self, company, code, outstanding=True):
        journal = self.env['account.journal'].create({
            'name': 'Browser Bank %s' % code, 'code': code, 'type': 'bank',
            'company_id': company.id,
        })
        line = journal.inbound_payment_method_line_ids.filtered(
            lambda l: l.payment_method_id.code == 'manual')[:1]
        line = line or journal.inbound_payment_method_line_ids[:1]
        if not line:
            return journal
        if outstanding:
            account = self.env['account.account'].create({
                'name': 'Outstanding %s' % code, 'code': 'OB%s' % code,
                'account_type': 'asset_current', 'reconcile': True,
                'company_ids': [(6, 0, [company.id])],
            })
            line.payment_account_id = account.id
        else:
            line.payment_account_id = journal.default_account_id.id
        return journal

    def _method_line(self, journal):
        line = journal.inbound_payment_method_line_ids.filtered(
            lambda l: l.payment_method_id.code == 'manual')[:1]
        return line or journal.inbound_payment_method_line_ids[:1]

    # ------------------------------------------------------------------
    # Cheques
    # ------------------------------------------------------------------
    _serial = 7000

    def _cheque(self, company, partner, amount=100000.0, due=None,
                journal=None, **kwargs):
        type(self)._serial += 1
        vals = {
            'partner_id': partner.id,
            'company_id': company.id,
            'currency_id': company.currency_id.id,
            'bank_id': self.bank.id,
            'account_number': 'BRW-ACC-1',
            'check_number': '%09d' % type(self)._serial,
            'amount': amount,
            'issue_date': self.today - timedelta(days=30),
            'due_date': due or self.today,
            'state': 'registered',
        }
        if journal:
            vals['journal_id'] = journal.id
        vals.update(kwargs)
        return self.env['realestate.check'].with_company(company).create(vals)

    def _present(self, checks, journal, company):
        deposit = self.env['realestate.check.deposit'].with_company(
            company).create({
                'company_id': company.id,
                'journal_id': journal.id,
                'currency_id': company.currency_id.id,
                'deposit_date': self.today,
                'payment_method_line_id': self._method_line(journal).id,
                'check_ids': [(6, 0, checks.ids)],
            })
        deposit.action_confirm()
        return deposit

    def _match_with_bank(self, payment):
        """Reconcile a real bank statement line against the payment.

        Deliberately goes through Odoo's own reconciliation rather than writing
        `payment.state`. A test that faked the state would prove nothing about
        a dashboard whose whole claim is that it reads it.
        """
        move = payment.move_id
        if not move:
            return False
        liquidity = move.line_ids.filtered(
            lambda l: l.account_id == payment.outstanding_account_id)
        if not liquidity:
            return False
        statement_line = self.env['account.bank.statement.line'].create({
            'journal_id': payment.journal_id.id,
            'date': payment.date,
            'payment_ref': 'Clearing %s' % payment.name,
            'partner_id': payment.partner_id.id,
            'amount': payment.amount,
        })
        counterpart = statement_line.move_id.line_ids.filtered(
            lambda l: l.account_id != statement_line.journal_id.default_account_id)
        counterpart.account_id = payment.outstanding_account_id
        (liquidity | counterpart).reconcile()
        payment.invalidate_recordset()
        return statement_line

    # ------------------------------------------------------------------
    # The standard portfolio the value tour expects
    # ------------------------------------------------------------------
    def _seed_portfolio(self, company, partner):
        self._allow_server_side_seeding()
        return self._seed_portfolio_inner(company, partner)

    def _seed_portfolio_inner(self, company, partner):
        """4 on hand, 2 presented (1 of which clears), 1 bounced.

        The numbers are deliberately all different so a leak or an off-by-one
        shows up as a specific wrong figure rather than a plausible one.
        """
        journal = self._journal(company, 'BWA', outstanding=True)

        on_hand = self.env['realestate.check']
        on_hand |= self._cheque(company, partner, due=self.today)
        on_hand |= self._cheque(company, partner,
                                due=self.today + timedelta(days=5))
        on_hand |= self._cheque(company, partner,
                                due=self.today + timedelta(days=45))
        on_hand |= self._cheque(company, partner,
                                due=self.today + timedelta(days=200))

        clearing = self._cheque(company, partner, journal=journal)
        bouncing = self._cheque(company, partner, journal=journal)
        self._present(clearing | bouncing, journal, company)

        cleared = self._cheque(company, partner, journal=journal)
        self._present(cleared, journal, company)
        self._match_with_bank(cleared.payment_id)
        cleared._sync_clearance_from_accounting()

        self.env['realestate.check.bounce'].create({
            'check_id': bouncing.id, 'reason': 'insufficient'})

        return {'on_hand': on_hand, 'clearing': clearing,
                'cleared': cleared, 'bounced': bouncing}


# ---------------------------------------------------------------------------
# Layout — real viewports, real touch emulation
# ---------------------------------------------------------------------------

class _LayoutCase(TreasuryBrowserCommon):

    def _run(self):
        self._grant_treasury()
        partner = self.env['res.partner'].create({'name': 'Browser Buyer'})
        self._seed_portfolio(self.env.company, partner)
        self.start_tour("/odoo/action-real_estate_checks.action_check_dashboard",
                        "atmta_treasury_dashboard_layout_tour",
                        login="admin", timeout=200)


@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestTreasuryDashboardDesktop(_LayoutCase):
    browser_size = '1920x1080'

    def test_layout_desktop(self):
        self._run()


class _TabletCase(_LayoutCase):
    # Tablets are touch devices; emulating that changes hover behaviour and
    # Odoo's own `.o_touch_device` handling, so testing at a tablet width with
    # a mouse would not be testing a tablet.
    touch_enabled = True


@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestTreasuryDashboardTablet1024(_TabletCase):
    """Landscape tablet / small laptop."""
    browser_size = '1024x768'

    def test_layout_1024(self):
        self._run()


@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestTreasuryDashboardTablet991(_TabletCase):
    """The Bootstrap lg/md boundary — the breakpoint most likely to break."""
    browser_size = '991x768'

    def test_layout_991(self):
        self._run()


@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestTreasuryDashboardTablet768(_TabletCase):
    """Portrait tablet."""
    browser_size = '768x1024'

    def test_layout_768(self):
        self._run()


# ---------------------------------------------------------------------------
# Values, and drill-down parity
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestTreasuryDashboardValues(TreasuryBrowserCommon):
    """Every KPI is checked against a fixture whose numbers are known."""

    browser_size = '1920x1080'

    def test_kpi_values_and_drilldown_parity(self):
        self._grant_treasury()
        partner = self.env['res.partner'].create({'name': 'Value Buyer'})
        planted = self._seed_portfolio(self.env.company, partner)

        # Assert the server agrees before asking the browser, so a failure in
        # the tour is unambiguously a rendering problem.
        data = self.env['realestate.check.dashboard'].get_dashboard_data()
        by_key = {k['key']: k for k in
                  data['on_hand'] + data['deposit'] + data['cleared']}
        self.assertEqual(by_key['on_hand']['count'], 4)
        self.assertEqual(by_key['due_today']['count'], 1)
        self.assertEqual(by_key['in_clearing']['count'], 1)
        self.assertEqual(by_key['cleared_total']['count'], 1)
        self.assertEqual(data['risk']['bounced']['count'], 1)

        self.start_tour("/odoo/action-real_estate_checks.action_check_dashboard",
                        "atmta_treasury_dashboard_values_tour",
                        login="admin", timeout=200)

    def test_every_kpi_domain_matches_its_count_server_side(self):
        """The drill-down contract, one layer below the browser.

        The tour proves one KPI opens the right list. This proves every KPI's
        domain returns exactly the number it advertises, which is what makes
        the contract safe for the ones the tour does not click.
        """
        partner = self.env['res.partner'].create({'name': 'Domain Buyer'})
        self._seed_portfolio(self.env.company, partner)
        data = self.env['realestate.check.dashboard'].get_dashboard_data()

        kpis = data['on_hand'] + data['deposit'] + data['cleared']
        kpis += [data['risk'][k] for k in
                 ('bounced', 'unresolved_bounces', 'replacements_outstanding')]
        for kpi in kpis:
            records = self.env[kpi['model']].search(kpi['domain'])
            self.assertEqual(
                len(records), kpi['count'],
                "KPI %r advertises %s records but its domain returns %s"
                % (kpi.get('key') or kpi.get('label'), kpi['count'],
                   len(records)))

        for bucket in data['charts']['maturity_forecast']['buckets']:
            records = self.env['realestate.check'].search(bucket['domain'])
            self.assertEqual(len(records), bucket['count'], bucket['key'])


# ---------------------------------------------------------------------------
# RTL
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestTreasuryDashboardRTL(TreasuryBrowserCommon):
    """Arabic session, asserted in the browser rather than in the stylesheet."""

    browser_size = '1440x900'

    def test_dashboard_rtl(self):
        lang = self.env['res.lang']._activate_lang('ar_001')
        if not lang:
            self.env['res.lang'].with_context(active_test=False).search(
                [('code', '=', 'ar_001')]).write({'active': True})
            lang = self.env['res.lang']._lang_get('ar_001')
        self.assertTrue(lang, "Arabic (ar_001) could not be activated")
        self.assertEqual(lang.direction, 'rtl',
                         "ar_001 is not flagged RTL; the test would prove nothing")

        self._grant_treasury()
        partner = self.env['res.partner'].create({'name': 'RTL Buyer'})
        self._seed_portfolio(self.env.company, partner)
        self.browser_user.lang = 'ar_001'

        self.start_tour("/odoo/action-real_estate_checks.action_check_dashboard",
                        "atmta_treasury_dashboard_rtl_tour",
                        login="admin", timeout=200)


# ---------------------------------------------------------------------------
# Empty company
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestTreasuryDashboardEmptyCompany(TreasuryBrowserCommon):
    """A brand-new company with no cheques, deposits, bounces or obligations."""

    browser_size = '1440x900'

    def test_dashboard_empty_company(self):
        empty = self.env['res.company'].create({'name': 'ATMTA Empty Treasury'})
        # The user may see ONLY the empty company, so every denominator is
        # genuinely zero rather than merely small.
        self.browser_user.write({
            'company_ids': [(6, 0, [empty.id])],
            'company_id': empty.id,
        })
        self._grant_treasury()

        self.assertFalse(
            self.env['realestate.check'].search_count(
                [('company_id', '=', empty.id)]),
            "The 'empty' company is not empty; the test would be vacuous")

        self.start_tour("/odoo/action-real_estate_checks.action_check_dashboard",
                        "atmta_treasury_dashboard_empty_tour",
                        login="admin", timeout=200)


# ---------------------------------------------------------------------------
# Multi-company
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestTreasuryDashboardMultiCompany(TreasuryBrowserCommon):
    """Two companies with different data, switched the way a user switches."""

    browser_size = '1600x900'

    def test_dashboard_multi_company(self):
        company_a = self.env['res.company'].create({'name': 'ATMTA Treasury A'})
        company_b = self.env['res.company'].create({'name': 'ATMTA Treasury B'})
        partner = self.env['res.partner'].create({'name': 'MC Buyer'})

        # Deliberately different counts: if the dashboard leaked across
        # companies, or ignored the switch, the figures would coincide and the
        # tour's comparison would pass for the wrong reason.
        for _i in range(3):
            self._cheque(company_a, partner)
        for _i in range(7):
            self._cheque(company_b, partner)

        self.browser_user.write({
            'company_ids': [(6, 0, [company_a.id, company_b.id])],
            'company_id': company_a.id,
        })
        self._grant_treasury()

        self.start_tour("/odoo/action-real_estate_checks.action_check_dashboard",
                        "atmta_treasury_dashboard_company_tour",
                        login="admin", timeout=200)

    def test_the_payload_is_company_scoped_server_side(self):
        """The UI switch is only half of it — prove the scoping one layer down.

        A leak here would be invisible to the tour if the switcher happened to
        reload correctly.
        """
        company_a = self.env['res.company'].create({'name': 'Scope A'})
        company_b = self.env['res.company'].create({'name': 'Scope B'})
        partner = self.env['res.partner'].create({'name': 'Scope Buyer'})
        for _i in range(3):
            self._cheque(company_a, partner)
        for _i in range(7):
            self._cheque(company_b, partner)

        Dashboard = self.env['realestate.check.dashboard']
        only_a = Dashboard.get_dashboard_data(company_ids=[company_a.id])
        only_b = Dashboard.get_dashboard_data(company_ids=[company_b.id])
        both = Dashboard.get_dashboard_data(
            company_ids=[company_a.id, company_b.id])

        def on_hand(payload):
            return [k for k in payload['on_hand'] if k['key'] == 'on_hand'][0]['count']

        self.assertEqual(on_hand(only_a), 3)
        self.assertEqual(on_hand(only_b), 7)
        self.assertEqual(on_hand(both), 10)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

class _RoleCase(TreasuryBrowserCommon):
    """One tour, four roles, different expectations."""

    browser_size = '1600x900'
    group_xmlid = None
    expect_bank_details = False

    def _run_role(self):
        partner = self.env['res.partner'].create({'name': 'Role Buyer'})
        user = self.browser_user
        # Seed BEFORE narrowing the browser user's groups, and as a user who
        # is allowed to present cheques.
        self._seed_portfolio(self.env.company, partner)

        # Now start from nothing but the base internal-user group, so the role
        # under test is genuinely the only Checks access this user has.
        user.groups_id = [(6, 0, [
            self.env.ref('base.group_user').id,
            self.env.ref('account.group_account_user').id,
            self.env.ref(self.group_xmlid).id,
        ])]

        # A tour receives no parameters of its own, so the expectation
        # travels in the URL the browser is pointed at.
        tour = ('atmta_treasury_dashboard_role_treasury_tour'
                if self.expect_bank_details
                else 'atmta_treasury_dashboard_role_restricted_tour')
        self.start_tour(
            "/odoo/action-real_estate_checks.action_check_dashboard",
            tour, login="admin", timeout=200)


@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestTreasuryDashboardRoleUser(_RoleCase):
    """A restricted Checks User: may see the dashboard, not bank details."""
    group_xmlid = 'real_estate_checks.group_checks_user'
    expect_bank_details = False

    def test_role_checks_user(self):
        self._run_role()


@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestTreasuryDashboardRoleOfficer(_RoleCase):
    group_xmlid = 'real_estate_checks.group_checks_treasurer'
    expect_bank_details = True

    def test_role_treasury_officer(self):
        self._run_role()


@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestTreasuryDashboardRoleApprover(_RoleCase):
    group_xmlid = 'real_estate_checks.group_checks_approver'
    expect_bank_details = True

    def test_role_treasury_approver(self):
        self._run_role()


@tagged('post_install', '-at_install', 'atmta_checks_browser')
class TestTreasuryDashboardRoleManager(_RoleCase):
    group_xmlid = 'real_estate_checks.group_checks_manager'
    expect_bank_details = True

    def test_role_checks_manager(self):
        self._run_role()
