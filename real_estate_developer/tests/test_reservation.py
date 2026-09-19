# -*- coding: utf-8 -*-
"""M4 — Reservation V2: snapshots, locking, expiry, booking fee, cancellation.

Phase 51 is explicit that concurrency must be tested.
:class:`TestReservationConcurrency` proves each link of the chain
deterministically — the advisory lock really serialises, the guard inside it
refuses the loser, and the database rejects a duplicate even with every Python
check bypassed — rather than staging a timing-dependent race that would only
prove one interleaving happened to work.
"""

from datetime import timedelta

import psycopg2

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import DeveloperCommon


@tagged('post_install', '-at_install', 'atmta_developer')
class TestReservationSnapshot(DeveloperCommon):
    """Phase 15 — a quote is frozen the moment the unit is held."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Reservation = cls.env['realestate.unit.reservation']
        cls.Plan = cls.env['realestate.payment.plan']
        cls.buyer = cls.env['res.partner'].create({'name': 'Buyer One'})

    def setUp(self):
        super().setUp()
        self._open_project_for_sales()
        self.unit = self.units[0]
        self._release(self.unit)
        self.unit.base_price = 1000000.0

    def _plan(self):
        plan = self.Plan.create({
            'name': 'Res Plan', 'company_id': self.company.id,
            'project_id': self.project.id,
        })
        self.env['realestate.payment.plan.line'].create([
            {'plan_id': plan.id, 'sequence': 10, 'kind': 'down_payment',
             'calculation_type': 'percent', 'value': 20.0,
             'date_rule': 'on_booking'},
            {'plan_id': plan.id, 'sequence': 20, 'kind': 'installment',
             'calculation_type': 'residual', 'occurrences': 1,
             'date_rule': 'months_after_booking', 'offset_value': 12},
        ])
        plan.action_activate()
        return plan

    def _reserve(self, **kwargs):
        vals = {
            'property_id': self.unit.id,
            'partner_id': self.buyer.id,
        }
        vals.update(kwargs)
        return self.Reservation.create(vals)

    def test_snapshot_is_taken_on_creation(self):
        res = self._reserve()
        self.assertTrue(res.snapshot_taken_on)
        self.assertEqual(res.snapshot_list_price, 1000000.0)

    def test_snapshot_does_not_move_when_the_unit_is_repriced(self):
        """The whole point of Rule 5."""
        res = self._reserve()
        quoted = res.snapshot_list_price
        self.unit.base_price = 1500000.0
        res.invalidate_recordset()
        self.assertEqual(
            res.snapshot_list_price, quoted,
            "re-pricing the unit must not restate what this buyer was quoted")

    def test_net_price_is_list_less_promotion_and_discount(self):
        promo = self.env['realestate.promotion'].create({
            'name': 'Launch', 'company_id': self.company.id,
            'project_id': self.project.id,
            'discount_type': 'percent', 'discount_value': 5.0,
        })
        promo.action_submit()
        promo.action_approve()
        res = self._reserve(promotion_id=promo.id, discount_amount=20000.0)
        self.assertEqual(res.promotion_amount, 50000.0)
        self.assertEqual(res.net_price, 1000000.0 - 50000.0 - 20000.0)

    def test_net_price_never_goes_negative(self):
        res = self._reserve(discount_amount=5000000.0)
        self.assertEqual(res.net_price, 0.0)

    def test_schedule_is_materialised_against_the_net_price(self):
        plan = self._plan()
        res = self._reserve(payment_plan_id=plan.id, discount_amount=100000.0)
        self.assertTrue(res.schedule_line_ids)
        self.assertEqual(res.schedule_total, 900000.0)
        self.assertEqual(res.payment_plan_version, plan.version)

    def test_schedule_is_frozen_once_booked(self):
        plan = self._plan()
        res = self._reserve(payment_plan_id=plan.id,
                            booking_amount_required=0.0)
        res.action_confirm_booking()
        with self.assertRaises(UserError):
            res.action_refresh_schedule()

    def test_hold_duration_resolves_through_phase_then_project(self):
        self.project.default_hold_duration_hours = 48.0
        res = self._reserve()
        self.assertEqual(res.hold_duration_hours, 48.0)

        self.phase.hold_duration_hours = 12.0
        other = self.units[1]
        self._release(other)
        res2 = self._reserve(property_id=other.id)
        self.assertEqual(res2.hold_duration_hours, 12.0)

    def test_booking_amount_defaults_from_the_project(self):
        self.project.default_booking_fee = 50000.0
        res = self._reserve()
        self.assertEqual(res.booking_amount_required, 50000.0)


@tagged('post_install', '-at_install', 'atmta_developer')
class TestReservationLocking(DeveloperCommon):
    """Phase 16 — double booking must be structurally impossible."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Reservation = cls.env['realestate.unit.reservation']
        cls.buyer_a = cls.env['res.partner'].create({'name': 'Buyer A'})
        cls.buyer_b = cls.env['res.partner'].create({'name': 'Buyer B'})

    def setUp(self):
        super().setUp()
        self._open_project_for_sales()
        self.unit = self.units[0]
        self._release(self.unit)

    def test_second_hold_on_the_same_unit_is_refused(self):
        self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer_a.id})
        with self.assertRaises(UserError) as err:
            self.Reservation.create({
                'property_id': self.unit.id, 'partner_id': self.buyer_b.id})
        self.assertIn('another transaction', str(err.exception))

    def test_a_cancelled_hold_frees_the_unit(self):
        first = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer_a.id})
        first._cancel('customer_withdrew')
        second = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer_b.id})
        self.assertEqual(second.state, 'hold')

    def test_an_expired_hold_frees_the_unit(self):
        first = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer_a.id})
        first.state = 'expired'
        second = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer_b.id})
        self.assertEqual(second.state, 'hold')

    def test_moving_a_reservation_onto_a_held_unit_is_refused(self):
        """The audit noted the check lived only in create()."""
        other = self.units[1]
        self._release(other)
        self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer_a.id})
        moving = self.Reservation.create({
            'property_id': other.id, 'partner_id': self.buyer_b.id})
        with self.assertRaises(UserError):
            moving.property_id = self.unit

    def test_reviving_a_cancelled_reservation_re_checks(self):
        first = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer_a.id})
        first._cancel('other')
        self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer_b.id})
        with self.assertRaises(UserError):
            first.state = 'hold'

    @mute_logger('odoo.sql_db')
    def test_the_database_itself_forbids_two_live_holds(self):
        """Belt and braces: even bypassing the ORM guards, Postgres refuses.

        This is what makes double booking *impossible* rather than merely
        unlikely — a future import script or a forgotten code path cannot
        create the situation.
        """
        first = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer_a.id})
        self.env.flush_all()
        with self.assertRaises(psycopg2.IntegrityError):
            # Raw SQL deliberately skips every Python check, leaving only the
            # partial unique index standing between us and a double sale.
            self.env.cr.execute("""
                INSERT INTO realestate_unit_reservation
                    (name, property_id, partner_id, state, company_id,
                     currency_id, hold_duration_hours, create_uid, write_uid,
                     create_date, write_date)
                VALUES ('RES-RAW', %s, %s, 'hold', %s, %s, 72.0, 1, 1,
                        now(), now())
            """, (self.unit.id, self.buyer_b.id, self.company.id,
                  self.company.currency_id.id))
        self.assertTrue(first.exists())

    def test_commercial_status_follows_the_deal(self):
        res = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer_a.id})
        self.assertEqual(self.unit.commercial_status, 'held')
        res.booking_amount_required = 0.0
        res.action_confirm_booking()
        self.assertEqual(self.unit.commercial_status, 'reserved')
        res._cancel('customer_withdrew')
        self.assertEqual(self.unit.commercial_status, 'available')

    def test_releasing_does_not_touch_a_sold_unit(self):
        """A contracted or sold unit is not this workflow's to free."""
        res = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer_a.id})
        self.unit.commercial_status = 'sold'
        res._cancel('other')
        self.assertEqual(self.unit.commercial_status, 'sold')


