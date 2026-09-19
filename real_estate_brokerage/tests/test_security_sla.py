# -*- coding: utf-8 -*-
"""M18 / M19 / M20 / M21 — record rules, attribution and the response clock."""

from odoo import fields
from odoo.tests import tagged

from .common import BrokerageCommon


@tagged('post_install', '-at_install')
class TestMultiCompanyIsolation(BrokerageCommon):
    """0.1 shipped with no `ir.rule` records at all."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_company = cls.env['res.company'].create(
            {'name': 'Rival Brokerage'})
        cls.rival_agent = cls.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Rival Agent',
                'login': 'rival.agent@test.example',
                'company_id': cls.other_company.id,
                'company_ids': [(6, 0, [cls.other_company.id])],
                'groups_id': [(6, 0, [
                    cls.env.ref('base.group_user').id,
                    cls.env.ref(
                        'real_estate_brokerage.group_realestate_sales_manager'
                    ).id,
                    cls.env.ref('sales_team.group_sale_salesman').id,
                ])],
            })

    def test_another_companys_listings_are_invisible(self):
        listing = self._listing()

        visible = self.Listing.with_user(self.rival_agent).search(
            [('id', '=', listing.id)])

        self.assertFalse(
            visible, "A rival company's agent could read our listing.")

    def test_seniority_does_not_escape_company_isolation(self):
        """The rival user above is a *manager*, and still sees nothing."""
        offer = self._offer()

        visible = self.Offer.with_user(self.rival_agent).search(
            [('id', '=', offer.id)])

        self.assertFalse(visible)

    def test_transactions_are_isolated(self):
        txn = self._transaction(self._listing(activate=True))

        visible = self.Transaction.with_user(self.rival_agent).search(
            [('id', '=', txn.id)])

        self.assertFalse(visible)

    def test_lead_registrations_are_isolated(self):
        """A competitor's registered-lead book is the most sensitive of all."""
        reg = self._registration(self._agreement(self._broker()))
        reg.action_submit()

        visible = self.env['realestate.lead.registration'].with_user(
            self.rival_agent).search([('id', '=', reg.id)])

        self.assertFalse(visible)

    def test_our_own_agent_still_sees_our_records(self):
        """An isolation rule that locks everybody out is not a fix."""
        listing = self._listing()

        visible = self.Listing.with_user(self.agent).search(
            [('id', '=', listing.id)])

        self.assertTrue(visible)


@tagged('post_install', '-at_install')
class TestProjectScoping(BrokerageCommon):
    """An agent working one tower has no business reading another's pipeline."""

    def setUp(self):
        super().setUp()
        self.other_project = self.env['realestate.project'].create({
            'name': 'Other Tower', 'code': 'OTW',
            'company_id': self.company.id, 'commercial_state': 'selling'})
        self.other_phase = self.env['realestate.phase'].create({
            'name': 'OT Phase', 'project_id': self.other_project.id,
            'commercial_state': 'selling'})

    def test_an_unscoped_user_sees_every_project(self):
        listing = self._listing()

        self.assertFalse(self.agent.re_project_scoped)
        self.assertIn(
            listing, self.Listing.with_user(self.agent).search([]))

    def test_a_scoped_team_hides_other_projects(self):
        team = self.Team.create({
            'name': 'Palm Heights Only',
            'realestate_project_scoped': True,
            'allowed_realestate_project_ids': [(6, 0, self.project.ids)],
        })
        self._join(team)
        ours = self._listing()
        theirs = self._listing(unit=self._other_project_unit())

        visible = self.Listing.with_user(self.agent).search([])

        self.assertIn(ours, visible)
        self.assertNotIn(theirs, visible)

    def test_the_allowed_set_resolves_for_a_domain(self):
        """`None` is right for Python and useless in an `ir.rule`."""
        team = self.Team.create({
            'name': 'Scoped',
            'realestate_project_scoped': True,
            'allowed_realestate_project_ids': [(6, 0, self.project.ids)],
        })
        self._join(team)
        self.agent.invalidate_recordset()

        self.assertTrue(self.agent.re_project_scoped)
        self.assertEqual(self.agent.re_allowed_project_ids, self.project)

    def test_matches_are_scoped_too(self):
        team = self.Team.create({
            'name': 'Palm Heights Only',
            'realestate_project_scoped': True,
            'allowed_realestate_project_ids': [(6, 0, self.project.ids)],
        })
        self._join(team)
        lead = self._opportunity()
        self._unit(price=1000000.0)
        self._other_project_unit()
        matches = lead._re_run_matching()

        visible = self.env['realestate.property.match'].with_user(
            self.agent).search([('id', 'in', matches.ids)])

        self.assertTrue(
            all(m.project_id == self.project for m in visible))

    # ------------------------------------------------------------------
    def _other_project_unit(self):
        unit = self._unit(released=False, project_id=self.other_project.id,
                          phase_id=self.other_phase.id)
        batch = self.env['realestate.unit.release.batch'].create({
            'project_id': self.other_project.id,
            'phase_id': self.other_phase.id,
            'property_ids': [(6, 0, unit.ids)],
        })
        batch.action_approve()
        batch.action_release()
        return unit


