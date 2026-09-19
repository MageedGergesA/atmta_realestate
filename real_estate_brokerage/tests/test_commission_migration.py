# -*- coding: utf-8 -*-
"""The semantic migration of `res.users.commission_share_default`.

The number did not move; its meaning did. These tests exist because every
technical test in the module stays green through the change — nothing is
broken, the arithmetic is right, and the input means something else.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import BrokerageCommon


class MigrationCommon(BrokerageCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Runner = cls.env[
            'realestate.commission.share.migration.runner']
        cls.Log = cls.env['realestate.commission.share.migration']

    _legacy_seq = 0

    def _legacy_agent(self, value, **kwargs):
        """An agent carrying a pre-0.3 default, untouched by the migration."""
        type(self)._legacy_agent_seq = getattr(
            type(self), '_legacy_agent_seq', 0) + 1
        seq = type(self)._legacy_agent_seq
        vals = {
            'name': 'Legacy Agent %d' % seq,
            'login': 'legacy.agent.%d@test.example' % seq,
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_brokerage.group_realestate_sales_agent').id,
                self.env.ref('sales_team.group_sale_salesman').id,
            ])],
            'is_realestate_agent': True,
            'commission_share_default': value,
        }
        vals.update(kwargs)
        return self.env['res.users'].with_context(
            no_reset_password=True).create(vals)

    def _history_at(self, agent, gross_percentage, price=1000000.0):
        """A closed deal this agent worked, at a known agency fee."""
        listing = self._mandated_listing(
            commission_basis='percentage',
            commission_percentage=gross_percentage)
        listing.action_activate()
        txn = self._transaction(listing, sale_price=price,
                                transaction_type='in_house',
                                selling_agent_id=agent.id)
        txn.action_sign_contract()
        txn.action_close()
        return txn


@tagged('post_install', '-at_install')
class TestMigrationAudit(MigrationCommon):
    """Classify before converting. Never the other way round."""

    def test_a_single_historical_rate_converts_deterministically(self):
        agent = self._legacy_agent(1.0)          # 1% of the sale
        self._history_at(agent, 2.5)             # on a 2.5% agency fee

        row = self.Runner.audit(agent)[0]

        self.assertEqual(row['result'], 'safe')
        self.assertEqual(row['converted_value'], 40.0)   # 1 / 2.5 × 100

    def test_two_historical_rates_are_ambiguous(self):
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.0)
        self._history_at(agent, 2.5)

        row = self.Runner.audit(agent)[0]

        self.assertEqual(row['result'], 'ambiguous')
        self.assertEqual(len(row['gross_rates_found']), 2)

    def test_a_default_nobody_ever_used_is_unused(self):
        agent = self._legacy_agent(2.5)

        row = self.Runner.audit(agent)[0]

        self.assertEqual(row['result'], 'unused')

    def test_a_zero_default_is_unused(self):
        agent = self._legacy_agent(0.0)

        self.assertEqual(self.Runner.audit(agent)[0]['result'], 'unused')

    def test_a_percentage_outside_the_range_is_invalid(self):
        agent = self._legacy_agent(150.0)

        self.assertEqual(self.Runner.audit(agent)[0]['result'], 'invalid')

    def test_a_conversion_over_a_hundred_percent_is_invalid(self):
        """3% of the sale against a 2% fee is more than the whole fee.

        The legacy pair cannot both be right, and quietly capping it at 100%
        would invent a number nobody agreed to.
        """
        agent = self._legacy_agent(3.0)
        self._history_at(agent, 2.0)

        row = self.Runner.audit(agent)[0]

        self.assertEqual(row['result'], 'invalid')

    def test_history_with_a_zero_gross_rate_is_ambiguous_not_safe(self):
        agent = self._legacy_agent(1.0)
        listing = self._listing(activate=True)
        txn = self._transaction(listing, sale_price=1000000.0,
                                selling_agent_id=agent.id)
        self.assertFalse(txn.commission_gross_amount)

        row = self.Runner.audit(agent)[0]

        self.assertEqual(row['result'], 'ambiguous')

    def test_the_audit_changes_nothing(self):
        """It is an audit. It reads."""
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.5)

        self.Runner.audit(agent)

        self.assertEqual(agent.commission_share_default, 1.0)
        self.assertEqual(agent.commission_share_migration_state,
                         'not_migrated')
        self.assertFalse(self.Log.search([('user_id', '=', agent.id)]))

    def test_the_audit_reports_the_evidence_it_reasoned_from(self):
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.5)

        row = self.Runner.audit(agent)[0]

        self.assertEqual(row['transaction_count'], 1)
        self.assertTrue(row['note'])
        self.assertIn('2.5', row['note'])


@tagged('post_install', '-at_install')
class TestMigrationRun(MigrationCommon):

    def test_a_safe_record_is_converted_and_evidenced(self):
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.5)

        self.Runner.run(agent)

        self.assertEqual(agent.commission_share_default, 40.0)
        self.assertEqual(agent.commission_share_legacy_value, 1.0)
        self.assertEqual(agent.commission_share_migration_state, 'safe')
        self.assertFalse(agent.commission_share_needs_review)

    def test_the_evidence_records_what_the_number_used_to_mean(self):
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.5)

        self.Runner.run(agent)
        log = agent.commission_share_migration_id

        self.assertEqual(log.original_value, 1.0)
        self.assertEqual(log.original_semantics, 'sale_price_pct')
        self.assertEqual(log.migrated_value, 40.0)
        self.assertEqual(log.migrated_semantics, 'gross_share_pct')
        self.assertEqual(log.gross_rate_used, 2.5)
        self.assertTrue(log.conversion_basis)
        self.assertTrue(log.migrated_on)
        self.assertTrue(log.migrated_by_id)

    def test_an_ambiguous_record_keeps_its_value_and_stops_being_usable(self):
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.0)
        self._history_at(agent, 2.5)

        self.Runner.run(agent)

        self.assertEqual(agent.commission_share_legacy_value, 1.0)
        self.assertEqual(agent.commission_share_default, 0.0)
        self.assertTrue(agent.commission_share_needs_review)

    def test_evidence_cannot_be_edited(self):
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.5)
        self.Runner.run(agent)

        with self.assertRaises(UserError):
            agent.commission_share_migration_id.original_value = 99.0

    def test_evidence_cannot_be_deleted(self):
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.5)
        self.Runner.run(agent)

        with self.assertRaises(UserError):
            agent.commission_share_migration_id.unlink()

    def test_the_run_is_idempotent(self):
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.5)

        self.Runner.run(agent)
        first_value = agent.commission_share_default
        first_logs = self.Log.search_count([('user_id', '=', agent.id)])
        self.Runner.run()

        self.assertEqual(agent.commission_share_default, first_value)
        self.assertEqual(
            self.Log.search_count([('user_id', '=', agent.id)]), first_logs)

    def test_a_converted_agent_is_not_re_converted(self):
        """Running twice must not divide by the rate a second time.

        This caught a real double-conversion: 40% re-read as a percentage of
        the sale price became 1,600% and was then discarded as invalid,
        silently zeroing a correctly-migrated agent.
        """
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.5)

        self.Runner.run(agent)
        self.Runner.run(agent)

        self.assertEqual(agent.commission_share_default, 40.0)
        self.assertEqual(agent.commission_share_legacy_value, 1.0)
        self.assertEqual(
            self.Log.search_count([('user_id', '=', agent.id)]), 1)

    def test_re_running_on_an_ambiguous_record_does_not_pile_up_evidence(self):
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.0)
        self._history_at(agent, 2.5)

        self.Runner.run(agent)
        self.Runner.run(agent)

        self.assertEqual(
            self.Log.search_count([('user_id', '=', agent.id)]), 1)

    def test_a_reviewed_record_survives_a_later_run(self):
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.0)
        self._history_at(agent, 2.5)
        self.Runner.run(agent)
        agent.action_review_commission_share(35.0, 'Agreed')

        self.Runner.run(agent)

        self.assertEqual(agent.commission_share_default, 35.0)
        self.assertEqual(agent.commission_share_migration_state, 'reviewed')


@tagged('post_install', '-at_install')
class TestAlreadyNewSemantics(MigrationCommon):
    """An audit cannot tell a 40 that means gross from a 40 that means sale."""

    def test_a_manager_can_declare_a_default_already_migrated(self):
        agent = self._legacy_agent(40.0)

        agent.action_mark_share_already_new(
            'Set by the 0.3 pilot; already a share of gross')

        self.assertEqual(agent.commission_share_migration_state, 'already_new')
        self.assertEqual(agent.commission_share_default, 40.0)
        self.assertFalse(agent.commission_share_needs_review)

    def test_the_declaration_is_evidenced(self):
        agent = self._legacy_agent(40.0)

        agent.action_mark_share_already_new('Set by the 0.3 pilot')
        log = agent.commission_share_migration_id

        self.assertEqual(log.result, 'already_new')
        self.assertEqual(log.original_semantics, 'gross_share_pct')
        self.assertEqual(log.reviewed_by_id, self.env.user)
        self.assertTrue(log.review_note)

    def test_a_later_run_leaves_it_alone(self):
        """The value must not then be divided by anything."""
        agent = self._legacy_agent(40.0)
        self._history_at(agent, 2.5)
        agent.action_mark_share_already_new('Already gross-share')

        self.Runner.run()

        self.assertEqual(agent.commission_share_default, 40.0)
        self.assertEqual(agent.commission_share_migration_state, 'already_new')

    def test_it_needs_a_reason(self):
        agent = self._legacy_agent(40.0)

        with self.assertRaises(UserError):
            agent.action_mark_share_already_new('')

    def test_an_agent_cannot_declare_it(self):
        agent = self._legacy_agent(40.0)

        with self.assertRaises(UserError):
            agent.with_user(self.agent).action_mark_share_already_new('yes')

    def test_it_cannot_overwrite_a_completed_migration(self):
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.5)
        self.Runner.run(agent)

        with self.assertRaises(UserError):
            agent.action_mark_share_already_new('changed my mind')


@tagged('post_install', '-at_install')
class TestMonetaryEquivalence(MigrationCommon):
    """Old money must equal new money, to the currency's rounding."""

    def test_the_converted_agent_is_paid_exactly_what_they_were_before(self):
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.5)
        self.Runner.run(agent)

        result = self.Runner.verify_equivalence(
            agent, sale_price=1000000.0, gross_rate=2.5)

        self.assertEqual(result['old'], 10000.0)
        self.assertEqual(result['new'], 10000.0)
        self.assertTrue(result['equal'])

    def test_equivalence_holds_across_a_range_of_prices(self):
        agent = self._legacy_agent(1.5)
        self._history_at(agent, 3.0)
        self.Runner.run(agent)

        for price in (250000.0, 1000000.0, 3750000.0, 12345678.0):
            with self.subTest(price=price):
                result = self.Runner.verify_equivalence(
                    agent, sale_price=price, gross_rate=3.0)
                self.assertTrue(
                    result['equal'],
                    "%s: old %s vs new %s"
                    % (price, result['old'], result['new']))

    def test_equivalence_holds_end_to_end_through_a_real_commission(self):
        """Not the formula in isolation — the money a line actually produces."""
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.5)
        old_money = 1000000.0 * 1.0 / 100.0          # what 0.1 would have paid
        self.Runner.run(agent)

        txn = self._history_at(agent, 2.5)
        line = self._commission(
            txn, calculation_method='share',
            share_percentage=agent.commission_share_default,
            partner_id=agent.partner_id.id)

        self.assertEqual(line.amount, old_money)


