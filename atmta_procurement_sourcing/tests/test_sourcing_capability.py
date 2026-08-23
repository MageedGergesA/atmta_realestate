# -*- coding: utf-8 -*-
"""What this module owns, and the one thing it must never do.

The M5 integration suites stay in `real_estate_procurement`: every one of them
calls `_budget()`, and the fixture chain behind it builds Construction budgets,
cost codes and WBS nodes. Sourcing has no business depending on Construction to
run its own tests, so what is proved here is what Sourcing owns and can prove
alone — above all that **sourcing moves no money**.

A bid is an offer. Publishing a tender, inviting a vendor, raising an RFQ and
receiving a price change no financial position, and the assertions below are
written so that they would fail if any of that stopped being true.
"""
from odoo import fields
from odoo.tests import TransactionCase, tagged

SOURCING_MODELS = (
    'realestate.procurement.sourcing.event',
    'realestate.procurement.sourcing.version',
    'realestate.procurement.sourcing.line',
    'realestate.procurement.sourcing.demand.allocation',
    'realestate.procurement.sourcing.invitation',
    'realestate.procurement.sourcing.acknowledgement',
    'realestate.procurement.bid.response',
    'realestate.procurement.bid.response.line',
    'realestate.procurement.sourcing.clarification',
    'realestate.procurement.sourcing.audit',
)


