# -*- coding: utf-8 -*-
"""M11 — negotiation, kept rather than overwritten.

0.1's offer had a single `amount`. A counter-offer changed it. So the sequence

    buyer 900k → seller counters 1.05M → buyer 980k → accepted

left one row reading 980,000, and the entire negotiation — who moved, how far,
how many times, how long each round took — was gone. There is no way to
reconstruct it, and it is the most commercially interesting data a brokerage
produces.

Here every movement appends a **revision**. The offer's `amount` stays the
current position, so nothing that reads it breaks; the history sits beside it.

```
REV 1  buyer      900,000   initial
REV 2  seller   1,050,000   counter        ← seller moved -50,000 off asking
REV 3  buyer      980,000   counter
REV 4  —          980,000   accepted
```

### Two things 0.1 did quietly that it should not have

**It rejected competing offers with nobody's approval.** `action_accept` looped
over the siblings and rejected them. Accepting one offer does end the others —
that part is right — but doing it when a *higher* offer is open, with no reason
recorded and no manager involved, is how a brokerage ends up explaining itself
to an owner. Now the higher offer blocks acceptance until somebody with
authority says why.

**It had no floor.** An owner's mandate names a minimum. 0.1 would happily
accept below it, which is the one thing the mandate exists to prevent.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

REVISION_TYPE = [
    ('initial', 'Initial Offer'),
    ('buyer_counter', 'Buyer Counter'),
    ('seller_counter', 'Seller Counter'),
    ('final', 'Final Position'),
]

APPROVAL_STATE = [
    ('not_required', 'Not Required'),
    ('pending', 'Awaiting Approval'),
    ('approved', 'Approved'),
    ('refused', 'Refused'),
]


class OfferRevision(models.Model):
    """One movement in a negotiation. Append-only, by construction."""
    _name = 'realestate.offer.revision'
    _description = 'Offer Revision'
    _order = 'offer_id, sequence, id'

    offer_id = fields.Many2one(
        'realestate.offer', string='Offer', required=True, index=True,
        ondelete='cascade')
    sequence = fields.Integer(string='Revision', required=True, default=1)
    revision_type = fields.Selection(
        REVISION_TYPE, string='Type', required=True, default='initial')

    amount = fields.Monetary(string='Amount', required=True)
    currency_id = fields.Many2one(
        related='offer_id.currency_id', readonly=True)
    conditions = fields.Text()
    deposit_amount = fields.Monetary()
    proposed_closing_date = fields.Date()
    note = fields.Char(string='Note')

    movement = fields.Monetary(
        string='Movement', compute='_compute_movement',
        help="How far this revision moved from the one before it. The number "
             "an owner asks for when deciding whether to hold.")

    recorded_by_id = fields.Many2one(
        'res.users', string='Recorded By', readonly=True,
        default=lambda self: self.env.user)
    recorded_on = fields.Datetime(
        readonly=True, default=fields.Datetime.now, index=True)

    @api.depends('amount', 'sequence', 'offer_id.revision_ids.amount')
    def _compute_movement(self):
        for rec in self:
            previous = rec.offer_id.revision_ids.filtered(
                lambda r, s=rec.sequence: r.sequence < s).sorted('sequence')[-1:]
            rec.movement = rec.amount - previous.amount if previous else 0.0

    def write(self, vals):
        """History is not edited.

        A `note` may be added after the fact — that is annotation, not
        revision — but the numbers and the type are what happened.
        """
        frozen = {'amount', 'revision_type', 'sequence', 'offer_id',
                  'recorded_by_id', 'recorded_on'}
        touched = frozen.intersection(vals)
        if touched and not self.env.context.get('re_revision_write'):
            raise UserError(_(
                "A negotiation revision records what happened and cannot be "
                "edited (%s). Add another revision instead — a corrected "
                "history is not a history."
            ) % ', '.join(sorted(touched)))
        return super().write(vals)

    def unlink(self):
        raise UserError(_(
            "Negotiation revisions cannot be deleted. The point of the record "
            "is that it is complete."))


class OfferV2(models.Model):
    _inherit = 'realestate.offer'
    _check_company_auto = True

    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)

    # ------------------------------------------------------------------
    # Negotiation history
    # ------------------------------------------------------------------
    revision_ids = fields.One2many(
        'realestate.offer.revision', 'offer_id', string='Negotiation History',
        readonly=True)
    revision_count = fields.Integer(compute='_compute_negotiation')
    initial_amount = fields.Monetary(
        string='Opening Offer', compute='_compute_negotiation', store=True,
        help="Where the buyer started. The gap between this and the final "
             "amount is the negotiation, and it is worth reporting on.")
    total_movement = fields.Monetary(
        string='Total Movement', compute='_compute_negotiation', store=True)

    @api.depends('revision_ids.amount', 'revision_ids.sequence', 'amount')
    def _compute_negotiation(self):
        for rec in self:
            revisions = rec.revision_ids.sorted('sequence')
            rec.revision_count = len(revisions)
            rec.initial_amount = revisions[:1].amount or rec.amount
            rec.total_movement = (rec.amount - rec.initial_amount
                                  if revisions else 0.0)

    # ------------------------------------------------------------------
    # Competing offers (M12)
    # ------------------------------------------------------------------
    competing_offer_ids = fields.Many2many(
        'realestate.offer', compute='_compute_competition',
        string='Competing Offers')
    competing_offer_count = fields.Integer(compute='_compute_competition')
    is_best_offer = fields.Boolean(
        compute='_compute_competition', string='Highest Open Offer')
    best_competing_amount = fields.Monetary(compute='_compute_competition')

    @api.depends('listing_id', 'amount', 'state')
    def _compute_competition(self):
        """One query for the whole set, not one per offer (M29)."""
        listings = self.listing_id
        open_offers = self.search([
            ('listing_id', 'in', listings.ids),
            ('state', 'in', ('submitted', 'countered')),
        ])
        by_listing = {}
        for offer in open_offers:
            by_listing.setdefault(offer.listing_id.id, self.browse())
            by_listing[offer.listing_id.id] |= offer

        for rec in self:
            siblings = by_listing.get(rec.listing_id.id, self.browse()) - rec
            rec.competing_offer_ids = siblings
            rec.competing_offer_count = len(siblings)
            best = max(siblings.mapped('amount') or [0.0])
            rec.best_competing_amount = best
            rec.is_best_offer = rec.amount >= best

    # ------------------------------------------------------------------
    # The owner's floor (M4 ↔ M11)
    # ------------------------------------------------------------------
    floor_price = fields.Monetary(
        string='Floor', compute='_compute_floor',
        groups='real_estate_brokerage.group_realestate_sales_manager',
        help="The lowest price the owner will accept, from the mandate or the "
             "listing's confidential minimum. Manager-visible only — telling a "
             "buyer's agent the floor gives the margin away.")
    below_floor = fields.Boolean(
        compute='_compute_floor', store=True,
        help="Safe to expose: it says an approval is needed without saying "
             "what the number is.")
    approval_state = fields.Selection(
        APPROVAL_STATE, default='not_required', required=True, tracking=True,
        string='Approval')
    approved_by_id = fields.Many2one('res.users', readonly=True)
    approved_on = fields.Datetime(readonly=True)
    approval_note = fields.Char(string='Approval Note')

    @api.depends('amount', 'listing_id.minimum_price',
                 'listing_id.active_mandate_id.minimum_price')
    def _compute_floor(self):
        for rec in self:
            floor = rec.sudo()._re_floor_price()
            rec.floor_price = floor
            rec.below_floor = bool(floor and rec.amount < floor)

    def _re_floor_price(self):
        """The binding floor: the mandate's if there is one, else the listing's.

        The mandate wins because it is the signed instruction from the owner;
        the listing's minimum is this company's own note.
        """
        self.ensure_one()
        mandate = self.listing_id.active_mandate_id
        if mandate and mandate.minimum_price:
            return mandate.minimum_price
        return self.listing_id.minimum_price or 0.0

    # ------------------------------------------------------------------
    # Create — the opening position becomes revision 1
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        offers = super().create(vals_list)
        for offer in offers:
            offer._append_revision('initial', offer.amount,
                                   note=_('Opening offer'))
            if offer._re_needs_approval():
                offer.approval_state = 'pending'
        return offers

    def _re_needs_approval(self):
        """Whether accepting this amount takes somebody more senior.

        Two ways in: the amount is under the owner's stated floor, or the
        mandate's negotiation authority does not stretch to it. They are the
        same decision, so they are one gate rather than two competing ones.
        """
        self.ensure_one()
        mandate = self.listing_id.active_mandate_id.sudo()
        if mandate and mandate.negotiation_authority == 'none':
            # Not an approval question — nobody here may accept at all.
            return False
        return bool(self.below_floor
                    or (mandate and not mandate._may_accept(self.amount)))

    def _append_revision(self, revision_type, amount, note=None):
        self.ensure_one()
        last = self.revision_ids.sorted('sequence')[-1:]
        return self.env['realestate.offer.revision'].create({
            'offer_id': self.id,
            'sequence': (last.sequence + 1) if last else 1,
            'revision_type': revision_type,
            'amount': amount,
            'conditions': self.conditions,
            'deposit_amount': self.deposit_amount,
            'proposed_closing_date': self.proposed_closing_date,
            'note': note,
        })

    # ------------------------------------------------------------------
    # Negotiating
    # ------------------------------------------------------------------
    def action_counter(self, amount, note=None):
        """The seller comes back with a number."""
        self.ensure_one()
        if self.state not in ('submitted', 'countered'):
            raise UserError(_(
                "Only an open offer can be countered; %s is %s."
            ) % (self.display_name, self.state))
        self._append_revision('seller_counter', amount, note=note)
        self.write({'amount': amount, 'state': 'countered'})
        self._refresh_approval()
        return True

    def action_buyer_revise(self, amount, note=None):
        """The buyer moves."""
        self.ensure_one()
        if self.state not in ('submitted', 'countered'):
            raise UserError(_(
                "Only an open offer can be revised; %s is %s."
            ) % (self.display_name, self.state))
        self._append_revision('buyer_counter', amount, note=note)
        self.write({'amount': amount, 'state': 'submitted'})
        self._refresh_approval()
        return True

    def _refresh_approval(self):
        """A movement can cross the floor in either direction."""
        self.ensure_one()
        self.invalidate_recordset(['below_floor', 'floor_price'])
        needed = self._re_needs_approval()
        if needed and self.approval_state in ('not_required', 'approved'):
            # Crossing back under the floor invalidates an approval given for
            # a higher number — that approval was for a different offer.
            self.write({'approval_state': 'pending', 'approved_by_id': False,
                        'approved_on': False})
        elif not needed and self.approval_state == 'pending':
            self.approval_state = 'not_required'

    # ------------------------------------------------------------------
    # Approval
    # ------------------------------------------------------------------
    def action_approve(self, note=None):
        if not self.env.user.has_group(
                'real_estate_brokerage.group_realestate_sales_manager'):
            raise UserError(_(
                "Approving an offer below the owner's floor is a manager's "
                "decision."))
        for rec in self:
            if rec.approval_state != 'pending':
                raise UserError(_(
                    "%s is not awaiting approval.") % rec.display_name)
            rec.write({
                'approval_state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
                'approval_note': note or rec.approval_note,
            })
        return True

    def action_refuse_approval(self, note=None):
        for rec in self:
            rec.write({'approval_state': 'refused',
                       'approval_note': note or rec.approval_note})
        return True

    # ------------------------------------------------------------------
    # Acceptance — the gates 0.1 did not have
    # ------------------------------------------------------------------
    def action_accept(self):
        for rec in self:
            rec._check_acceptable()
        result = super().action_accept()
        for rec in self:
            rec._append_revision('final', rec.amount, note=_('Accepted'))
        return result

    def _check_acceptable(self):
        self.ensure_one()

        mandate = self.listing_id.active_mandate_id
        if not mandate and self.listing_id.inventory_type == 'external':
            # Same rule as activating the listing. When the mandate expires
            # the cron only pauses publication, so the listing can still be
            # `active` with no authority behind it — and without a mandate
            # there is no floor and no negotiation authority to check either.
            raise UserError(_(
                "Listing %s has no active owner mandate. Accepting an offer "
                "on somebody else's property without a signed instruction is "
                "what the mandate exists to prevent. Renew the mandate first."
            ) % self.listing_id.display_name)
        if mandate:
            mandate._check_still_valid()
            if mandate.sudo().negotiation_authority == 'none':
                # No approval substitutes for this one. The owner reserved
                # every acceptance to themselves, and a manager here cannot
                # grant an authority the agency was never given.
                raise UserError(_(
                    "The mandate on %s reserves acceptance to the owner. Refer "
                    "the offer to them; nobody here can accept it."
                ) % self.listing_id.display_name)

        # Below the floor — or outside a limited mandate's authority — one
        # gate, not two. A manager's approval is the record that somebody with
        # the standing to do so went back to the owner and got a yes; without
        # it, the acceptance is the agency quietly discounting the owner's
        # property.
        if self._re_needs_approval() and self.approval_state != 'approved':
            raise UserError(_(
                "Offer %(ref)s is below the owner's minimum, or outside what "
                "the mandate lets us accept, and has not been approved.\n\n"
                "Approving it records that a manager went back to the owner. "
                "Accepting without that is the one thing the mandate exists to "
                "prevent.", ref=self.display_name))

        # Asked as a query, not read off `competing_offer_ids`. That field is
        # a non-stored compute for the form view; a guard that decides whether
        # an owner's best offer gets binned must not depend on whether a cache
        # happens to be fresh.
        higher = self.search([
            ('listing_id', '=', self.listing_id.id),
            ('id', '!=', self.id),
            ('state', 'in', ('submitted', 'countered')),
            ('amount', '>', self.amount),
        ], order='amount desc', limit=1)
        if higher and not self.env.context.get('re_accept_lower_approved'):
            raise UserError(_(
                "There is a higher open offer on %(listing)s "
                "(%(best)s vs %(this)s).\n\n"
                "Accepting the lower one may well be right — a cash buyer with "
                "no chain often beats a bigger number — but it is the owner's "
                "call and it has to be recorded. Use \"Accept Over Higher "
                "Offer\" and give the reason.",
                listing=self.listing_id.display_name,
                best=higher.amount, this=self.amount))
        return True

    def action_accept_over_higher(self, reason):
        """Accept knowing a higher offer is open, on the record."""
        self.ensure_one()
        if not reason:
            raise UserError(_(
                "Accepting below a higher competing offer needs a reason. "
                "\"Cash buyer, no chain, 14-day completion\" is a reason; "
                "silence is not."))
        if not self.env.user.has_group(
                'real_estate_brokerage.group_realestate_sales_manager'):
            raise UserError(_(
                "Accepting over a higher offer is a manager's decision."))
        self.invalidate_recordset(['competing_offer_ids',
                                   'best_competing_amount', 'is_best_offer'])
        self.message_post(body=_(
            "Accepted over a higher open offer of %(best)s. Reason: %(reason)s",
            best=self.best_competing_amount, reason=reason))
        return self.with_context(
            re_accept_lower_approved=True).action_accept()

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('amount')
    def _check_amount_positive(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_(
                    "An offer of %s is not an offer.") % rec.amount)

    @api.constrains('company_id', 'listing_id')
    def _check_company_matches_listing(self):
        for rec in self.sudo():
            if (rec.listing_id.company_id
                    and rec.listing_id.company_id != rec.company_id):
                raise ValidationError(_(
                    "Offer %(ref)s is in %(own)s but its listing belongs to "
                    "%(other)s.", ref=rec.name,
                    own=rec.company_id.display_name,
                    other=rec.listing_id.company_id.display_name))

    def action_view_revisions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Negotiation — %s') % self.display_name,
            'res_model': 'realestate.offer.revision',
            'view_mode': 'list,form',
            'domain': [('offer_id', '=', self.id)],
        }
