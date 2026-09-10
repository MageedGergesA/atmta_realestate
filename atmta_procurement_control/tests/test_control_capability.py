# -*- coding: utf-8 -*-
"""What Control adds, and the direction it adds it in.

The reservation arithmetic against a real Construction budget is exercised by
the M3 suites in `real_estate_procurement`, where the fixtures that build a
baselined budget live. Control has no Construction dependency and should not
grow one for the sake of a test.

What belongs here is the claim Wave 6 makes: that this module is additive.
Request is installed and working before this module loads; afterwards the same
requisition carries the control half, the fields are owned here, the columns
are still on the requisition's own table, and the seams Request left open now
do something.
"""
from odoo.tests import TransactionCase, tagged

CONTROL_FIELDS = (
    'reservation_ids', 'approval_step_ids', 'current_step_id',
    'reserved_amount', 'converted_amount', 'reservation_status',
    'all_approvals_done', 'waiting_since', 'days_waiting',
    'control_status', 'control_note', 'approval_control_status',
)
STORED_ON_REQUEST = (
    'reserved_amount', 'converted_amount', 'reservation_status',
    'all_approvals_done', 'waiting_since', 'days_waiting',
    'current_step_id', 'approval_control_status',
)


@tagged('post_install', '-at_install', 'atmta_control')
class TestControlCapability(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Request = cls.env['realestate.material.request']
        cls.Line = cls.env['realestate.material.request.line']
        cls.Reservation = cls.env['realestate.procurement.reservation']

    # -- the models this module owns ---------------------------------------
    def test_it_owns_ten_models(self):
        for name in ('realestate.procurement.approval.rule',
                     'realestate.procurement.approval.step',
                     'realestate.procurement.budget.exception',
                     'realestate.procurement.control',
                     'realestate.procurement.control.exception',
                     'realestate.procurement.purchase.exception',
                     'realestate.procurement.reservation',
                     'realestate.procurement.reservation.conversion',
                     'realestate.procurement.reservation.release'):
            self.assertIn(name, self.env)
            self.assertEqual(self.env[name]._original_module,
                             'atmta_procurement_control',
                             "%s is declared by another module." % name)

    def test_it_declares_no_request_model(self):
        """Control extends the requisition; it must never declare it."""
        for name in ('realestate.material.request',
                     'realestate.material.request.line'):
            self.assertEqual(self.env[name]._original_module,
                             'atmta_procurement_request')

    def test_it_depends_on_request_and_request_does_not_depend_on_it(self):
        Module = self.env['ir.module.module']
        control = Module.search([('name', '=', 'atmta_procurement_control')])
        request = Module.search([('name', '=', 'atmta_procurement_request')])
        self.assertIn('atmta_procurement_request',
                      control.dependencies_id.mapped('name'))
        self.assertNotIn('atmta_procurement_control',
                         request.dependencies_id.mapped('name'),
                         "AD-008: the cycle is back.")

    # -- additive, in the right direction ----------------------------------
    def test_control_puts_the_control_half_back_on_the_requisition(self):
        for name in CONTROL_FIELDS:
            self.assertIn(name, self.Request._fields,
                          "Control did not add %s back." % name)
        for name in ('reservation_ids', 'reserved_amount'):
            self.assertIn(name, self.Line._fields)

    def test_the_control_fields_are_owned_here(self):
        self.env.cr.execute("""
            SELECT f.name, d.module
              FROM ir_model_fields f
              JOIN ir_model_data d
                ON d.model = 'ir.model.fields' AND d.res_id = f.id
             WHERE f.model = 'realestate.material.request'
               AND f.name = ANY(%s)
        """, (list(CONTROL_FIELDS),))
        owners = dict(self.env.cr.fetchall())
        self.assertEqual(len(owners), len(CONTROL_FIELDS))
        for name, module in owners.items():
            self.assertEqual(module, 'atmta_procurement_control',
                             "%s is owned by %s" % (name, module))

    def test_the_stored_columns_stay_on_the_requisition_table(self):
        """Moving a field's owner must not move, drop or recreate its column."""
        self.env.cr.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name = 'realestate_material_request'
               AND column_name = ANY(%s)
        """, (list(STORED_ON_REQUEST),))
        found = {r[0] for r in self.env.cr.fetchall()}
        self.assertEqual(found, set(STORED_ON_REQUEST))

    # -- the seams now do something ----------------------------------------
    def test_the_seams_are_filled(self):
        project = self.env['realestate.project'].create({
            'name': 'Control seams', 'code': 'CTS1',
            'company_id': self.env.company.id})
        product = self.env['product.product'].create({
            'name': 'Control seam item', 'type': 'consu', 'purchase_ok': True,
            'uom_id': self.env.ref('uom.product_uom_unit').id})
        req = self.Request.create({'project_id': project.id,
                                   'requested_by_id': self.env.user.id})
        self.Line.create({'request_id': req.id, 'product_id': product.id,
                          'qty': 4, 'uom_id': product.uom_id.id,
                          'estimated_unit_cost': 250.0})
        req.invalidate_recordset()
        req.action_submit()
        # `_on_submitted` is Control's: it is what records the position the
        # approvers are answering.
        self.assertEqual(req.state, 'submitted')
        self.assertTrue('approval_control_status' in req._fields)
        self.assertIsInstance(req._control_findings(), list)
        self.assertFalse(req._has_approval_snapshot(),
                         "no rule matches, so there is no snapshot")

    def test_the_reservation_sequence_kept_its_identity(self):
        seq = self.env['ir.sequence'].search(
            [('code', '=', 'realestate.procurement.reservation')])
        self.assertEqual(len(seq), 1,
                         "a second sequence for the same code hands out "
                         "duplicate reservation numbers")
        self.assertEqual(seq.prefix, 'RES/%(year)s/')

    def test_the_expiry_job_is_not_duplicated(self):
        self.env.cr.execute("""
            SELECT count(*) FROM ir_model_data
             WHERE model = 'ir.cron' AND name = 'cron_expire_reservations'
        """)
        self.assertEqual(self.env.cr.fetchone()[0], 1,
                         "a reservation must not be able to expire twice")
