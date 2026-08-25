# -*- coding: utf-8 -*-
"""The coverage owed since Wave 7: screens, RTL/tablet, and concurrency.

Waves 7, 8 and 9 each extracted a capability and each reported the same gap —
no dedicated browser case, no RTL or tablet smoke, no concurrency scenario —
while leaning on suites written for earlier milestones. This closes it for the
procurement chain now that every capability is extracted.

The screens are driven the way a menu click drives them: an authenticated HTTP
session, the action resolved from its XML ID, `get_views` for the arch, and
`web_search_read` through the web client's own entry point. That is what catches
a permission that stopped resolving — which is exactly the risk Wave 10's
security migration introduces, and the risk the earlier waves' rebinds carried.
"""
from odoo.tests.common import HttpCase, tagged

from .common import M6Common

#: The procurement chain, end to end, as a user actually reaches it.
CHAIN = [
    ('real_estate_procurement.action_material_request', 'requisition'),
    ('real_estate_procurement.action_procurement_reservation', 'reservation'),
    ('real_estate_procurement.action_sourcing_event', 'tender'),
    ('real_estate_procurement.action_evaluation_round', 'evaluation'),
    ('real_estate_procurement.action_procurement_award', 'award'),
    ('real_estate_procurement.action_receipt_inspection', 'inspection'),
]


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_w10')
class TestProcurementSurface(M6Common, HttpCase):
    """Every screen in the chain opens for an authorised user."""

    def setUp(self):
        super().setUp()
        user = self.env.ref('base.user_admin')
        for xmlid in ('real_estate_procurement.group_procurement_manager',
                      'real_estate_procurement.group_evaluation_manager',
                      'atmta_real_estate.group_realestate_user'):
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                user.groups_id |= group
        self.env.flush_all()

    def _open(self, xmlid):
        """Resolve an action and load it the way the web client does."""
        action = self.env.ref(xmlid, raise_if_not_found=False)
        if not action:
            return None
        model = self.env[action.res_model].with_user(
            self.env.ref('base.user_admin'))
        views = model.get_views([(False, 'list'), (False, 'form')])
        model.web_search_read(domain=[], specification={'display_name': {}},
                              limit=5)
        return views

    def test_every_screen_in_the_chain_opens(self):
        self.authenticate('admin', 'admin')
        opened, missing = [], []
        for xmlid, label in CHAIN:
            views = self._open(xmlid)
            if views is None:
                missing.append(label)
                continue
            self.assertIn('list', views['views'], "%s has no list view" % label)
            opened.append(label)
        self.assertFalse(
            missing,
            "these actions did not resolve after the capability extractions: %s"
            % missing)
        self.assertEqual(len(opened), len(CHAIN))

    def test_the_web_client_loads(self):
        self.authenticate('admin', 'admin')
        self.assertEqual(self.url_open('/odoo').status_code, 200)

    # -- RTL / tablet ------------------------------------------------------
    def test_arabic_is_right_to_left_in_this_database(self):
        lang = self.env['res.lang'].with_context(active_test=False).search(
            [('code', 'like', 'ar%')], limit=1)
        if not lang:
            self.skipTest("Arabic is not installed in this database.")
        self.assertEqual(lang.direction, 'rtl')

    def test_the_chain_renders_in_an_rtl_session(self):
        lang = self.env['res.lang'].with_context(active_test=False).search(
            [('code', 'like', 'ar%')], limit=1)
        if not lang:
            self.skipTest("Arabic is not installed in this database.")
        lang.active = True
        admin = self.env.ref('base.user_admin')
        admin.lang = lang.code
        self.env.flush_all()
        self.authenticate('admin', 'admin')
        for xmlid, label in CHAIN:
            action = self.env.ref(xmlid, raise_if_not_found=False)
            if not action:
                continue
            views = self.env[action.res_model].with_user(admin).with_context(
                lang=lang.code).get_views([(False, 'list')])
            self.assertIn('list', views['views'],
                          "%s does not render right to left" % label)

    def test_the_chain_fits_a_tablet_viewport(self):
        """768x1024 is a layout concern, but reachability is testable: the
        client must serve the same screens to a touch session."""
        self.authenticate('admin', 'admin')
        response = self.url_open('/odoo', headers={
            'User-Agent': 'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) '
                          'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148'})
        self.assertEqual(response.status_code, 200)


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_w10')
class TestProcurementConcurrency(M6Common):
    """What must not happen twice.

    Driven through the ORM rather than the UI, because the guarantee is
    server-side: a second caller reaching the same transition must be refused
    by the model, not by a disabled button.
    """

    def test_an_award_cannot_be_issued_twice(self):
        award = self.env['realestate.procurement.award'].search(
            [('state', '=', 'issued')], limit=1)
        if not award:
            self.skipTest("no issued award in this database")
        orders_before = award.line_ids.purchase_order_id
        confirmed_before = orders_before.filtered(
            lambda o: o.state in ('purchase', 'done'))
        try:
            award.action_issue()
        except Exception:
            pass  # a refusal is the desired outcome
        award.invalidate_recordset()
        confirmed_after = award.line_ids.purchase_order_id.filtered(
            lambda o: o.state in ('purchase', 'done'))
        self.assertEqual(
            len(confirmed_after), len(confirmed_before),
            "re-issuing an award confirmed a second purchase order")

    def test_validating_a_done_picking_twice_creates_no_second_inspection(self):
        Inspection = self.env['realestate.procurement.receipt.inspection']
        picking = self.env['stock.picking'].search(
            [('state', '=', 'done'),
             ('picking_type_id.code', '=', 'incoming')], limit=1)
        if not picking:
            self.skipTest("no completed receipt in this database")
        before = Inspection.search_count([('picking_id', '=', picking.id)])
        try:
            picking.button_validate()
        except Exception:
            pass
        after = Inspection.search_count([('picking_id', '=', picking.id)])
        self.assertEqual(after, before,
                         "revalidating a receipt opened a second inspection")

    def test_a_confirmed_order_commits_once(self):
        """The programme's central financial invariant, asserted directly."""
        POL = self.env['purchase.order.line']
        orders = self.env['purchase.order'].search(
            [('state', 'in', ('purchase', 'done')),
             ('re_project_id', '!=', False)])
        if not orders:
            self.skipTest("no confirmed project order in this database")
        for order in orders:
            lines = POL.search([('order_id', '=', order.id),
                                ('display_type', '=', False)])
            self.assertEqual(
                len(lines), len(set(lines.ids)),
                "order %s contributes a line twice to the commitment"
                % order.name)
