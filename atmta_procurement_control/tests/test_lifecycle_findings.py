# -*- coding: utf-8 -*-
"""Regressions found by the procurement lifecycle run, each role held alone.

Every test here gives a user exactly one procurement role and has them do the
thing that role exists to do. The lifecycle run found each of these failing
with an AccessError or a refusal that belonged to somebody else's step.
"""
from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, new_test_user, tagged

ROLE = 'atmta_roles.group_procurement_'


@tagged('post_install', '-at_install', 'atmta_control')
class TestControlLifecycleFindings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.Request = env['realestate.material.request']
        cls.Rule = env['realestate.procurement.approval.rule']
        # The project reader right comes from whichever application owns the
        # project screens (Construction, Developer...). None is installed in
        # this suite, so the test grants the read the lifecycle run had.
        env['ir.model.access'].create({
            'name': 'test project reader',
            'model_id': env['ir.model']._get('realestate.project').id,
            'group_id': env.ref('base.group_user').id,
            'perm_read': True,
        })
        cls.project = env['realestate.project'].create({
            'name': 'Lifecycle findings', 'code': 'CLF1',
            'company_id': env.company.id})
        cls.product = env['product.product'].create({
            'name': 'Lifecycle rebar', 'type': 'consu', 'purchase_ok': True,
            'uom_id': env.ref('uom.product_uom_unit').id})

        def user(login, *roles):
            return new_test_user(
                env, login=login, email='%s@example.com' % login,
                groups=','.join(['base.group_user']
                                + [ROLE + role for role in roles]))
        cls.requester = user('clf_requester', 'requester')
        cls.buyer = user('clf_buyer', 'buyer')
        cls.approver = user('clf_approver', 'approver')
        cls.manager = user('clf_manager', 'manager')
        # The lifecycle run's workaround for the approver's missing access.
        # Tests about something other than that access use this user, so
        # that each test fails for its own reason only.
        cls.approver_buyer = user('clf_approver_buyer', 'approver', 'buyer')
        env.company.procurement_allow_self_approval = False

    def _request(self, user=None, qty=10, unit=100.0):
        user = user or self.requester
        return self.Request.with_user(user).create({
            'project_id': self.project.id,
            'requested_by_id': user.id,
            'line_ids': [(0, 0, {'product_id': self.product.id, 'qty': qty,
                                 'uom_id': self.product.uom_id.id,
                                 'estimated_unit_cost': unit})],
        })

    def _rule(self, name, group, sequence=10, approver=None):
        return self.Rule.create({
            'name': name, 'sequence': sequence, 'min_amount': 0.0,
            'group_id': self.env.ref(ROLE + group).id,
            'approver_user_id': approver.id if approver else False,
        })

    # -- item 6: the requester ---------------------------------------------
    def test_requester_opens_and_saves_their_requisition_form(self):
        self._rule('Every requisition', 'approver')
        request = self._request()
        request.action_submit()
        request.with_user(self.approver_buyer).action_approve()
        self.assertEqual(request.state, 'approved')
        self.assertTrue(request.sudo().reservation_ids,
                        "the fixture must hold a reservation to prove the "
                        "form still opens with one")
        form = Form(request.with_user(self.requester))
        form.justification = 'Raft pour'
        form.save()
        self.assertEqual(request.justification, 'Raft pour')

    def test_requester_opens_the_budget_exception_wizard(self):
        request = self._request()
        request.action_submit()
        action = request.with_user(self.requester
                                   ).action_request_budget_exception()
        Wizard = self.env[action['res_model']].with_user(
            self.requester).with_context(action['context'])
        form = Form(Wizard)
        form.reason = 'The pour cannot wait for the budget transfer.'
        wizard = form.save()
        result = wizard.action_request()
        exception = self.env[result['res_model']].browse(result['res_id'])
        self.assertEqual(exception.request_id, request)
        self.assertEqual(exception.state, 'requested')

    # -- item 6: the buyer -------------------------------------------------
    def test_buyer_opens_the_budget_exception_wizard(self):
        request = self._request()
        request.action_submit()
        action = request.with_user(self.buyer).action_request_budget_exception()
        form = Form(self.env[action['res_model']].with_user(
            self.buyer).with_context(action['context']))
        form.reason = 'Sourcing came back above the estimate.'
        wizard = form.save()
        result = wizard.action_request()
        exception = self.env[result['res_model']].browse(result['res_id'])
        self.assertEqual(exception.request_id, request)

    # -- item 6: the approver ----------------------------------------------
    def test_approver_alone_approves_a_submitted_requisition(self):
        self._rule('Every requisition', 'approver')
        request = self._request()
        request.action_submit()
        # The approver reads the requisition as the screen does, steps and
        # all, and decides from the header.
        Form(request.with_user(self.approver))
        request.with_user(self.approver).action_approve()
        self.assertEqual(request.state, 'approved')
        self.assertEqual(request.approval_step_ids.approver_id, self.approver)

    def test_approver_alone_approves_from_the_step(self):
        self._rule('Every requisition', 'approver')
        request = self._request()
        request.action_submit()
        request.approval_step_ids.with_user(self.approver).action_approve()
        self.assertEqual(request.state, 'approved')

    # -- item 8: header Approve on a multi-step requisition ----------------
    def test_header_approve_takes_only_the_steps_the_user_may_decide(self):
        self._rule('Every requisition — approver', 'approver', sequence=10)
        self._rule('Named manager', 'manager', sequence=20,
                   approver=self.manager)
        request = self._request()
        request.action_submit()
        first, second = request.approval_step_ids.sorted('sequence')

        request.with_user(self.approver_buyer).action_approve()
        self.assertEqual(first.decision, 'approved')
        self.assertEqual(second.decision, 'pending')
        self.assertEqual(request.state, 'submitted')

        # Nothing left that the approver may decide: that is a refusal, not
        # a silent no-op.
        with self.assertRaises(UserError):
            request.with_user(self.approver_buyer).action_approve()

        request.with_user(self.manager).action_approve()
        self.assertEqual(second.decision, 'approved')
        self.assertEqual(request.state, 'approved')

    # -- UX: a rejected requisition can be resubmitted from the form -------
    def test_submit_is_offered_on_a_rejected_requisition(self):
        self._rule('Every requisition', 'approver')
        request = self._request()
        request.action_submit()
        step = request.approval_step_ids
        step.sudo().comment = 'Wrong grade'
        step.with_user(self.approver_buyer).action_reject()
        self.assertEqual(request.state, 'rejected')

        arch = self.Request.with_user(self.requester).get_view(
            view_type='form')['arch']
        button = etree.fromstring(arch).xpath(
            "//header/button[@name='action_submit']")[0]
        invisible = button.get('invisible')
        self.assertFalse(eval(invisible, {'state': request.state}),  # noqa: S307
                         "Submit is hidden on a rejected requisition "
                         "(%s) although the model accepts it" % invisible)
        request.with_user(self.requester).action_submit()
        self.assertEqual(request.state, 'submitted')
