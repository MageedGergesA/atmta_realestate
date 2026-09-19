# -*- coding: utf-8 -*-
"""Regressions for what the procurement lifecycle run found at the PO gate."""
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.atmta_procurement_evaluation.tests.common import \
    EvaluationLifecycleCommon


@tagged('post_install', '-at_install', 'atmta_procurement_purchase')
class TestPurchaseLifecycleFindings(EvaluationLifecycleCommon):

    def _approved_partial_award(self, quantity=600.0):
        round_ = self._finalised_round()
        top = round_.candidate_ids.filtered(lambda c: c.rank == 1)
        award = self.env['realestate.procurement.award'].create({
            'sourcing_event_id': round_.sourcing_event_id.id,
            'round_id': round_.id,
            'award_type': 'partial',
            'line_ids': [(0, 0, {'candidate_id': top.id})],
        })
        award.line_ids.allocation_ids.quantity = quantity
        award.action_submit()
        approver = self._user('lcf.award.approver',
                              'atmta_roles.group_procurement_manager')
        award.with_user(approver).action_approve()
        self.assertEqual(award.state, 'approved')
        return award

    # -- item 1: confirming the RFQ is not a way round issuing the award -----
    def test_confirming_an_awarded_rfq_directly_is_refused(self):
        award = self._approved_partial_award(600.0)
        order = award.line_ids.purchase_order_id
        self.assertTrue(order._award_authorisation(),
                        "the approved award should still name its order")

        with self.assertRaises(UserError):
            order.with_context(skip_alternative_check=True).button_confirm()

        self.assertIn(order.state, ('draft', 'sent'))
        self.assertEqual(order.order_line.mapped('product_qty'), [1000.0],
                         "the refused confirmation still touched the order")
        self.assertEqual(award.state, 'approved')

    def test_issuing_the_award_confirms_the_awarded_quantity(self):
        award = self._approved_partial_award(600.0)
        order = award.line_ids.purchase_order_id

        award.action_issue()

        self.assertEqual(award.state, 'issued')
        self.assertEqual(order.state, 'purchase')
        self.assertEqual(order.order_line.mapped('product_qty'), [600.0])

    def test_a_refused_issue_leaves_the_award_approved(self):
        """Issuing marks the award issued before the orders confirm, so a
        failure has to take that mark back with it."""
        award = self._approved_partial_award(600.0)
        order = award.line_ids.purchase_order_id
        event = award.sourcing_event_id
        event.sudo().write({'state': 'cancelled'})
        with self.assertRaises(UserError):
            with self.env.cr.savepoint():
                award.action_issue()
        award.invalidate_recordset()
        order.invalidate_recordset()
        self.assertEqual(award.state, 'approved')
        self.assertIn(order.state, ('draft', 'sent'))
