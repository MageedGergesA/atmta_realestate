"""Multi-party leases (Phase 7).

A lease is rarely one person. Real cases this model covers:

* a couple where both names are on the lease (tenant + co_tenant)
* a corporate lease where the company signs and named staff occupy
  (company + occupant)
* a lease backed by a guarantor
* an agent signing under power of attorney (authorized_representative)
* an emergency contact who is not a party to the lease at all

``realestate.contract.partner_id`` is preserved as the canonical *primary*
tenant so every downstream module, report and API endpoint keeps working. It is
kept in sync with the party flagged ``is_primary`` in both directions.

Contacts are always ``res.partner`` -- this model never duplicates contact data.
"""

from markupsafe import Markup
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

PARTY_ROLES = [
    ('tenant', 'Tenant'),
    ('co_tenant', 'Co-Tenant'),
    ('occupant', 'Occupant'),
    ('company', 'Company / Corporate Tenant'),
    ('guarantor', 'Guarantor'),
    ('authorized_representative', 'Authorized Representative'),
    ('emergency_contact', 'Emergency Contact'),
    ('other', 'Other'),
]

#: Roles that can legitimately be the billed, primary counterparty.
BILLABLE_ROLES = ('tenant', 'co_tenant', 'company')

#: Guard against recursion between party writes and the contract mirror.
SYNC_CTX = 're_syncing_parties'


