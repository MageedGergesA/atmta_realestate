# -*- coding: utf-8 -*-
"""M3G–M3J — the approval matrix, and the two Phase 0 bypasses it closes.

Phase 0 found two ways past approval and neither needed any special access:

```
    tick "urgent"          → the request approved itself
    be in the approver     → approve your own request
    group
```

Both were failures of the same kind. Approval was treated as a capability
question — is this user allowed to press the button — when it is a separation
question: is this the *other* person. M3 answers the second question, on the
server, with the requester's identity rather than with a group membership.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import M3Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM3Approval(M3Common):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        self.Rule = self.env['realestate.procurement.approval.rule']
        self.approver = self._user(
            'Approver', 'real_estate_procurement.group_procurement_approver')
        self.director = self._user(
            'Director', 'real_estate_procurement.group_procurement_manager')
        self.requester = self._user(
            'Requester', 'real_estate_procurement.group_procurement_requester')

    def _user(self, name, *groups):
        login = '%s.m3.%d' % (name.lower(), self._next())
        return self.env['res.users'].create({
            'name': name, 'login': login,
            'email': '%s@example.com' % login,
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [self.env.ref(xmlid).id for xmlid in (
                'base.group_user', 'real_estate_developer.group_dev_readonly',
                'real_estate_construction.group_construction_user') + groups
            ])],
        })

    def _rule(self, name, group, **kwargs):
        values = {
            'name': name,
            'group_id': self.env.ref(group).id,
            'company_id': self.company.id,
        }
        values.update(kwargs)
        return self.Rule.create(values)

    # -- TEST H --------------------------------------------------------
    def test_h_a_requester_cannot_approve_their_own_request(self):
        self.company.procurement_allow_self_approval = False
        self._rule('Any amount',
                   'real_estate_procurement.group_procurement_approver')
        request = self._demand(1_000.0, approve=False,
                               requested_by_id=self.approver.id)

        step = request.approval_step_ids
        self.assertEqual(len(step), 1)
        with self.assertRaises(UserError):
            step.with_user(self.approver).action_approve()

        self.assertEqual(request.state, 'submitted')
        self.assertEqual(request.reserved_amount, 0.0)

        # Somebody else in the same group can, which is the whole point.
        other = self._user(
            'Second Approver',
            'real_estate_procurement.group_procurement_approver')
        step.with_user(other).action_approve()
        self.assertEqual(request.state, 'approved')
        self.assertEqual(request.approved_by_id, other)
        self.assertEqual(request.reserved_amount, 1_000_000.0)

    def test_h2_self_approval_is_possible_only_where_stated_and_bounded(self):
        """An explicit company decision, with a ceiling."""
        self.company.write({
            'procurement_allow_self_approval': True,
            'procurement_self_approval_limit': 500_000.0,
        })
        self._rule('Any amount',
                   'real_estate_procurement.group_procurement_approver')

        small = self._demand(400.0, approve=False,
                             requested_by_id=self.approver.id)
        small.approval_step_ids.with_user(self.approver).action_approve()
        self.assertEqual(small.state, 'approved')

        large = self._demand(900.0, approve=False,
                             requested_by_id=self.approver.id)
        with self.assertRaises(UserError):
            large.approval_step_ids.with_user(self.approver).action_approve()

    def test_h3_the_decision_maker_is_the_session_user(self):
        """`approved_by` is never taken from what the caller passed in."""
        self._rule('Any amount',
                   'real_estate_procurement.group_procurement_approver')
        request = self._demand(1_000.0, approve=False)
        step = request.approval_step_ids

        step.with_user(self.approver).with_context(
            approver_id=self.director.id).action_approve()

        self.assertEqual(step.approver_id, self.approver)
        self.assertEqual(request.approved_by_id, self.approver)

    # -- TEST I --------------------------------------------------------
    def test_i_urgent_demand_still_meets_every_control(self):
        self._set_budget_policy('block')
        self._rule('Any amount',
                   'real_estate_procurement.group_procurement_approver')
        request = self._demand(1_000.0, approve=False, priority='1')

        self.assertEqual(request.state, 'submitted')
        self.assertTrue(request.approval_step_ids,
                        "Urgent demand generates approval steps like any "
                        "other kind.")
        self.assertEqual(request.reserved_amount, 0.0)
        self.assertFalse(request.purchase_order_ids)

    def test_i2_urgency_can_require_more_authority_and_never_less(self):
        """The priority dimension adds an approver. There is no rule shape
        that removes one."""
        self._rule('Standard',
                   'real_estate_procurement.group_procurement_approver',
                   sequence=10)
        self._rule('Emergency authority',
                   'real_estate_procurement.group_procurement_manager',
                   sequence=20, priority='1')

        normal = self._demand(1_000.0, approve=False)
        urgent = self._demand(1_000.0, approve=False, priority='1')

        self.assertEqual(len(normal.approval_step_ids), 1)
        self.assertEqual(len(urgent.approval_step_ids), 2)

    # -- Matrix dimensions ---------------------------------------------
    def test_rules_match_on_amount_project_type_and_position(self):
        self._rule('Small', 'real_estate_procurement.group_procurement_approver',
                   min_amount=0.0, max_amount=1_000_000.0)
        self._rule('Large', 'real_estate_procurement.group_procurement_manager',
                   min_amount=1_000_000.0)
        self._rule('Other project',
                   'real_estate_procurement.group_procurement_manager',
                   project_id=self._project().id)
        self._rule('Services only',
                   'real_estate_procurement.group_procurement_manager',
                   procurement_type='service')

        small = self._demand(500.0, approve=False)
        large = self._demand(4_000.0, approve=False)

        self.assertEqual(small.approval_step_ids.mapped('rule_name'),
                         ['Small'])
        self.assertEqual(large.approval_step_ids.mapped('rule_name'),
                         ['Large'])

    def test_an_over_budget_position_can_require_its_own_approver(self):
        self._rule('Everything',
                   'real_estate_procurement.group_procurement_approver')
        self._rule('Over budget authority',
                   'real_estate_procurement.group_procurement_manager',
                   sequence=20, budget_status='over_budget')

        self._demand(9_000.0)
        over = self._demand(3_000.0, approve=False)

        self.assertEqual(over.approval_control_status, 'over_budget')
        self.assertEqual(len(over.approval_step_ids), 2)

    # -- M3H snapshot --------------------------------------------------
    def test_a_step_keeps_the_authority_it_was_raised_under(self):
        rule = self._rule(
            'Original name',
            'real_estate_procurement.group_procurement_approver')
        request = self._demand(1_000.0, approve=False)
        step = request.approval_step_ids

        rule.write({'name': 'Renamed later', 'active': False})

        self.assertEqual(step.rule_name, 'Original name')
        self.assertEqual(step.amount_basis, 1_000_000.0)

    def test_changing_the_basis_invalidates_the_pending_approval(self):
        self._rule('Any amount',
                   'real_estate_procurement.group_procurement_approver')
        request = self._demand(1_000.0, approve=False)
        step = request.approval_step_ids

        request.action_back_to_draft()
        request.line_ids.qty = 2_000.0
        request.with_context(re_procurement_revision=True).write(
            {'state': 'submitted'})

        with self.assertRaises(UserError):
            step.with_user(self.approver).action_approve()

    # -- M3H rejection -------------------------------------------------
    def test_rejection_needs_a_reason_and_keeps_earlier_approvals(self):
        self._rule('First', 'real_estate_procurement.group_procurement_approver',
                   sequence=10)
        self._rule('Second',
                   'real_estate_procurement.group_procurement_manager',
                   sequence=20)
        request = self._demand(1_000.0, approve=False)
        first, second = request.approval_step_ids.sorted('sequence')

        first.with_user(self.approver).action_approve()
        with self.assertRaises(UserError):
            second.with_user(self.director).action_reject()

        second.comment = 'The scope belongs to the subcontractor.'
        second.with_user(self.director).action_reject()

        self.assertEqual(request.state, 'rejected')
        self.assertEqual(first.decision, 'approved',
                         "The first approval happened. It is not erased "
                         "because a later one refused.")
        self.assertEqual(second.decision, 'rejected')
        self.assertEqual(request.reserved_amount, 0.0)

    def test_resubmission_opens_a_new_cycle(self):
        self._rule('Any amount',
                   'real_estate_procurement.group_procurement_approver')
        request = self._demand(1_000.0, approve=False)
        step = request.approval_step_ids
        step.comment = 'Not this quarter.'
        step.with_user(self.approver).action_reject()

        request.action_submit()

        self.assertEqual(request.state, 'submitted')
        self.assertEqual(len(request.approval_step_ids), 2,
                         "The rejected cycle is kept; a new one is opened "
                         "beside it.")
        self.assertEqual(
            len(request.approval_step_ids.filtered(
                lambda s: s.decision == 'rejected')), 1)

    def test_steps_are_decided_in_sequence(self):
        self._rule('First', 'real_estate_procurement.group_procurement_approver',
                   sequence=10)
        self._rule('Second',
                   'real_estate_procurement.group_procurement_manager',
                   sequence=20)
        request = self._demand(1_000.0, approve=False)
        first, second = request.approval_step_ids.sorted('sequence')

        with self.assertRaises(UserError):
            second.with_user(self.director).action_approve()

        first.with_user(self.approver).action_approve()
        self.assertEqual(request.state, 'submitted',
                         "One of two approvals is not an approval.")
        second.with_user(self.director).action_approve()
        self.assertEqual(request.state, 'approved')

    def test_a_named_approver_gets_one_activity_and_only_one(self):
        self._rule('Named', 'real_estate_procurement.group_procurement_approver',
                   approver_user_id=self.approver.id)
        request = self._demand(1_000.0, approve=False)
        step = request.approval_step_ids

        self.assertTrue(step.activity_id)
        self.assertEqual(step.activity_id.user_id, self.approver)
        activity = step.activity_id
        step._schedule_activity()
        self.assertEqual(step.activity_id, activity,
                         "Scheduling twice must not produce a second entry.")

        with self.assertRaises(UserError):
            step.with_user(self.director).action_approve()
        step.with_user(self.approver).action_approve()
        self.assertFalse(step.activity_id.exists())

    def test_approval_aging_is_recorded_on_the_request(self):
        self._rule('Any amount',
                   'real_estate_procurement.group_procurement_approver')
        request = self._demand(1_000.0, approve=False)

        self.assertTrue(request.submitted_on)
        self.assertEqual(request.current_step_id, request.approval_step_ids)
        self.assertTrue(request.waiting_since)

        request.approval_step_ids.with_user(self.approver).action_approve()
        self.assertFalse(request.current_step_id)

    def test_a_decided_step_cannot_be_deleted(self):
        self._rule('Any amount',
                   'real_estate_procurement.group_procurement_approver')
        request = self._demand(1_000.0, approve=False)
        step = request.approval_step_ids
        step.with_user(self.approver).action_approve()

        with self.assertRaises(UserError):
            step.unlink()
