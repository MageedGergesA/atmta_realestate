"""First-class property allocation on a lease (Phase 6) and the overlapping
lease engine (Phase 5).

``realestate.contract.property.line`` is the one record that puts a unit on a
lease, whatever the lease's shape:

* a single-unit lease mirrors its unit, dates and rent into one allocation
  (see ``_sync_property_lines`` on the contract)
* a multi-unit lease has its units entered directly as allocations

so one overlap constraint covers every lease, and occupancy, rent roll and
availability read one relation. The legacy unit line model that multi-unit
leases once used was removed in 0.10; its rows were carried over as
allocations (``migrations/0.10``).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .lease_states import BLOCKING_LIFECYCLE, OCCUPYING_LIFECYCLE

#: Sentinel used when a lease has no end date (open-ended / rolling).
OPEN_ENDED = '9999-12-31'

LINE_OCCUPANCY = [
    ('pending', 'Pending Move-In'),
    ('occupied', 'Occupied'),
    ('vacated', 'Vacated'),
]

#: How the allocation got here -- lets the sync layer own what it created
#: without stomping rows a user added by hand.
ORIGINS = [
    ('manual', 'Manual'),
    ('single', 'Mirrored from Single-Unit Field'),
]


class ContractPropertyLine(models.Model):
    _name = 'realestate.contract.property.line'
    _description = 'Lease Property Allocation'
    _inherit = ['mail.thread']
    _order = 'contract_id, sequence, start_date, id'
    _rec_name = 'property_id'

    sequence = fields.Integer(default=10)

    contract_id = fields.Many2one(
        'realestate.contract', string='Lease', required=True,
        ondelete='cascade', index=True,
    )
    property_id = fields.Many2one(
        'realestate.property', string='Property', required=True,
        ondelete='restrict', index=True, tracking=True,
        check_company=True, domain="[('is_leasable', '=', True)]",
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        related='contract_id.company_id', store=True, precompute=True,
    )
    currency_id = fields.Many2one(
        'res.currency', related='contract_id.currency_id',
        store=True, readonly=True,
    )

    # ---------------- Term ----------------
    start_date = fields.Date(string='Start Date', required=True, tracking=True, index=True)
    end_date = fields.Date(string='End Date', tracking=True, index=True,
                           help="Leave empty for an open-ended allocation.")

    # ---------------- Commercials ----------------
    allocated_rent = fields.Monetary(
        string='Allocated Rent', tracking=True,
        help="Share of the lease rent attributable to this property, per "
             "billing period.",
    )
    security_deposit = fields.Monetary(string='Security Deposit', tracking=True)
    area_sqm = fields.Float(related='property_id.area_sqm', store=True, readonly=True)
    rent_per_sqm = fields.Monetary(
        string='Rent / sqm', compute='_compute_rent_per_sqm', store=True,
    )

    # ---------------- Physical occupancy ----------------
    move_in_date = fields.Date(string='Move-In', tracking=True)
    move_out_date = fields.Date(string='Move-Out', tracking=True)
    occupancy_status = fields.Selection(
        LINE_OCCUPANCY, string='Occupancy', default='pending',
        compute='_compute_occupancy', store=True,
        # Not required, for the same reason as realestate.property.occupancy_status:
        # NOT NULL is enforced at INSERT, before the compute runs.
    )
    occupies_property = fields.Boolean(
        string='Currently Occupying', compute='_compute_occupancy', store=True,
        index=True,
        help="True when this allocation makes the property occupied today. "
             "Drives realestate.property.occupancy_status.",
    )

    # ---------------- Lifecycle mirrors ----------------
    lifecycle_state = fields.Selection(
        related='contract_id.lifecycle_state', store=True, index=True, readonly=True,
    )
    partner_id = fields.Many2one(
        related='contract_id.partner_id', string='Tenant', store=True, readonly=True,
    )
    is_blocking = fields.Boolean(
        string='Blocks Other Leases', compute='_compute_is_blocking', store=True,
        index=True,
        help="True when this allocation reserves the property against other "
             "leases. Used by the overlap constraint.",
    )

    origin = fields.Selection(ORIGINS, default='manual', required=True, copy=False)
    notes = fields.Char()

    _sql_constraints = [
        ('dates_order', 'CHECK(end_date IS NULL OR end_date >= start_date)',
         'Allocation end date must be on or after the start date.'),
    ]

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('allocated_rent', 'area_sqm')
    def _compute_rent_per_sqm(self):
        for rec in self:
            rec.rent_per_sqm = (rec.allocated_rent / rec.area_sqm) if rec.area_sqm else 0.0

    @api.depends('lifecycle_state')
    def _compute_is_blocking(self):
        for rec in self:
            rec.is_blocking = rec.lifecycle_state in BLOCKING_LIFECYCLE

    @api.depends('lifecycle_state', 'start_date', 'end_date',
                 'move_in_date', 'move_out_date')
    def _compute_occupancy(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.move_out_date and rec.move_out_date <= today:
                rec.occupancy_status = 'vacated'
                rec.occupies_property = False
                continue
            running = (
                rec.lifecycle_state in OCCUPYING_LIFECYCLE
                and rec.start_date and rec.start_date <= today
                and (not rec.end_date or rec.end_date >= today)
            )
            if running:
                rec.occupancy_status = 'occupied'
                rec.occupies_property = True
            else:
                rec.occupancy_status = 'pending'
                rec.occupies_property = False

    @api.constrains('property_id')
    def _check_property_is_leasable(self):
        """Only a unit or a room can carry a lease.

        ``is_leasable`` already said so, but nothing enforced it: a compound, a
        building, a floor or a common area could be picked on a lease, and its
        billing and occupancy then counted against a record the occupancy
        figures exclude.
        """
        for rec in self:
            if rec.property_id and not rec.property_id.is_leasable:
                raise ValidationError(_(
                    "'%(prop)s' cannot be leased: only units and rooms that are "
                    "not common areas can carry a lease.",
                    prop=rec.property_id.display_name))

    # ------------------------------------------------------------------
    # Phase 5 -- overlapping lease protection
    # ------------------------------------------------------------------
    @api.constrains('property_id', 'start_date', 'end_date',
                    'lifecycle_state', 'contract_id')
    def _check_no_overlap(self):
        """Reject any allocation that collides with another blocking lease.

        Intervals are treated as **closed** on both ends, so a lease starting
        on the day a previous lease ends is a conflict (both cover that day),
        while starting the day after is fine.
        """
        for rec in self:
            if not rec.is_blocking or not rec.property_id or not rec.start_date:
                continue
            rec._lock_property()
            conflict = rec._find_conflicting_allocation()
            if conflict:
                raise ValidationError(_(
                    "Property '%(prop)s' is already committed to lease "
                    "'%(lease)s' from %(start)s to %(end)s.\n"
                    "Requested: %(rstart)s to %(rend)s.",
                    prop=rec.property_id.display_name,
                    lease=conflict.contract_id.display_name,
                    start=conflict.start_date,
                    end=conflict.end_date or _('open-ended'),
                    rstart=rec.start_date,
                    rend=rec.end_date or _('open-ended'),
                ))

    def _lock_property(self):
        """Serialize concurrent allocations of the same property.

        A plain SELECT-then-INSERT check is racy: two transactions can both see
        "no conflict" and both commit. A transaction-scoped Postgres advisory
        lock keyed on the property id makes the check-and-insert atomic without
        locking the property row itself (which would block unrelated edits).
        The lock is released automatically at COMMIT or ROLLBACK.
        """
        self.ensure_one()
        if not self.property_id:
            return
        self.env.cr.execute(
            'SELECT pg_advisory_xact_lock(%s, %s)',
            (self._advisory_lock_namespace(), self.property_id.id),
        )

    @api.model
    def _advisory_lock_namespace(self):
        """Stable 32-bit namespace so our locks cannot collide with another
        addon's advisory locks."""
        return 0x5245414C  # 'REAL'

    def _find_conflicting_allocation(self):
        """Return the first blocking allocation overlapping this one, if any."""
        self.ensure_one()
        return self.search(
            self._conflict_domain(), limit=1, order='start_date',
        )

    def _conflict_domain(self):
        """Domain matching other blocking allocations that overlap this term."""
        self.ensure_one()
        end = self.end_date or fields.Date.to_date(OPEN_ENDED)
        domain = [
            ('property_id', '=', self.property_id.id),
            ('is_blocking', '=', True),
            ('start_date', '<=', end),
            '|', ('end_date', '=', False), ('end_date', '>=', self.start_date),
        ]
        if self.id:
            domain.append(('id', '!=', self.id))
        # An amendment supersedes the row it replaces rather than colliding
        # with it, and a lease never conflicts with itself.
        if self.contract_id:
            domain.append(('contract_id', '!=', self.contract_id.id))
        return domain

    @api.model
    def _check_property_free(self, property_id, start_date, end_date,
                            exclude_contract=None):
        """Reusable pre-flight check for callers that want to validate *before*
        creating anything (renewal wizard, amendment apply, portal).

        Returns the conflicting allocation recordset (empty when free).
        """
        end = end_date or fields.Date.to_date(OPEN_ENDED)
        domain = [
            ('property_id', '=', property_id),
            ('is_blocking', '=', True),
            ('start_date', '<=', end),
            '|', ('end_date', '=', False), ('end_date', '>=', start_date),
        ]
        if exclude_contract:
            domain.append(('contract_id', '!=', exclude_contract))
        return self.search(domain, limit=1)

    # ------------------------------------------------------------------
    # Company integrity (Phase 28)
    # ------------------------------------------------------------------
    @api.constrains('company_id', 'property_id', 'contract_id')
    def _check_company_consistency(self):
        for rec in self:
            prop_company = rec.property_id.company_id
            if prop_company and rec.company_id and prop_company != rec.company_id:
                raise ValidationError(_(
                    "Property '%(prop)s' belongs to company '%(pc)s' but the "
                    "lease belongs to '%(cc)s'.",
                    prop=rec.property_id.display_name,
                    pc=prop_company.display_name,
                    cc=rec.company_id.display_name,
                ))

    @api.constrains('start_date', 'end_date', 'contract_id')
    def _check_within_contract_term(self):
        for rec in self:
            contract = rec.contract_id
            if not contract or not contract.start_date:
                continue
            if rec.start_date < contract.start_date:
                raise ValidationError(_(
                    "Allocation for '%(prop)s' starts before the lease start "
                    "date (%(cstart)s).",
                    prop=rec.property_id.display_name, cstart=contract.start_date,
                ))
            if contract.end_date and rec.end_date and rec.end_date > contract.end_date:
                raise ValidationError(_(
                    "Allocation for '%(prop)s' ends after the lease end date "
                    "(%(cend)s).",
                    prop=rec.property_id.display_name, cend=contract.end_date,
                ))

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------
    def action_register_move_in(self, date=None):
        """Mark physical occupancy. Called by realestate.move.in."""
        for rec in self:
            rec.move_in_date = date or fields.Date.context_today(rec)
        return True

    def action_register_move_out(self, date=None):
        for rec in self:
            if not rec.move_in_date:
                raise UserError(_(
                    "Cannot record a move-out for '%s' before a move-in.",
                    rec.property_id.display_name,
                ))
            rec.move_out_date = date or fields.Date.context_today(rec)
        return True

    @api.depends('property_id', 'start_date', 'end_date')
    def _compute_display_name(self):
        # v18+ idiom; ``name_get`` is removed in v19.
        for rec in self:
            rec.display_name = '%s (%s → %s)' % (
                rec.property_id.display_name or _('Property'),
                rec.start_date or '?',
                rec.end_date or '∞',
            )
