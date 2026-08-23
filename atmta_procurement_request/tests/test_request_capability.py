# -*- coding: utf-8 -*-
"""What this module owns, tested without the module that controls it.

The integration suites for the requisition lifecycle live in
`real_estate_procurement`, because their fixtures build Construction budgets and
this module has no business depending on Construction. What is tested here is
the thing Wave 6 actually claims: that demand is a capability in its own right,
that it carries no knowledge of the control system, and that the seams it
leaves open do nothing at all until something fills them.

A test that only passes because Control happens to be installed would prove the
opposite of the point, so every assertion below is about absence as much as
presence.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

CONTROL_FIELDS = (
    'reservation_ids', 'approval_step_ids', 'current_step_id',
    'reserved_amount', 'converted_amount', 'reservation_status',
    'all_approvals_done', 'waiting_since', 'days_waiting',
    'control_status', 'control_note', 'approval_control_status',
)
SEAMS = ('_control_findings', '_release_reservations',
         '_cancel_pending_approvals', '_on_submitted',
         '_has_approval_snapshot')


@tagged('post_install', '-at_install', 'atmta_request')
class TestRequestCapability(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Request = cls.env['realestate.material.request']
        cls.Line = cls.env['realestate.material.request.line']
        cls.control_installed = cls.env['ir.module.module'].search_count([
            ('name', '=', 'atmta_procurement_control'),
            ('state', '=', 'installed')])
        cls.project = cls.env['realestate.project'].create({
            'name': 'Request capability', 'code': 'RQC1',
            'company_id': cls.env.company.id})
        cls.product = cls.env['product.product'].create({
            'name': 'Request capability item', 'type': 'consu',
            'purchase_ok': True,
            'uom_id': cls.env.ref('uom.product_uom_unit').id})

    def _request(self, qty=10, unit=100.0):
        req = self.Request.create({'project_id': self.project.id,
                                   'requested_by_id': self.env.user.id})
        self.Line.create({'request_id': req.id, 'product_id': self.product.id,
                          'qty': qty, 'uom_id': self.product.uom_id.id,
                          'estimated_unit_cost': unit})
        req.invalidate_recordset()
        return req

    # -- the models this module owns ---------------------------------------
    def test_it_owns_seven_models(self):
        for name in ('realestate.material.request',
                     'realestate.material.request.line',
                     'realestate.material.request.revision',
                     'realestate.material.request.revise',
                     'realestate.material.request.rfq',
                     'realestate.procurement.plan',
                     'realestate.procurement.plan.line'):
            self.assertIn(name, self.env,
                          "%s is not loaded — Request does not own it." % name)
            self.assertEqual(
                self.env[name]._original_module, 'atmta_procurement_request',
                "%s is declared by another module." % name)

    # -- and what it must not know about -----------------------------------
    def test_no_request_field_points_into_control(self):
        """The whole of AD-008 in one assertion.

        Skipped rather than faked when Control is installed: with the
        extension loaded these fields exist by design, and asserting they do
        not would be asserting the wrong thing.
        """
        if self.control_installed:
            self.skipTest("Control is installed; see the standalone install "
                          "matrix for the isolated run.")
        for name in CONTROL_FIELDS:
            self.assertNotIn(
                name, self.Request._fields,
                "%s is a control field and must not be declared by Request."
                % name)
        for name in ('reservation_ids', 'reserved_amount'):
            self.assertNotIn(name, self.Line._fields)

    def test_the_seams_exist(self):
        for hook in SEAMS:
            self.assertTrue(hasattr(self.Request, hook),
                            "missing seam %s" % hook)
        self.assertTrue(hasattr(self.Line, '_check_unlink_allowed'))

    def test_the_seams_are_inert_without_control(self):
        if self.control_installed:
            self.skipTest("Control is installed; the seams are filled.")
        req = self._request()
        self.assertEqual(req._control_findings(), [])
        self.assertTrue(req._release_reservations('nothing to release'))
        self.assertTrue(req._cancel_pending_approvals())
        self.assertFalse(req._has_approval_snapshot())

    # -- the lifecycle it can run on its own -------------------------------
    def test_a_requisition_can_be_raised_and_submitted(self):
        req = self._request()
        self.assertEqual(req.state, 'draft')
        self.assertTrue(req.name.startswith('MR-'),
                        "the sequence moved with the model")
        self.assertEqual(req.line_ids.estimated_cost, 1000.0)
        req.action_submit()
        self.assertEqual(req.state, 'submitted')

    def test_submitting_with_no_lines_is_refused(self):
        req = self.Request.create({'project_id': self.project.id,
                                   'requested_by_id': self.env.user.id})
        with self.assertRaises(UserError):
            req.action_submit()

    def test_a_revision_keeps_the_basis_and_reopens_the_demand(self):
        req = self._request()
        req.action_submit()
        snapshot = req.action_revise('The basis changed.')
        self.assertEqual(req.state, 'draft')
        self.assertEqual(req.revision, 1)
        self.assertEqual(snapshot.request_id, req)

    def test_a_revision_without_a_reason_is_refused(self):
        req = self._request()
        req.action_submit()
        with self.assertRaises(UserError):
            req.action_revise('   ')

    def test_cancelling_is_possible_without_a_control_system(self):
        req = self._request()
        req.action_submit()
        req.action_cancel()
        self.assertEqual(req.state, 'cancelled')

    def test_a_line_can_be_deleted_when_nothing_holds_it(self):
        if self.control_installed:
            self.skipTest("Control decides what is held; tested there.")
        req = self._request()
        req.line_ids.unlink()
        self.assertFalse(req.line_ids)

    # -- data quality is a Request feature, not a control readout ----------
    def test_data_quality_reports_request_problems_on_its_own(self):
        """A line whose amount nobody knows. Unknown is not zero, and the
        warning list is a Request feature that must work with no control
        system to consult."""
        req = self.Request.create({'project_id': self.project.id,
                                   'requested_by_id': self.env.user.id})
        self.Line.create({'request_id': req.id, 'product_id': self.product.id,
                          'qty': 3, 'uom_id': self.product.uom_id.id})
        req.invalidate_recordset()
        self.assertFalse(req.line_ids.estimate_is_known)
        self.assertTrue(req.has_data_quality_warning)
        self.assertIn('no estimate', req.data_quality_warnings)

    def test_data_quality_never_reports_a_control_finding_on_its_own(self):
        """Whatever it says, it cannot be saying anything about reservations:
        with no control system installed there is nothing to report."""
        if self.control_installed:
            self.skipTest("Control is installed; control findings are real.")
        req = self._request()
        req.invalidate_recordset()
        text = (req.data_quality_warnings or '').lower()
        for word in ('reservation', 'reserved', 'capacity'):
            self.assertNotIn(word, text)
