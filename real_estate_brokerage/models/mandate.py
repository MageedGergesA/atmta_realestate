# -*- coding: utf-8 -*-
"""M4 — the owner mandate.

A brokerage markets property it does not own. The document that says it may is
the mandate, and Brokerage 0.1 had none at all: any agent could create a listing
for anybody's property and advertise it, with no record of who authorised it, on
what terms, until when, or at what commission.

### What a mandate is

An **instruction from an owner**, with a type that determines what the agency
may do:

| Type | Meaning |
|---|---|
| `exclusive` | only this agency may market it, and it earns on any sale |
| `sole_agency` | only this agency may market it; the owner may still sell privately |
| `open` | the owner has instructed several agencies; first to sell earns |
| `co_brokerage` | shared with a named partner agency on agreed terms |
| `internal_developer` | ATMTA's own inventory — the "owner" is the company |

### Signed terms are not editable

Once a mandate is `active` its commercial terms are frozen. Changing an agreed
commission rate or asking floor after signature is not an edit, it is a
different agreement — so it requires a new mandate, and the old one stays on
record. That is the whole reason the model exists.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

MANDATE_TYPE = [
    ('exclusive', 'Exclusive'),
    ('sole_agency', 'Sole Agency'),
    ('open', 'Open / Multiple Agency'),
    ('co_brokerage', 'Co-brokerage'),
    ('internal_developer', 'Internal (Developer Inventory)'),
]

MANDATE_STATE = [
    ('draft', 'Draft'),
    ('pending_signature', 'Pending Signature'),
    ('active', 'Active'),
    ('expired', 'Expired'),
    ('terminated', 'Terminated'),
    ('cancelled', 'Cancelled'),
]

#: Terms that define the agreement. Frozen once signed.
SIGNED_TERMS = {
    'mandate_type', 'owner_partner_id', 'asking_price', 'minimum_price',
    'commission_basis', 'commission_percentage', 'commission_fixed',
    'date_start', 'date_end', 'authorized_marketing', 'negotiation_authority',
    'listing_id', 'company_id', 'currency_id',
}


class ListingMandate(models.Model):
    _name = 'realestate.listing.mandate'
    _description = 'Listing Owner Mandate'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index=True)
    listing_id = fields.Many2one(
        'realestate.listing', string='Listing', required=True, index=True,
        ondelete='cascade', check_company=True, tracking=True)
    property_id = fields.Many2one(
        related='listing_id.property_id', store=True, readonly=True,
        string='Property')
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)

    owner_partner_id = fields.Many2one(
        'res.partner', string='Owner', required=True, index=True,
        tracking=True,
        help="Who instructed us. For internal Developer inventory this is the "
             "company itself.")
    mandate_type = fields.Selection(
        MANDATE_TYPE, string='Type', required=True, default='open',
        tracking=True, index=True)

    # ------------------------------------------------------------------
    # Terms
    # ------------------------------------------------------------------
    date_start = fields.Date(
        string='Valid From', required=True,
        default=fields.Date.context_today, tracking=True)
    date_end = fields.Date(string='Valid Until', tracking=True, index=True)
    asking_price = fields.Monetary(string='Asking Price', tracking=True)
    minimum_price = fields.Monetary(
        string='Minimum Acceptable', tracking=True,
        groups='real_estate_brokerage.group_realestate_sales_manager',
        help="The owner's floor. Confidential: an external broker who learns "
             "it has been handed the entire negotiating margin.")

    commission_basis = fields.Selection([
        ('percentage', 'Percentage of Sale Price'),
        ('fixed', 'Fixed Amount'),
    ], string='Commission Basis', default='percentage', required=True,
        tracking=True)
    commission_percentage = fields.Float(string='Commission %', tracking=True)
    commission_fixed = fields.Monetary(string='Commission Amount',
                                       tracking=True)

    authorized_marketing = fields.Boolean(
        string='Marketing Authorised', default=True,
        help="Whether we may advertise publicly. Some owners instruct quietly "
             "and an unauthorised listing is a breach of the mandate.")
    negotiation_authority = fields.Selection([
        ('none', 'Refer Every Offer'),
        ('above_minimum', 'May Accept At or Above the Minimum'),
        ('full', 'Full Authority'),
    ], string='Negotiation Authority', default='none', required=True,
        help="How far the agency may go without going back to the owner.")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    state = fields.Selection(
        MANDATE_STATE, default='draft', required=True, tracking=True,
        index=True, copy=False)
    signed_on = fields.Date(readonly=True, copy=False, tracking=True)
    signed_by_id = fields.Many2one(
        'res.users', string='Countersigned By', readonly=True, copy=False)
    terminated_on = fields.Date(readonly=True, copy=False)
    termination_reason = fields.Char(readonly=True, copy=False)

    document_ids = fields.Many2many(
        'ir.attachment', 'realestate_mandate_attachment_rel', 'mandate_id',
        'attachment_id', string='Signed Documents')
    notes = fields.Html()

    days_to_expiry = fields.Integer(compute='_compute_expiry')
    is_expiring_soon = fields.Boolean(compute='_compute_expiry',
                                      search='_search_expiring_soon')

    _sql_constraints = [
        ('mandate_commission_positive',
         'CHECK (commission_percentage >= 0 AND commission_fixed >= 0)',
         'Commission cannot be negative.'),
    ]

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('date_end', 'state')
    def _compute_expiry(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.date_end or rec.state != 'active':
                rec.days_to_expiry = 0
                rec.is_expiring_soon = False
                continue
            rec.days_to_expiry = (rec.date_end - today).days
            rec.is_expiring_soon = 0 <= rec.days_to_expiry <= 30

    def _search_expiring_soon(self, operator, value):
        if operator not in ('=', '!='):
            raise UserError(_("Unsupported operator on 'Expiring Soon'."))
        today = fields.Date.context_today(self)
        horizon = fields.Date.add(today, days=30)
        soon = [('state', '=', 'active'), ('date_end', '>=', today),
                ('date_end', '<=', horizon)]
        want = bool(value) if operator == '=' else not bool(value)
        return soon if want else ['!'] + soon

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_end and rec.date_end < rec.date_start:
                raise ValidationError(_(
                    "Mandate %s expires before it begins.") % rec.name)

    @api.constrains('state', 'listing_id', 'mandate_type')
    def _check_one_active_exclusive(self):
        """Two live exclusives on one property is a contradiction in terms."""
        for rec in self:
            if rec.state != 'active' or rec.mandate_type not in (
                    'exclusive', 'sole_agency'):
                continue
            other = self.search([
                ('listing_id', '=', rec.listing_id.id),
                ('id', '!=', rec.id),
                ('state', '=', 'active'),
                ('mandate_type', 'in', ('exclusive', 'sole_agency')),
            ], limit=1)
            if other:
                raise ValidationError(_(
                    "Listing %(listing)s already has an active exclusive "
                    "mandate (%(other)s). A property cannot be exclusively "
                    "instructed to two parties at once.",
                    listing=rec.listing_id.display_name, other=other.name))

    @api.constrains('minimum_price', 'asking_price')
    def _check_minimum_below_asking(self):
        for rec in self:
            if (rec.minimum_price and rec.asking_price
                    and rec.minimum_price > rec.asking_price):
                raise ValidationError(_(
                    "Mandate %s sets a minimum above the asking price.")
                    % rec.name)

    # ------------------------------------------------------------------
    # Create / write
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                company_id = vals.get('company_id') or self.env.company.id
                vals['name'] = self.env['ir.sequence'].with_company(
                    company_id).next_by_code(
                        'realestate.listing.mandate') or 'MND/NEW'
        return super().create(vals_list)

    def write(self, vals):
        """Signed terms are frozen.

        M4 is explicit: *do not silently overwrite signed mandate terms.* An
        agreed commission rate that can be edited afterwards is not an
        agreement, and it is exactly how commission disputes start.
        """
        frozen = self.filtered(lambda m: m.state in ('active', 'expired',
                                                     'terminated'))
        touched = SIGNED_TERMS & set(vals)
        if frozen and touched:
            raise UserError(_(
                "Mandate %(name)s has been signed, so its terms are fixed. "
                "You tried to change: %(fields)s.\n\n"
                "Changing agreed terms after signature is a different "
                "agreement, not an edit. Terminate this mandate and record the "
                "new one — the original stays on file, which is what protects "
                "both sides in a commission dispute.",
                name=frozen[0].name, fields=', '.join(sorted(touched))))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Lifecycle actions
    # ------------------------------------------------------------------
    def action_submit_for_signature(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft mandate can be sent for "
                                  "signature."))
            if not rec.asking_price:
                raise UserError(_(
                    "Set the asking price before sending mandate %s for "
                    "signature.") % rec.name)
            rec.state = 'pending_signature'
        return True

    def action_activate(self):
        for rec in self:
            if rec.state not in ('draft', 'pending_signature'):
                raise UserError(_(
                    "Mandate %(name)s is %(state)s and cannot be activated.",
                    name=rec.name, state=rec.state))
            rec.write({
                'state': 'active',
                'signed_on': fields.Date.context_today(rec),
                'signed_by_id': self.env.user.id,
            })
            rec.message_post(body=_(
                "Mandate active. Its commercial terms are now fixed; a change "
                "requires a new mandate."))
        return True

    def action_terminate(self, reason=None):
        for rec in self:
            if rec.state != 'active':
                raise UserError(_(
                    "Only an active mandate can be terminated."))
            rec.write({
                'state': 'terminated',
                'terminated_on': fields.Date.context_today(rec),
                'termination_reason': reason or rec.termination_reason,
            })
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == 'active':
                raise UserError(_(
                    "Mandate %s is signed and active — terminate it rather "
                    "than cancelling, so the record of what was agreed "
                    "survives.") % rec.name)
            rec.state = 'cancelled'
        return True

    # ------------------------------------------------------------------
    # Guards used by the listing
    # ------------------------------------------------------------------
    def _check_still_valid(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        if self.state != 'active':
            raise UserError(_(
                "Mandate %(name)s is %(state)s, so the listing is not "
                "authorised.", name=self.name, state=self.state))
        if self.date_end and self.date_end < today:
            raise UserError(_(
                "Mandate %(name)s expired on %(date)s. Renew it before "
                "marketing the property again.",
                name=self.name, date=self.date_end))
        if not self.authorized_marketing:
            raise UserError(_(
                "Mandate %s does not authorise marketing. The owner instructed "
                "us quietly; advertising would breach the instruction."
            ) % self.name)
        return True

    def _may_accept(self, amount):
        """Whether the agency may accept `amount` without the owner."""
        self.ensure_one()
        if self.negotiation_authority == 'full':
            return True
        if self.negotiation_authority == 'none':
            return False
        floor = self.sudo().minimum_price
        return bool(floor) and amount >= floor

    # ------------------------------------------------------------------
    # Cron (M4 — expiry)
    # ------------------------------------------------------------------
    @api.model
    def _cron_expire_mandates(self):
        """Expire, and flag what is about to.

        Expiry is a state change with a real consequence — the listing loses
        its authorisation — so it is recorded rather than merely computed.
        """
        today = fields.Date.context_today(self)
        expiring = self.search([
            ('state', '=', 'active'),
            ('date_end', '!=', False),
            ('date_end', '<', today),
        ])
        for mandate in expiring:
            mandate.state = 'expired'
            mandate.message_post(body=_(
                "Mandate expired on %s. The listing is no longer "
                "authorised.") % mandate.date_end)
        if expiring:
            expiring.mapped('listing_id').filtered(
                lambda listing: listing.state == 'active'
                and listing.inventory_type == 'external'
            ).write({'publication_state': 'paused'})
        return True
