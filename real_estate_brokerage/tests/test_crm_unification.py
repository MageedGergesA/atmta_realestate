# -*- coding: utf-8 -*-
"""M1 — one customer opportunity spine.

The founding assertion of this release: there is **one** pipeline, it is
`crm.lead`, and the legacy Brokerage lead can no longer grow a second one.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import BrokerageCommon


@tagged('post_install', '-at_install', 'atmta_brokerage')
class TestCrmIsCanonical(BrokerageCommon):

    def test_the_requirements_layer_lives_on_crm_lead(self):
        """Budget, area, bedrooms and types are CRM fields now."""
        for field in ('re_intent', 're_market', 're_budget_min',
                      're_budget_max', 're_area_min', 're_area_max',
                      're_bedrooms_min', 're_property_type_ids',
                      're_project_ids', 're_location_ids'):
            self.assertIn(field, self.Lead._fields,
                          "crm.lead is missing %s" % field)

    def test_the_flag_marks_only_real_estate_opportunities(self):
        """A generic CRM pipeline in the same database stays untouched."""
        generic = self.Lead.create({'name': 'Generic Deal',
                                    'type': 'opportunity'})
        self.assertFalse(generic.re_is_realestate)

        re_lead = self._opportunity()
        self.assertTrue(re_lead.re_is_realestate)

    def test_the_flag_is_set_by_any_requirement(self):
        lead = self.Lead.create({'name': 'Budget Only',
                                 're_budget_max': 500000.0})
        self.assertTrue(lead.re_is_realestate)

    def test_no_parallel_pipeline_state_was_added(self):
        """The pipeline is `stage_id`; loss is Odoo's own mechanic.

        A second selection is what made the legacy model impossible to
        reconcile with CRM in the first place.
        """
        added = set(self.Lead._fields) - set(
            self.env['crm.lead']._fields.keys() - {
                f for f in self.Lead._fields if f.startswith('re_')})
        for suspicious in ('re_state', 're_stage', 're_pipeline_state'):
            self.assertNotIn(suspicious, self.Lead._fields)

    def test_the_real_estate_stages_exist_and_are_ordered(self):
        keys = ['new', 'contacted', 'qualified', 'matching', 'viewing',
                'negotiation', 'closing', 'won']
        stages = [self._stage(k) for k in keys]
        sequences = [s.sequence for s in stages]
        self.assertEqual(sequences, sorted(sequences),
                         "The real-estate stages are not in pipeline order")
        self.assertTrue(stages[-1].is_won, "The last stage is not a won stage")

    def test_the_stages_are_team_scoped(self):
        """A stage with no team is global and would pollute every pipeline."""
        for key in ('new', 'qualified', 'won'):
            stage = self._stage(key)
            self.assertTrue(
                stage.team_id,
                "Stage %r has no team, so it appears in every unrelated "
                "pipeline in the database" % stage.name)
            self.assertEqual(stage.team_id, self.re_team)

    def test_odoo_duplicate_detection_is_reused_not_rebuilt(self):
        """M5 — Odoo already surfaces candidates without auto-merging."""
        self.assertIn('duplicate_lead_ids', self.Lead._fields)
        first = self._opportunity()
        second = self._opportunity()
        second.invalidate_recordset()
        self.assertIn(first, second.duplicate_lead_ids,
                      "Two opportunities for one partner are not flagged")
        # Surfaced, never merged: both records still exist and are unchanged.
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertNotEqual(first, second)

    def test_stage_durations_come_from_odoo(self):
        """M26 — funnel elapsed time needs no custom history table."""
        field = self.Lead._fields.get('duration_tracking')
        self.assertTrue(field, "crm.lead lost duration_tracking")
        self.assertEqual(field.type, 'json',
                         "duration_tracking is no longer Odoo's stage-duration "
                         "field, so the funnel would need a custom history "
                         "table after all")

    def test_loss_uses_odoo_mechanics(self):
        lead = self._opportunity()
        reason = self.env.ref('real_estate_brokerage.lost_reason_re_price')
        lead.action_set_lost(lost_reason_id=reason.id)
        lead.invalidate_recordset()
        self.assertFalse(lead.active)
        self.assertEqual(lead.lost_reason_id, reason)


@tagged('post_install', '-at_install', 'atmta_brokerage')
class TestLegacyLeadIsClosed(BrokerageCommon):
    """The second spine cannot grow back."""

    def test_creating_a_legacy_lead_is_refused(self):
        with self.assertRaises(UserError) as err:
            self.LegacyLead.create({'partner_id': self.buyer.id})
        message = str(err.exception)
        self.assertIn('now CRM opportunities', message)
        self.assertIn('CRM pipeline', message)

    def test_the_migration_may_still_create_one(self):
        """Otherwise the migration could not run at all."""
        legacy = self._legacy_lead()
        self.assertTrue(legacy.id)
        self.assertTrue(legacy.name.startswith('LEAD-'))

    def test_the_legacy_model_and_table_survive(self):
        """Rule 5 — production references must stay readable."""
        self.assertIn('realestate.lead', self.env)
        legacy = self._legacy_lead()
        self.assertTrue(self.LegacyLead.search([('id', '=', legacy.id)]))

    def test_legacy_actions_route_to_crm_rather_than_500(self):
        legacy = self._legacy_lead()
        for method in ('action_qualify', 'action_match', 'action_mark_lost',
                       'action_reopen', 'action_schedule_viewing'):
            with self.assertRaises(UserError) as err:
                getattr(legacy, method)()
            self.assertIn('CRM', str(err.exception))

    def test_a_migrated_legacy_lead_points_at_its_opportunity(self):
        legacy = self._legacy_lead()
        self.Migration.run()
        legacy.invalidate_recordset()
        self.assertTrue(legacy.crm_lead_id)
        action = legacy.action_open_crm_lead()
        self.assertEqual(action['res_model'], 'crm.lead')
        self.assertEqual(action['res_id'], legacy.crm_lead_id.id)

    def test_the_bridge_is_visible_from_both_sides(self):
        legacy = self._legacy_lead()
        self.Migration.run()
        legacy.invalidate_recordset()
        crm_lead = legacy.crm_lead_id
        self.assertEqual(crm_lead.re_legacy_lead_id, legacy)
        self.assertEqual(crm_lead.re_legacy_reference, legacy.name)


@tagged('post_install', '-at_install', 'atmta_brokerage')
class TestWorkflowRepointedAtCrm(BrokerageCommon):
    """Viewings and offers hang off the opportunity now."""

    def test_viewing_and_offer_carry_a_crm_link(self):
        self.assertIn('crm_lead_id', self.Viewing._fields)
        self.assertIn('crm_lead_id', self.Offer._fields)

    def test_the_legacy_link_was_not_removed(self):
        """Production rows reference it."""
        self.assertIn('lead_id', self.Viewing._fields)
        self.assertIn('lead_id', self.Offer._fields)

    def test_setting_the_legacy_link_mirrors_to_crm(self):
        legacy = self._legacy_lead()
        self.Migration.run()
        legacy.invalidate_recordset()
        listing = self._listing(activate=True)
        viewing = self.Viewing.create({
            'listing_id': listing.id,
            'lead_id': legacy.id,
            'scheduled_at': '2026-09-01 10:00:00',
        })
        self.assertEqual(viewing.crm_lead_id, legacy.crm_lead_id)

    def test_setting_the_crm_link_mirrors_to_legacy(self):
        legacy = self._legacy_lead()
        self.Migration.run()
        legacy.invalidate_recordset()
        listing = self._listing(activate=True)
        offer = self.Offer.create({
            'listing_id': listing.id,
            'partner_id': self.buyer.id,
            'crm_lead_id': legacy.crm_lead_id.id,
            'amount': 900000.0,
        })
        self.assertEqual(offer.lead_id, legacy)

    def test_a_crm_only_opportunity_needs_no_legacy_row(self):
        """The normal case from now on: no legacy record exists at all."""
        opportunity = self._opportunity()
        listing = self._listing(activate=True)
        offer = self.Offer.create({
            'listing_id': listing.id,
            'partner_id': self.buyer.id,
            'crm_lead_id': opportunity.id,
            'amount': 950000.0,
        })
        self.assertEqual(offer.crm_lead_id, opportunity)
        self.assertFalse(offer.lead_id)


@tagged('post_install', '-at_install', 'atmta_brokerage')
class TestFirstTouchAttribution(BrokerageCommon):
    """M16 — the original source is a fact and is never overwritten."""

    def test_first_touch_is_captured_at_creation(self):
        source = self.env['utm.source'].create({'name': 'Property Finder'})
        lead = self._opportunity(source_id=source.id)
        self.assertEqual(lead.re_first_source_id, source)
        self.assertTrue(lead.re_first_seen_on)

    def test_changing_the_current_source_does_not_rewrite_the_first(self):
        first = self.env['utm.source'].create({'name': 'Billboard'})
        later = self.env['utm.source'].create({'name': 'Retargeting'})
        lead = self._opportunity(source_id=first.id)
        lead.source_id = later.id
        lead.invalidate_recordset()
        self.assertEqual(lead.source_id, later)
        self.assertEqual(lead.re_first_source_id, first,
                         "The original attribution was overwritten")

    def test_first_touch_fields_are_readonly(self):
        for field in ('re_first_source_id', 're_first_medium_id',
                      're_first_campaign_id', 're_first_seen_on'):
            self.assertTrue(self.Lead._fields[field].readonly, field)
