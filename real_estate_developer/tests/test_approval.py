# -*- coding: utf-8 -*-
"""Phases 10 & 34 — discount authority and the commercial approval engine.

The audit found that `proposed_price` was a free money field, `proposed_vs_base`
computed the deviation from base, and then nothing happened with it: any agent
could sell at any price. These tests pin the gate shut, and — just as
importantly — pin that the gate is enforced **server-side**, not by hiding a
button.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import DeveloperCommon


@tagged('post_install', '-at_install', 'atmta_developer')
class TestApprovalAuthority(DeveloperCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rule = cls.env['realestate.commercial.approval.rule']
        cls.Request = cls.env['realestate.commercial.approval.request']

        cls.group_agent = cls.env.ref('real_estate_developer.group_dev_agent')
        cls.group_manager = cls.env.ref('real_estate_developer.group_dev_manager')

        cls.agent = cls.env['res.users'].create({
            'name': 'Sales Agent', 'login': 'appr_agent',
            'email': 'agent@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'groups_id': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.group_agent.id])],
        })
        cls.manager = cls.env['res.users'].create({
            'name': 'Sales Manager', 'login': 'appr_manager',
            'email': 'manager@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'groups_id': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.group_manager.id])],
        })

        # Agent ≤2%, Manager ≤5%. Thresholds are configuration, never constants
        # in code — the spec is explicit about not hard-coding them.
        cls.rule_agent = cls.Rule.create({
            'name': 'Agent up to 2%', 'sequence': 10,
            'company_id': cls.company.id, 'action_type': 'discount',
            'threshold_type': 'percent', 'min_value': 0.0, 'max_value': 2.0,
            'approver_group_id': cls.group_agent.id,
        })
        cls.rule_manager = cls.Rule.create({
            'name': 'Manager up to 5%', 'sequence': 20,
            'company_id': cls.company.id, 'action_type': 'discount',
            'threshold_type': 'percent', 'min_value': 2.01, 'max_value': 5.0,
            'approver_group_id': cls.group_manager.id,
        })
        cls.director = cls.env['res.users'].create({
            'name': 'Commercial Director', 'login': 'appr_director',
            'email': 'director@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'groups_id': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.group_manager.id])],
        })
        cls.rule_director = cls.Rule.create({
            'name': 'Director above 5%', 'sequence': 30,
            'company_id': cls.company.id, 'action_type': 'discount',
            'threshold_type': 'percent', 'min_value': 5.01, 'max_value': 0.0,
            'approver_user_id': cls.director.id,
        })

    # ---- rule resolution ----

    def test_band_selects_the_right_authority(self):
        self.assertEqual(
            self.Rule._find_rule('discount', 1.5, self.company), self.rule_agent)
        self.assertEqual(
            self.Rule._find_rule('discount', 4.0, self.company), self.rule_manager)

    def test_an_ungoverned_action_finds_no_rule(self):
        """No rule is 'unconfigured', not 'forbidden'.

        `unit_swap` has no authority configured here, so nothing governs it.
        `_require_approval` treats that as permitted, because blocking every
        unconfigured action would make the module unusable out of the box.
        """
        self.assertFalse(self.Rule._find_rule('unit_swap', 40.0, self.company))

    def test_unbounded_top_authority(self):
        """max_value = 0 means 'no ceiling', which the top tier needs."""
        self.assertEqual(
            self.Rule._find_rule('discount', 40.0, self.company),
            self.rule_director)

    def test_project_rule_beats_company_rule(self):
        scoped = self.Rule.create({
            'name': 'Palm Heights agents up to 3%', 'sequence': 10,
            'company_id': self.company.id, 'action_type': 'discount',
            'project_id': self.project.id,
            'threshold_type': 'percent', 'min_value': 0.0, 'max_value': 3.0,
            'approver_group_id': self.group_agent.id,
        })
        self.assertEqual(
            self.Rule._find_rule('discount', 2.5, self.company,
                                 project=self.project),
            scoped, "a project-specific rule must win over a company-wide one")
        self.assertEqual(
            self.Rule._find_rule('discount', 2.5, self.company),
            self.rule_manager, "with no project, the company rule still governs")

    def test_a_rule_must_name_an_approver(self):
        with self.assertRaises(ValidationError):
            self.Rule.create({
                'name': 'Nobody', 'company_id': self.company.id,
                'action_type': 'discount', 'threshold_type': 'any',
            })

    def test_band_must_be_ordered(self):
        with self.assertRaises(ValidationError):
            self.Rule.create({
                'name': 'Backwards', 'company_id': self.company.id,
                'action_type': 'discount', 'threshold_type': 'percent',
                'min_value': 5.0, 'max_value': 2.0,
                'approver_group_id': self.group_manager.id,
            })

    # ---- the gate ----

    def test_within_authority_passes_without_a_request(self):
        """A user whose band covers the value just acts; no paperwork.

        The manager is used rather than the agent because agents deliberately
        have read-only access to promotions -- a campaign is commercial
        configuration, not a deal. The manager still *holds* agent authority,
        because Odoo materialises implied groups into `groups_id`, so this
        genuinely exercises the ≤2% band.
        """
        promo = self.env['realestate.promotion'].create({
            'name': 'Small campaign', 'company_id': self.company.id,
            'project_id': self.project.id,
            'discount_type': 'percent', 'discount_value': 1.5,
        })
        self.assertEqual(
            self.Rule._find_rule('discount', 1.5, self.company), self.rule_agent)
        promo.action_submit()
        promo.with_user(self.manager).action_approve()
        self.assertEqual(promo.state, 'active')

    def test_beyond_authority_is_refused_server_side(self):
        promo = self.env['realestate.promotion'].create({
            'name': 'Big campaign', 'company_id': self.company.id,
            'project_id': self.project.id,
            'discount_type': 'percent', 'discount_value': 4.0,
        })
        promo.action_submit()
        with self.assertRaises(UserError) as err:
            promo.with_user(self.agent).action_approve()
        self.assertIn('approval', str(err.exception).lower())
        self.assertEqual(promo.state, 'pending_approval',
                         "a refused action must change nothing")

    def test_the_named_authority_may_act_directly(self):
        promo = self.env['realestate.promotion'].create({
            'name': 'Manager campaign', 'company_id': self.company.id,
            'project_id': self.project.id,
            'discount_type': 'percent', 'discount_value': 4.0,
        })
        promo.action_submit()
        promo.with_user(self.manager).action_approve()
        self.assertEqual(promo.state, 'active')

    def test_an_approved_request_unblocks_the_action(self):
        """Someone WITHOUT the authority can still act once it is granted.

        Set at 8%, above the manager's 5% ceiling, so the manager genuinely
        lacks the authority and the approved request is what unblocks them.
        """
        director = self.director
        promo = self.env['realestate.promotion'].create({
            'name': 'Requested campaign', 'company_id': self.company.id,
            'project_id': self.project.id,
            'discount_type': 'percent', 'discount_value': 8.0,
        })
        promo.action_submit()

        with self.assertRaises(UserError):
            promo.with_user(self.manager).action_approve()

        request = promo.with_user(self.manager)._open_approval_request(
            'discount', 8.0, reason='Bulk buyer')
        self.assertEqual(request.state, 'pending')
        request.with_user(director).action_approve()
        self.assertEqual(request.state, 'approved')

        promo.with_user(self.manager).action_approve()
        self.assertEqual(promo.state, 'active')

    # ---- self-approval ----

    def test_self_approval_is_refused_by_default(self):
        request = self.Request.create({
            'action_type': 'discount', 'company_id': self.company.id,
            'requested_value': 4.0, 'state': 'pending',
            'rule_id': self.rule_manager.id,
            'requested_by_id': self.manager.id,
        })
        with self.assertRaises(UserError) as err:
            request.with_user(self.manager).action_approve()
        self.assertIn('raised it', str(err.exception))

    def test_self_approval_can_be_configured_on(self):
        self.rule_manager.allow_self_approval = True
        request = self.Request.create({
            'action_type': 'discount', 'company_id': self.company.id,
            'requested_value': 4.0, 'state': 'pending',
            'rule_id': self.rule_manager.id,
            'requested_by_id': self.manager.id,
        })
        request.with_user(self.manager).action_approve()
        self.assertEqual(request.state, 'approved')

    def test_an_unauthorised_user_cannot_approve(self):
        request = self.Request.create({
            'action_type': 'discount', 'company_id': self.company.id,
            'requested_value': 4.0, 'state': 'pending',
            'rule_id': self.rule_manager.id,
            'requested_by_id': self.agent.id,
        })
        with self.assertRaises(UserError):
            request.with_user(self.agent).action_approve()

    def test_approver_may_grant_less_than_requested(self):
        request = self.Request.create({
            'action_type': 'discount', 'company_id': self.company.id,
            'requested_value': 5.0, 'state': 'pending',
            'rule_id': self.rule_manager.id,
            'requested_by_id': self.agent.id,
        })
        request.with_user(self.manager).action_approve(approved_value=3.0)
        self.assertEqual(request.approved_value, 3.0)

    def test_a_smaller_approval_does_not_cover_a_bigger_ask(self):
        promo = self.env['realestate.promotion'].create({
            'name': 'Creeping campaign', 'company_id': self.company.id,
            'project_id': self.project.id,
            'discount_type': 'percent', 'discount_value': 5.0,
        })
        promo.action_submit()
        request = promo._open_approval_request('discount', 5.0)
        request.with_user(self.manager).action_approve(approved_value=3.0)
        # 5% still needs 5% of authority; a 3% grant does not reach it, and the
        # manager's own band tops out at 5.0 exactly -- so raise the ask above it.
        promo.discount_value = 8.0
        with self.assertRaises(UserError):
            promo.with_user(self.manager).action_approve()

    def test_rejected_request_does_not_unblock(self):
        promo = self.env['realestate.promotion'].create({
            'name': 'Rejected campaign', 'company_id': self.company.id,
            'project_id': self.project.id,
            'discount_type': 'percent', 'discount_value': 4.0,
        })
        promo.action_submit()
        request = promo._open_approval_request('discount', 4.0)
        request.with_user(self.manager).action_reject(note='Margin too thin')
        self.assertEqual(request.state, 'rejected')
        # Above the manager's ceiling, so only the rejected request could have
        # unblocked it -- and a rejection unblocks nothing.
        promo.discount_value = 8.0
        with self.assertRaises(UserError):
            promo.with_user(self.manager).action_approve()


@tagged('post_install', '-at_install', 'atmta_developer')
class TestPromotions(DeveloperCommon):
    """Phase 9 — campaigns are not negotiated discounts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Promotion = cls.env['realestate.promotion']

    def test_percentage_promotion_amount(self):
        promo = self.Promotion.create({
            'name': 'Launch 5%', 'company_id': self.company.id,
            'project_id': self.project.id,
            'discount_type': 'percent', 'discount_value': 5.0,
        })
        self.assertEqual(promo._amount_for(1000000.0), 50000.0)

    def test_fixed_promotion_never_exceeds_the_price(self):
        promo = self.Promotion.create({
            'name': 'Flat 2m', 'company_id': self.company.id,
            'project_id': self.project.id,
            'discount_type': 'fixed', 'discount_value': 2000000.0,
        })
        self.assertEqual(promo._amount_for(1000000.0), 1000000.0,
                         "a promotion must not make a unit cost less than zero")

    def test_promotion_over_100_percent_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.Promotion.create({
                'name': 'Free flat', 'company_id': self.company.id,
                'project_id': self.project.id,
                'discount_type': 'percent', 'discount_value': 120.0,
            })

    def test_scope_restricts_eligibility(self):
        other = self.Project.create({
            'name': 'Other Project', 'code': 'OP2',
            'company_id': self.company.id})
        promo = self.Promotion.create({
            'name': 'Palm only', 'company_id': self.company.id,
            'project_id': self.project.id,
            'discount_type': 'percent', 'discount_value': 5.0,
        })
        self.assertTrue(promo._covers(self.units[0]))
        foreign = self._make_units(count=1, prefix='OP2-U',
                                   project=other, phase=None)
        self.assertFalse(promo._covers(foreign))

    def test_dates_gate_the_campaign(self):
        from datetime import timedelta
        from odoo import fields as odoo_fields
        today = odoo_fields.Date.context_today(self.Promotion)
        promo = self.Promotion.create({
            'name': 'Future', 'company_id': self.company.id,
            'project_id': self.project.id,
            'discount_type': 'percent', 'discount_value': 1.0,
            'date_start': today + timedelta(days=5),
        })
        promo.action_submit()
        promo.action_approve()
        self.assertFalse(promo._is_live_on(today))
        self.assertTrue(promo._is_live_on(today + timedelta(days=6)))

    def test_end_before_start_is_rejected(self):
        from datetime import timedelta
        from odoo import fields as odoo_fields
        today = odoo_fields.Date.context_today(self.Promotion)
        with self.assertRaises(ValidationError):
            self.Promotion.create({
                'name': 'Backwards', 'company_id': self.company.id,
                'project_id': self.project.id,
                'discount_type': 'percent', 'discount_value': 1.0,
                'date_start': today, 'date_end': today - timedelta(days=1),
            })