@tagged('post_install', '-at_install')
class TestAmbiguousBlocking(MigrationCommon):
    """Block the commission path. Block nothing else."""

    def setUp(self):
        super().setUp()
        self.ambiguous_agent = self._legacy_agent(1.0)
        self._history_at(self.ambiguous_agent, 2.0)
        self._history_at(self.ambiguous_agent, 2.5)
        self.Runner.run(self.ambiguous_agent)

    def test_seeding_defaults_refuses_and_names_the_agent(self):
        txn = self._closed_deal(gross_percentage=2.0)
        txn.selling_agent_id = self.ambiguous_agent.id

        with self.assertRaises(UserError) as caught:
            txn.action_add_default_commissions()

        self.assertIn(self.ambiguous_agent.name, str(caught.exception))

    def test_a_line_from_an_unresolved_default_cannot_be_approved(self):
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(
            txn, calculation_method='share', share_percentage=40.0,
            partner_id=self.ambiguous_agent.partner_id.id,
            share_source='user_default')

        self.assertTrue(line.needs_config_review)
        with self.assertRaises(UserError):
            line.action_approve()

    def test_a_manually_entered_share_is_unaffected(self):
        """A number somebody typed is a decision, not an inherited ambiguity."""
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(
            txn, calculation_method='share', share_percentage=40.0,
            partner_id=self.ambiguous_agent.partner_id.id,
            share_source='manual')

        line.action_approve()

        self.assertEqual(line.state, 'approved')

    def test_the_agent_can_still_sell(self):
        """An unresolved payout default is not a reason to stop somebody
        working."""
        lead = self._opportunity(user_id=self.ambiguous_agent.id)
        listing = self._listing(activate=True)
        offer = self._offer(listing=listing, amount=1000000.0)

        offer.action_accept()

        self.assertEqual(offer.state, 'accepted')
        self.assertTrue(lead.exists())

    def test_a_manager_can_resolve_it(self):
        self.ambiguous_agent.action_review_commission_share(
            35.0, 'Agreed with the agent: 35% of gross going forward')

        self.assertEqual(self.ambiguous_agent.commission_share_default, 35.0)
        self.assertEqual(
            self.ambiguous_agent.commission_share_migration_state, 'reviewed')
        self.assertFalse(self.ambiguous_agent.commission_share_needs_review)

    def test_the_resolution_is_recorded_against_the_evidence(self):
        self.ambiguous_agent.action_review_commission_share(
            35.0, 'Agreed with the agent')
        log = self.ambiguous_agent.commission_share_migration_id

        self.assertEqual(log.reviewed_by_id, self.env.user)
        self.assertTrue(log.reviewed_on)
        self.assertEqual(log.review_note, 'Agreed with the agent')

    def test_a_review_needs_a_reason(self):
        with self.assertRaises(UserError):
            self.ambiguous_agent.action_review_commission_share(35.0, '')

    def test_an_agent_cannot_resolve_their_own(self):
        with self.assertRaises(UserError):
            self.ambiguous_agent.with_user(
                self.agent).action_review_commission_share(35.0, 'because')

    def test_after_resolution_seeding_works(self):
        self.ambiguous_agent.action_review_commission_share(35.0, 'Agreed')
        txn = self._closed_deal(gross_percentage=2.0)
        txn.selling_agent_id = self.ambiguous_agent.id

        txn.action_add_default_commissions()

        self.assertTrue(txn.commission_ids)
        self.assertEqual(txn.commission_ids[0].share_percentage, 35.0)


