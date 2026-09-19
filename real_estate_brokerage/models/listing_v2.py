# -*- coding: utf-8 -*-
"""M3 / M21 — the listing, made explicit about what it is selling.

Brokerage 0.1's listing knew one thing: a property and a price. It could not
express the single most important distinction in a brokerage business — whether
the thing being sold belongs to the company or to somebody else — and it had no
company of its own, so a Company A property could be listed, offered on and
transacted by Company B's agent with nothing to object.

### Internal versus external

| | Internal | External |
|---|---|---|
| The unit | ATMTA Developer inventory | a third party's property |
| Availability | **Developer's** engine decides | the mandate decides |
| Price authority | Developer pricing | the owner, via the mandate |
| Reservation | Developer's, always | this module's transaction |

No second physical property master is created for internal inventory: it is the
same `realestate.property` Developer already owns, which is Rule 1 of the whole
suite.

### The availability defect this fixes

`action_activate` called Module 1's `_check_available_for_new_sale()`, which
only knows `sold` / `maintenance` / `inactive`. Developer's own
`_check_available_for_sale()` additionally enforces released, in-window, not
blocked, project and phase selling, and not already committed. Developer's
source even records the gap:

> *"Module 1 is frozen and is also used by Brokerage, so the richer check lives
> here."*

Nothing ever called it. So an unreleased or already-reserved Developer unit
could be listed and marketed. Fixed here, inside Brokerage — no frozen module
is touched.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

LISTING_INVENTORY = [
    ('internal', 'Internal (Developer Inventory)'),
    ('external', 'External (Third-party Owner)'),
]

LISTING_DEAL = [
    ('sale', 'For Sale'),
    ('rent', 'For Rent'),
]

LISTING_PUBLICATION = [
    ('unpublished', 'Not Published'),
    ('internal_only', 'Internal Only'),
    ('published', 'Published'),
    ('paused', 'Paused'),
]


class ListingV2(models.Model):
    _inherit = 'realestate.listing'
    _check_company_auto = True

    # ------------------------------------------------------------------
    # M21 — a listing belongs to a company
    # ------------------------------------------------------------------
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company,
        help="0.1 had no company on any Brokerage model at all, so every "
             "listing was global.")

    # ------------------------------------------------------------------
    # What is being sold, and whose it is
    # ------------------------------------------------------------------
    inventory_type = fields.Selection(
        LISTING_INVENTORY, string='Inventory', required=True,
        default='external', tracking=True, index=True,
        help="Internal listings are Developer inventory and obey Developer's "
             "availability, pricing and reservation authority. External "
             "listings are somebody else's property and obey their mandate.\n\n"
             "Derived from the property — a unit inside a Developer project is "
             "internal — and overridable, because a developer does "
             "occasionally broker a unit it has already sold.")
    deal_type = fields.Selection(
        LISTING_DEAL, string='Deal', required=True, default='sale',
        tracking=True, index=True)

    owner_partner_id = fields.Many2one(
        'res.partner', string='Owner', tracking=True, index=True,
        help="The external owner who instructed us. Required for an external "
             "listing; for internal inventory the owner is the company.\n\n"
             "Defaults to the property's registered owner, which is who a "
             "resale instruction actually comes from.")

    project_id = fields.Many2one(
        related='property_id.project_id', store=True, index=True,
        readonly=True, string='Project',
        help="Drives team project scoping (M20) and the matching engine.")

    # ------------------------------------------------------------------
    # Price authority (M22 — the confidential floor is not for brokers)
    # ------------------------------------------------------------------
    minimum_price = fields.Monetary(
        string='Confidential Minimum', tracking=True,
        groups='real_estate_brokerage.group_realestate_sales_manager',
        help="The lowest price the owner will accept. Never shown to an "
             "external broker or a junior agent — it is the seller's "
             "negotiating floor, and disclosing it gives the whole margin "
             "away.")
    commission_basis = fields.Selection([
        ('percentage', 'Percentage of Price'),
        ('fixed', 'Fixed Amount'),
        ('mandate', 'Per the Mandate'),
    ], string='Commission Basis', default='mandate')
    commission_percentage = fields.Float(string='Commission %')
    commission_fixed = fields.Monetary(string='Commission Amount')

    # ------------------------------------------------------------------
    # Marketing and exclusivity
    # ------------------------------------------------------------------
    publication_state = fields.Selection(
        LISTING_PUBLICATION, string='Publication', default='unpublished',
        tracking=True,
        help="Where the listing may be advertised. Separate from its "
             "lifecycle state: a listing can be commercially active and "
             "deliberately unadvertised.")
    is_exclusive = fields.Boolean(
        string='Exclusive', compute='_compute_from_mandate', store=True,
        help="Derived from the active mandate. Exclusivity is a contractual "
             "fact, not a checkbox somebody can tick.")
    available_from = fields.Date(string='Available From')

    # ------------------------------------------------------------------
    # Mandate (M4)
    # ------------------------------------------------------------------
    mandate_ids = fields.One2many(
        'realestate.listing.mandate', 'listing_id', string='Mandates')
    active_mandate_id = fields.Many2one(
        'realestate.listing.mandate', string='Active Mandate',
        compute='_compute_from_mandate', store=True)
    mandate_expiry_date = fields.Date(
        related='active_mandate_id.date_end', store=True, readonly=True,
        string='Mandate Expires')

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('mandate_ids.state', 'mandate_ids.mandate_type',
                 'mandate_ids.date_end')
    def _compute_from_mandate(self):
        for rec in self:
            active = rec.mandate_ids.filtered(lambda m: m.state == 'active')[:1]
            rec.active_mandate_id = active
            rec.is_exclusive = bool(
                active and active.mandate_type in ('exclusive', 'sole_agency'))

    # ------------------------------------------------------------------
    # Create — derive what the caller did not say
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """Read the inventory type off the property when it is not given.

        Done at create rather than as a stored compute. A required stored
        compute added to a table that already has rows has to back-fill every
        one of them during the upgrade, and it would also fight anybody who
        deliberately sets the value — a developer does occasionally broker a
        unit it has already sold, and that choice must stick.
        """
        Property = self.env['realestate.property']
        for vals in vals_list:
            prop = Property.browse(vals.get('property_id')) \
                if vals.get('property_id') else Property.browse()
            if not vals.get('inventory_type'):
                vals['inventory_type'] = (
                    'internal' if prop and prop.project_id else 'external')
            if (vals['inventory_type'] == 'external'
                    and not vals.get('owner_partner_id') and prop.owner_id):
                vals['owner_partner_id'] = prop.owner_id.id
        return super().create(vals_list)

    @api.onchange('property_id')
    def _onchange_property_inventory(self):
        """Same derivation, live, so the form shows it before saving."""
        for rec in self:
            prop = rec.property_id
            if not prop:
                continue
            rec.inventory_type = 'internal' if prop.project_id else 'external'
            if rec.inventory_type == 'external' and not rec.owner_partner_id:
                rec.owner_partner_id = prop.owner_id

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('inventory_type', 'owner_partner_id')
    def _check_owner_for_external(self):
        for rec in self:
            if rec.inventory_type == 'external' and not rec.owner_partner_id:
                raise ValidationError(_(
                    "External listing %s names no owner. A brokerage listing "
                    "without an instructing owner is a listing nobody "
                    "authorised.") % rec.display_name)

    @api.constrains('company_id', 'property_id')
    def _check_company_consistency(self):
        """M21 — a listing may not straddle companies."""
        for rec in self.sudo():
            prop_company = rec.property_id.company_id
            if prop_company and prop_company != rec.company_id:
                raise ValidationError(_(
                    "Listing %(listing)s is in %(own)s but its property "
                    "belongs to %(other)s.",
                    listing=rec.name, own=rec.company_id.display_name,
                    other=prop_company.display_name))

    @api.constrains('minimum_price', 'list_price')
    def _check_minimum_below_asking(self):
        for rec in self:
            if (rec.minimum_price and rec.list_price
                    and rec.minimum_price > rec.list_price):
                raise ValidationError(_(
                    "The confidential minimum is above the asking price on "
                    "%s.") % rec.display_name)

    # ------------------------------------------------------------------
    # Activation — the availability defect, fixed
    # ------------------------------------------------------------------
    def action_activate(self):
        """Refuse to market inventory that is not actually sellable.

        0.1 called Module 1's legacy check, which knows only `sold`,
        `maintenance` and `inactive`. For **internal** inventory the authority
        is Developer's availability engine, and it is asked here.
        """
        for rec in self:
            rec._check_activation_allowed()
        return super().action_activate()

    def _check_activation_allowed(self):
        self.ensure_one()
        if self.inventory_type == 'internal':
            # Developer decides. Its check covers released, in-window, not
            # blocked, project and phase selling, and not already committed —
            # none of which Module 1's legacy check can see.
            self.property_id._check_available_for_sale()
        else:
            mandate = self.active_mandate_id
            if not mandate:
                raise UserError(_(
                    "Listing %s has no active owner mandate. Marketing "
                    "somebody else's property without a signed instruction is "
                    "what the mandate exists to prevent."
                ) % self.display_name)
            mandate._check_still_valid()
        return True

    # ------------------------------------------------------------------
    # Developer authority (M24) — Brokerage never mutates inventory
    # ------------------------------------------------------------------
    def _assert_not_internal(self, what):
        """Brokerage must not perform `what` on Developer inventory itself."""
        self.ensure_one()
        if self.inventory_type != 'internal':
            return True
        raise UserError(_(
            "%(what)s is not Brokerage's to do for Developer inventory. Unit "
            "%(unit)s belongs to project %(project)s, and its availability, "
            "pricing, discounts, payment plan and reservation are the "
            "Developer module's authority.\n\n"
            "Use the Developer reservation flow; Brokerage records the "
            "commercial relationship, not the inventory state.",
            what=what, unit=self.property_id.display_name,
            project=self.project_id.display_name or _('(none)')))

    def action_view_mandates(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Mandates — %s') % self.display_name,
            'res_model': 'realestate.listing.mandate',
            'view_mode': 'list,form',
            'domain': [('listing_id', '=', self.id)],
            'context': {'default_listing_id': self.id,
                        'default_owner_partner_id': self.owner_partner_id.id},
        }
