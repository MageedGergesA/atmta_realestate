# -*- coding: utf-8 -*-
"""Regressions found by the procurement lifecycle run, on the dashboard."""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, new_test_user, tagged

ROLE = 'atmta_roles.group_procurement_'


@tagged('post_install', '-at_install')
class TestDashboardLifecycleFindings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.Dashboard = env['realestate.procurement.dashboard']
        cls.approver = new_test_user(
            env, login='alf_approver', email='alf_approver@example.com',
            groups='base.group_user,%sapprover' % ROLE)
        cls.manager = new_test_user(
            env, login='alf_manager', email='alf_manager@example.com',
            groups='base.group_user,%smanager' % ROLE)
        project = env['realestate.project'].create({
            'name': 'Dashboard findings', 'code': 'ALF1',
            'company_id': env.company.id})
        product = env['product.product'].create({
            'name': 'Dashboard findings item', 'type': 'consu',
            'purchase_ok': True, 'uom_id': env.ref('uom.product_uom_unit').id})
        request = env['realestate.material.request'].create({
            'project_id': project.id, 'requested_by_id': env.user.id,
            'line_ids': [(0, 0, {'product_id': product.id, 'qty': 1,
                                 'uom_id': product.uom_id.id,
                                 'estimated_unit_cost': 10.0})],
        })
        request.action_submit()
        cls.request = request
        Step = env['realestate.procurement.approval.step']
        cls.group_step = Step.create({
            'request_id': request.id, 'rule_name': 'Any approver',
            'group_id': env.ref(ROLE + 'approver').id, 'sequence': 10,
        })
        cls.named_step = Step.create({
            'request_id': request.id, 'rule_name': 'Named manager',
            'group_id': env.ref(ROLE + 'manager').id,
            'approver_user_id': cls.manager.id, 'sequence': 20,
        })

    def _my_approvals(self, user):
        Dashboard = self.Dashboard.with_user(user)
        tiles = {t['key']: t for s in Dashboard.get_dashboard()['sections']
                 for t in s['tiles']}
        action = Dashboard.action_drill('my_approvals')
        listed = self.env[action['res_model']].with_user(user).search(
            action['domain'])
        self.assertEqual(tiles['my_approvals']['value'], len(listed),
                         "the tile and the list it opens disagree")
        return listed.filtered(lambda s: s.request_id == self.request
                               and not s.rule_id)

    def test_group_steps_wait_for_the_members_of_the_group(self):
        self.assertEqual(self._my_approvals(self.approver), self.group_step)

    def test_named_steps_wait_only_for_the_named_person(self):
        self.assertEqual(self._my_approvals(self.manager), self.named_step)


@tagged('post_install', '-at_install')
class TestProjectPolicyForm(TransactionCase):

    def test_receipt_inspection_is_on_the_project_form(self):
        # The policy group on the project form is the manager's, and reading
        # a project at all is the project application's right.
        manager = new_test_user(
            self.env, login='alf_policy_manager',
            groups='base.group_user,%smanager' % ROLE)
        # Reading a project is the project application's right and no
        # procurement role carries it; granted here so the assertion is
        # about the form and not about that.
        self.env['ir.model.access'].create({
            'name': 'test project reader',
            'model_id': self.env['ir.model']._get('realestate.project').id,
            'group_id': self.env.ref('base.group_user').id,
            'perm_read': True,
        })
        arch = self.env['realestate.project'].with_user(manager).get_view(
            view_type='form')['arch']
        self.assertIn('name="procurement_receipt_inspection"', arch)