@tagged('post_install', '-at_install')
class TestEntitlementSnapshot(MigrationCommon):
    """A promise made on a particular day, on a particular basis."""

    def test_the_basis_is_frozen_onto_the_line_at_creation(self):
        txn = self._closed_deal(gross_percentage=2.0)

        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)

        self.assertEqual(line.snapshot_gross_amount, 20000.0)
        self.assertEqual(line.snapshot_share_percentage, 50.0)
        self.assertEqual(line.snapshot_amount, 10000.0)
        self.assertEqual(line.snapshot_method, 'share')
        self.assertTrue(line.snapshot_taken_on)
        self.assertTrue(line.effective_date)

    def test_changing_the_agents_default_does_not_move_an_existing_line(self):
        """The requirement, stated directly."""
        agent = self._legacy_agent(0.0)
        agent.commission_share_default = 40.0
        txn = self._closed_deal(gross_percentage=2.0)
        txn.selling_agent_id = agent.id
        txn.action_add_default_commissions()
        line = txn.commission_ids[0]
        original = line.amount

        agent.commission_share_default = 90.0

        self.assertEqual(line.share_percentage, 40.0)
        self.assertEqual(line.amount, original)

    def test_an_approved_entitlement_stops_following_the_gross(self):
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)
        line.action_approve()

        txn.action_set_gross_manually(100000.0, 'Renegotiated after closing')

        self.assertEqual(line.amount, 10000.0)
        self.assertTrue(line.entitlement_locked)

    def test_a_draft_entitlement_still_follows_the_gross(self):
        """Before approval it is a working figure, not a promise."""
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)

        txn.action_set_gross_manually(40000.0, 'Corrected the fee')

        self.assertEqual(line.amount, 20000.0)

    def test_approval_re_freezes_at_the_approved_figure(self):
        txn = self._closed_deal(gross_percentage=2.0)
        line = self._commission(txn, calculation_method='share',
                                share_percentage=50.0)
        txn.action_set_gross_manually(40000.0, 'Corrected the fee')

        line.action_approve()

        self.assertEqual(line.snapshot_amount, 20000.0)
        self.assertEqual(line.amount, 20000.0)

    def test_a_seeded_line_records_where_its_share_came_from(self):
        agent = self._legacy_agent(0.0)
        agent.commission_share_default = 40.0
        txn = self._closed_deal(gross_percentage=2.0)
        txn.selling_agent_id = agent.id

        txn.action_add_default_commissions()
        line = txn.commission_ids[0]

        self.assertEqual(line.share_source, 'user_default')
        self.assertIn('40', line.source_rule)