@tagged('post_install', '-at_install')
class TestResponseSla(BrokerageCommon):
    """How fast somebody called back is the strongest predictor there is."""

    def test_a_new_opportunity_starts_a_clock(self):
        self.re_team.re_first_response_hours = 4.0

        lead = self._opportunity()

        self.assertTrue(lead.re_response_deadline)
        self.assertEqual(lead.re_sla_state, 'pending')

    def test_a_team_with_no_target_has_no_clock(self):
        self.re_team.re_first_response_hours = 0.0

        lead = self._opportunity()

        self.assertFalse(lead.re_response_deadline)
        self.assertFalse(lead.re_sla_state)

    def test_responding_in_time_reads_on_time(self):
        self.re_team.re_first_response_hours = 4.0
        lead = self._opportunity()

        lead.action_log_first_response()

        self.assertEqual(lead.re_sla_state, 'on_time')
        self.assertTrue(lead.re_first_response_by_id)

    def test_responding_after_the_deadline_reads_late(self):
        self.re_team.re_first_response_hours = 4.0
        lead = self._opportunity()
        lead.re_response_deadline = fields.Datetime.subtract(
            fields.Datetime.now(), hours=1)

        lead.action_log_first_response()

        self.assertEqual(lead.re_sla_state, 'late')

    def test_an_unanswered_overdue_lead_reads_breached(self):
        self.re_team.re_first_response_hours = 4.0
        lead = self._opportunity()

        lead.re_response_deadline = fields.Datetime.subtract(
            fields.Datetime.now(), hours=1)

        self.assertEqual(lead.re_sla_state, 'breached')

    def test_only_the_first_response_counts(self):
        self.re_team.re_first_response_hours = 4.0
        lead = self._opportunity()
        lead.action_log_first_response()
        first = lead.re_first_response_on

        lead.action_log_first_response()

        self.assertEqual(lead.re_first_response_on, first)

    def test_a_tracking_notification_is_not_a_response(self):
        """Moving a stage is not calling anybody back."""
        self.re_team.re_first_response_hours = 4.0
        lead = self._opportunity()

        lead.stage_id = self._stage('qualified')

        self.assertFalse(lead.re_first_response_on)

    def test_an_internal_note_is_not_a_response(self):
        """A note to a colleague is not somebody calling the customer back."""
        self.re_team.re_first_response_hours = 4.0
        lead = self._opportunity()

        lead.message_post(body='Chasing the owner for keys',
                          message_type='comment',
                          subtype_xmlid='mail.mt_note')

        self.assertFalse(lead.re_first_response_on)

    def test_a_message_to_the_customer_is(self):
        self.re_team.re_first_response_hours = 4.0
        lead = self._opportunity()

        lead.message_post(body='Called back, viewing on Thursday',
                          message_type='comment',
                          subtype_xmlid='mail.mt_comment')

        self.assertTrue(lead.re_first_response_on)

    def test_the_deadline_does_not_move_when_the_target_does(self):
        """A deadline that moves is not a deadline."""
        self.re_team.re_first_response_hours = 4.0
        lead = self._opportunity()
        original = lead.re_response_deadline

        self.re_team.re_first_response_hours = 48.0

        self.assertEqual(lead.re_response_deadline, original)


@tagged('post_install', '-at_install')
class TestMarketingAttribution(BrokerageCommon):
    """The channel with the most leads is not the best channel."""

    def test_a_source_counts_its_leads(self):
        source = self.env['utm.source'].create({'name': 'Facebook'})
        source.re_spend = 120000.0
        self._opportunity(source_id=source.id)
        self._opportunity(partner=self.other_buyer, source_id=source.id)

        self.assertEqual(source.re_lead_count, 2)
        self.assertEqual(source.re_cost_per_lead, 60000.0)

    def test_cost_per_deal_needs_a_deal(self):
        source = self.env['utm.source'].create({'name': 'Billboards'})
        source.re_spend = 200000.0
        self._opportunity(source_id=source.id)

        self.assertEqual(source.re_won_count, 0)
        self.assertEqual(source.re_cost_per_deal, 0.0)

    def test_first_touch_is_what_is_counted_not_the_latest_source(self):
        """Re-tagging a lead must not move the credit."""
        first = self.env['utm.source'].create({'name': 'Referral'})
        later = self.env['utm.source'].create({'name': 'Retargeting'})
        lead = self._opportunity(source_id=first.id)

        lead.source_id = later.id

        self.assertEqual(lead.re_first_source_id, first)
        self.assertEqual(first.re_lead_count, 1)
        self.assertEqual(later.re_lead_count, 0)

    def test_the_source_survives_all_the_way_to_commission(self):
        """0.1 could name the source and never tie it to a pound earned."""
        source = self.env['utm.source'].create({'name': 'Referral'})
        lead = self._opportunity(source_id=source.id)
        txn = self._closed_deal(gross_percentage=2.0)
        txn.crm_lead_id = lead.id
        source.invalidate_recordset()

        self.assertEqual(txn.re_source_id, source)
        self.assertEqual(source.re_revenue, txn.commission_gross_amount)
