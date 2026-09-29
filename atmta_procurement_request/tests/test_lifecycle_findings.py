# -*- coding: utf-8 -*-
"""Regressions found by the procurement lifecycle run, on demand and plans."""
from unittest.mock import patch

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, new_test_user, tagged

ROLE = 'atmta_roles.group_procurement_'


@tagged('post_install', '-at_install', 'atmta_request')
class TestRequestLifecycleFindings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.Request = env['realestate.material.request']
        cls.Plan = env['realestate.procurement.plan']
        # Project read belongs to the application owning the project screens,
        # none of which is installed here; grant what the lifecycle run had.
        env['ir.model.access'].create({
            'name': 'test project reader',
            'model_id': env['ir.model']._get('realestate.project').id,
            'group_id': env.ref('base.group_user').id,
            'perm_read': True,
        })
        cls.project = env['realestate.project'].create({
            'name': 'Request findings', 'code': 'RLF1',
            'company_id': env.company.id})
        cls.product = env['product.product'].create({
            'name': 'Request findings item', 'type': 'consu',
            'purchase_ok': True,
            'uom_id': env.ref('uom.product_uom_unit').id})
        cls.requester = new_test_user(
            env, login='rlf_requester', email='rlf_requester@example.com',
            groups='base.group_user,%srequester' % ROLE)
        # Holds write access to the requisition through the approver role,
        # so what refuses the revision is the rule and not an ACL.
        cls.approver = new_test_user(
            env, login='rlf_approver', email='rlf_approver@example.com',
            groups='base.group_user,%sapprover' % ROLE)

    def _request(self, user):
        return self.Request.with_user(user).create({
            'project_id': self.project.id,
            'requested_by_id': user.id,
            'line_ids': [(0, 0, {'product_id': self.product.id, 'qty': 5,
                                 'uom_id': self.product.uom_id.id,
                                 'estimated_unit_cost': 10.0})],
        })

    def _plan(self):
        return self.Plan.create({
            'project_id': self.project.id,
            'title': 'Findings plan',
            'line_ids': [(0, 0, {'description': 'Planned rebar',
                                 'product_id': self.product.id,
                                 'quantity': 5,
                                 'estimated_unit_cost': 10.0})],
        })

    @staticmethod
    def _button(model, xpath):
        arch = model.get_view(view_type='form')['arch']
        return etree.fromstring(arch).xpath(xpath)[0]

    # -- item 6: the requester revises their own requisition ---------------
    def test_requester_revises_through_the_wizard(self):
        request = self._request(self.requester)
        request.with_user(self.requester).action_submit()
        action = request.with_user(self.requester
                                   ).action_open_revision_wizard()
        form = Form(self.env[action['res_model']].with_user(
            self.requester).with_context(action['context']))
        form.reason = 'Quantity was mistyped.'
        form.save().action_revise()
        self.assertEqual(request.state, 'draft')
        self.assertEqual(request.revision, 1)
        self.assertEqual(request.revision_ids.revised_by_id, self.requester)

    def test_an_approver_does_not_revise_what_they_decide(self):
        request = self._request(self.requester)
        request.action_submit()
        with self.assertRaises(UserError):
            request.with_user(self.approver).action_revise('not my act')
        self.assertEqual(request.state, 'submitted')

    # -- item 12: a draft plan raises no requisition -----------------------
    def test_a_draft_plan_line_cannot_raise_a_requisition(self):
        plan = self._plan()
        with self.assertRaises(UserError):
            plan.line_ids.action_create_requisition()
        self.assertFalse(plan.line_ids.request_ids)
        plan.action_review()
        with self.assertRaises(UserError):
            plan.line_ids.action_create_requisition()
        plan.action_approve()
        request = plan.line_ids.action_create_requisition()
        self.assertEqual(request.plan_line_id, plan.line_ids)

    # -- UX: the plan buttons open what they created -----------------------
    def test_raise_requisition_button_opens_the_requisition(self):
        plan = self._plan()
        plan.action_review()
        plan.action_approve()
        plan.action_activate()
        button = self._button(
            self.Plan, "//field[@name='line_ids']/list/button")
        result = getattr(plan.line_ids, button.get('name'))()
        self.assertIsInstance(result, dict,
                              "the button returns a record, so the screen "
                              "stays on the plan")
        self.assertEqual(result['res_model'], 'realestate.material.request')
        self.assertEqual(result['res_id'], plan.line_ids.request_ids.id)

    def test_new_revision_button_opens_the_revision(self):
        plan = self._plan()
        plan.action_review()
        plan.action_approve()
        button = self._button(
            self.Plan, "//header/button[@string='New Revision']")
        result = getattr(plan, button.get('name'))()
        self.assertIsInstance(result, dict,
                              "the button returns a record, so the screen "
                              "stays on the superseded plan")
        self.assertEqual(result['res_model'], 'realestate.procurement.plan')
        self.assertEqual(result['res_id'], plan.superseded_by_id.id)

    # -- R3: cancelling withdraws the approvals it will never decide ------
    def test_cancelling_withdraws_the_undecided_approvals(self):
        """Rejection and a return to draft both withdraw the open steps.

        Cancelling left them behind, so a cancelled requisition kept live
        approval steps for ever: they sat in approvers' queues and counted as
        work nobody could ever clear. Approval steps belong to Control, which
        this module does not depend on, so what is asserted here is the seam
        Control fills — the same seam `action_back_to_draft` already uses.
        """
        request = self._request(self.requester)
        request.action_submit()
        Request = type(self.Request)
        with patch.object(Request, '_cancel_pending_approvals',
                          autospec=True,
                          side_effect=Request._cancel_pending_approvals
                          ) as withdraw:
            request.action_cancel()
        self.assertEqual(request.state, 'cancelled')
        self.assertTrue(
            withdraw.called,
            "action_cancel left the undecided approval steps behind.")

    def test_returning_to_draft_still_withdraws_them(self):
        """The behaviour cancellation is being aligned with, pinned."""
        request = self._request(self.requester)
        request.action_submit()
        Request = type(self.Request)
        with patch.object(Request, '_cancel_pending_approvals',
                          autospec=True,
                          side_effect=Request._cancel_pending_approvals
                          ) as withdraw:
            request.action_back_to_draft()
        self.assertEqual(request.state, 'draft')
        self.assertTrue(withdraw.called)
