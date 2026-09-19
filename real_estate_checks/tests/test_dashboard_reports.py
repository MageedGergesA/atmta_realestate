# -*- coding: utf-8 -*-
"""M20 / M21 / M25 / M33 — the dashboard, forecast, registers and reports.

Two things are asserted throughout: every count matches its own drill-down, and
nothing that is paper is ever labelled as cash.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import tagged

from .common import ChecksCommon


@tagged('post_install', '-at_install', 'atmta_checks')
class TestDashboardPayload(ChecksCommon):

    def setUp(self):
        super().setUp()
        self.today = fields.Date.context_today(self.env['res.partner'])
        self.Dashboard = self.env['realestate.check.dashboard']
        self.on_hand = self._check(due_date=self.today)
        self.future = self._check(due_date=self.today + timedelta(days=45))
        self.presented = self._check(journal=self.journal_outstanding)
        self._deposit(self.presented, journal=self.journal_outstanding)
        self.cleared = self._check(journal=self.journal_direct)
        self._deposit(self.cleared, journal=self.journal_direct)
        self.cleared._sync_clearance_from_accounting()

    def test_the_payload_loads(self):
        data = self.Dashboard.get_dashboard_data()
        for section in ('on_hand', 'deposit', 'cleared', 'risk', 'coverage',
                        'charts', 'currency_warning'):
            self.assertIn(section, data)

    def test_every_kpi_declares_whether_it_is_paper_or_cash(self):
        """M20 — never label 'PDC amount received' as 'cash collected'."""
        data = self.Dashboard.get_dashboard_data()
        for section in ('on_hand', 'deposit'):
            for kpi in data[section]:
                self.assertEqual(kpi['kind'], 'paper',
                                 '%s is not paper?' % kpi['key'])
        for kpi in data['cleared']:
            self.assertEqual(kpi['kind'], 'cash')

    def test_the_coverage_block_labels_paper_as_paper(self):
        data = self.Dashboard.get_dashboard_data()
        received = data['coverage']['pdc_received']
        self.assertEqual(received['kind'], 'paper')
        self.assertIn('NOT cash collected', received['note'])

    def test_every_kpi_count_equals_its_drilldown(self):
        """The discipline from Rental Dashboard V2: a count and its list can
        never disagree, because they are the same domain."""
        data = self.Dashboard.get_dashboard_data()
        kpis = data['on_hand'] + data['deposit'] + data['cleared']
        for kpi in kpis:
            records = self.env[kpi['model']].search(kpi['domain'])
            self.assertEqual(
                len(records), kpi['count'],
                'KPI %s says %s but its drill-down returns %s'
                % (kpi['key'], kpi['count'], len(records)))

    def test_risk_domains_match_too(self):
        data = self.Dashboard.get_dashboard_data()
        for key in ('bounced', 'unresolved_bounces',
                    'replacements_outstanding'):
            block = data['risk'][key]
            records = self.env[block['model']].search(block['domain'])
            self.assertEqual(len(records), block['count'], key)

    def test_the_drilldown_action_validates_its_model(self):
        with self.assertRaises(ValueError):
            self.Dashboard.action_drilldown('res.users', [])
        action = self.Dashboard.action_drilldown(
            'realestate.check', [('id', '=', self.on_hand.id)])
        self.assertEqual(action['res_model'], 'realestate.check')

    def test_the_bounce_rate_denominator_is_presented_cheques(self):
        """Dividing by the whole portfolio would flatter it with paper that
        has never been near a bank."""
        data = self.Dashboard.get_dashboard_data()
        rate = data['risk']['bounce_rate']
        self.assertIn('presented', rate['basis'])
        self.assertEqual(rate['value'], 0.0)

        bounced = self._check(journal=self.journal_outstanding)
        self._deposit(bounced, journal=self.journal_outstanding)
        self.Bounce.create({'check_id': bounced.id, 'reason': 'insufficient'})
        data = self.Dashboard.get_dashboard_data()
        self.assertGreater(data['risk']['bounce_rate']['value'], 0.0)

    def test_the_payload_is_company_scoped(self):
        other = self.env['res.company'].create({'name': 'Other Co'})
        data = self.Dashboard.get_dashboard_data(company_ids=[other.id])
        self.assertEqual(data['on_hand'][0]['count'], 0)

    def test_multi_currency_is_flagged_not_summed(self):
        """M30 — do not add EGP to SAR and present one number."""
        data = self.Dashboard.get_dashboard_data()
        self.assertFalse(data['currency_warning']['multi_currency'])

        self._check(currency_id=self._other_currency().id)
        data = self.Dashboard.get_dashboard_data()
        self.assertTrue(data['currency_warning']['multi_currency'])
        self.assertIn('NOT converted', data['currency_warning']['message'])


@tagged('post_install', '-at_install', 'atmta_checks')
class TestMaturityForecast(ChecksCommon):
    """M21 — what should mature next month, without calling it cash."""

    def setUp(self):
        super().setUp()
        self.today = fields.Date.context_today(self.env['res.partner'])
        self.Dashboard = self.env['realestate.check.dashboard']

    def test_every_bucket_is_present_even_when_empty(self):
        forecast = self.Dashboard.get_maturity_forecast()
        keys = [b['key'] for b in forecast['buckets']]
        for expected in ('past_due', 'today', 'd7', 'd30', 'd60', 'd90',
                         'd180', 'd365', 'beyond'):
            self.assertIn(expected, keys)

    def test_cheques_land_in_the_right_bucket(self):
        self._check(due_date=self.today, amount=1000.0)
        self._check(due_date=self.today + timedelta(days=5), amount=2000.0)
        self._check(due_date=self.today + timedelta(days=20), amount=3000.0)
        forecast = self.Dashboard.get_maturity_forecast()
        buckets = {b['key']: b for b in forecast['buckets']}
        self.assertEqual(buckets['today']['amount'], 1000.0)
        self.assertEqual(buckets['d7']['amount'], 2000.0)
        self.assertEqual(buckets['d30']['amount'], 3000.0)

    def test_the_note_refuses_to_call_it_guaranteed_cash(self):
        forecast = self.Dashboard.get_maturity_forecast()
        self.assertIn('not guaranteed cash', forecast['note'])

    def test_each_bucket_drills_down_to_its_own_records(self):
        self._check(due_date=self.today + timedelta(days=5))
        forecast = self.Dashboard.get_maturity_forecast()
        for bucket in forecast['buckets']:
            records = self.Check.search(bucket['domain'])
            self.assertEqual(len(records), bucket['count'], bucket['key'])

    def test_it_can_be_grouped_by_a_second_dimension(self):
        self._check(due_date=self.today)
        forecast = self.Dashboard.get_maturity_forecast(groupby='partner_id')
        today_bucket = [b for b in forecast['buckets']
                        if b['key'] == 'today'][0]
        self.assertTrue(today_bucket['split'])
        self.assertEqual(today_bucket['split'][0]['label'], self.buyer.name)

    def test_only_cheques_on_hand_are_forecast(self):
        """A cheque at the bank is no longer a future maturity."""
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        forecast = self.Dashboard.get_maturity_forecast()
        total = sum(b['amount'] for b in forecast['buckets'])
        self.assertEqual(total, 0.0)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestRegistersAndReports(ChecksCommon):
    """M23 / M24 / M25."""

    def test_the_eight_registers_exist(self):
        for xmlid in ('action_report_check_register',
                      'action_report_pdc_maturity',
                      'action_report_deposit_register',
                      'action_report_cleared_register',
                      'action_report_bounce_register',
                      'action_report_replacement_register',
                      'action_report_check_coverage',
                      'action_report_customer_pdc'):
            action = self.env.ref('real_estate_checks.%s' % xmlid,
                                  raise_if_not_found=False)
            self.assertTrue(action, xmlid)

    def test_the_registers_use_operational_view_types(self):
        """M25 — a register is a dataset, not a PDF."""
        for xmlid in ('action_report_check_register',
                      'action_report_pdc_maturity',
                      'action_report_check_coverage'):
            action = self.env.ref('real_estate_checks.%s' % xmlid)
            self.assertIn('list', action.view_mode)
            self.assertIn('pivot', action.view_mode)

    def test_the_deposit_slip_renders(self):
        checks = self._check() | self._check()
        deposit = self._deposit(checks)
        report = self.env.ref('real_estate_checks.action_report_deposit_slip')
        html = self.env['ir.actions.report']._render_qweb_html(
            report.report_name, deposit.ids)[0]
        body = html.decode() if isinstance(html, bytes) else html
        self.assertIn('Deposit Slip', body)
        self.assertIn(deposit.name, body)
        for check in checks:
            self.assertIn(check.check_number, body)

    def test_the_acknowledgement_is_not_called_a_payment_receipt(self):
        """M24 — the wording is the whole point."""
        contract = self._signed_contract()
        self._check(sale_contract_id=contract.id)
        report = self.env.ref(
            'real_estate_checks.action_report_pdc_acknowledgement')
        html = self.env['ir.actions.report']._render_qweb_html(
            report.report_name, contract.ids)[0]
        body = html.decode() if isinstance(html, bytes) else html
        self.assertIn('PDC Acknowledgement', body)
        # The disclaimer is emphasised and wrapped in the template, so tags and
        # newlines are stripped before asserting on the wording itself.
        import re as _re
        text = _re.sub(r'\s+', ' ', _re.sub(r'<[^>]+>', '', body))
        self.assertIn('not a payment receipt', text)
        self.assertIn('no payment has been made', text)
        self.assertNotIn('Payment Receipt', text)

    def test_the_acknowledgement_lists_only_paper_still_held(self):
        contract = self._signed_contract()
        held = self._check(sale_contract_id=contract.id)
        banked = self._check(sale_contract_id=contract.id,
                             journal=self.journal_outstanding)
        self._deposit(banked, journal=self.journal_outstanding)
        report = self.env.ref(
            'real_estate_checks.action_report_pdc_acknowledgement')
        html = self.env['ir.actions.report']._render_qweb_html(
            report.report_name, contract.ids)[0]
        body = html.decode() if isinstance(html, bytes) else html
        self.assertIn(held.check_number, body)
        self.assertNotIn(banked.check_number, body)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestPerformance(ChecksCommon):
    """M33 — aggregation happens in the database."""

    def test_the_dashboard_does_not_load_records(self):
        """A 100,000-cheque portfolio must not be read into Python.

        Asserted by query count rather than by inspection: the payload is
        built from a bounded number of grouped queries no matter how many
        cheques exist.
        """
        for _ in range(20):
            self._check()
        Dashboard = self.env['realestate.check.dashboard']
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertQueryCount(__system__=200):
            Dashboard.get_dashboard_data()

    def test_the_hot_columns_are_indexed(self):
        for name in ('company_id', 'due_date', 'state', 'partner_id',
                     'deposit_id', 'sale_contract_id', 'sale_installment_id',
                     'payment_id', 'maturity_bucket', 'accounting_state'):
            field = self.Check._fields[name]
            self.assertTrue(field.index, '%s is not indexed' % name)

    def test_location_counts_are_grouped_not_looped(self):
        for _ in range(5):
            self._check()
        self.safe.invalidate_recordset()
        with self.assertQueryCount(__system__=10):
            self.safe._compute_check_count()
