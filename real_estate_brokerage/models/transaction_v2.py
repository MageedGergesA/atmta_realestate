# -*- coding: utf-8 -*-
"""M12 / M24 — the transaction, and the boundary it kept crossing.

### The defect

`action_close` did this, for every transaction, whatever the inventory was:

```python
rec.property_id.write({'owner_id': rec.buyer_id.id, 'state': 'sold'})
```

For an **external** listing that is right — somebody else's property changed
hands and we brokered it. For **internal** Developer inventory it is Brokerage
reaching into the Developer module and declaring a unit sold, behind the back
of the reservation engine, the release batches, the payment plan, the contract
and the collection schedule. Developer would still believe the unit was
available; its own contract flow would then try to sell it again.

M24 is unambiguous — *"Brokerage must never bypass Developer reservation
logic"* — so an internal transaction now **requires** a Developer contract and
takes its inventory truth from it. Brokerage records the commercial
relationship: who introduced the buyer, who gets paid, what the commission was.
Developer owns the unit.

```
                     ┌── external ──▶ Brokerage owns the sale end-to-end
   transaction ──────┤
                     └── internal ──▶ Developer contract owns the unit,
                                      Brokerage owns the commission
```

### The other thing 0.1 lost

A cancelled transaction freed the listing but said nothing about why, and a
transaction that fell through after the contract was signed was indistinguishable
from one cancelled the same afternoon. Both are now recorded.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

CANCELLATION_REASON = [
    ('buyer_withdrew', 'Buyer Withdrew'),
    ('seller_withdrew', 'Seller Withdrew'),
    ('finance_failed', 'Finance Fell Through'),
    ('survey', 'Survey / Condition'),
    ('title', 'Title or Legal Issue'),
    ('better_offer', 'Owner Took Another Offer'),
    ('duplicate', 'Recorded in Error'),
    ('other', 'Other'),
]


class TransactionV2(models.Model):
    _inherit = 'realestate.transaction'
    _check_company_auto = True

    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)

    inventory_type = fields.Selection(
        related='listing_id.inventory_type', store=True, readonly=True,
        string='Inventory')
    project_id = fields.Many2one(
        related='listing_id.project_id', store=True, readonly=True)

    # ------------------------------------------------------------------
    # M24 — the Developer contract that actually owns an internal unit
    # ------------------------------------------------------------------
    developer_contract_id = fields.Many2one(
        'realestate.sale.contract', string='Developer Contract',
        ondelete='restrict', copy=False, index=True,
        help="For internal inventory the unit is sold by the Developer "
             "module's contract, not by this transaction. Brokerage records "
             "who introduced the buyer and what the commission was; the "
             "reservation, the payment plan and the unit's own state stay "
             "where they belong.")

    crm_lead_id = fields.Many2one(
        'crm.lead', string='Opportunity', index=True, ondelete='set null',
        help="Which opportunity produced this deal — the link that makes "
             "source-to-revenue attribution possible.")

    cancellation_reason_code = fields.Selection(
        CANCELLATION_REASON, string='Cancellation Reason', tracking=True)
    cancelled_after_contract = fields.Boolean(
        string='Fell Through After Signing', readonly=True, copy=False,
        help="A deal lost the afternoon it was recorded and one lost after "
             "contracts were signed are not the same event, and only one of "
             "them costs anybody money.")
    cancelled_on = fields.Date(readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------
    @api.constrains('company_id', 'listing_id')
    def _check_company_matches_listing(self):
        for rec in self.sudo():
            if (rec.listing_id.company_id
                    and rec.listing_id.company_id != rec.company_id):
                raise ValidationError(_(
                    "Transaction %(ref)s is in %(own)s but its listing belongs "
                    "to %(other)s.", ref=rec.name,
                    own=rec.company_id.display_name,
                    other=rec.listing_id.company_id.display_name))

    @api.constrains('sale_price')
    def _check_sale_price(self):
        for rec in self:
            if rec.sale_price <= 0:
                raise ValidationError(_(
                    "A sale price of %s is not a sale.") % rec.sale_price)

    def _check_developer_authority(self):
        """Internal inventory closes through Developer, or not at all."""
        self.ensure_one()
        if self.inventory_type != 'internal':
            return True
        if not self.developer_contract_id:
            raise UserError(_(
                "Unit %(unit)s is Developer inventory in project "
                "%(project)s.\n\n"
                "Closing it here would mark it sold behind the reservation "
                "engine's back — Developer would still believe it was "
                "available and could sell it a second time. Create the "
                "Developer sale contract, then link it on this transaction.",
                unit=self.property_id.display_name,
                project=self.project_id.display_name or _('(none)')))
        contract = self.developer_contract_id
        if contract.property_id != self.property_id:
            raise UserError(_(
                "The linked Developer contract is for %(contract_unit)s, not "
                "%(unit)s.",
                contract_unit=contract.property_id.display_name,
                unit=self.property_id.display_name))
        return True

    # ------------------------------------------------------------------
    # Closing
    # ------------------------------------------------------------------
    def action_close(self):
        """Close, without Brokerage touching Developer inventory."""
        for rec in self:
            rec._check_developer_authority()

        internal = self.filtered(lambda t: t.inventory_type == 'internal')
        external = self - internal

        # External deals keep 0.1's behaviour exactly: Brokerage is the only
        # module with any claim on the property, so it does the honours.
        result = super(TransactionV2, external).action_close() if external \
            else None
        # A sold listing is not advertised, whichever inventory it was. 0.1's
        # close predates publication, so the internal path's unpublish is
        # applied here for external deals too.
        external.listing_id.filtered(
            lambda listing: listing.state == 'sold'
            and listing.publication_state != 'unpublished'
        ).write({'publication_state': 'unpublished'})
        for rec in internal:
            rec._close_internal()
        return result

    def _close_internal(self):
        """Record the commercial close; let Developer own the unit.

        Deliberately does **not** write `property_id.state`, `owner_id`,
        `listing.sold_date` or any sale order. The Developer contract already
        did all of that through its own reservation, payment plan and invoice
        flow, and doing it twice is how two modules end up disagreeing about
        who owns a flat.
        """
        self.ensure_one()
        if self.state not in ('contract_signed', 'deposit_received'):
            raise UserError(_(
                "Transaction %s must have a signed contract before closing."
            ) % self.display_name)
        if self.transaction_type == 'brokerage' and not self.commission_ids:
            raise UserError(_(
                "Add at least one commission line for a brokerage transaction "
                "before closing."))

        self.state = 'closed'
        self._stamp_actual_closing_date()
        # The listing may stop being marketed — that *is* Brokerage's record.
        self.listing_id.write({
            'state': 'sold',
            'sold_date': self.closing_date,
            'final_sale_price': self.sale_price,
            'publication_state': 'unpublished',
        })
        self.message_post(body=_(
            "Closed against Developer contract %s. The unit's state, "
            "ownership and payment schedule remain the Developer module's."
        ) % self.developer_contract_id.display_name)
        return True

    # ------------------------------------------------------------------
    # Cancellation, with a reason
    # ------------------------------------------------------------------
    def action_cancel(self, reason=None):
        for rec in self:
            if rec.state == 'closed':
                raise UserError(_(
                    "Closed transactions cannot be cancelled. Record a "
                    "reversal instead."))
            code = reason or rec.cancellation_reason_code
            if not code:
                raise UserError(_(
                    "Cancelling %s needs a reason. A pipeline that loses deals "
                    "for unrecorded reasons cannot be improved."
                ) % rec.display_name)
            rec.write({
                'cancellation_reason_code': code,
                'cancelled_after_contract': rec.state == 'contract_signed',
                'cancelled_on': fields.Date.today(),
            })
        result = super().action_cancel()
        # The accepted offer dies with the deal. Left `accepted`, it survives
        # the listing being re-opened, and the next acceptance leaves two
        # accepted offers on one property. A buyer-side collapse is the buyer
        # withdrawing; anything else is the offer being turned down.
        for rec in self:
            offer = rec.offer_id
            if offer.state != 'accepted':
                continue
            code = rec.cancellation_reason_code
            offer.write({
                'state': 'withdrawn' if code in (
                    'buyer_withdrew', 'finance_failed') else 'rejected',
                'rejection_reason': 'transaction_cancelled',
            })
            offer.message_post(body=_(
                "Transaction %(txn)s was cancelled (%(reason)s).",
                txn=rec.display_name,
                reason=dict(CANCELLATION_REASON).get(code, code)))
        return result

    # ------------------------------------------------------------------
    # Attribution
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """Carry the opportunity across from the offer when it is known."""
        transactions = super().create(vals_list)
        for txn in transactions:
            if not txn.crm_lead_id and txn.offer_id.crm_lead_id:
                txn.crm_lead_id = txn.offer_id.crm_lead_id
        return transactions


class OfferTransactionBridge(models.Model):
    """The offer's own promotion path, held to the same boundary."""
    _inherit = 'realestate.offer'

    def action_create_transaction(self):
        self.ensure_one()
        result = super().action_create_transaction()
        # `super()` may have returned the existing transaction rather than
        # creating one; either way the new record wants the opportunity.
        txn = self.listing_id.transaction_id
        if txn and not txn.crm_lead_id and self.crm_lead_id:
            txn.crm_lead_id = self.crm_lead_id
        return result
