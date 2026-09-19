# -*- coding: utf-8 -*-
"""Phases 21–24 — Sale Contract V2.

### On the lifecycle (Phase 21)

The spec proposes replacing `draft → signed → completed` with a deeper chain.
The audit found the real values are `draft → signed → handed_over → cancelled`
— there is no `completed` — and that `real_estate_handover` both filters on
`state == 'signed'` and calls `action_handover()`.

So `state` is **extended, not replaced**: every existing value keeps its exact
meaning and the new stages are added around them. No bridge and no migration of
existing rows is needed, because nothing is being redefined.

Phase 21 also asks that lifecycle, signature, billing, collection and handover
not be mixed. They are not mixed here — but the mixing it warns about (a
billing milestone living inside the lifecycle field, as `invoiced` did on the
*rental* contract) does not exist on this model. Separation is therefore
achieved by **adding** the other four dimensions as their own computed fields,
rather than by splitting a field that was never overloaded. That is the smaller,
safer change, and it leaves five downstream modules undisturbed.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .reservation_v2 import LIVE_RESERVATION_STATES

#: Lifecycle. `draft`, `signed`, `handed_over` and `cancelled` are the original
#: four and are unchanged; the rest are new stages around them.
CONTRACT_LIFECYCLE = [
    ('draft', 'Draft'),
    ('pending_approval', 'Pending Approval'),
    ('pending_signature', 'Pending Signature'),
    ('signed', 'Signed'),
    ('active', 'Active'),
    ('financially_cleared', 'Financially Cleared'),
    ('handed_over', 'Handed Over'),
    ('cancelled', 'Cancelled'),
    ('terminated', 'Terminated'),
    ('transferred', 'Transferred'),
]

#: Lifecycle states in which the deal is live and its obligations stand.
CONTRACT_LIVE = ('signed', 'active', 'financially_cleared', 'handed_over')

PARTY_ROLE = [
    ('primary_buyer', 'Primary Buyer'),
    ('co_buyer', 'Co-Buyer'),
    ('spouse', 'Spouse'),
    ('company', 'Company'),
    ('representative', 'Authorised Representative'),
    ('assignee', 'Assignee'),
    ('guarantor', 'Guarantor'),
]


class SaleContractV2(models.Model):
    _name = 'realestate.sale.contract'
    _inherit = ['realestate.sale.contract',
                'realestate.commercial.approval.mixin']
    _check_company_auto = True

    # ------------------------------------------------------------------
    # Phase 21 — lifecycle, extended not replaced
    # ------------------------------------------------------------------
    state = fields.Selection(
        selection_add=[
            ('draft',),
            ('pending_approval', 'Pending Approval'),
            ('pending_signature', 'Pending Signature'),
            ('signed',),
            ('active', 'Active'),
            ('financially_cleared', 'Financially Cleared'),
            ('handed_over',),
            ('cancelled',),
            ('terminated', 'Terminated'),
            ('transferred', 'Transferred'),
        ],
        ondelete={
            'pending_approval': 'set default',
            'pending_signature': 'set default',
            'active': lambda recs: recs.write({'state': 'signed'}),
            'financially_cleared': lambda recs: recs.write({'state': 'signed'}),
            'terminated': lambda recs: recs.write({'state': 'cancelled'}),
            'transferred': lambda recs: recs.write({'state': 'cancelled'}),
        },
    )

    # ---- The other four dimensions, each computed from its own evidence ----
    signature_state = fields.Selection([
        ('not_sent', 'Not Sent'),
        ('sent', 'Sent for Signature'),
        ('signed', 'Signed'),
    ], default='not_sent', tracking=True, copy=False,
        help="Where the paperwork is. Independent of the lifecycle: a contract "
             "can be commercially agreed and still unsigned.")
    signature_sent_on = fields.Date(readonly=True, copy=False)

    billing_state = fields.Selection([
        ('not_invoiced', 'Not Invoiced'),
        ('partially_invoiced', 'Partially Invoiced'),
        ('fully_invoiced', 'Fully Invoiced'),
    ], compute='_compute_financial_state', store=True,
        help="How much of the schedule has reached the ledger.")

    collection_state = fields.Selection([
        ('nothing_due', 'Nothing Due'),
        ('current', 'Current'),
        ('overdue', 'Overdue'),
        ('settled', 'Settled'),
    ], compute='_compute_financial_state', store=True,
        help="Whether the buyer is keeping up. Derived from invoice residuals, "
             "never from a payment existing.")

    # ------------------------------------------------------------------
    # Phase 24 — the commercial snapshot
    # ------------------------------------------------------------------
    snapshot_taken_on = fields.Datetime(readonly=True, copy=False)
    price_book_id = fields.Many2one(
        'realestate.price.book', readonly=True, copy=False)
    snapshot_base_price = fields.Monetary(
        string='Base Price (signed)', readonly=True, copy=False)
    snapshot_premium_total = fields.Monetary(
        string='Premiums (signed)', readonly=True, copy=False)
    snapshot_list_price = fields.Monetary(
        string='List Price (signed)', readonly=True, copy=False)
    promotion_id = fields.Many2one(
        'realestate.promotion', readonly=True, copy=False, check_company=True)
    promotion_amount = fields.Monetary(readonly=True, copy=False)
    discount_amount = fields.Monetary(readonly=True, copy=False)
    booking_credit = fields.Monetary(
        string='Booking Credit', readonly=True, copy=False,
        help="Booking money already received that counts towards the price. "
             "Carried from the reservation so the same money is not charged "
             "twice.")

    payment_plan_id = fields.Many2one(
        'realestate.payment.plan', string='Payment Plan', tracking=True,
        check_company=True)
    payment_plan_version = fields.Integer(readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Phase 22 — parties
    # ------------------------------------------------------------------
    party_ids = fields.One2many(
        'realestate.sale.contract.party', 'contract_id', string='Parties')
    party_count = fields.Integer(compute='_compute_party_count')

    # ------------------------------------------------------------------
    # Phase 23 — multi-asset package
    # ------------------------------------------------------------------
    # `property_id` stays the primary unit and stays required: Checks, Handover,
    # the API and the Portal all read it, and the audit proved they do. Accessory
    # assets (parking, storage) are additive lines, so nothing downstream has to
    # learn a new shape to keep working.
    property_line_ids = fields.One2many(
        'realestate.sale.contract.property.line', 'contract_id',
        string='Included Units')
    property_line_count = fields.Integer(compute='_compute_party_count')

    # ------------------------------------------------------------------
    # Financial roll-up (Phase 25/26)
    # ------------------------------------------------------------------
    installment_count = fields.Integer(compute='_compute_financial_state', store=True)
    scheduled_amount = fields.Monetary(
        compute='_compute_financial_state', store=True, string='Scheduled')
    overdue_amount = fields.Monetary(
        compute='_compute_financial_state', store=True, string='Overdue')
    next_due_date = fields.Date(compute='_compute_financial_state', store=True)

    approval_request_ids = fields.One2many(
        'realestate.commercial.approval.request', 'res_id',
        domain=[('res_model', '=', 'realestate.sale.contract')],
        string='Approvals')

    # ==================================================================
    # Computes
    # ==================================================================
    def _compute_party_count(self):
        for rec in self:
            rec.party_count = len(rec.party_ids)
            rec.property_line_count = len(rec.property_line_ids)

    @api.depends('installment_ids.current_amount',
                 'installment_ids.invoiced_amount',
                 'installment_ids.paid_amount',
                 'installment_ids.residual_amount',
                 'installment_ids.date_due',
                 'installment_ids.payment_state',
                 'installment_ids.state')
    def _compute_financial_state(self):
        today = fields.Date.context_today(self)
        for rec in self:
            live = rec.installment_ids.filtered(
                lambda i: i.state != 'cancelled')
            rec.installment_count = len(live)
            rec.scheduled_amount = sum(live.mapped('current_amount'))

            invoiced = sum(live.mapped('invoiced_amount'))
            if not live or not invoiced:
                rec.billing_state = 'not_invoiced'
            elif invoiced >= rec.scheduled_amount - 0.0001:
                rec.billing_state = 'fully_invoiced'
            else:
                rec.billing_state = 'partially_invoiced'

            outstanding = live.filtered(lambda i: i.residual_amount > 0)
            overdue = outstanding.filtered(
                lambda i: i.date_due and i.date_due < today)
            rec.overdue_amount = sum(overdue.mapped('residual_amount'))
            rec.next_due_date = min(
                outstanding.mapped('date_due'), default=False)

            if not live:
                rec.collection_state = 'nothing_due'
            elif not outstanding:
                rec.collection_state = 'settled'
            elif overdue:
                rec.collection_state = 'overdue'
            else:
                rec.collection_state = 'current'

    @api.depends('invoice_id.amount_total', 'invoice_id.amount_residual',
                 'invoice_id.payment_state', 'invoice_id.state', 'sale_price',
                 'installment_ids.current_amount',
                 'installment_ids.invoiced_amount',
                 'installment_ids.paid_amount',
                 'installment_ids.residual_amount',
                 'installment_ids.state')
    def _compute_balances(self):
        """The form's Balances, read from the instalments when they bill.

        V1 read only the whole-price invoice (`invoice_id`), which V2 no longer
        raises. A contract billed per instalment therefore showed nothing
        invoiced, nothing paid and its whole price due however much the buyer
        had paid. A contract that does carry a V1 invoice, or that has no
        instalments yet, keeps the V1 figures.
        """
        legacy = self.browse()
        for rec in self:
            live = rec.installment_ids.filtered(lambda i: i.state != 'cancelled')
            if (rec.invoice_id and rec.invoice_id.state == 'posted') or not live:
                legacy |= rec
                continue
            # Same bases as the instalments: invoiced is untaxed (comparable
            # with the schedule), paid and still due are the cash reality.
            paid = sum(live.mapped('paid_amount'))
            due = sum(live.mapped('residual_amount'))
            rec.invoiced_amount = sum(live.mapped('invoiced_amount'))
            rec.paid_amount = paid
            rec.balance_due = due
            rec.progress = (paid / (paid + due) * 100.0) if (paid + due) else 0.0
        if legacy:
            super(SaleContractV2, legacy)._compute_balances()

    # ==================================================================
    # Phase 24 — snapshot from the reservation
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        contracts = super().create(vals_list)
        contracts._take_snapshot()
        contracts._sync_primary_party()
        return contracts

    def _take_snapshot(self):
        """Copy the agreed terms from the reservation, or from the unit.

        A contract must never re-derive its price from current master pricing.
        Where a reservation exists it is the source of truth, because that is
        what the buyer actually agreed to.
        """
        for rec in self:
            if rec.snapshot_taken_on:
                continue
            res = rec.reservation_id
            if res and res.snapshot_taken_on:
                rec.write({
                    'snapshot_taken_on': fields.Datetime.now(),
                    'price_book_id': res.price_book_id.id,
                    'snapshot_base_price': res.snapshot_base_price,
                    'snapshot_premium_total': res.snapshot_premium_total,
                    'snapshot_list_price': res.snapshot_list_price,
                    'promotion_id': res.promotion_id.id,
                    'promotion_amount': res.promotion_amount,
                    'discount_amount': res.discount_amount,
                    'payment_plan_id': (rec.payment_plan_id.id
                                        or res.payment_plan_id.id),
                    'payment_plan_version': res.payment_plan_version,
                    'booking_credit': (
                        res.booking_amount_received
                        if res.booking_counts_towards_price else 0.0),
                })
                continue
            prop = rec.property_id
            rec.write({
                'snapshot_taken_on': fields.Datetime.now(),
                'snapshot_base_price': (
                    prop.base_price - prop.premium_total) if prop else 0.0,
                'snapshot_premium_total': prop.premium_total if prop else 0.0,
                'snapshot_list_price': prop.list_price_developer if prop else 0.0,
            })

    def _sync_primary_party(self):
        """Keep `partner_id` and the primary-buyer party row in step."""
        Party = self.env['realestate.sale.contract.party']
        for rec in self:
            if not rec.partner_id:
                continue
            primary = rec.party_ids.filtered(
                lambda p: p.role == 'primary_buyer')
            if not primary:
                Party.create({
                    'contract_id': rec.id,
                    'partner_id': rec.partner_id.id,
                    'role': 'primary_buyer',
                    'share': 100.0,
                })
            elif primary[0].partner_id != rec.partner_id:
                primary[0].partner_id = rec.partner_id

    def write(self, vals):
        res = super().write(vals)
        if 'partner_id' in vals:
            self._sync_primary_party()
        return res

    # ==================================================================
    # Phase 21 — workflow
    # ==================================================================
    def action_submit_for_approval(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft contracts can be submitted."))
            rec.state = 'pending_approval'

    def action_send_for_signature(self):
        for rec in self:
            if rec.state not in ('draft', 'pending_approval'):
                raise UserError(_(
                    "Contract %s cannot be sent for signature from %s."
                ) % (rec.name, rec.state))
            rec.write({
                'state': 'pending_signature',
                'signature_state': 'sent',
                'signature_sent_on': fields.Date.context_today(rec),
            })

    def action_sign(self):
        """Sign the contract and raise the commercial obligations.

        Overrides V1. The old implementation created a bridge sale order,
        appended discount and maintenance lines to it, and invoiced **the whole
        price at once** — while `realestate.sale.installment` offered a second,
        per-installment invoicing path. Both posted. The audit recorded that as
        two competing invoicing architectures on one contract.

        There is now one: signing generates the installment schedule, and each
        installment is invoiced individually when it falls due.
        """
        for rec in self:
            if rec.state not in ('draft', 'pending_approval', 'pending_signature'):
                raise UserError(_(
                    "Contract %s cannot be signed from %s."
                ) % (rec.name, rec.state))
            if not rec.property_id:
                raise UserError(_("Contract %s has no unit.") % rec.name)
            if not rec.sale_price:
                raise UserError(_("Contract %s has no price.") % rec.name)
            rec._check_unit_free_to_sign()

            rec._take_snapshot()
            rec.write({
                'state': 'signed',
                'signature_state': 'signed',
                'signing_date': rec.signing_date or fields.Date.context_today(rec),
            })
            rec._generate_installments()
            rec.property_id.commercial_status = 'contracted'
            if rec.reservation_id and rec.reservation_id.state != 'confirmed':
                rec.reservation_id.state = 'confirmed'
        return True

    def _deal_reservations(self):
        """The reservations this contract was made from.

        V2 links a contract to its reservation through ``reservation_id``; the
        V1 "Create Sale Contract" button also writes ``sale_contract_id`` on
        the reservation. Either link makes the reservation this deal's own.
        """
        self.ensure_one()
        return self.reservation_id | self.env[
            'realestate.unit.reservation'].sudo().search(
                [('sale_contract_id', '=', self.id)])

    def _check_unit_free_to_sign(self):
        """Refuse to sign a unit that another deal holds or has bought.

        Signing is where a unit becomes contracted, and it used to check
        nothing: a direct contract was signed on a unit held for another buyer,
        and both deals stood. The unit must be free of every live commitment
        except this deal's own reservation.
        """
        self.ensure_one()
        prop = self.property_id
        own = self._deal_reservations()
        other_hold = self.env['realestate.unit.reservation'].sudo().search([
            ('property_id', '=', prop.id),
            ('state', 'in', LIVE_RESERVATION_STATES),
            ('id', 'not in', own.ids),
        ], limit=1)
        if other_hold:
            raise UserError(_(
                "Contract %(contract)s cannot be signed: unit %(unit)s is held "
                "by reservation %(reservation)s for %(buyer)s. Cancel that "
                "reservation first, or sign the contract made from it."
            ) % {'contract': self.name, 'unit': prop.display_name,
                 'reservation': other_hold.name,
                 'buyer': other_hold.partner_id.display_name})
        other_contract = self.sudo().search([
            ('property_id', '=', prop.id),
            ('id', '!=', self.id),
            ('state', 'in', CONTRACT_LIVE),
        ], limit=1)
        if other_contract:
            raise UserError(_(
                "Contract %(contract)s cannot be signed: unit %(unit)s is "
                "already sold on contract %(other)s."
            ) % {'contract': self.name, 'unit': prop.display_name,
                 'other': other_contract.name})
        # A deal made from its own live or converted reservation already
        # passed the availability engine when the unit was held, and that hold
        # is exactly what makes the unit read "committed" now. Anything else
        # has to find the unit on the market today.
        if not own.filtered(
                lambda r: r.state in LIVE_RESERVATION_STATES + ('confirmed',)):
            prop._check_available_for_sale()

    def action_activate(self):
        for rec in self:
            if rec.state != 'signed':
                raise UserError(_(
                    "Only a signed contract can be activated."))
            rec.state = 'active'

    def action_mark_financially_cleared(self):
        """Everything owed has been collected."""
        for rec in self:
            if rec.state not in ('signed', 'active'):
                raise UserError(_(
                    "Contract %s is not live.") % rec.name)
            if rec.collection_state != 'settled':
                raise UserError(_(
                    "Contract %s still has %s outstanding. It cannot be marked "
                    "financially cleared."
                ) % (rec.name, rec.scheduled_amount - sum(
                    rec.installment_ids.mapped('paid_amount'))))
            rec.state = 'financially_cleared'

    def action_handover(self):
        """Preserved for `real_estate_handover`, which calls this by name.

        The handover module filters on `state == 'signed'` and then calls this
        method. Both must keep working exactly as they did, so the accepted
        source states are widened rather than narrowed.
        """
        for rec in self:
            if rec.state not in ('signed', 'active', 'financially_cleared'):
                raise UserError(_(
                    "Only a live contract can be handed over. Contract %s is %s."
                ) % (rec.name, rec.state))
            rec.write({
                'state': 'handed_over',
                'handover_date': rec.handover_date or fields.Date.context_today(rec),
            })
            if rec.property_id and rec.partner_id:
                rec.property_id.owner_id = rec.partner_id
                rec.property_id.commercial_status = 'sold'
        return True

    def action_cancel(self):
        """The V1 Cancel button, kept for contracts that commit nothing yet.

        V1 only flipped the state. On a signed contract that left every
        instalment live, so the auto-invoice cron went on billing a cancelled
        deal, and no penalty, forfeiture or amendment was recorded. A live
        contract is cancelled through the Cancel Contract flow
        (``_apply_cancellation``), which settles the schedule; this refuses and
        says so.
        """
        live = self.filtered(
            lambda c: c.state in ('signed', 'active', 'financially_cleared'))
        if live:
            raise UserError(_(
                "Contract %s is %s. Use Cancel Contract, which cancels the "
                "open instalments and records the penalty, forfeiture and "
                "refund."
            ) % (live[0].name, dict(self._fields['state'].selection).get(
                live[0].state, live[0].state)))
        res = super().action_cancel()
        # A draft made from a booking kept the unit committed through its
        # reservation; with the contract gone that commitment ends.
        for rec in self:
            rec._deal_reservations()._apply_commercial_status()
        return res

    # ==================================================================
    # Phase 25 — the installment engine
    # ==================================================================
    def _schedule_rows(self):
        """The schedule this contract should raise obligations from.

        The reservation's frozen schedule wins over regenerating from the plan:
        it is what the buyer agreed to, and the plan may have been versioned
        since.
        """
        self.ensure_one()
        res = self.reservation_id
        if res and res.schedule_line_ids:
            return [{
                'sequence': line.sequence,
                'kind': line.kind,
                'name': line.name,
                'percent': line.percent,
                'amount': line.amount,
                'date_due': line.date_due,
            } for line in res.schedule_line_ids.sorted(
                lambda l: (l.date_due, l.sequence))]

        plan = self.payment_plan_id
        if not plan:
            return []
        rows = plan._generate_schedule(
            total_price=self.sale_price,
            booking_date=self.contract_date or fields.Date.context_today(self),
            contract_date=self.contract_date,
            handover_date=(self.expected_handover_date
                           or (self.project_id.expected_handover_date
                               if self.project_id else False)),
        )
        plan._validate_schedule_total(rows, self.sale_price)
        return rows

    def _generate_installments(self):
        """Create the commercial obligations. Idempotent by construction."""
        Installment = self.env['realestate.sale.installment']
        for rec in self:
            if rec.installment_ids.filtered(lambda i: i.state != 'cancelled'):
                # Already raised. Re-running must not double the schedule.
                continue
            rows = rec._schedule_rows()
            if not rows:
                raise UserError(_(
                    "Contract %s has no payment plan and no reservation "
                    "schedule, so no instalments can be raised. Set a payment "
                    "plan before signing."
                ) % rec.name)
            Installment.create([{
                'sale_contract_id': rec.id,
                'sequence': row['sequence'],
                'kind': rec._map_kind(row['kind']),
                'description': row['name'],
                'percent': row['percent'],
                'original_amount': row['amount'],
                'date_due': row['date_due'],
            } for row in rows])
            rec.message_post(body=_(
                "%s instalment(s) raised, totalling %s."
            ) % (len(rows), sum(r['amount'] for r in rows)))
        return True

    @staticmethod
    def _map_kind(plan_kind):
        """Plan vocabulary → the installment vocabulary Checks already knows.

        `realestate.sale.installment.kind` was `down / installment / balloon`
        and `real_estate_checks` groups on it, so the plan's richer kinds are
        mapped onto values that already existed wherever possible.
        """
        return {
            'booking': 'down',
            'down_payment': 'down',
            'installment': 'installment',
            'balloon': 'balloon',
            'handover': 'handover',
            'maintenance': 'maintenance',
            'service': 'service',
            'custom': 'installment',
        }.get(plan_kind, 'installment')

    def action_view_installments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Instalments — %s') % self.name,
            'res_model': 'realestate.sale.installment',
            'views': [(False, 'list'), (False, 'form')],
            'view_mode': 'list,form',
            'domain': [('sale_contract_id', '=', self.id)],
            'context': {'default_sale_contract_id': self.id},
            'target': 'current',
        }

    # ==================================================================
    # Compatibility: the V1 whole-price invoice path
    # ==================================================================
    def _create_sale_and_invoice(self):
        """Neutralised — the second invoicing path is gone.

        V1 raised one invoice for the entire sale price and let an
        `account.payment.term` split its due dates, while the installment model
        offered a per-installment invoice for the same money. Nothing prevented
        both from running, and both posted to the ledger.

        The method is kept rather than deleted because it is referenced by V1's
        `action_sign`, which older data and any customisation may still call.
        It now does nothing and says why.
        """
        self.ensure_one()
        return False


class SaleContractParty(models.Model):
    """Phase 22 — who is actually buying.

    `partner_id` remains the primary buyer and is what every downstream module
    reads. This adds the others without duplicating `res.partner`.
    """
    _name = 'realestate.sale.contract.party'
    _description = 'Sale Contract Party'
    _order = 'contract_id, sequence, id'
    _check_company_auto = True

    contract_id = fields.Many2one(
        'realestate.sale.contract', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True, readonly=True)
    sequence = fields.Integer(default=10)
    partner_id = fields.Many2one(
        'res.partner', string='Party', required=True, ondelete='restrict')
    role = fields.Selection(PARTY_ROLE, required=True, default='co_buyer')
    share = fields.Float(
        string='Share %', default=0.0,
        help="Ownership share. Only the buying roles need to add up to 100%.")
    is_signatory = fields.Boolean(default=True)
    id_document = fields.Char(string='ID / Passport')
    notes = fields.Char()

    _sql_constraints = [
        ('party_uniq', 'UNIQUE(contract_id, partner_id, role)',
         'This party already holds that role on the contract.'),
    ]

    @api.constrains('role', 'contract_id')
    def _check_single_primary(self):
        for party in self:
            if party.role != 'primary_buyer':
                continue
            others = self.search_count([
                ('contract_id', '=', party.contract_id.id),
                ('role', '=', 'primary_buyer'),
                ('id', '!=', party.id),
            ])
            if others:
                raise ValidationError(_(
                    "Contract %s already has a primary buyer. Add the other "
                    "party as a co-buyer."
                ) % party.contract_id.name)

    @api.constrains('share')
    def _check_share(self):
        for party in self:
            if party.share < 0 or party.share > 100:
                raise ValidationError(_(
                    "A party's share must be between 0 and 100%."))


class SaleContractPropertyLine(models.Model):
    """Phase 23 — apartment plus parking plus storage, on one contract.

    Additive on purpose. `contract.property_id` stays required and stays the
    primary unit, because Checks, Handover, the API and the Portal all read it;
    the audit confirmed each. Accessory assets live here, so no downstream
    module has to learn a new shape.
    """
    _name = 'realestate.sale.contract.property.line'
    _description = 'Sale Contract Property Line'
    _order = 'contract_id, sequence, id'
    _check_company_auto = True

    contract_id = fields.Many2one(
        'realestate.sale.contract', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='contract_id.currency_id', store=True, readonly=True)
    sequence = fields.Integer(default=10)
    property_id = fields.Many2one(
        'realestate.property', string='Unit', required=True,
        ondelete='restrict', index=True, check_company=True)
    is_primary = fields.Boolean(
        string='Primary Unit',
        help="Mirrors contract.property_id. Exactly one line is primary.")
    amount = fields.Monetary(string='Allocated Price')
    notes = fields.Char()

    _sql_constraints = [
        ('contract_property_uniq', 'UNIQUE(contract_id, property_id)',
         'That unit is already on this contract.'),
    ]

    @api.constrains('is_primary', 'contract_id')
    def _check_single_primary(self):
        for line in self:
            if not line.is_primary:
                continue
            others = self.search_count([
                ('contract_id', '=', line.contract_id.id),
                ('is_primary', '=', True),
                ('id', '!=', line.id),
            ])
            if others:
                raise ValidationError(_(
                    "Contract %s already has a primary unit."
                ) % line.contract_id.name)