@tagged('post_install', '-at_install')
class TestApprovalsWaitingAreOnesSomebodyIsWaitingFor(TransactionCase):
    """"Waiting" means the requisition is still awaiting a decision.

    Found by driving the Overview: the approval tiles counted undecided steps
    for ever, whatever became of the requisition under them. The step's own
    `_check_may_decide` refuses a decision on anything that is not
    `submitted`, so those figures put work in an inbox that nobody can ever
    clear.

    `action_cancel` has since learned to withdraw the steps it leaves behind,
    which closes one way in — but not the tile's reason for filtering on the
    requisition's state: requisitions cancelled before that fix still carry
    pending steps, and a requisition that moved forward to approved, ordered
    or received keeps its pending steps too, which cancellation never
    covered. So the withdrawal and the leftover are asserted separately.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.Dashboard = env['realestate.procurement.dashboard']
        cls.approver = new_test_user(
            env, login='alf_waiting_approver',
            email='alf_waiting_approver@example.com',
            groups='base.group_user,%sapprover' % ROLE)
        project = env['realestate.project'].create({
            'name': 'Waiting findings', 'code': 'ALF2',
            'company_id': env.company.id})
        product = env['product.product'].create({
            'name': 'Waiting findings item', 'type': 'consu',
            'purchase_ok': True, 'uom_id': env.ref('uom.product_uom_unit').id})
        cls.request = env['realestate.material.request'].create({
            'project_id': project.id, 'requested_by_id': env.user.id,
            'line_ids': [(0, 0, {'product_id': product.id, 'qty': 1,
                                 'uom_id': product.uom_id.id,
                                 'estimated_unit_cost': 10.0})],
        })
        cls.request.action_submit()
        cls.step = env['realestate.procurement.approval.step'].create({
            'request_id': cls.request.id, 'rule_name': 'Any approver',
            'group_id': env.ref(ROLE + 'approver').id, 'sequence': 10,
        })

    def _tile(self, key):
        Dashboard = self.Dashboard.with_user(self.approver)
        tiles = {t['key']: t for s in Dashboard.get_dashboard()['sections']
                 for t in s['tiles']}
        action = Dashboard.action_drill(key)
        listed = self.env[action['res_model']].with_user(
            self.approver).search(action['domain'])
        self.assertEqual(tiles[key]['value'], len(listed),
                         "the tile and the list it opens disagree")
        return listed

    def _age_chart_total(self):
        Dashboard = self.Dashboard.with_user(self.approver)
        chart = [c for c in Dashboard.get_dashboard()['charts']
                 if c['key'] == 'approvals_by_age'][0]
        return sum(chart['series'][0]['data'])

    def test_the_step_is_counted_while_the_requisition_is_submitted(self):
        self.assertIn(self.step, self._tile('my_approvals'))

    def test_a_cancelled_requisition_waits_for_nobody(self):
        self.request.action_cancel()
        self.assertEqual(self.request.state, 'cancelled')
        # `action_cancel` withdraws the steps it leaves behind, so the step is
        # no longer pending. That is the ending the step should have; the
        # subject here is still the tile, which must not count it either way.
        self.assertEqual(self.step.decision, 'cancelled')
        self.assertNotIn(self.step, self._tile('my_approvals'))

    def test_a_step_left_pending_on_a_cancelled_requisition_is_not_counted(
            self):
        """The half the withdrawal does not reach.

        Requisitions cancelled before `action_cancel` learned to withdraw
        their steps still carry pending ones, so the tile cannot lean on the
        decision alone. The requisition's state is what decides whether
        anybody may act, which is what `_check_may_decide` says too.
        """
        self.request.action_cancel()
        # The pre-fix leftover, reproduced: a pending step whose requisition
        # will never be decided again.
        self.step.decision = 'pending'
        self.assertNotIn(self.step, self._tile('my_approvals'))

    def test_a_cancelled_requisition_is_not_a_stale_approval(self):
        self.step.requested_on = fields.Datetime.now() - timedelta(days=30)
        self.step.flush_recordset()
        self.assertGreater(self.step.waiting_days, 7)
        before = self._age_chart_total()
        self.assertIn(self.step, self._tile('approvals_stale'))

        self.request.action_cancel()
        self.assertNotIn(self.step, self._tile('approvals_stale'))
        self.assertEqual(self._age_chart_total(), before - 1)