@tagged('post_install', '-at_install', 'atmta_sourcing')
class TestSourcingCapability(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Event = cls.env['realestate.procurement.sourcing.event']
        cls.Request = cls.env['realestate.material.request']
        cls.Line = cls.env['realestate.material.request.line']
        cls.company = cls.env.company
        cls.project = cls.env['realestate.project'].create({
            'name': 'Sourcing capability', 'code': 'SCP1',
            'company_id': cls.company.id})
        cls.product = cls.env['product.product'].create({
            'name': 'Sourcing capability item', 'type': 'consu',
            'purchase_ok': True,
            'uom_id': cls.env.ref('uom.product_uom_unit').id})
        cls.vendor = cls.env['res.partner'].create(
            {'name': 'Sourcing capability vendor', 'supplier_rank': 1})
        day = fields.Date.add(fields.Date.context_today(cls.env['res.partner']),
                              days=14)
        cls.close_at = fields.Datetime.to_datetime('%s 12:00:00' % day)

    def _event(self, title):
        return self.Event.create({
            'title': title, 'company_id': self.company.id,
            'project_id': self.project.id,
            'sourcing_method': 'competitive_tender',
            'close_datetime': self.close_at})

    def _request(self, qty=10, unit=1000.0, approve=False):
        req = self.Request.create({'project_id': self.project.id,
                                   'requested_by_id': self.env.user.id})
        self.Line.create({'request_id': req.id, 'product_id': self.product.id,
                          'qty': qty, 'uom_id': self.product.uom_id.id,
                          'estimated_unit_cost': unit})
        req.invalidate_recordset()
        if approve:
            # Only approved demand may authorise a tender, and approving is
            # Control's. Sourcing does not depend on Control, so a site that
            # installed Sourcing alone genuinely cannot run this flow — which
            # is worth saying out loud rather than working around.
            if not hasattr(req, 'action_approve'):
                self.skipTest("atmta_procurement_control is not installed, so "
                              "no demand can be approved to tender.")
            self.company.write({'procurement_allow_self_approval': True,
                                'procurement_self_approval_limit': 1e9})
            # Approving is gated on the approver role. Control still names the
            # legacy group for that check — a Wave 6 leftover recorded in
            # 39_GAPS.md — so grant whichever of the two exists here.
            for xmlid in ('real_estate_procurement.group_procurement_approver',
                          'atmta_roles.group_procurement_approver'):
                grp = self.env.ref(xmlid, raise_if_not_found=False)
                if grp:
                    self.env.user.groups_id |= grp
            req.action_submit()
            if req.state == 'submitted':
                req.action_approve()
            req.invalidate_recordset()
        return req

    # -- ownership ---------------------------------------------------------
    def test_it_owns_ten_models(self):
        for name in SOURCING_MODELS:
            self.assertIn(name, self.env)
            self.assertEqual(self.env[name]._original_module,
                             'atmta_procurement_sourcing',
                             "%s is declared by another module." % name)

    def test_it_declares_no_request_model(self):
        """Sourcing extends the requisition; it must never declare it."""
        for name in ('realestate.material.request',
                     'realestate.material.request.line'):
            self.assertEqual(self.env[name]._original_module,
                             'atmta_procurement_request')

    def test_the_dependency_points_downward_only(self):
        Module = self.env['ir.module.module']
        sourcing = Module.search([('name', '=', 'atmta_procurement_sourcing')])
        request = Module.search([('name', '=', 'atmta_procurement_request')])
        deps = sourcing.dependencies_id.mapped('name')
        self.assertIn('atmta_procurement_request', deps)
        self.assertNotIn('atmta_procurement_sourcing',
                         request.dependencies_id.mapped('name'),
                         "Request must not depend on Sourcing.")
        for upper in ('atmta_procurement_evaluation', 'atmta_procurement_award'):
            self.assertNotIn(upper, deps,
                             "Sourcing must not depend upward on %s." % upper)

    def test_request_no_longer_declares_the_vendor_trade(self):
        """AD-009: the trade a line belongs to is solicitation, so Sourcing
        contributes it and Request installs without vendor governance."""
        self.env.cr.execute("""
            SELECT d.module FROM ir_model_fields f
              JOIN ir_model_data d
                ON d.model = 'ir.model.fields' AND d.res_id = f.id
             WHERE f.model = 'realestate.material.request.line'
               AND f.name = 'vendor_category_id'
        """)
        row = self.env.cr.fetchone()
        self.assertTrue(row, "vendor_category_id is missing entirely")
        self.assertEqual(row[0], 'atmta_procurement_sourcing')

    # -- native RFQ ownership ---------------------------------------------
    def test_there_is_no_second_rfq_model(self):
        """Requests for quotation are native Odoo purchase orders.

        A duplicate ATMTA RFQ model would fork the operational truth away from
        the ledger the rest of Odoo reads.
        """
        self.assertNotIn('realestate.procurement.rfq', self.env)
        self.assertIn('purchase.order', self.env)
        self.assertEqual(self.env['purchase.order']._original_module, 'purchase')
        self.assertEqual(self.env['purchase.order.line']._original_module,
                         'purchase')

    # -- the invariant this capability exists to keep ----------------------
    def test_creating_a_tender_moves_no_money(self):
        req = self._request(qty=100, unit=1000.0, approve=True)
        before_po = self.env['purchase.order'].search_count(
            [('state', 'in', ('purchase', 'done'))])
        event = self._event('No-money tender')
        event.action_allocate_request(req)
        event.action_invite_vendor(self.vendor)
        self.assertEqual(
            self.env['purchase.order'].search_count(
                [('state', 'in', ('purchase', 'done'))]),
            before_po,
            "inviting a vendor confirmed a purchase order")
        for order in event.invitation_ids.mapped('purchase_order_id'):
            self.assertIn(order.state, ('draft', 'sent'),
                          "a tender RFQ must not arrive confirmed")

    def test_an_authorised_amount_is_a_ceiling_not_a_holding(self):
        """The event may total the demand it carries; that is a comparison
        figure, not money the event holds."""
        req = self._request(qty=50, unit=2000.0, approve=True)
        # What Control reserved at approval, before any tender exists.
        Res = self.env.get('realestate.procurement.reservation')
        before = sum(Res.search([('request_id', '=', req.id)]).mapped(
            'amount_active')) if Res is not None else 0.0

        event = self._event('Ceiling tender')
        event.action_allocate_request(req)
        event.invalidate_recordset()
        self.assertEqual(event.authorised_amount, 100_000.0,
                         "the event should total the demand it carries")

        # The tender may describe that demand; it may not resize the holding.
        if Res is not None:
            after = sum(Res.search([('request_id', '=', req.id)]).mapped(
                'amount_active'))
            self.assertEqual(
                after, before,
                "allocating demand to a tender changed the reservation — "
                "sourcing moved money, which is Control's job alone")
            self.assertGreater(before, 0.0,
                               "the fixture reserved nothing, so this "
                               "comparison could not have failed")

    # -- versioning --------------------------------------------------------
    def test_a_published_tender_starts_at_revision_zero(self):
        req = self._request(approve=True)
        event = self._event('Version tender')
        event.action_allocate_request(req)
        event.action_invite_vendor(self.vendor)
        event.action_publish()
        self.assertTrue(event.current_version_id)
        self.assertEqual(event.current_version_id.revision, 0)

    def test_an_addendum_raises_the_revision(self):
        req = self._request(approve=True)
        event = self._event('Addendum tender')
        event.action_allocate_request(req)
        event.action_invite_vendor(self.vendor)
        event.action_publish()
        event.action_issue_addendum(reason='Scope clarified')
        self.assertEqual(event.current_version_id.revision, 1)

    # -- the sequence came with the model ----------------------------------
    def test_the_sourcing_sequences_are_not_duplicated(self):
        for code in ('realestate.procurement.sourcing.event',
                     'realestate.procurement.bid.response',
                     'realestate.procurement.sourcing.clarification'):
            self.assertEqual(
                self.env['ir.sequence'].search_count([('code', '=', code)]), 1,
                "a second sequence for %s hands out duplicate numbers" % code)
