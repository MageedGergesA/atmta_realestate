"""Occupancy, the one property status dimension that belongs to leasing.

The multi-dimensional status model -- ``commercial_status``,
``construction_status``, ``handover_status``, ``maintenance_status``, the
read-only ``state`` bridge and the translation of legacy ``state`` writes --
lives in ``atmta_property_core``. It used to live here, although only occupancy
is about leasing; it moved so Developer and Brokerage keep the state-to-dimension
sync without the Rental app.

What stays here is what depends on leases:

* ``occupancy_status``, computed from active lease allocations;
* ``property_line_ids``, the allocations it is computed from;
* the legacy ``occupancy_state`` mirror;
* the occupancy rule in the ``state`` bridge: an occupied unit reads as
  ``rented`` unless it is archived, blocked, under maintenance or sold;
* archival that refuses while a lease is live.

With this module installed the ``state`` bridge behaves exactly as it did before
the split.
"""

from odoo import api, fields, models


OCCUPANCY_STATUS = [
    ('vacant', 'Vacant'),
    ('partially_occupied', 'Partially Occupied'),
    ('occupied', 'Occupied'),
]

#: Construction states in which a unit cannot be leased. Expressed as the
#: NOT-ready set rather than ``!= 'ready'`` so that ``delivered`` (a developer
#: value that also means buildable) does not wrongly block availability.
CONSTRUCTION_NOT_READY = ('planning', 'under_construction', 'finishing')


class PropertyOccupancy(models.Model):
    _inherit = 'realestate.property'

    occupancy_status = fields.Selection(
        OCCUPANCY_STATUS, string='Occupancy', index=True,
        compute='_compute_occupancy_status', store=True, tracking=True,
        # Deliberately NOT required: a stored computed column gets its NOT NULL
        # constraint applied at INSERT, before the compute has run, so
        # required=True here makes every property creation fail. The compute
        # always assigns a value, which is the guarantee that actually matters.
        help="Derived from active lease allocations. Owned by rental; "
             "never written directly.",
    )

    #: Canonical lease allocations covering this property. Populated by
    #: ``realestate.contract._sync_property_lines`` for every lease shape.
    property_line_ids = fields.One2many(
        'realestate.contract.property.line', 'property_id',
        string='Lease Allocations',
    )

    # ------------------------------------------------------------------
    # Occupancy in the legacy state bridge
    # ------------------------------------------------------------------
    # The full dependency list is repeated on purpose: it is correct whether
    # Odoo merges an override's @api.depends with the parent's or replaces it.
    @api.depends('commercial_status', 'maintenance_status', 'active',
                 'occupancy_status')
    def _compute_legacy_state(self):
        return super()._compute_legacy_state()

    def _legacy_state_value(self):
        """Put occupancy back in its place: after archival, maintenance and sale,
        before the commercial pipeline -- exactly where it sat before the model
        moved to Property Core."""
        self.ensure_one()
        hard_block = (
            not self.active
            or self.commercial_status in ('blocked', 'sold')
            or self.maintenance_status in ('maintenance', 'blocked')
        )
        if not hard_block and self.occupancy_status in ('occupied', 'partially_occupied'):
            return 'rented'
        return super()._legacy_state_value()

    # ------------------------------------------------------------------
    # Occupancy is computed from the authoritative lease records
    # ------------------------------------------------------------------
    @api.depends(
        'property_line_ids.occupies_property',
        'property_line_ids.property_id',
        'child_ids.occupancy_status',
    )
    def _compute_occupancy_status(self):
        """Occupancy = does an active lease allocation cover this unit today?

        Parents (compound / building / floor) roll up from their children so a
        building shows ``partially_occupied`` while some units are vacant.
        """
        # Batch the leaf lookup: one query for the whole recordset instead of
        # one per record (Phase 32).
        leaf_ids = [r.id for r in self if not r.child_ids]
        occupied_ids = set()
        if leaf_ids:
            # sudo(): occupancy is a property-level fact derived from leases the
            # reader may not be entitled to see. Without it a user who can read
            # a building but not its leases would see it as vacant.
            Line = self.env['realestate.contract.property.line'].sudo()
            # ``_read_group`` (not the deprecated ``read_group``): returns
            # plain tuples in v17+, and ``read_group`` is removed in v19.
            groups = Line._read_group(
                [('property_id', 'in', leaf_ids), ('occupies_property', '=', True)],
                groupby=['property_id'],
            )
            occupied_ids = {prop.id for (prop,) in groups if prop}

        for rec in self:
            if not rec.child_ids:
                rec.occupancy_status = 'occupied' if rec.id in occupied_ids else 'vacant'
                continue
            # Normalise before comparing. During a recompute cascade a child's
            # own status may not be assigned yet and reads as False; treating
            # that as "something in between" would wrongly report a fully
            # vacant building as partially occupied.
            child_states = {
                state or 'vacant' for state in rec.child_ids.mapped('occupancy_status')
            }
            if child_states == {'occupied'}:
                rec.occupancy_status = 'occupied'
            elif child_states == {'vacant'}:
                rec.occupancy_status = 'vacant'
            else:
                rec.occupancy_status = 'partially_occupied'

    @api.depends('occupancy_status')
    def _compute_occupancy_state(self):
        """Legacy ``occupancy_state`` mirrors the new dimension.

        Overrides the original implementation (which read ``state``) so both
        fields cannot disagree.
        """
        mapping = {
            'vacant': 'available',
            'occupied': 'rented',
            'partially_occupied': 'partial',
        }
        for rec in self:
            rec.occupancy_state = mapping.get(rec.occupancy_status, 'available')

    # ------------------------------------------------------------------
    # Archival that respects live leases
    # ------------------------------------------------------------------
    def set_property_inactive(self):
        """Rental adds ONE refusal to the rule Property Core already has: a
        unit with a live lease is not this button's to block, because the
        lease -- not this button -- decides when a unit is free.

        It filters and then delegates. Replacing Core's implementation
        outright silently dropped Core's own guard, so a unit that was
        reserved, sold or under maintenance got blocked anyway the moment it
        happened to be vacant. Extending a rule means adding a condition to
        it, not writing a new one that happens to be shorter.
        """
        lettable = self.filtered(lambda rec: rec.occupancy_status == 'vacant')
        return super(PropertyOccupancy, lettable).set_property_inactive()

    def set_property_available(self):
        for rec in self:
            if rec.commercial_status == 'blocked':
                rec.commercial_status = 'available'