@tagged('post_install', '-at_install')
class TestLegacyUpgrade(MigrationCommon):
    """A database that looks like 0.1, taken through both migrations.

    Not the pieces in isolation — a single dataset carrying legacy leads,
    legacy user defaults and legacy percentage commission rows, upgraded, and
    then checked for the one thing that matters: no money moves on a basis
    nobody can explain.
    """

    def setUp(self):
        super().setUp()
        # A 0.1 world.
        self.legacy_lead = self._legacy_lead()
        self.convertible = self._legacy_agent(1.0)
        self._history_at(self.convertible, 2.5)
        self.unconvertible = self._legacy_agent(1.0)
        self._history_at(self.unconvertible, 2.0)
        self._history_at(self.unconvertible, 3.0)
        # A pre-0.2 commission row: percentage of the sale, no gross anywhere.
        self.legacy_txn = self._transaction(
            self._listing(inventory_type='external',
                          owner_partner_id=self.seller.id),
            sale_price=1000000.0, transaction_type='in_house')
        self.legacy_line = self._commission(
            self.legacy_txn, calculation_method='percentage', percentage=2.0)

    def test_both_migrations_run_over_the_same_dataset(self):
        lead_result = self.env['realestate.lead.migration'].run()
        share_result = self.Runner.run()

        self.assertTrue(lead_result)
        self.assertTrue(share_result)
        self.assertTrue(self.legacy_lead.crm_lead_id
                        or self.legacy_lead.migration_state)

    def test_the_convertible_agent_keeps_their_money(self):
        self.Runner.run()

        self.assertEqual(self.convertible.commission_share_default, 40.0)
        self.assertTrue(self.Runner.verify_equivalence(
            self.convertible, sale_price=1000000.0, gross_rate=2.5)['equal'])

    def test_the_unconvertible_agent_is_stopped_rather_than_guessed_at(self):
        self.Runner.run()

        self.assertEqual(self.unconvertible.commission_share_default, 0.0)
        self.assertEqual(self.unconvertible.commission_share_legacy_value, 1.0)
        self.assertTrue(self.unconvertible.commission_share_needs_review)

    def test_the_legacy_commission_row_survives_but_cannot_pay(self):
        self.Runner.run()

        self.assertTrue(self.legacy_line.exists())
        self.assertEqual(self.legacy_line.amount, 20000.0)
        with self.assertRaises(UserError):
            self.legacy_line.action_approve()

    def test_no_legacy_row_can_reach_a_vendor_bill(self):
        """The gate that matters, asserted across the whole dataset."""
        self.Runner.run()

        payable = self.env['realestate.commission'].search([
            ('state', 'in', ('billed', 'paid'))])

        for line in payable:
            with self.subTest(line=line.display_name):
                self.assertTrue(
                    line.transaction_id.commission_gross_amount
                    or (line.calculation_method == 'fixed'
                        and line.fixed_amount > 0),
                    "%s reached a payable with no calculable basis"
                    % line.display_name)

    def test_running_both_migrations_twice_changes_nothing(self):
        self.env['realestate.lead.migration'].run()
        self.Runner.run()
        converted = self.convertible.commission_share_default
        logs = self.Log.search_count([])
        leads = self.env['crm.lead'].search_count([])

        self.env['realestate.lead.migration'].run()
        self.Runner.run()

        self.assertEqual(self.convertible.commission_share_default, converted)
        self.assertEqual(self.Log.search_count([]), logs)
        self.assertEqual(self.env['crm.lead'].search_count([]), leads)


