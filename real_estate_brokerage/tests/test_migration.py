# -*- coding: utf-8 -*-
"""M30 — the legacy pipeline migration, and its five classifications.

The rule under test throughout: **nothing is merged on a guess.** A name, an
email or a phone number is not an identity, and one customer legitimately has
several concurrent opportunities.
"""

from odoo.tests.common import tagged

from .common import BrokerageCommon


@tagged('post_install', '-at_install', 'atmta_brokerage')
class TestMigrationClassification(BrokerageCommon):

    def _run(self):
        return self.Migration.run()

    # ------------------------------------------------------------------
    # MIGRATE — no counterpart exists
    # ------------------------------------------------------------------
    def test_a_lead_with_no_crm_counterpart_is_migrated(self):
        legacy = self._legacy_lead()
        report = self._run()

        self.assertEqual(len(report['migrated']), 1)
        legacy.invalidate_recordset()
        self.assertEqual(legacy.migration_state, 'migrated')
        self.assertTrue(legacy.crm_lead_id)

    def test_the_brief_is_carried_across(self):
        legacy = self._legacy_lead(
            budget_min=750000.0, budget_max=1100000.0,
            area_min=100.0, area_max=180.0, bedroom_count_min=3,
            property_type_ids=[(6, 0, self.property_type.ids)],
            preferred_city='New Cairo', preferred_district='Fifth Settlement')
        self._run()
        legacy.invalidate_recordset()
        crm_lead = legacy.crm_lead_id

        self.assertEqual(crm_lead.re_budget_min, 750000.0)
        self.assertEqual(crm_lead.re_budget_max, 1100000.0)
        self.assertEqual(crm_lead.re_area_min, 100.0)
        self.assertEqual(crm_lead.re_area_max, 180.0)
        self.assertEqual(crm_lead.re_bedrooms_min, 3)
        self.assertEqual(crm_lead.re_property_type_ids, self.property_type)
        self.assertTrue(crm_lead.re_is_realestate)

        # The location became a normalised row, not four flat columns.
        self.assertEqual(len(crm_lead.re_location_ids), 1)
        self.assertEqual(crm_lead.re_location_ids.city, 'New Cairo')
        self.assertEqual(crm_lead.re_location_ids.district, 'Fifth Settlement')

    def test_the_legacy_reference_survives(self):
        """A document printed before the migration still quotes LEAD-00001."""
        legacy = self._legacy_lead()
        reference = legacy.name
        self._run()
        legacy.invalidate_recordset()
        self.assertEqual(legacy.crm_lead_id.re_legacy_reference, reference)

    def test_the_salesperson_and_source_are_carried(self):
        legacy = self._legacy_lead(source='referral')
        self._run()
        legacy.invalidate_recordset()
        crm_lead = legacy.crm_lead_id
        self.assertEqual(crm_lead.user_id, self.agent)
        self.assertTrue(crm_lead.source_id)
        self.assertEqual(crm_lead.source_id.name, 'Referral')

    def test_the_legacy_state_maps_to_a_stage(self):
        for state, stage_key in (('new', 'new'), ('qualified', 'qualified'),
                                 ('matched', 'matching'),
                                 ('viewing_scheduled', 'viewing'),
                                 ('offer', 'negotiation'),
                                 ('converted', 'won')):
            legacy = self._legacy_lead(
                partner=self.env['res.partner'].create(
                    {'name': 'Buyer %s' % state}),
                state=state)
            self._run()
            legacy.invalidate_recordset()
            self.assertEqual(legacy.crm_lead_id.stage_id, self._stage(stage_key),
                             "legacy state %r mapped to the wrong stage" % state)

    def test_a_lost_legacy_lead_becomes_a_lost_opportunity(self):
        """M17 — loss is `active = False` + a reason, never a stage."""
        reason = self.env['realestate.lost.reason'].create(
            {'name': 'Went with a competitor'})
        legacy = self._legacy_lead(state='lost', loss_reason_id=reason.id)
        self._run()
        legacy.invalidate_recordset()
        crm_lead = legacy.crm_lead_id.with_context(active_test=False)

        self.assertFalse(crm_lead.active)
        self.assertTrue(crm_lead.lost_reason_id)
        self.assertEqual(crm_lead.lost_reason_id.name, 'Went with a competitor')
        self.assertTrue(crm_lead.date_closed)

    # ------------------------------------------------------------------
    # LINK EXISTING — exactly one deterministic counterpart
    # ------------------------------------------------------------------
    def test_a_single_open_opportunity_is_linked_not_duplicated(self):
        existing = self._opportunity()
        legacy = self._legacy_lead()
        report = self._run()

        self.assertEqual(len(report['linked']), 1)
        legacy.invalidate_recordset()
        self.assertEqual(legacy.migration_state, 'linked')
        self.assertEqual(legacy.crm_lead_id, existing)

    def test_linking_fills_blanks_but_never_overwrites(self):
        """The CRM record is the one people have been working."""
        existing = self._opportunity(re_budget_max=2000000.0, re_area_min=0.0)
        legacy = self._legacy_lead(budget_max=900000.0, area_min=150.0)
        self._run()
        existing.invalidate_recordset()

        # Already had a budget → untouched.
        self.assertEqual(existing.re_budget_max, 2000000.0)
        # Had no area → filled from the legacy brief.
        self.assertEqual(existing.re_area_min, 150.0)

    def test_a_closed_opportunity_is_not_a_counterpart(self):
        """A lost deal and a fresh approach by the same customer are two
        different things — and the brief must not resurrect the lost one."""
        closed = self._opportunity()
        closed.action_set_lost()
        legacy = self._legacy_lead()
        report = self._run()

        self.assertEqual(len(report['migrated']), 1,
                         "A lost opportunity was treated as the counterpart")
        legacy.invalidate_recordset()
        self.assertTrue(legacy.crm_lead_id)
        self.assertNotEqual(legacy.crm_lead_id, closed)
        self.assertIn('closed', report['migrated'][0]['note'].lower())
        # And the lost deal stayed lost.
        closed.invalidate_recordset()
        self.assertFalse(closed.with_context(active_test=False).active)

    # ------------------------------------------------------------------
    # LEGITIMATE MULTIPLE
    # ------------------------------------------------------------------
    def test_several_live_opportunities_produce_a_new_one(self):
        """A villa and an apartment are two deals, not one duplicate."""
        first = self._opportunity(name='Villa Interest')
        second = self._opportunity(name='Apartment Interest')
        legacy = self._legacy_lead()
        report = self._run()

        self.assertEqual(len(report['multiple']), 1)
        legacy.invalidate_recordset()
        self.assertEqual(legacy.migration_state, 'multiple')
        self.assertTrue(legacy.crm_lead_id)
        self.assertNotIn(legacy.crm_lead_id, first | second,
                         "The legacy brief was merged into an existing deal")
        self.assertIn('legitimate', report['multiple'][0]['note'].lower())

    # ------------------------------------------------------------------
    # AMBIGUOUS — same organisation, different person
    # ------------------------------------------------------------------
    def test_a_different_contact_at_the_same_company_is_ambiguous(self):
        """Ahmed at ACME is not Sara at ACME.

        `child_of` the commercial partner finds them both, which is right for
        surfacing a candidate and wrong for merging one.
        """
        acme = self.env['res.partner'].create({
            'name': 'ACME Holding', 'is_company': True})
        ahmed = self.env['res.partner'].create({
            'name': 'Ahmed', 'parent_id': acme.id})
        sara = self.env['res.partner'].create({
            'name': 'Sara', 'parent_id': acme.id})

        self._opportunity(partner=sara)
        legacy = self._legacy_lead(partner=ahmed)
        report = self._run()

        self.assertEqual(len(report['ambiguous']), 1)
        legacy.invalidate_recordset()
        self.assertEqual(legacy.migration_state, 'ambiguous')
        self.assertFalse(legacy.crm_lead_id,
                         "An ambiguous match was linked anyway")
        self.assertIn('DIFFERENT contact', legacy.migration_note)

    def test_the_same_contact_at_a_company_is_linked(self):
        """The inverse, so 'ambiguous' is not passing by refusing everything."""
        acme = self.env['res.partner'].create({
            'name': 'ACME Two', 'is_company': True})
        ahmed = self.env['res.partner'].create({
            'name': 'Ahmed Two', 'parent_id': acme.id})

        existing = self._opportunity(partner=ahmed)
        legacy = self._legacy_lead(partner=ahmed)
        self._run()

        legacy.invalidate_recordset()
        self.assertEqual(legacy.migration_state, 'linked')
        self.assertEqual(legacy.crm_lead_id, existing)

    # ------------------------------------------------------------------
    # SKIPPED
    # ------------------------------------------------------------------
    def test_a_lead_with_no_contact_is_skipped_not_guessed(self):
        """M30 — never merge on a name."""
        legacy = self.LegacyLead.with_context(re_legacy_migration=True).create({
            'partner_name': 'Ahmed Hassan',
            'agent_id': self.agent.id,
        })
        report = self._run()

        self.assertEqual(len(report['skipped']), 1)
        legacy.invalidate_recordset()
        self.assertEqual(legacy.migration_state, 'skipped')
        self.assertFalse(legacy.crm_lead_id)
        self.assertIn('not an identity', legacy.migration_note)

    def test_two_people_with_the_same_name_are_never_merged(self):
        first = self.LegacyLead.with_context(re_legacy_migration=True).create({
            'partner_name': 'Ahmed Hassan', 'agent_id': self.agent.id})
        second = self.LegacyLead.with_context(re_legacy_migration=True).create({
            'partner_name': 'Ahmed Hassan', 'agent_id': self.agent.id})
        self._run()
        first.invalidate_recordset()
        second.invalidate_recordset()
        self.assertEqual(first.migration_state, 'skipped')
        self.assertEqual(second.migration_state, 'skipped')
        self.assertFalse(first.crm_lead_id)
        self.assertFalse(second.crm_lead_id)

    # ------------------------------------------------------------------
    # Idempotence
    # ------------------------------------------------------------------
    def test_the_migration_is_idempotent(self):
        self._legacy_lead()
        self._legacy_lead(partner=self.other_buyer)
        first_report = self._run()
        created = self.Lead.search_count([('re_legacy_lead_id', '!=', False)])
        self.assertEqual(first_report['total'], 2)

        second_report = self._run()
        self.assertEqual(second_report['total'], 0,
                         "A second run found work to do")
        self.assertEqual(
            self.Lead.search_count([('re_legacy_lead_id', '!=', False)]),
            created, "A second run created more opportunities")

    def test_a_skipped_row_is_retried_on_the_next_run(self):
        """Skipped is not final — fixing the data and re-running must work."""
        legacy = self.LegacyLead.with_context(re_legacy_migration=True).create({
            'partner_name': 'Later Fixed', 'agent_id': self.agent.id})
        self._run()
        legacy.invalidate_recordset()
        self.assertEqual(legacy.migration_state, 'skipped')

        legacy.partner_id = self.buyer.id
        report = self._run()
        self.assertEqual(report['total'], 1)
        legacy.invalidate_recordset()
        self.assertIn(legacy.migration_state, ('migrated', 'linked'))
        self.assertTrue(legacy.crm_lead_id)

    def test_the_report_names_every_record(self):
        """Migration output must contain counts AND references."""
        legacy = self._legacy_lead()
        report = self._run()
        row = report['migrated'][0]
        for key in ('legacy_id', 'reference', 'partner', 'crm_lead_id', 'note'):
            self.assertIn(key, row)
        self.assertEqual(row['reference'], legacy.name)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def test_the_conversation_is_linked_rather_than_moved(self):
        legacy = self._legacy_lead()
        legacy.message_post(body='Customer called about the corner unit.')
        message_count = len(legacy.message_ids)
        self._run()
        legacy.invalidate_recordset()

        # The legacy thread is intact — re-parenting it would be destructive
        # if the migration were ever re-run against a partial database.
        self.assertEqual(len(legacy.message_ids), message_count)
        # And the new opportunity says where it came from.
        bodies = ' '.join(legacy.crm_lead_id.message_ids.mapped('body'))
        self.assertIn(legacy.name, bodies)

    def test_followers_are_carried_across(self):
        legacy = self._legacy_lead()
        legacy.message_subscribe(partner_ids=self.seller.ids)
        self._run()
        legacy.invalidate_recordset()
        followers = legacy.crm_lead_id.message_follower_ids.mapped('partner_id')
        self.assertIn(self.seller, followers)
