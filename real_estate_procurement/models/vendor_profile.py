# -*- coding: utf-8 -*-
"""M4A — the governance record that hangs off a vendor, and why it is separate.

`res.partner` stays the vendor master (Rule 1). It already holds the name, the
address, the tax identity, the bank details and the commercial relationship,
and every one of those is used by Accounting, Sales and Purchase. What it does
not hold — and should not — is a procurement workflow: onboarding states,
review dates, a responsible buyer, a governance status that means something
only inside Procurement. Putting those on the partner would mean every module
that touches a contact inherits Procurement's state machine.

So one profile per **partner and company**. Per company because the whole
point of M4AA is that a vendor active in Cairo Developments and never assessed
in the Coastal subsidiary is two different governance situations about one
company's supplier.

### What the profile is not

It is not a qualification. `governance_status = active` says the vendor has
been onboarded and is not suspended; it says nothing whatever about which
trades they may be sourced for. That question has its own record, per trade,
with dates — see `realestate.procurement.vendor.qualification`. Conflating the
two is the single most common way this feature is built wrong, so the fields
are named to make the conflation hard to write down.

### Three levels of "allowed", which are genuinely different

```
    PROSPECTIVE       may be considered and assessed
    SOURCING ELIGIBLE may be invited to quote
    AWARD ELIGIBLE    may receive the order
```

A vendor mid-assessment is the first and not the second. A vendor qualified
with a pre-award condition outstanding is the second and not the third. The
distinction is answered per question by the eligibility service's `purpose`
argument, not stored as a third status field that would drift.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .vendor_restriction import RESTRICTION_TYPE

GOVERNANCE_STATUS = [
    ('draft', 'Draft'),
    ('under_review', 'Under Review'),
    ('active', 'Active'),
    ('restricted', 'Restricted'),
    ('suspended', 'Suspended'),
    ('inactive', 'Inactive'),
]

#: Statuses in which the vendor may not be sourced at all, whatever any
#: qualification says.
BLOCKED_STATUSES = ('suspended', 'inactive')


class VendorProfile(models.Model):
    _name = 'realestate.procurement.vendor.profile'
    _description = 'Vendor Governance Profile'
    _inherit = ['mail.thread']
    _rec_names_search = ['partner_id', 'vendor_ref']
    _order = 'partner_id'

    partner_id = fields.Many2one(
        'res.partner', string='Vendor', required=True, index=True,
        ondelete='cascade', tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    vendor_ref = fields.Char(
        string='Vendor Reference', copy=False,
        help="The company's own supplier number, where it keeps one.")
    active = fields.Boolean(default=True)

    governance_status = fields.Selection(
        GOVERNANCE_STATUS, default='draft', required=True, tracking=True,
        readonly=True, copy=False,
        help="Where the vendor stands with Procurement as an organisation. "
             "Never a statement about any particular trade — that is what "
             "qualifications are for.")
    responsible_id = fields.Many2one(
        'res.users', string='Responsible Buyer', tracking=True)
    registration_date = fields.Date(
        string='Registered', default=lambda self: fields.Date.context_today(self))
    first_approved_date = fields.Date(readonly=True, copy=False)
    last_reviewed_date = fields.Date(readonly=True, copy=False)
    next_review_date = fields.Date(
        tracking=True,
        help="When this vendor's governance is due to be looked at again. "
             "Independent of any single qualification's expiry.")
    notes = fields.Html()

    qualification_ids = fields.One2many(
        'realestate.procurement.vendor.qualification', 'profile_id',
        string='Assessments')
    qualification_count = fields.Integer(compute='_compute_qualifications')
    current_qualification_ids = fields.Many2many(
        'realestate.procurement.vendor.qualification',
        relation='procurement_profile_current_qualification_rel',
        column1='profile_id', column2='qualification_id',
        compute='_compute_qualifications', string='Current Qualifications')
    qualified_category_ids = fields.Many2many(
        'realestate.procurement.vendor.category',
        relation='procurement_profile_trade_rel',
        column1='profile_id', column2='trade_id',
        compute='_compute_qualifications', string='Qualified Trades',
        help="Derived from approved qualifications that are valid today. "
             "Read-only on purpose: a trade list somebody could tick would "
             "be a second, unevidenced answer to the same question.")

    restriction_ids = fields.One2many(
        'realestate.procurement.vendor.restriction', 'profile_id',
        string='Restrictions')
    active_restriction_ids = fields.Many2many(
        'realestate.procurement.vendor.restriction',
        relation='procurement_profile_active_restriction_rel',
        column1='profile_id', column2='restriction_id',
        compute='_compute_restrictions', string='Active Restrictions')
    is_restricted = fields.Boolean(compute='_compute_restrictions')
    restriction_summary = fields.Char(compute='_compute_restrictions')

    # -- M4T, kept deliberately small ---------------------------------
    potential_related_party = fields.Boolean(
        tracking=True,
        help="Flagged for a conflict-of-interest look. A marker for the "
             "people who decide, not a workflow — M26 owns that.")
    conflict_review_required = fields.Boolean(tracking=True)
    conflict_review_complete = fields.Boolean(tracking=True, readonly=True,
                                              copy=False)
    conflict_review_note = fields.Text()

    # -- M4S ------------------------------------------------------------
    duplicate_warning = fields.Char(
        compute='_compute_duplicate_warning',
        help="Exact matches on identifiers this database already holds. No "
             "fuzzy matching and no automatic merging — both belong to "
             "whoever owns the partner master.")

    _sql_constraints = [
        ('partner_company_uniq', 'unique(partner_id, company_id)',
         'A vendor has one governance profile per company.'),
    ]

    @api.depends('partner_id', 'company_id')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = '%s [%s]' % (
                rec.partner_id.display_name or _('Vendor'),
                rec.company_id.name)

    @api.depends('qualification_ids.state', 'qualification_ids.is_current',
                 'qualification_ids.expiry_date',
                 'qualification_ids.result')
    def _compute_qualifications(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.qualification_count = len(rec.qualification_ids)
            current = rec.qualification_ids.filtered(
                lambda q: q.is_current and q.state == 'approved'
                and q.effective_date <= today
                and (not q.expiry_date or q.expiry_date >= today))
            rec.current_qualification_ids = current
            rec.qualified_category_ids = current.filtered(
                lambda q: q.result in ('qualified',
                                       'qualified_with_conditions')
            ).mapped('category_id')

    @api.depends('restriction_ids.state', 'restriction_ids.restriction_type',
                 'restriction_ids.effective_from',
                 'restriction_ids.effective_to')
    def _compute_restrictions(self):
        today = fields.Date.context_today(self)
        labels = dict(RESTRICTION_TYPE)
        for rec in self:
            active = rec.restriction_ids.filtered(
                lambda r: r.restriction_type and r._applies_on(today))
            rec.active_restriction_ids = active
            rec.is_restricted = bool(active)
            rec.restriction_summary = ', '.join(
                labels.get(restriction.restriction_type, '')
                for restriction in active)

    @api.depends('partner_id.vat', 'partner_id.email')
    def _compute_duplicate_warning(self):
        Partner = self.env['res.partner']
        for rec in self:
            partner = rec.partner_id
            clashes = Partner.browse()
            if partner.vat:
                clashes |= Partner.search([('vat', '=', partner.vat),
                                           ('id', '!=', partner.id)], limit=5)
            if partner.email:
                clashes |= Partner.search([('email', '=ilike', partner.email),
                                           ('id', '!=', partner.id)], limit=5)
            rec.duplicate_warning = _(
                "Same tax number or email as: %s") % ', '.join(
                    clashes.mapped('display_name')) if clashes else False

    # ------------------------------------------------------------------
    @api.model
    def _get_or_create(self, partner, company):
        """The profile for this vendor in this company, made if absent.

        Called when a qualification or restriction is raised. Nothing creates
        profiles in bulk on upgrade day: a database with 4,000 legacy
        suppliers would gain 4,000 empty governance records saying nothing,
        and the migration's job is to classify the population, not to invent
        governance for it.
        """
        if not partner:
            return self.browse()
        company = company or self.env.company
        existing = self.sudo().search([
            ('partner_id', '=', partner.id),
            ('company_id', '=', company.id),
        ], limit=1)
        if existing:
            return existing.with_env(self.env)
        return self.sudo().create({
            'partner_id': partner.id,
            'company_id': company.id,
        }).with_env(self.env)

    def _on_qualification_approved(self, qualification):
        """A first approval onboards the vendor; later ones only date-stamp."""
        for rec in self:
            values = {'last_reviewed_date': qualification.approved_date
                      or fields.Date.context_today(rec)}
            if not rec.first_approved_date:
                values['first_approved_date'] = values['last_reviewed_date']
            if rec.governance_status in ('draft', 'under_review'):
                values['governance_status'] = 'active'
            rec.write(values)

    # ------------------------------------------------------------------
    def action_start_review(self):
        for rec in self:
            if rec.governance_status != 'draft':
                raise UserError(_(
                    "%s is not in draft.") % rec.display_name)
            rec.governance_status = 'under_review'

    def action_activate(self):
        for rec in self:
            if rec.governance_status in BLOCKED_STATUSES:
                raise UserError(_(
                    "%s is %s. Lift the restriction that put it there rather "
                    "than overwriting the status — otherwise the reason "
                    "disappears.", rec.display_name,
                    dict(GOVERNANCE_STATUS)[rec.governance_status]))
            rec.governance_status = 'active'

    def action_deactivate(self):
        self.write({'governance_status': 'inactive'})

    def action_mark_conflict_reviewed(self):
        self.write({'conflict_review_complete': True,
                    'conflict_review_required': False})

    def action_view_qualifications(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Qualifications'),
            'res_model': 'realestate.procurement.vendor.qualification',
            'view_mode': 'list,form',
            'domain': [('profile_id', '=', self.id)],
            'context': {'default_partner_id': self.partner_id.id,
                        'default_company_id': self.company_id.id},
        }

    def _sync_status_from_restrictions(self):
        """Reflect restrictions in the headline status, without losing it.

        The status is derived here and nowhere else, so a lifted suspension
        returns the vendor to whatever they were rather than to a guess.
        """
        for rec in self:
            active = rec.active_restriction_ids
            if active.filtered(lambda r: r.restriction_type in (
                    'sourcing_suspension', 'debarment', 'temporary_hold')):
                status = 'suspended'
            elif active:
                status = 'restricted'
            elif rec.governance_status in ('suspended', 'restricted'):
                status = 'active' if rec.first_approved_date else 'under_review'
            else:
                continue
            rec.governance_status = status
