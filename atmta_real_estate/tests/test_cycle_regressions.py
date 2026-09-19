"""Defects found by walking the whole Rental cycle end to end.

Each test names what a user saw. The cycle itself (lease → deposit → move-in →
billing → maintenance → meters → amendment → renewal → termination → move-out →
deposit settlement → unit turn) is covered piecewise by the other suites; these
pin the places where the pieces did not agree.
"""

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestCycleRegressions(LeaseCase):

    def _billed_lease(self):
        lease = self.activate(self.make_lease(
            start=self.today.replace(day=1) - relativedelta(months=3),
            rent=1000.0, use_billing_engine=True))
        lease.action_generate_billing_schedule()
        return lease

    # ------------------------------------------------------------------
    # Lease totals
    # ------------------------------------------------------------------
    def test_a_paid_lease_shows_the_payment_in_its_totals(self):
        """Total Paid stayed 0 and Balance Due stayed the whole term."""
        lease = self._billed_lease()
        first = lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        self.env['realestate.account.tools'].register_payment(first.move_id)
        self.env.invalidate_all()

        obligations = lease.contract_payment_ids
        self.assertGreater(first.amount_paid, 0.0)
        self.assertAlmostEqual(lease.total_paid, first.amount_paid, places=2)
        self.assertAlmostEqual(
            lease.total_scheduled, sum(obligations.mapped('amount_due')), places=2)
        self.assertAlmostEqual(
            lease.balance_due, sum(obligations.mapped('amount_residual')), places=2)
        self.assertAlmostEqual(
            lease.balance_due, lease.total_scheduled - lease.total_paid, places=2)

    def test_a_partly_paid_invoice_counts_only_what_was_paid(self):
        lease = self._billed_lease()
        first = lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        half = first.move_id.amount_total / 2.0
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=first.move_id.ids,
        ).create({'amount': half})._create_payments()
        self.env.invalidate_all()
        self.assertAlmostEqual(lease.total_paid, half, places=2)

    def test_the_summary_balance_is_what_is_invoiced_and_unpaid(self):
        """'Balance Due $29,850' sat beside a green 'Paid' badge."""
        lease = self._billed_lease()
        self.assertEqual(lease.amount_outstanding, 0.0, "Nothing invoiced yet.")
        first = lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        self.env.invalidate_all()
        self.assertAlmostEqual(lease.amount_outstanding, first.move_id.amount_total, places=2)
        self.env['realestate.account.tools'].register_payment(first.move_id)
        self.env.invalidate_all()
        self.assertEqual(lease.payment_status, 'paid')
        self.assertAlmostEqual(lease.amount_outstanding, 0.0, places=2)
        self.assertGreater(lease.balance_due, 0.0, "The rest of the term is still to bill.")
        view = self.env.ref('atmta_real_estate.view_realestate_contract_form')
        arch = etree.fromstring(lease.get_view(view.id, 'form')['arch'])
        summary = arch.xpath("//group[@name='lease_summary']")[0]
        self.assertTrue(summary.xpath(".//field[@name='amount_outstanding']"))
        self.assertFalse(summary.xpath(".//field[@name='balance_due']"))

    # ------------------------------------------------------------------
    # What can carry a lease
    # ------------------------------------------------------------------
    def test_a_compound_or_building_cannot_be_leased(self):
        for structural in (self.compound, self.building):
            with self.subTest(level=structural.hierarchy_level), \
                    self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    self.make_lease(prop=structural)

    def test_the_unit_pickers_offer_leasable_records_only(self):
        for model in ('realestate.contract', 'realestate.contract.property.line'):
            with self.subTest(model=model):
                domain = self.env[model]._fields['property_id'].domain
                self.assertIn("('is_leasable', '=', True)", domain)

    # ------------------------------------------------------------------
    # One exit, one unit turn
    # ------------------------------------------------------------------
    def test_move_out_then_termination_opens_a_single_unit_turn(self):
        lease = self._billed_lease()
        cutoff = self.today.replace(day=1) + relativedelta(months=1)
        termination = self.env['realestate.contract.termination'].create({
            'contract_id': lease.id,
            'requested_end_date': cutoff,
            'effective_date': cutoff,
            'reason': 'tenant_notice',
            'requested_by': 'tenant',
        })
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()
        move_out = self.env['realestate.move.out'].create({
            'contract_id': lease.id,
            'property_id': self.unit_a.id,
            'termination_id': termination.id,
            'scheduled_date': self.today,
            'tenant_acknowledged': True,
        })
        move_out.action_start_inspection()
        move_out.action_complete()
        termination.action_complete()

        turns = self.env['realestate.unit.turn'].search(
            [('property_id', '=', self.unit_a.id)])
        self.assertEqual(len(turns), 1, turns.mapped('display_name'))
        self.assertEqual(turns.termination_id, termination)
        self.assertEqual(turns.move_out_id, move_out)

    def test_a_ready_turn_is_not_reopened_by_completing_the_termination(self):
        """The unit was cleaned and marked ready before the paperwork closed."""
        lease = self._billed_lease()
        cutoff = self.today.replace(day=1) + relativedelta(months=1)
        termination = self.env['realestate.contract.termination'].create({
            'contract_id': lease.id,
            'requested_end_date': cutoff,
            'effective_date': cutoff,
            'reason': 'tenant_notice',
            'requested_by': 'tenant',
        })
        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()
        move_out = self.env['realestate.move.out'].create({
            'contract_id': lease.id,
            'property_id': self.unit_a.id,
            'scheduled_date': self.today,
            'tenant_acknowledged': True,
        })
        move_out.action_start_inspection()
        move_out.action_complete()
        turn = move_out.unit_turn_id
        for step in ('action_start', 'action_cleaning_done', 'action_repair_done',
                     'action_pass_inspection', 'action_mark_ready'):
            getattr(turn, step)()
        self.assertEqual(turn.state, 'ready')

        termination.action_complete()
        open_turns = self.env['realestate.unit.turn'].search([
            ('property_id', '=', self.unit_a.id),
            ('state', 'not in', ('ready', 'cancelled')),
        ])
        self.assertFalse(open_turns)

    # ------------------------------------------------------------------
    # Deposit configuration
    # ------------------------------------------------------------------
    def test_deposit_account_pickers_offer_reconcilable_liabilities_only(self):
        """Settings accepted an account the first receipt then refused."""
        for model in ('res.company', 'res.config.settings', 'realestate.contract.deposit'):
            field = ('deposit_account_id' if model == 'realestate.contract.deposit'
                     else 're_deposit_account_id')
            with self.subTest(model=model):
                domain = self.env[model]._fields[field].domain
                self.assertIn("('reconcile', '=', True)", domain)
                self.assertIn('liability_current', domain)

    # ------------------------------------------------------------------
    # Maintenance requests
    # ------------------------------------------------------------------
    def _maintenance_request(self):
        return self.env['realestate.maintenance.request'].create({
            'property_id': self.unit_a.id,
            'description': 'Kitchen tap leaking',
        })

    def test_resetting_started_maintenance_releases_the_unit(self):
        """Back in draft nothing is being worked on; the unit stayed blocked."""
        request = self._maintenance_request()
        request.action_schedule()
        request.action_start_progress()
        self.assertEqual(self.unit_a.maintenance_status, 'maintenance')
        request.action_reset_to_draft()
        self.assertEqual(request.state, 'draft')
        self.assertEqual(self.unit_a.maintenance_status, 'normal')

    def test_scheduling_and_completing_record_their_dates(self):
        request = self._maintenance_request()
        request.action_schedule()
        self.assertEqual(request.scheduled_date, self.today)
        request.action_start_progress()
        request.action_complete()
        self.assertEqual(request.completion_date, self.today)

    def test_dates_already_entered_are_kept(self):
        request = self._maintenance_request()
        planned = self.today + relativedelta(days=5)
        request.scheduled_date = planned
        request.action_schedule()
        self.assertEqual(request.scheduled_date, planned)

    def test_reset_to_draft_is_not_offered_on_a_draft_request(self):
        view = self.env.ref('atmta_real_estate.view_maintenance_request_form')
        arch = etree.fromstring(
            self.env['realestate.maintenance.request'].get_view(view.id, 'form')['arch'])
        button = arch.xpath("//button[@name='action_reset_to_draft']")[0]
        self.assertEqual(button.get('invisible'), "state == 'draft'")

    def test_a_lease_has_one_open_termination_at_a_time(self):
        lease = self._billed_lease()
        cutoff = self.today.replace(day=1) + relativedelta(months=1)
        vals = {
            'contract_id': lease.id,
            'requested_end_date': cutoff,
            'effective_date': cutoff,
            'reason': 'tenant_notice',
            'requested_by': 'tenant',
        }
        Termination = self.env['realestate.contract.termination']
        first = Termination.create(vals)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            Termination.create(vals)
        first.action_cancel()
        self.assertTrue(Termination.create(vals), "A cancelled one does not block.")

    def test_move_out_defaults_to_the_termination_effective_date(self):
        lease = self._billed_lease()
        cutoff = self.today.replace(day=1) + relativedelta(months=1)
        self.env['realestate.contract.termination'].create({
            'contract_id': lease.id,
            'requested_end_date': cutoff,
            'effective_date': cutoff,
            'reason': 'tenant_notice',
            'requested_by': 'tenant',
        }).action_give_notice()
        context = lease.action_schedule_move_out()['context']
        self.assertEqual(context['default_scheduled_date'], cutoff)
        self.assertNotEqual(context['default_scheduled_date'], lease.end_date)

    # ------------------------------------------------------------------
    # Termination settlement preview
    # ------------------------------------------------------------------
    def test_outstanding_rent_stops_at_the_effective_date(self):
        """A draft termination showed the whole remaining term as owed."""
        lease = self._billed_lease()
        cutoff = self.today.replace(day=1) + relativedelta(months=1)
        termination = self.env['realestate.contract.termination'].create({
            'contract_id': lease.id,
            'requested_end_date': cutoff,
            'effective_date': cutoff,
            'reason': 'tenant_notice',
            'requested_by': 'tenant',
        })
        owed_until_exit = sum(lease.contract_payment_ids.filtered(
            lambda o: not o.is_settled and o.period_start <= cutoff
        ).mapped('amount_residual'))
        whole_term = sum(lease.contract_payment_ids.mapped('amount_residual'))
        self.assertLess(owed_until_exit, whole_term)
        self.assertAlmostEqual(termination.outstanding_balance, owed_until_exit, places=2)

        termination.action_give_notice()
        termination.action_approve()
        termination.action_settle()
        self.env.invalidate_all()
        remaining = sum(lease.contract_payment_ids.filtered(
            lambda o: not o.is_settled and o.state != 'cancelled').mapped('amount_residual'))
        self.assertAlmostEqual(termination.outstanding_balance, remaining, places=2)

    def test_the_settings_form_restricts_the_deposit_pickers(self):
        """Settings offered every account and journal; its fields are related."""
        Settings = self.env['res.config.settings']
        view = Settings.get_view(self.env.ref('base_setup.res_config_settings_view_form').id, 'form')
        fields_info = view['models']['res.config.settings']
        self.assertIn('re_deposit_journal_id', fields_info)
        for name, needle in (
                ('re_deposit_account_id', "('reconcile', '=', True)"),
                ('re_deposit_journal_id', "('type', 'in', ('bank', 'cash'))"),
                ('re_deposit_forfeit_income_account_id', "'income_other'")):
            with self.subTest(field=name):
                arch = etree.fromstring(view['arch'])
                node = arch.xpath("//field[@name='%s']" % name)[0]
                domain = node.get('domain') or Settings._fields[name].domain
                self.assertIn(needle, domain)

    def test_a_deposit_requested_before_setup_can_be_received_after_it(self):
        """The empty account stayed on the deposit after Settings were filled."""
        account = self.company.re_deposit_account_id
        self.company.re_deposit_account_id = False
        lease = self.activate(self.make_lease(prop=self.unit_b))
        deposit = self.env['realestate.contract.deposit'].create({
            'contract_id': lease.id,
            'partner_id': self.tenant.id,
            'requested_amount': 500.0,
        })
        deposit.action_request()
        self.assertFalse(deposit.deposit_account_id)

        self.company.re_deposit_account_id = account
        deposit.action_register_receipt()
        self.assertEqual(deposit.state, 'held')
        self.assertEqual(deposit.deposit_account_id, account)
        self.assertAlmostEqual(deposit.held_amount, 500.0, places=2)
