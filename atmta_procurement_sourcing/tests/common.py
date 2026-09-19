# -*- coding: utf-8 -*-
"""A tender taken to closed bids, without Construction.

Shared by the lifecycle-finding regressions of Sourcing and of the
capabilities above it (Evaluation, Award, Purchase), so that each of them
reproduces its finding on the same shape of tender instead of on a private
variant of it. Nothing here is a fixture for a Construction budget: the
capabilities under test own no budget and must be provable without one.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase


class SourcingLifecycleCommon(TransactionCase):

    _seq = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.uom = cls.env.ref('uom.product_uom_unit')
        # Maker/checker on spend is lifted for the fixture user only, the same
        # way the integration suites do it; tests that are about a refusal
        # use separate users.
        cls.company.write({
            'procurement_allow_self_approval': True,
            'procurement_self_approval_limit': 1e9,
        })
        for xmlid in ('atmta_roles.group_procurement_buyer',
                      'atmta_roles.group_procurement_approver',
                      'atmta_roles.group_procurement_manager',
                      'atmta_roles.group_procurement_evaluation_manager',
                      'atmta_roles.group_procurement_commercial_evaluator'):
            cls.env.user.groups_id |= cls.env.ref(xmlid)
        cls.project = cls.env['realestate.project'].create({
            'name': 'Lifecycle findings', 'code': 'LCF1',
            'company_id': cls.company.id})
        cls.product = cls.env['product.product'].create({
            'name': 'Lifecycle rebar', 'type': 'consu', 'purchase_ok': True,
            'uom_id': cls.uom.id, 'uom_po_id': cls.uom.id})
        cls.vendors = cls.env['res.partner'].create([
            {'name': 'Lifecycle Vendor %s' % k, 'supplier_rank': 1,
             'email': 'lcf%s@example.com' % k.lower()} for k in 'ABC'])

    def _require_rfqs(self):
        """Skip where no module declares the purchase-order side of a tender.

        An invitation writes `re_project_id`, `re_sourcing_event_id` and
        `re_sourcing_line_id` onto the RFQ it raises, and all three are
        declared by `atmta_procurement_purchase` — the gate that loads last.
        Sourcing alone therefore cannot raise an RFQ at all, which is worth
        saying out loud rather than working around: everything from the
        invitation onwards is proved where the gate is installed.
        """
        if not self.env['ir.module.module'].sudo().search_count(
                [('name', '=', 'atmta_procurement_purchase'),
                 ('state', '=', 'installed')]):
            self.skipTest(
                "atmta_procurement_purchase is not installed, so no request "
                "for quotation can be raised: the purchase-order fields a "
                "tender RFQ carries are declared there.")

    def _next(self):
        type(self)._seq += 1
        return type(self)._seq

    def _user(self, login, *xmlids):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'email': '%s@example.com' % login,
            'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id]
                           + [self.env.ref(x).id for x in xmlids])],
        })

    def _request(self, qty=1000, unit=100.0):
        """Approved demand for one line."""
        req = self.env['realestate.material.request'].create({
            'project_id': self.project.id,
            'requested_by_id': self.env.user.id,
        })
        self.env['realestate.material.request.line'].create({
            'request_id': req.id, 'product_id': self.product.id, 'qty': qty,
            'uom_id': self.uom.id, 'estimated_unit_cost': unit})
        req.invalidate_recordset()
        req.action_submit()
        if hasattr(req, 'action_approve'):
            if req.state == 'submitted':
                req.action_approve()
        else:
            # Approving is Control's, and Sourcing does not depend on Control.
            # Without it installed the approved state is written directly —
            # the only thing these tests need from it is that the demand is
            # approved, not how it came to be.
            req.with_context(re_procurement_revision=True).state = 'approved'
        self.assertEqual(req.state, 'approved')
        return req

    def _event(self, request=None, quantities=None, **kwargs):
        vals = {
            'title': 'Lifecycle tender %d' % self._next(),
            'company_id': self.company.id,
            'project_id': self.project.id,
            'sourcing_method': 'competitive_tender',
            'close_datetime': fields.Datetime.now() + timedelta(days=7),
        }
        vals.update(kwargs)
        event = self.env['realestate.procurement.sourcing.event'].create(vals)
        if request is not None:
            event.action_allocate_request(request, quantities=quantities)
        return event

    def _published(self, request=None, vendors=None):
        self._require_rfqs()
        event = self._event(request or self._request())
        for vendor in (vendors if vendors is not None else self.vendors):
            event.action_invite_vendor(vendor)
        event.action_publish()
        return event

    def _price(self, invitation, unit):
        invitation.purchase_order_id.order_line.write(
            {'price_unit': unit, 'taxes_id': [(5, 0, 0)]})

    def _closed_with_bids(self, prices=(90.0, 80.0, 100.0), request=None):
        event = self._published(request)
        for invitation, unit in zip(event.invitation_ids, prices):
            self._price(invitation, unit)
            invitation.action_record_bid()
        event.action_close()
        return event