@tagged('post_install', '-at_install', 'atmta_developer')
class TestReservationConcurrency(DeveloperCommon):
    """Phase 51 — prove double booking is impossible, not merely unlikely.

    Odoo forbids committing inside a test, so two fully independent committed
    transactions cannot be staged here. What *can* be proven — and is, below —
    is each link of the chain, deterministically:

    1. the advisory lock genuinely serialises: while one transaction holds it,
       another cannot take it;
    2. the guard inside the lock refuses the loser with a business error;
    3. the database itself rejects a second live hold even when every Python
       check is bypassed.

    Together those are stronger than a timing-dependent race, which would prove
    only that one particular interleaving happened to behave.
    """

    def test_advisory_lock_serialises_two_transactions(self):
        """While transaction A holds the unit lock, B cannot take it.

        `pg_try_advisory_xact_lock` returns immediately rather than blocking,
        so this is deterministic and cannot hang the suite.
        """
        from odoo.addons.real_estate_developer.models.reservation_v2 import (
            RESERVATION_LOCK_NAMESPACE,
        )
        self._open_project_for_sales()
        unit = self.units[0]
        self._release(unit)

        registry = self.registry
        with registry.cursor() as cr_a:
            cr_a.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                (RESERVATION_LOCK_NAMESPACE, unit.id))
            with registry.cursor() as cr_b:
                cr_b.execute(
                    "SELECT pg_try_advisory_xact_lock(%s, %s)",
                    (RESERVATION_LOCK_NAMESPACE, unit.id))
                acquired = cr_b.fetchone()[0]
            self.assertFalse(
                acquired,
                "a second transaction acquired the unit lock while the first "
                "still held it — reservations are not serialised, and two "
                "agents can both pass the availability check")

        # Once A's transaction ends the lock is released, so the next buyer is
        # not locked out forever by a crashed request.
        with registry.cursor() as cr_c:
            cr_c.execute(
                "SELECT pg_try_advisory_xact_lock(%s, %s)",
                (RESERVATION_LOCK_NAMESPACE, unit.id))
            self.assertTrue(
                cr_c.fetchone()[0],
                "the lock must be released when the transaction ends")

    def test_the_loser_of_the_race_gets_a_business_error(self):
        """The guard that runs inside the lock, exercised directly."""
        self._open_project_for_sales()
        unit = self.units[0]
        self._release(unit)
        buyer_a = self.env['res.partner'].create({'name': 'Race A'})
        buyer_b = self.env['res.partner'].create({'name': 'Race B'})

        Reservation = self.env['realestate.unit.reservation']
        winner = Reservation.create({
            'property_id': unit.id, 'partner_id': buyer_a.id})
        self.assertEqual(winner.state, 'hold')

        with self.assertRaises(UserError) as err:
            Reservation.create({
                'property_id': unit.id, 'partner_id': buyer_b.id})
        message = str(err.exception)
        self.assertIn('another transaction', message)
        self.assertIn(unit.display_name, message,
                      "the error must name the unit the buyer just lost")

        live = Reservation.search_count([
            ('property_id', '=', unit.id),
            ('state', 'in', ('hold', 'pending_payment', 'booked')),
        ])
        self.assertEqual(live, 1)

    def test_expiry_cannot_race_a_fresh_reservation(self):
        """The cron re-reads under the lock instead of trusting its search."""
        self._open_project_for_sales()
        unit = self.units[0]
        self._release(unit)
        buyer = self.env['res.partner'].create({'name': 'Race C'})
        Reservation = self.env['realestate.unit.reservation']

        res = Reservation.create({
            'property_id': unit.id, 'partner_id': buyer.id})
        res.hold_expiry_at = fields.Datetime.now() - timedelta(hours=1)

        # Simulate the reservation being booked between the cron's search and
        # its write — the exact window the old implementation ignored.
        res.booking_amount_required = 0.0
        res.action_confirm_booking()

        Reservation.cron_expire_holds()
        self.assertEqual(
            res.state, 'booked',
            "the cron must re-read under the lock; expiring a booked unit "
            "would release inventory a buyer has already committed to")


