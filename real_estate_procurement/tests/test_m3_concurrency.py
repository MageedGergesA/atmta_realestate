# -*- coding: utf-8 -*-
"""M3D/M3AD — what stops two people spending the same money.

### The race

```
    User A                          User B
    reads: 10,000,000 available     reads: 10,000,000 available
    reserves 8,000,000              reserves 8,000,000
                    → 16,000,000 reserved against a 10,000,000 budget
```

Nobody did anything wrong and no validation was skipped. Both transactions
read a true number and then made it false. Only serialising the read and the
write together prevents it, which is what `lock_control_scopes()` does with a
PostgreSQL transaction-level advisory lock.

### What is proved here, and what is not

Odoo's test infrastructure runs a test inside a single transaction, and
`registry.enter_test_mode()` makes additional cursors share that one
connection so that uncommitted fixture data is visible to them. That means a
genuine two-connection race cannot be staged inside a test: the second
"transaction" would be the same transaction, and it would take the advisory
lock it already holds.

So this file proves the parts that can be proved deterministically, and says
plainly which part cannot:

* the advisory lock is really taken — asserted against `pg_locks`, not
  against the fact that a line of code exists;
* the lock keys are deterministic and sorted, which is what stops two
  multi-code requisitions deadlocking against each other;
* unrelated scopes take different locks and do not queue behind each other;
* the database itself, not a Python check, refuses a second active
  reservation for one requisition line;
* the sequential equivalent of the race — A reserves, then B tries — is
  refused with the right remaining figure.

The one thing not proved by execution is two operating-system processes
colliding. That is a limitation of the harness, stated rather than papered
over.
"""

from psycopg2 import IntegrityError
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import M3Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM3Concurrency(M3Common):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0),
                      (self.electrical, 5_000_000.0)])
        self.Control = self.env['realestate.procurement.control']

    def _advisory_locks_held(self):
        self.env.cr.execute("""
            SELECT count(*) FROM pg_locks
             WHERE locktype = 'advisory' AND pid = pg_backend_pid()
        """)
        return self.env.cr.fetchone()[0]

    # ------------------------------------------------------------------
    def test_the_advisory_lock_is_actually_taken(self):
        before = self._advisory_locks_held()
        self.Control.lock_control_scopes(
            [(self.company.id, self.project.id, self.concrete.id)])

        self.assertEqual(
            self._advisory_locks_held(), before + 1,
            "A lock that is not held is a comment about locking.")

    def test_approving_demand_locks_its_control_scope(self):
        before = self._advisory_locks_held()
        self._demand(1_000.0)

        self.assertGreater(self._advisory_locks_held(), before)

    def test_lock_order_does_not_depend_on_the_order_lines_were_typed(self):
        """M3D deadlock safety.

        Two requisitions spanning concrete and electrical in opposite orders
        must take the two locks in the same sequence, or they can wait for
        each other for ever.
        """
        forward = self.Control.lock_control_scopes([
            (self.company.id, self.project.id, self.concrete.id),
            (self.company.id, self.project.id, self.electrical.id),
        ])
        backward = self.Control.lock_control_scopes([
            (self.company.id, self.project.id, self.electrical.id),
            (self.company.id, self.project.id, self.concrete.id),
        ])

        self.assertEqual(forward, backward)
        self.assertEqual(forward, sorted(forward))

    def test_unrelated_scopes_are_different_locks(self):
        other_project = self._project()
        tokens = {
            self.Control._scope_token(self.company.id, self.project.id,
                                      self.concrete.id),
            self.Control._scope_token(self.company.id, self.project.id,
                                      self.electrical.id),
            self.Control._scope_token(self.company.id, other_project.id,
                                      self.concrete.id),
        }
        self.assertEqual(len(tokens), 3,
                         "Locking one project's concrete must not queue "
                         "another project behind it.")

    def test_a_duplicate_scope_is_locked_once(self):
        tokens = self.Control.lock_control_scopes([
            (self.company.id, self.project.id, self.concrete.id),
            (self.company.id, self.project.id, self.concrete.id),
        ])
        self.assertEqual(len(tokens), 1)

    # ------------------------------------------------------------------
    def test_the_database_refuses_a_second_active_reservation(self):
        """A Python check cannot see an uncommitted row. An index can.

        Written as a raw insert precisely because it bypasses every ORM
        constraint — which is what a second transaction effectively does.
        """
        request = self._demand(1_000.0)
        reservation = request.reservation_ids
        self.assertEqual(reservation.state, 'reserved')

        with self.assertRaises(IntegrityError), \
                mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            self.env.cr.execute("""
                INSERT INTO realestate_procurement_reservation
                    (name, company_id, project_id, request_id,
                     request_line_id, state, amount_reserved, amount_active,
                     amount_released, create_uid, write_uid,
                     create_date, write_date)
                VALUES ('DUPLICATE', %s, %s, %s, %s, 'reserved', 1, 1, 0,
                        %s, %s, now(), now())
            """, (self.company.id, self.project.id, request.id,
                  request.line_ids.id, self.env.uid, self.env.uid))

    def test_re_approval_does_not_reserve_twice(self):
        """Idempotent by construction, whoever calls it and however often."""
        request = self._demand(1_000.0)
        Reservation = self.env['realestate.procurement.reservation']

        Reservation.reserve_request(request)
        Reservation.reserve_request(request)

        self.assertEqual(len(request.reservation_ids), 1)
        self.assertEqual(self._reserved(self.project), 1_000_000.0)

    def test_confirming_the_same_order_twice_converts_once(self):
        request = self._demand(1_000.0)
        request.action_create_rfqs(vendors=self.vendor)
        order = request.purchase_order_ids
        self._confirm(order)
        order.button_confirm()

        reservation = request.reservation_ids
        self.assertEqual(len(reservation.conversion_ids), 1)
        self.assertEqual(reservation.amount_converted, 1_000_000.0)
        self.assertEqual(self._commitment(self.project), 1_000_000.0)

    def test_the_second_of_two_oversized_requests_is_refused(self):
        """The sequential shape of the race, with the arithmetic checked."""
        self._set_budget_policy('block')
        self._demand(8_000.0)

        with self.assertRaises(UserError) as caught:
            self._demand(8_000.0)

        message = str(caught.exception)
        self.assertIn('2,000,000', message)
        self.assertIn('6,000,000', message,
                      "It should also say how far short it is.")
        self.assertEqual(self._reserved(self.project, self.concrete),
                         8_000_000.0)
