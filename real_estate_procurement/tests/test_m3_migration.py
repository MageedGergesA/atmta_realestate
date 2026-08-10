# -*- coding: utf-8 -*-
"""M3AA/M3AB — what an upgrade does to demand that already exists.

The rule the whole migration is built on:

```
    HISTORIC CONFIRMED PURCHASE ORDERS ARE ALREADY COMMITMENT.
    THEY NEVER RECEIVE A RESERVATION.
```

A reservation created on top of an existing commitment for the same money is
the 6,000,000-for-3,000,000 error, made during an upgrade where nobody is
watching. Everything else follows from that: classify, report, and let a
manager switch control on deliberately once they have read the list.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import M3Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM3Migration(M3Common):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        self.Reservation = self.env['realestate.procurement.reservation']

    def _legacy_ordered_request(self):
        """Demand that already produced a confirmed order, as M2 left it."""
        request = self._demand(2_000.0)
        request.action_create_rfqs(vendors=self.vendor)
        self._confirm(request.purchase_order_ids)
        # Wipe the M3 record entirely, so the request looks exactly like one
        # migrated from a database that never had reservations.
        request.reservation_ids.conversion_ids.unlink()
        request.reservation_ids.with_context(
            re_reservation_engine=True).write({'state': 'draft'})
        request.reservation_ids.unlink()
        request.invalidate_recordset()
        return request

    # -- TEST Q --------------------------------------------------------
    def test_q_a_legacy_confirmed_order_keeps_its_commitment_and_gains_no_reservation(self):
        request = self._legacy_ordered_request()
        self.assertEqual(self._commitment(self.project), 2_000_000.0)
        self.assertFalse(request.reservation_ids)

        request._classify_procurement_governance()
        self.assertEqual(request.governance_status, 'legacy_confirmed_po')

        created = self.Request.action_activate_procurement_reservations(
            project=self.project)

        self.assertFalse(created.filtered(
            lambda r: r.request_id == request),
            "Already-committed demand must never be reserved on top.")
        self.assertEqual(self._commitment(self.project), 2_000_000.0)
        self.assertEqual(self._reserved(self.project), 0.0)

    def test_classification_names_what_it_can_and_guesses_nothing(self):
        ordered = self._legacy_ordered_request()

        sourcing = self._demand(1_000.0)
        sourcing.action_create_rfqs(vendors=self.vendor)
        sourcing.reservation_ids.with_context(
            re_reservation_engine=True).write({'state': 'draft'})
        sourcing.reservation_ids.unlink()

        approved = self._demand(500.0)
        approved.reservation_ids.with_context(
            re_reservation_engine=True).write({'state': 'draft'})
        approved.reservation_ids.unlink()

        draft = self._request(self.project, [(self.product, 1.0)])

        requests = ordered | sourcing | approved | draft
        requests._classify_procurement_governance()

        self.assertEqual(ordered.governance_status, 'legacy_confirmed_po')
        self.assertEqual(sourcing.governance_status, 'sourcing_draft_rfq')
        self.assertEqual(approved.governance_status, 'approved_unordered')
        self.assertEqual(draft.governance_status, 'not_applicable')

    def test_history_is_audited_and_never_rewritten(self):
        """Urgent-bypassed and self-approved records are evidence.

        Both were legitimate under the rules in force when they happened.
        Un-approving them now to make the new version look tidy would be
        rewriting commercial history.
        """
        legacy = self._request(self.project, [(self.product, 1.0)],
                               priority='1')
        legacy.with_context(re_procurement_revision=True).write({
            'state': 'approved',
            'approved_by_id': self.env.user.id,
            'approval_date': self.env.cr.now(),
        })
        legacy._classify_procurement_governance()

        self.assertTrue(legacy.legacy_urgent_bypass)
        self.assertTrue(legacy.legacy_self_approved)
        self.assertEqual(legacy.state, 'approved',
                         "Still approved. The flag is a finding, not a "
                         "reversal.")

    # -- The rollout runbook -------------------------------------------
    def test_activation_stages_reservations_before_it_moves_capacity(self):
        request = self._demand(3_000.0)
        request.reservation_ids.with_context(
            re_reservation_engine=True).write({'state': 'draft'})
        request.reservation_ids.unlink()

        created = self.Request.action_activate_procurement_reservations(
            project=self.project)

        self.assertEqual(len(created), 1)
        self.assertEqual(created.state, 'draft')
        self.assertEqual(
            self._reserved(self.project), 0.0,
            "Nothing is consumed until somebody has looked at the list.")
        self.assertEqual(self._position(self.concrete)['available'],
                         10_000_000.0)

        created.action_reserve()

        self.assertEqual(created.state, 'reserved')
        self.assertEqual(self._reserved(self.project), 3_000_000.0)
        self.assertEqual(self._position(self.concrete)['available'],
                         7_000_000.0)

    # -- TEST M3AB idempotency -----------------------------------------
    def test_running_the_migration_twice_changes_nothing(self):
        ordered = self._legacy_ordered_request()
        approved = self._demand(1_000.0)
        approved.reservation_ids.with_context(
            re_reservation_engine=True).write({'state': 'draft'})
        approved.reservation_ids.unlink()

        commitment_before = self._commitment(self.project)

        for _run in range(2):
            (ordered | approved)._classify_procurement_governance()
            self.Request.action_activate_procurement_reservations(
                project=self.project)

        self.assertEqual(len(approved.reservation_ids), 1,
                         "A second run must not stage a second reservation.")
        self.assertFalse(ordered.reservation_ids)
        self.assertEqual(self._commitment(self.project), commitment_before)
        self.assertEqual(
            self.env['realestate.procurement.control.exception'].search_count(
                [('request_id', 'in', (ordered | approved).ids)]), 0)

    def test_activation_is_a_managers_decision(self):
        buyer = self._purchase_user(
            'm3.mig.buyer.%d' % self._next(),
            'real_estate_procurement.group_procurement_user',
            'real_estate_developer.group_dev_readonly',
            'real_estate_construction.group_construction_user')
        with self.assertRaises(UserError):
            self.Request.with_user(
                buyer).action_activate_procurement_reservations()