class ContractParty(models.Model):
    _name = 'realestate.contract.party'
    _description = 'Lease Party'
    _inherit = ['mail.thread']
    _order = 'contract_id, is_primary desc, sequence, id'
    _rec_name = 'partner_id'

    sequence = fields.Integer(default=10)

    contract_id = fields.Many2one(
        'realestate.contract', string='Lease', required=True,
        ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True, readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Contact', required=True,
        ondelete='restrict', index=True, tracking=True,
    )
    role = fields.Selection(
        PARTY_ROLES, string='Role', required=True, default='tenant',
        tracking=True, index=True,
    )
    is_primary = fields.Boolean(
        string='Primary Tenant', tracking=True,
        help="The billed counterparty. Mirrored to the lease's Tenant field. "
             "Exactly one party per lease may be primary.",
    )
    responsibility_pct = fields.Float(
        string='Responsibility (%)', default=0.0, tracking=True,
        help="Share of the rent obligation this party carries. Informational "
             "unless split invoicing is configured; when any party sets it, "
             "the billable parties must total 100%.",
    )

    start_date = fields.Date(string='From', tracking=True)
    end_date = fields.Date(string='Until', tracking=True)
    is_active_party = fields.Boolean(
        string='Currently Active', compute='_compute_is_active_party',
        search='_search_is_active_party',
        help="Whether today falls within the party's From and Until dates.",
    )

    # Convenience mirrors -- read-only, never stored copies of contact data.
    email = fields.Char(related='partner_id.email', readonly=True)
    phone = fields.Char(related='partner_id.phone', readonly=True)
    mobile = fields.Char(related='partner_id.mobile', readonly=True)

    notes = fields.Text()

    _sql_constraints = [
        ('unique_partner_role_per_contract',
         'unique(contract_id, partner_id, role)',
         'This contact already holds that role on the lease.'),
        ('responsibility_range',
         'CHECK(responsibility_pct >= 0 AND responsibility_pct <= 100)',
         'Responsibility must be between 0 and 100 percent.'),
    ]

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('start_date', 'end_date')
    def _compute_is_active_party(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_active_party = (
                (not rec.start_date or rec.start_date <= today)
                and (not rec.end_date or rec.end_date >= today)
            )

    def _search_is_active_party(self, operator, value):
        if operator not in ('=', '!='):
            raise UserError(_("Currently Active cannot be searched with '%s'.", operator))
        today = fields.Date.context_today(self)
        active = ['&',
                  '|', ('start_date', '=', False), ('start_date', '<=', today),
                  '|', ('end_date', '=', False), ('end_date', '>=', today)]
        return active if bool(value) == (operator == '=') else ['!'] + active

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('is_primary', 'contract_id', 'role')
    def _check_single_primary(self):
        for rec in self:
            if not rec.is_primary:
                continue
            if rec.role not in BILLABLE_ROLES:
                raise ValidationError(_(
                    "A '%s' cannot be the primary tenant -- only a tenant, "
                    "co-tenant or corporate tenant can be billed.",
                    dict(PARTY_ROLES).get(rec.role, rec.role),
                ))
            others = self.search_count([
                ('contract_id', '=', rec.contract_id.id),
                ('is_primary', '=', True),
                ('id', '!=', rec.id),
            ])
            if others:
                raise ValidationError(_(
                    "Lease '%s' already has a primary tenant. Clear the existing "
                    "one first.", rec.contract_id.display_name))

    @api.constrains('responsibility_pct', 'contract_id', 'role')
    def _check_responsibility_total(self):
        """Billable parties' shares must total 100% -- once they are all set.

        Validation is deliberately deferred until **every** billable party has
        a share. Checking as soon as any one party declares a percentage makes
        the grid impossible to fill in: adding a 40% co-tenant to a lease whose
        tenant is not yet set would fail on the way to a perfectly valid 40/60
        split.

        The trade-off is explicit: a half-filled split is allowed to sit in the
        database, and is caught the moment the last share is entered.
        """
        for contract in self.mapped('contract_id'):
            billable = contract.party_ids.filtered(
                lambda p: p.role in BILLABLE_ROLES)
            if not billable:
                continue
            declared = billable.filtered(lambda p: p.responsibility_pct)
            if not declared:
                continue  # nobody has declared a split -- perfectly normal
            if len(declared) < len(billable):
                continue  # still being filled in
            total = sum(billable.mapped('responsibility_pct'))
            if abs(total - 100.0) > 0.01:
                raise ValidationError(_(
                    "Responsibility shares on lease '%(lease)s' total %(total).2f%% "
                    "-- they must total 100%% across tenants, co-tenants and "
                    "corporate tenants (or be left blank entirely).",
                    lease=contract.display_name, total=total,
                ))

    @api.constrains('start_date', 'end_date')
    def _check_party_dates(self):
        for rec in self:
            if rec.start_date and rec.end_date and rec.start_date > rec.end_date:
                raise ValidationError(_(
                    "Party '%s' has an end date before its start date.",
                    rec.partner_id.display_name))

    # ------------------------------------------------------------------
    # Primary-party mirror
    # ------------------------------------------------------------------
    def _mirror_primary_to_contract(self):
        """Push the primary party onto ``contract.partner_id``."""
        if self.env.context.get(SYNC_CTX):
            return
        for rec in self:
            if rec.is_primary and rec.contract_id.partner_id != rec.partner_id:
                rec.contract_id.with_context(**{SYNC_CTX: True}).partner_id = rec.partner_id

    @api.model_create_multi
    def create(self, vals_list):
        parties = super().create(vals_list)
        parties._mirror_primary_to_contract()
        return parties

    def write(self, vals):
        res = super().write(vals)
        if {'is_primary', 'partner_id'} & set(vals):
            self._mirror_primary_to_contract()
        return res

    def action_make_primary(self):
        """Promote this party, demoting whoever held the flag."""
        self.ensure_one()
        if self.role not in BILLABLE_ROLES:
            raise UserError(_(
                "Change the role to Tenant, Co-Tenant or Company before making "
                "'%s' the primary tenant.", self.partner_id.display_name))
        self.contract_id.party_ids.filtered(
            lambda p: p.is_primary and p != self
        ).write({'is_primary': False})
        self.is_primary = True
        self.contract_id.message_post(body=Markup(_(
            "Primary tenant changed to <b>%s</b>.")) % self.partner_id.display_name)
        return True


class ContractPartyMixin(models.Model):
    """Party collection + primary sync on the lease itself."""
    _inherit = 'realestate.contract'

    party_ids = fields.One2many(
        'realestate.contract.party', 'contract_id', string='Parties', copy=True,
    )
    party_count = fields.Integer(compute='_compute_party_count')
    guarantor_ids = fields.Many2many(
        'res.partner', string='Guarantors',
        compute='_compute_role_partners',
        help="Read-only view of the parties holding the guarantor role.",
    )
    occupant_ids = fields.Many2many(
        'res.partner', string='Occupants', compute='_compute_role_partners',
    )

    @api.depends('party_ids')
    def _compute_party_count(self):
        for rec in self:
            rec.party_count = len(rec.party_ids)

    @api.depends('party_ids.role', 'party_ids.partner_id')
    def _compute_role_partners(self):
        for rec in self:
            rec.guarantor_ids = rec.party_ids.filtered(
                lambda p: p.role == 'guarantor').mapped('partner_id')
            rec.occupant_ids = rec.party_ids.filtered(
                lambda p: p.role == 'occupant').mapped('partner_id')

    def _ensure_primary_party(self):
        """Materialise ``partner_id`` as a primary party row.

        Runs on create and whenever ``partner_id`` changes, so a lease created
        the old way (tenant only, no party grid) still gets a correct party
        list, and the two can never disagree.
        """
        if self.env.context.get(SYNC_CTX):
            return
        Party = self.env['realestate.contract.party'].with_context(**{SYNC_CTX: True})
        for contract in self:
            if not contract.partner_id:
                continue
            primary = contract.party_ids.filtered('is_primary')
            if primary:
                if primary[0].partner_id != contract.partner_id:
                    primary[0].partner_id = contract.partner_id
                continue
            existing = contract.party_ids.filtered(
                lambda p: p.partner_id == contract.partner_id
                and p.role in BILLABLE_ROLES)
            if existing:
                existing[0].is_primary = True
            else:
                Party.create({
                    'contract_id': contract.id,
                    'partner_id': contract.partner_id.id,
                    'role': 'company' if contract.partner_id.is_company else 'tenant',
                    'is_primary': True,
                    'start_date': contract.start_date,
                    'end_date': contract.end_date,
                })

    @api.model_create_multi
    def create(self, vals_list):
        contracts = super().create(vals_list)
        contracts._ensure_primary_party()
        return contracts

    def write(self, vals):
        res = super().write(vals)
        if 'partner_id' in vals and not self.env.context.get(SYNC_CTX):
            self._ensure_primary_party()
        return res

    def action_view_parties(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Lease Parties'),
            'res_model': 'realestate.contract.party',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }
