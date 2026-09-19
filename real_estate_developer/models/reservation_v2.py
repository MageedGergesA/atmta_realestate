# -*- coding: utf-8 -*-
"""Phases 15–20 — Reservation V2.

The existing ``realestate.unit.reservation`` is extended, not replaced:
`real_estate_maquette` opens its form from three different 3D/2D entry points,
and `realestate.property` computes its active reservation from it.

### Phase 16, which is the point of this file

The audit found that `create()` checked availability and then wrote the property
status **with no lock of any kind** — no advisory lock, no unique index, no
constraint. Two agents clicking at the same moment both read "available", both
pass the check, and both commit. That is a double-sold unit.

Two independent mechanisms fix it, and both are needed:

1. **A Postgres partial unique index** on ``property_id`` restricted to live
   states. This makes two live holds on one unit *structurally impossible* —
   not unlikely, impossible — regardless of what any Python path does, now or
   in five years when somebody writes a new import script.
2. **An advisory transaction lock** taken before the availability check. The
   index alone would let the loser hit a raw `IntegrityError`, which aborts the
   transaction and shows a Postgres message. The lock serialises the two
   transactions so the loser re-reads, sees the winner's hold, and gets a clean
   business error naming the unit.

The index is the guarantee. The lock is the good error message.
"""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .commercial_states import (
    CONTRACT_ENDED_STATES,
    PROMOTION_RELEASING_STATES,
)

#: Reservation states that hold a unit off the market. The partial unique index
#: below is built from exactly this tuple — change one and change the other.
LIVE_RESERVATION_STATES = ('hold', 'pending_payment', 'booked')

#: Arbitrary but stable namespace for `pg_advisory_xact_lock`, so developer
#: reservations cannot collide with another module's advisory locks.
RESERVATION_LOCK_NAMESPACE = 0x52455356  # "RESV"


class UnitReservationV2(models.Model):
    # The approval mixin is mixed in HERE rather than on the V1 model, because
    # this is the layer that gates discounts and hold extensions. Without it
    # `_require_approval` does not exist and those actions raise AttributeError.
    _name = 'realestate.unit.reservation'
    _inherit = ['realestate.unit.reservation',
                'realestate.commercial.approval.mixin']
    _check_company_auto = True

    # ------------------------------------------------------------------
    # Phase 15 — lifecycle
    # ------------------------------------------------------------------
    #: `hold`, `booked`, `confirmed`, `expired` and `cancelled` keep their exact
    #: meaning; `draft`, `pending_payment` and `rejected` are added. Downstream
    #: code filtering on ('hold', 'booked') therefore keeps working, which
    #: matters because `realestate.property._compute_active_reservation` and
    #: three maquette entry points do exactly that.
    state = fields.Selection(
        selection_add=[
            ('draft', 'Draft'),
            ('hold',),
            ('pending_payment', 'Awaiting Booking Payment'),
            ('booked',),
            ('confirmed',),
            ('expired',),
            ('rejected', 'Rejected'),
            ('cancelled',),
        ],
        ondelete={'draft': 'set default',
                  'pending_payment': 'set default',
                  'rejected': 'set default'},
    )

    # ------------------------------------------------------------------
    # Phase 15 — the commercial snapshot (Rule 5)
    # ------------------------------------------------------------------
    # Every figure below is copied at hold time and never recomputed. The
    # existing `proposed_price` / `property_base_price` pair read the property
    # *live*, so re-pricing a unit silently restated what a buyer had been
    # quoted.
    snapshot_taken_on = fields.Datetime(readonly=True, copy=False)
    price_book_id = fields.Many2one(
        'realestate.price.book', string='Price Book', readonly=True, copy=False)
    snapshot_base_price = fields.Monetary(
        string='Base Price (snapshot)', readonly=True, copy=False)
    snapshot_premium_total = fields.Monetary(
        string='Premiums (snapshot)', readonly=True, copy=False)
    snapshot_list_price = fields.Monetary(
        string='List Price (snapshot)', readonly=True, copy=False)

    # Only a live campaign may be picked. The domain is the convenience;
    # `_check_promotion` is the rule, because 2D/3D and the API write here too.
    promotion_id = fields.Many2one(
        'realestate.promotion', string='Promotion', tracking=True,
        check_company=True, domain="[('state', '=', 'active')]")
    promotion_amount = fields.Monetary(
        string='Promotion', readonly=True, copy=False)

    discount_amount = fields.Monetary(string='Discount', tracking=True)
    discount_percent = fields.Float(
        string='Discount %', compute='_compute_discount_percent', store=True)

    net_price = fields.Monetary(
        string='Net Selling Price', compute='_compute_net_price', store=True,
        help="List price less promotion and discount. This is the number the "
             "payment plan is applied to and the contract is signed at.")

    payment_plan_id = fields.Many2one(
        'realestate.payment.plan', string='Payment Plan', tracking=True,
        check_company=True)
    payment_plan_version = fields.Integer(readonly=True, copy=False)
    schedule_line_ids = fields.One2many(
        'realestate.reservation.schedule.line', 'reservation_id',
        string='Payment Schedule', readonly=True, copy=False)
    schedule_total = fields.Monetary(
        compute='_compute_schedule_total', string='Scheduled Total')

    approval_request_ids = fields.One2many(
        'realestate.commercial.approval.request', 'res_id',
        domain=[('res_model', '=', 'realestate.unit.reservation')],
        string='Approvals')

    # ------------------------------------------------------------------
    # Phase 18 — booking fee as a real flow
    # ------------------------------------------------------------------
    booking_amount_required = fields.Monetary(
        string='Booking Amount Due', tracking=True,
        help="Defaults from the phase, then the project.")
    booking_amount_received = fields.Monetary(
        string='Booking Amount Received', tracking=True)
    booking_payment_state = fields.Selection([
        ('not_due', 'Not Required'),
        ('due', 'Due'),
        ('partial', 'Partially Received'),
        ('received', 'Received'),
        ('refunded', 'Refunded'),
        ('forfeited', 'Forfeited'),
    ], compute='_compute_booking_payment_state', store=True, tracking=True)
    booking_is_refundable = fields.Boolean(
        string='Refundable', default=True, tracking=True,
        help="Whether the booking amount is returned if the buyer walks away. "
             "Contractual, so it is recorded per reservation.")
    booking_counts_towards_price = fields.Boolean(
        compute='_compute_booking_counts', store=True,
        help="Taken from the payment plan. When true the booking amount is "
             "part of the price, so it must not be charged again in the "
             "schedule.")

    # ------------------------------------------------------------------
    # Phase 19/20 — extensions and cancellation
    # ------------------------------------------------------------------
    extension_ids = fields.One2many(
        'realestate.reservation.extension', 'reservation_id',
        string='Hold Extensions')
    extension_count = fields.Integer(compute='_compute_extension_count')

    cancellation_reason_code = fields.Selection([
        ('customer_withdrew', 'Customer Withdrew'),
        ('financing_failed', 'Financing Failed'),
        ('developer_withdrew', 'Developer Withdrew'),
        ('unit_changed', 'Changed to Another Unit'),
        ('price_not_agreed', 'Price Not Agreed'),
        ('expired_unpaid', 'Expired Unpaid'),
        ('duplicate', 'Duplicate Entry'),
        ('other', 'Other'),
    ], string='Cancellation Reason', tracking=True, copy=False)
    cancellation_note = fields.Text(copy=False)
    cancelled_on = fields.Datetime(readonly=True, copy=False)
    cancelled_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    cancelled_by_customer = fields.Boolean(
        string='Customer-Requested', copy=False)
    refund_amount = fields.Monetary(string='Refundable', copy=False)
    forfeited_amount = fields.Monetary(string='Forfeited', copy=False)

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('discount_amount', 'snapshot_list_price')
    def _compute_discount_percent(self):
        for rec in self:
            rec.discount_percent = (
                rec.discount_amount / rec.snapshot_list_price * 100.0
                if rec.snapshot_list_price else 0.0)

    @api.depends('snapshot_list_price', 'promotion_amount', 'discount_amount')
    def _compute_net_price(self):
        for rec in self:
            rec.net_price = max(
                (rec.snapshot_list_price or 0.0)
                - (rec.promotion_amount or 0.0)
                - (rec.discount_amount or 0.0), 0.0)

    @api.depends('schedule_line_ids.amount')
    def _compute_schedule_total(self):
        for rec in self:
            rec.schedule_total = sum(rec.schedule_line_ids.mapped('amount'))

    @api.depends('booking_amount_required', 'booking_amount_received', 'state')
    def _compute_booking_payment_state(self):
        for rec in self:
            if rec.state in ('cancelled', 'rejected', 'expired'):
                if rec.forfeited_amount:
                    rec.booking_payment_state = 'forfeited'
                    continue
                if rec.refund_amount:
                    rec.booking_payment_state = 'refunded'
                    continue
            required = rec.booking_amount_required or 0.0
            received = rec.booking_amount_received or 0.0
            if not required:
                rec.booking_payment_state = 'not_due'
            elif received <= 0:
                rec.booking_payment_state = 'due'
            elif received < required:
                rec.booking_payment_state = 'partial'
            else:
                rec.booking_payment_state = 'received'

    @api.depends('payment_plan_id.booking_handling')
    def _compute_booking_counts(self):
        for rec in self:
            rec.booking_counts_towards_price = (
                rec.payment_plan_id.booking_handling == 'part_of_price'
                if rec.payment_plan_id else True)

    @api.depends('hold_started_at', 'hold_duration_hours',
                 'extension_ids.hours_added')
    def _compute_hold_expiry(self):
        """Start plus duration plus every recorded extension.

        Overrides V1, which knew only the duration. An extension wrote the
        expiry directly, so editing the duration afterwards recomputed the
        expiry from the duration alone and silently took the extension back.
        """
        for rec in self:
            if rec.hold_started_at and rec.hold_duration_hours:
                rec.hold_expiry_at = rec.hold_started_at + timedelta(
                    hours=rec.hold_duration_hours
                    + sum(rec.extension_ids.mapped('hours_added')))
            else:
                rec.hold_expiry_at = False

    def _compute_extension_count(self):
        for rec in self:
            rec.extension_count = len(rec.extension_ids)

    # ==================================================================
    # Phase 16 — locking
    # ==================================================================
    def init(self):
        """A partial unique index: at most one live reservation per unit.

        This is the guarantee that double booking cannot happen. It holds
        against concurrent transactions, against a future code path that
        forgets to lock, and against a data import — none of which a Python
        check can promise.
        """
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                realestate_reservation_one_live_per_property
            ON realestate_unit_reservation (property_id)
            WHERE state IN %s
        """, (LIVE_RESERVATION_STATES,))

    @api.model
    def _lock_property(self, property_id):
        """Serialise everyone competing for this unit, for this transaction.

        Released automatically at commit or rollback, so a crashed request can
        never leave a unit locked.
        """
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (RESERVATION_LOCK_NAMESPACE, int(property_id)))

    @api.model
    def _assert_no_live_reservation(self, property_id, ignore_ids=None):
        """Re-read after locking. The winner is already here; the loser is told."""
        domain = [
            ('property_id', '=', property_id),
            ('state', 'in', LIVE_RESERVATION_STATES),
        ]
        if ignore_ids:
            domain.append(('id', 'not in', list(ignore_ids)))
        existing = self.sudo().search(domain, limit=1)
        if existing:
            prop = self.env['realestate.property'].browse(property_id)
            raise UserError(_(
                "Unit %s has just been held by another transaction (%s). "
                "Please choose another unit."
            ) % (prop.display_name, existing.name))

    # ==================================================================
    # Create / write
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            property_id = vals.get('property_id')
            if not property_id:
                continue
            # Lock BEFORE checking. Checking first and locking after is the
            # same race with extra steps.
            self._lock_property(property_id)
            state = vals.get('state', 'hold')
            if state in LIVE_RESERVATION_STATES:
                self._assert_no_live_reservation(property_id)
                self.env['realestate.property'].browse(
                    property_id)._check_available_for_sale()
        reservations = super().create(vals_list)
        for rec in reservations:
            rec._check_promotion()
            rec._take_snapshot()
            rec._apply_commercial_status()
        return reservations

    def write(self, vals):
        """Re-point or re-activate a reservation only under the same lock.

        The audit noted the original check lived solely in ``create()``, so
        moving an existing reservation onto another unit was unguarded.
        """
        moving = 'property_id' in vals
        reviving = vals.get('state') in LIVE_RESERVATION_STATES
        if moving or reviving:
            for rec in self:
                target = vals.get('property_id', rec.property_id.id)
                if not target:
                    continue
                self._lock_property(target)
                new_state = vals.get('state', rec.state)
                if new_state in LIVE_RESERVATION_STATES:
                    self._assert_no_live_reservation(
                        target, ignore_ids=rec.ids)
        res = super().write(vals)
        if vals.get('promotion_id'):
            self._check_promotion()
        if 'state' in vals or moving:
            self._apply_commercial_status()
        return res

    # ==================================================================
    # Snapshot
    # ==================================================================
    def _take_snapshot(self):
        """Freeze price and plan at the moment the unit is held.

        Nothing here is ever recomputed. A master price change tomorrow must
        not restate what this buyer was quoted today.
        """
        for rec in self:
            if rec.snapshot_taken_on:
                continue
            prop = rec.property_id
            book = self.env['realestate.price.book']._find_active(prop) if prop else False

            promo = rec.promotion_id
            list_price = prop.list_price_developer if prop else 0.0
            promo_amount = (promo._amount_for(list_price)
                            if promo and promo._covers(prop) else 0.0)

            plan = rec.payment_plan_id
            rec.write({
                'snapshot_taken_on': fields.Datetime.now(),
                'price_book_id': book.id if book else False,
                'snapshot_base_price': (
                    prop.base_price - prop.premium_total if prop else 0.0),
                'snapshot_premium_total': prop.premium_total if prop else 0.0,
                'snapshot_list_price': list_price,
                'promotion_amount': promo_amount,
                'payment_plan_version': plan.version if plan else 0,
            })
            if not rec.booking_amount_required:
                rec.booking_amount_required = rec._default_booking_amount()
            if not rec.hold_duration_hours or rec.hold_duration_hours == 72.0:
                rec.hold_duration_hours = rec._resolved_hold_hours()
            if plan:
                rec._build_schedule()

    def _check_promotion(self, frozen_on=None):
        """Refuse a promotion this deal is not entitled to.

        The snapshot used to ask only whether the campaign covered the unit, so
        a draft or cancelled campaign, one outside its dates, or one already
        used up to its cap was applied like any other. Measured on today;
        booking passes ``frozen_on``, the date the quote was frozen, so a
        campaign that ended (or was expired by the cron) after the unit was
        held still honours the quote.
        """
        for rec in self:
            promo = rec.promotion_id
            if not promo:
                continue
            date = frozen_on or fields.Date.context_today(rec)
            accepted_states = ('active', 'expired') if frozen_on else ('active',)
            if promo.state not in accepted_states:
                raise UserError(_(
                    "Promotion '%(promo)s' is %(state)s and cannot be applied "
                    "to reservation %(res)s."
                ) % {'promo': promo.display_name, 'res': rec.name,
                     'state': dict(promo._fields['state'].selection).get(
                         promo.state, promo.state)})
            if ((promo.date_start and date < promo.date_start)
                    or (promo.date_end and date > promo.date_end)):
                raise UserError(_(
                    "Promotion '%(promo)s' does not run on %(date)s."
                ) % {'promo': promo.display_name, 'date': date})
            if promo.max_uses:
                others = self.sudo().search_count([
                    ('promotion_id', '=', promo.id),
                    ('id', '!=', rec.id),
                    ('state', 'not in', PROMOTION_RELEASING_STATES),
                ])
                if others >= promo.max_uses:
                    raise UserError(_(
                        "Promotion '%(promo)s' is limited to %(max)s use(s) "
                        "and they are all taken."
                    ) % {'promo': promo.display_name, 'max': promo.max_uses})

    def _default_booking_amount(self):
        """Phase/project default, resolved through the M1 inheritance chain."""
        self.ensure_one()
        if self.phase_id:
            return self.phase_id._resolve_booking_fee()
        if self.project_id:
            return self.project_id.default_booking_fee or 0.0
        return 0.0

    def _resolved_hold_hours(self):
        """Phase override, else project default, else Odoo's 72 hours."""
        self.ensure_one()
        if self.phase_id:
            return self.phase_id._resolve_hold_duration_hours()
        if self.project_id:
            return self.project_id.default_hold_duration_hours or 72.0
        return 72.0

    def _build_schedule(self):
        """Materialise the plan against this deal's net price."""
        self.ensure_one()
        self.schedule_line_ids.unlink()
        plan = self.payment_plan_id
        if not plan or not self.net_price:
            return
        rows = plan._generate_schedule(
            total_price=self.net_price,
            booking_date=fields.Date.context_today(self),
            handover_date=(self.project_id.expected_handover_date
                           if self.project_id else False),
            booking_amount=self.booking_amount_required,
        )
        plan._validate_schedule_total(rows, self.net_price)
        self.env['realestate.reservation.schedule.line'].create([{
            'reservation_id': self.id,
            'sequence': row['sequence'],
            'kind': row['kind'],
            'name': row['name'],
            'percent': row['percent'],
            'amount': row['amount'],
            'date_due': row['date_due'],
        } for row in rows])

    def action_refresh_schedule(self):
        """Rebuild the schedule after a price or plan change, pre-booking."""
        for rec in self:
            if rec.state not in ('draft', 'hold'):
                raise UserError(_(
                    "The schedule of reservation %s is fixed once it is "
                    "booked. Cancel and re-quote instead."
                ) % rec.name)
            rec._build_schedule()

    # ==================================================================
    # Commercial status
    # ==================================================================
    def _apply_commercial_status(self):
        """Keep the unit's commercial status in step with its live deal.

        The original code only ever moved a unit from `available` to
        `reserved`, and only if it happened to be `available` — so a
        reservation on a unit in any other state left the inventory silently
        wrong. This writes the dimension Module 1 actually owns, using its full
        vocabulary: a hold is `held`, a booking is `reserved`.
        """
        for rec in self:
            prop = rec.property_id
            if not prop:
                continue
            live = prop.reservation_ids.filtered(
                lambda r: r.state in LIVE_RESERVATION_STATES)
            if not live:
                # A booking turned into a contract is `confirmed`, which is not
                # a live state -- yet while that contract stands the unit is
                # still this buyer's. Releasing it here put a unit with a draft
                # contract back on the market, and a second buyer could hold it.
                if prop.reservation_ids.filtered(
                        lambda r: r._commits_unit_through_contract()):
                    if prop.commercial_status in ('available', 'held'):
                        prop.commercial_status = 'reserved'
                    continue
                # Only release a unit that this module put on hold. A unit that
                # is contracted or sold is not ours to free.
                if prop.commercial_status in ('held', 'reserved'):
                    prop.commercial_status = 'available'
                continue
            target = 'reserved' if any(
                r.state == 'booked' for r in live) else 'held'
            if prop.commercial_status != target:
                prop.commercial_status = target

    def _commits_unit_through_contract(self):
        """A confirmed reservation whose sale contract has not been ended."""
        self.ensure_one()
        if self.state != 'confirmed':
            return False
        contracts = self.sale_contract_id | self.env[
            'realestate.sale.contract'].sudo().search(
                [('reservation_id', '=', self.id)])
        return bool(contracts.filtered(
            lambda c: c.state not in CONTRACT_ENDED_STATES))

    # ==================================================================
    # Workflow
    # ==================================================================
    def action_hold(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft reservations can be held."))
            rec.property_id._check_available_for_sale()
            rec.write({
                'state': 'hold',
                'hold_started_at': fields.Datetime.now(),
            })

    def action_request_booking_payment(self):
        for rec in self:
            if rec.state != 'hold':
                raise UserError(_(
                    "Only an on-hold reservation can move to awaiting payment."))
            if not rec.booking_amount_required:
                raise UserError(_(
                    "Set the booking amount due before requesting payment."))
            rec.state = 'pending_payment'

    def action_confirm_booking(self):
        """Book the unit. Overrides the V1 action to add real gates."""
        for rec in self:
            if rec.state not in ('hold', 'pending_payment'):
                raise UserError(_(
                    "Only an on-hold or awaiting-payment reservation can be "
                    "booked."))
            if rec.booking_amount_required and (
                    rec.booking_amount_received < rec.booking_amount_required):
                raise UserError(_(
                    "Reservation %s has received %s of the %s booking amount "
                    "due. Record the balance before booking."
                ) % (rec.name, rec.booking_amount_received,
                     rec.booking_amount_required))
            rec._check_discount_authority()
            rec._check_promotion(frozen_on=(
                fields.Datetime.context_timestamp(
                    rec, rec.snapshot_taken_on).date()
                if rec.snapshot_taken_on else None))
            rec.state = 'booked'

    def _check_discount_authority(self):
        """Phase 10 gate, enforced where the deal actually commits."""
        self.ensure_one()
        if self.discount_amount > 0 and self.discount_percent > 0:
            self._require_approval(
                'discount', self.discount_percent,
                amount=self.discount_amount,
                reason=_('Discount on reservation %s') % self.name)

    def _approval_needed(self):
        """The discount gate `_check_discount_authority` applies at booking."""
        self.ensure_one()
        if (self.state in ('hold', 'pending_payment')
                and self.discount_amount > 0 and self.discount_percent > 0):
            return ('discount', self.discount_percent, self.discount_amount,
                    _('Discount on reservation %s') % self.name)
        return None

    def action_reject(self, reason=None):
        for rec in self:
            if rec.state in ('confirmed', 'cancelled'):
                raise UserError(_(
                    "Reservation %s can no longer be rejected.") % rec.name)
            rec.write({
                'state': 'rejected',
                'cancellation_reason_code': 'developer_withdrew',
                'cancellation_note': reason,
                'cancelled_on': fields.Datetime.now(),
                'cancelled_by_id': self.env.user.id,
            })

    # ==================================================================
    # Phase 20 — structured cancellation
    # ==================================================================
    def action_open_cancellation(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cancel Reservation'),
            'res_model': 'realestate.reservation.cancel',
            'views': [(False, 'form')],
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_reservation_id': self.id},
        }

    def _cancel(self, reason_code, note=None, by_customer=False,
                refund_amount=0.0, forfeited_amount=0.0):
        """The single cancellation path. Never just set state='cancelled'."""
        for rec in self:
            if rec.state == 'cancelled':
                raise UserError(_(
                    "Reservation %s is already cancelled.") % rec.name)
            if rec.sale_contract_id:
                raise UserError(_(
                    "Reservation %s has become sale contract %s. Cancel the "
                    "contract instead — cancelling the reservation would leave "
                    "the contract without an origin."
                ) % (rec.name, rec.sale_contract_id.name))
            received = rec.booking_amount_received or 0.0
            if refund_amount + forfeited_amount > received + 0.0001:
                raise ValidationError(_(
                    "Reservation %s received %s, but %s is being refunded and "
                    "%s forfeited. The two cannot exceed what was received."
                ) % (rec.name, received, refund_amount, forfeited_amount))
            rec.write({
                'state': 'cancelled',
                'cancellation_reason_code': reason_code,
                'cancellation_note': note,
                'cancelled_by_customer': by_customer,
                'cancelled_on': fields.Datetime.now(),
                'cancelled_by_id': self.env.user.id,
                'refund_amount': refund_amount,
                'forfeited_amount': forfeited_amount,
            })
            rec.message_post(body=_(
                "Cancelled (%s). Refundable %s, forfeited %s."
            ) % (dict(rec._fields['cancellation_reason_code'].selection).get(
                reason_code, reason_code), refund_amount, forfeited_amount))
        return True

    def action_cancel(self):
        """V1 compatibility: keep the plain button working, but record a reason.

        The original set ``state = 'cancelled'`` and nothing else. Downstream
        code and existing buttons still call this, so it now routes through the
        structured path with an explicit 'other' reason rather than being
        removed.

        It records no refund and no forfeiture, so it is refused once booking
        money has been received: cancelling here would leave that money
        unexplained, which is exactly what the cancellation wizard refuses.
        """
        paid = self.filtered(lambda r: (r.booking_amount_received or 0.0) > 0.0)
        if paid:
            raise UserError(_(
                "Reservation %s has received %s of booking money. Cancel it "
                "with the cancellation form, which records how much is refunded "
                "and how much is forfeited."
            ) % (paid[0].name, paid[0].booking_amount_received))
        return self._cancel('other', note=_('Cancelled without a stated reason'))

    # ==================================================================
    # Phase 17 — expiry
    # ==================================================================
    @api.model
    def cron_expire_holds(self):
        """Expire stale holds. Idempotent, batched, multi-company safe.

        Overrides V1's version, which searched without a limit, looped
        record-by-record writing property state inside the loop, and could
        race with a new reservation taken in the same instant.
        """
        now = fields.Datetime.now()
        stale = self.search([
            ('state', 'in', ('hold', 'pending_payment')),
            ('hold_expiry_at', '!=', False),
            ('hold_expiry_at', '<', now),
        ], limit=500)
        if not stale:
            return 0

        expired = self.browse()
        for rec in stale:
            # Lock the unit so expiry cannot race a reservation being taken on
            # it right now: whoever gets the lock first wins, and the other
            # re-reads the truth.
            self._lock_property(rec.property_id.id)
            rec.invalidate_recordset(['state', 'hold_expiry_at'])
            if rec.state not in ('hold', 'pending_payment'):
                continue                      # somebody booked it meanwhile
            if rec.hold_expiry_at and rec.hold_expiry_at >= now:
                continue                      # extended meanwhile
            expired |= rec

        if expired:
            expired.write({'state': 'expired'})
            expired._apply_commercial_status()
            for rec in expired:
                rec.message_post(body=_("Hold expired."))
        return len(expired)

    # ==================================================================
    # Phase 19 — audited extensions
    # ==================================================================
    def action_extend_hold(self, hours=24.0, reason=None):
        """Extend a hold, on the record.

        V1 did ``rec.hold_duration_hours += 24.0`` — no record of who extended
        it, by how much, or why. An extension is a commercial concession and is
        audited like one.
        """
        for rec in self:
            if rec.state not in ('hold', 'pending_payment'):
                raise UserError(_(
                    "Only a live hold can be extended. Reservation %s is %s."
                ) % (rec.name, rec.state))
            old_expiry = rec.hold_expiry_at
            rec._require_approval(
                'reservation_extension', hours,
                reason=reason or _('Hold extension on %s') % rec.name)
            new_expiry = (old_expiry or fields.Datetime.now()) + timedelta(hours=hours)
            self.env['realestate.reservation.extension'].create({
                'reservation_id': rec.id,
                'old_expiry': old_expiry,
                'new_expiry': new_expiry,
                'hours_added': hours,
                'reason': reason,
            })
            # Written directly rather than through hold_duration_hours, so the
            # extension is not silently undone the next time the duration is
            # recomputed.
            rec.hold_expiry_at = new_expiry
        return True

    def action_view_extensions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Hold Extensions'),
            'res_model': 'realestate.reservation.extension',
            'views': [(False, 'list'), (False, 'form')],
            'view_mode': 'list,form',
            'domain': [('reservation_id', '=', self.id)],
            'target': 'current',
        }