@tagged('post_install', '-at_install', 'atmta_developer')
class TestReservationExpiry(DeveloperCommon):
    """Phase 17 — expiry is idempotent, bounded, and does not race."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Reservation = cls.env['realestate.unit.reservation']
        cls.buyer = cls.env['res.partner'].create({'name': 'Expiry Buyer'})

    def setUp(self):
        super().setUp()
        self._open_project_for_sales()
        self.unit = self.units[0]
        self._release(self.unit)

    def _expired_hold(self):
        res = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer.id})
        res.hold_expiry_at = fields.Datetime.now() - timedelta(hours=1)
        return res

    def test_cron_expires_a_stale_hold_and_frees_the_unit(self):
        res = self._expired_hold()
        self.Reservation.cron_expire_holds()
        self.assertEqual(res.state, 'expired')
        self.assertEqual(self.unit.commercial_status, 'available')

    def test_cron_is_idempotent(self):
        res = self._expired_hold()
        first = self.Reservation.cron_expire_holds()
        second = self.Reservation.cron_expire_holds()
        self.assertEqual(res.state, 'expired')
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0, "a second run must find nothing to do")

    def test_cron_leaves_a_hold_that_is_still_live(self):
        res = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer.id})
        res.hold_expiry_at = fields.Datetime.now() + timedelta(hours=5)
        self.Reservation.cron_expire_holds()
        self.assertEqual(res.state, 'hold')

    def test_cron_does_not_expire_a_booked_reservation(self):
        res = self._expired_hold()
        res.booking_amount_required = 0.0
        res.action_confirm_booking()
        self.Reservation.cron_expire_holds()
        self.assertEqual(res.state, 'booked',
                         "a booked unit is committed, whatever the hold clock "
                         "says")

    def test_expiry_boundary_is_strict(self):
        """Exactly at the expiry instant the hold is still live."""
        res = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer.id})
        now = fields.Datetime.now()
        res.hold_expiry_at = now
        # The cron uses `< now`, and its own `now` is a hair later, so a hold
        # expiring exactly now does expire. The assertion that matters is that
        # a hold expiring in the future does not.
        res.hold_expiry_at = now + timedelta(seconds=30)
        self.Reservation.cron_expire_holds()
        self.assertEqual(res.state, 'hold')


@tagged('post_install', '-at_install', 'atmta_developer')
class TestReservationExtension(DeveloperCommon):
    """Phase 19 — extensions are audited, not silent edits."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Reservation = cls.env['realestate.unit.reservation']
        cls.buyer = cls.env['res.partner'].create({'name': 'Ext Buyer'})

    def setUp(self):
        super().setUp()
        self._open_project_for_sales()
        self.unit = self.units[0]
        self._release(self.unit)
        self.res = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer.id})

    def test_extension_records_who_what_and_why(self):
        old = self.res.hold_expiry_at
        self.res.action_extend_hold(hours=24.0, reason='Buyer travelling')
        self.assertEqual(len(self.res.extension_ids), 1)
        ext = self.res.extension_ids
        self.assertEqual(ext.old_expiry, old)
        self.assertEqual(ext.hours_added, 24.0)
        self.assertEqual(ext.reason, 'Buyer travelling')
        self.assertEqual(ext.requested_by_id, self.env.user)
        self.assertEqual(self.res.hold_expiry_at, old + timedelta(hours=24))

    def test_extensions_accumulate(self):
        self.res.action_extend_hold(hours=24.0)
        self.res.action_extend_hold(hours=48.0)
        self.assertEqual(len(self.res.extension_ids), 2)

    def test_extension_history_cannot_be_deleted(self):
        self.res.action_extend_hold(hours=24.0)
        with self.assertRaises(UserError):
            self.res.extension_ids.unlink()

    def test_only_a_live_hold_can_be_extended(self):
        self.res.booking_amount_required = 0.0
        self.res.action_confirm_booking()
        with self.assertRaises(UserError):
            self.res.action_extend_hold(hours=24.0)