@tagged('post_install', '-at_install')
class TestLegacySeedingIsShadowed(MigrationCommon):
    """The 0.1 seeding method still exists in `transaction.py`.

    It reads `commission_share_default` straight into a `percentage` line —
    a percentage of the *sale price*, which is the exact defect the migration
    exists to undo. It is shadowed at runtime by the override in
    `commission_v2.py`, and that shadowing is load-order dependent. Pinned
    here rather than trusted: if an import order ever changes, this fails
    loudly instead of quietly paying somebody fifty times too much.
    """

    def test_the_override_is_what_actually_runs(self):
        agent = self._legacy_agent(0.0)
        agent.commission_share_default = 40.0
        txn = self._closed_deal(gross_percentage=2.0)
        txn.selling_agent_id = agent.id

        txn.action_add_default_commissions()
        line = txn.commission_ids.filtered(
            lambda c: c.partner_id == agent.partner_id)

        self.assertEqual(
            line.calculation_method, 'share',
            "The 0.1 seeding method is no longer shadowed: this line is a "
            "percentage of the sale price, not a share of the gross.")
        self.assertEqual(line.share_percentage, 40.0)
        self.assertEqual(line.amount, 8000.0)     # 40% of a 20,000 gross
        self.assertNotEqual(line.amount, 400000.0)   # 40% of the sale price

    def test_the_override_refuses_for_an_unresolved_agent(self):
        """The 0.1 method would have seeded happily. This one must not."""
        agent = self._legacy_agent(1.0)
        self._history_at(agent, 2.0)
        self._history_at(agent, 2.5)
        self.Runner.run(agent)
        txn = self._closed_deal(gross_percentage=2.0)
        txn.selling_agent_id = agent.id

        with self.assertRaises(UserError):
            txn.action_add_default_commissions()