class ReservationScheduleLine(models.Model):
    """The payment schedule as quoted, frozen on the reservation."""
    _name = 'realestate.reservation.schedule.line'
    _description = 'Reservation Payment Schedule Line'
    _order = 'reservation_id, sequence, date_due, id'

    reservation_id = fields.Many2one(
        'realestate.unit.reservation', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='reservation_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='reservation_id.currency_id', store=True, readonly=True)
    sequence = fields.Integer()
    kind = fields.Char()
    name = fields.Char()
    percent = fields.Float(string='%')
    amount = fields.Monetary()
    date_due = fields.Date(string='Due')


class ReservationExtension(models.Model):
    """Phase 19 — who extended a hold, by how much, and why."""
    _name = 'realestate.reservation.extension'
    _description = 'Reservation Hold Extension'
    _order = 'create_date desc, id desc'

    reservation_id = fields.Many2one(
        'realestate.unit.reservation', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='reservation_id.company_id', store=True, index=True, readonly=True)
    property_id = fields.Many2one(
        related='reservation_id.property_id', store=True, readonly=True)
    old_expiry = fields.Datetime(readonly=True)
    new_expiry = fields.Datetime(readonly=True, required=True)
    hours_added = fields.Float(readonly=True)
    reason = fields.Char()
    requested_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True)

    def unlink(self):
        raise UserError(_(
            "Hold extensions are an audit record and cannot be deleted."))