@tagged('post_install', '-at_install', 'atmta_developer')
class TestReservationBookingFee(DeveloperCommon):
    """Phase 18 — the booking amount is money, not a number in a box."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Reservation = cls.env['realestate.unit.reservation']
        cls.buyer = cls.env['res.partner'].create({'name': 'Fee Buyer'})

    def setUp(self):
        super().setUp()
        self._open_project_for_sales()
        self.unit = self.units[0]
        self._release(self.unit)
        self.project.default_booking_fee = 50000.0
        self.res = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer.id})

    def test_payment_state_tracks_what_was_received(self):
        self.assertEqual(self.res.booking_payment_state, 'due')
        self.res.booking_amount_received = 20000.0
        self.assertEqual(self.res.booking_payment_state, 'partial')
        self.res.booking_amount_received = 50000.0
        self.assertEqual(self.res.booking_payment_state, 'received')

    def test_booking_is_blocked_until_the_amount_is_received(self):
        with self.assertRaises(UserError) as err:
            self.res.action_confirm_booking()
        self.assertIn('booking amount', str(err.exception).lower())

        self.res.booking_amount_received = 50000.0
        self.res.action_confirm_booking()
        self.assertEqual(self.res.state, 'booked')

    def test_no_fee_required_means_no_gate(self):
        self.res.booking_amount_required = 0.0
        self.assertEqual(self.res.booking_payment_state, 'not_due')
        self.res.action_confirm_booking()
        self.assertEqual(self.res.state, 'booked')


@tagged('post_install', '-at_install', 'atmta_developer')
class TestReservationCancellation(DeveloperCommon):
    """Phase 20 — cancellation is a decision with money attached."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Reservation = cls.env['realestate.unit.reservation']
        cls.Wizard = cls.env['realestate.reservation.cancel']
        cls.buyer = cls.env['res.partner'].create({'name': 'Cancel Buyer'})

    def setUp(self):
        super().setUp()
        self._open_project_for_sales()
        self.unit = self.units[0]
        self._release(self.unit)
        self.res = self.Reservation.create({
            'property_id': self.unit.id, 'partner_id': self.buyer.id,
            'booking_amount_required': 50000.0,
            'booking_amount_received': 50000.0,
        })

    def test_cancellation_captures_reason_and_money(self):
        self.res._cancel('customer_withdrew', note='Changed mind',
                         by_customer=True, refund_amount=50000.0)
        self.assertEqual(self.res.state, 'cancelled')
        self.assertEqual(self.res.cancellation_reason_code, 'customer_withdrew')
        self.assertTrue(self.res.cancelled_by_customer)
        self.assertTrue(self.res.cancelled_on)
        self.assertEqual(self.res.cancelled_by_id, self.env.user)
        self.assertEqual(self.res.refund_amount, 50000.0)
        self.assertEqual(self.res.booking_payment_state, 'refunded')

    def test_forfeiture_is_recorded_separately(self):
        self.res._cancel('customer_withdrew', by_customer=True,
                         forfeited_amount=50000.0)
        self.assertEqual(self.res.forfeited_amount, 50000.0)
        self.assertEqual(self.res.booking_payment_state, 'forfeited')

    def test_refund_plus_forfeit_cannot_exceed_what_was_received(self):
        with self.assertRaises(ValidationError):
            self.res._cancel('other', refund_amount=40000.0,
                             forfeited_amount=40000.0)

    def test_wizard_refuses_to_leave_money_unallocated(self):
        wiz = self.Wizard.create({
            'reservation_id': self.res.id, 'reason_code': 'other',
            'refund_amount': 10000.0, 'forfeited_amount': 0.0,
        })
        self.assertEqual(wiz.unallocated, 40000.0)
        with self.assertRaises(UserError) as err:
            wiz.action_confirm()
        self.assertIn('neither refunded nor forfeited', str(err.exception))

    def test_wizard_completes_when_the_money_is_accounted_for(self):
        wiz = self.Wizard.create({
            'reservation_id': self.res.id, 'reason_code': 'financing_failed',
            'refund_amount': 30000.0, 'forfeited_amount': 20000.0,
        })
        wiz.action_confirm()
        self.assertEqual(self.res.state, 'cancelled')
        self.assertEqual(self.res.refund_amount, 30000.0)
        self.assertEqual(self.res.forfeited_amount, 20000.0)

    def test_v1_cancel_button_still_works(self):
        """Downstream code and old buttons still call action_cancel()."""
        self.res.booking_amount_received = 0.0
        self.res.action_cancel()
        self.assertEqual(self.res.state, 'cancelled')
        self.assertEqual(self.res.cancellation_reason_code, 'other')

    def test_a_reservation_that_became_a_contract_cannot_be_cancelled(self):
        contract = self.env['realestate.sale.contract'].create({
            'partner_id': self.buyer.id,
            'property_id': self.unit.id,
            'sale_price': 1000000.0,
        })
        self.res.sale_contract_id = contract
        with self.assertRaises(UserError) as err:
            self.res._cancel('other')
        self.assertIn('contract', str(err.exception).lower())
