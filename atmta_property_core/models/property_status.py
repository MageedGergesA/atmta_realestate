"""Multi-dimensional property status.

The legacy ``realestate.property.state`` field mixed unrelated concerns into one
selection: commercial availability, rental occupancy, maintenance and archival.
Several call sites across modules wrote to it and clobbered each other.

The concerns are split into orthogonal dimensions, each with one owner:

===================== ============================= ==========================
Dimension             Owner                         Written by
===================== ============================= ==========================
commercial_status     Sales / brokerage             developer, brokerage
occupancy_status      Rental                        computed from leases
construction_status   Construction                  construction, developer
handover_status       Handover                      handover
maintenance_status    Facilities                    maintenance requests
===================== ============================= ==========================

``state`` survives as a read-only computed bridge so downstream readers keep
working, and ``create``/``write`` translate a legacy ``state`` value into the
dimension its writer actually owns.

This model lived in ``atmta_real_estate`` although only occupancy is about
leasing. It moved here so Developer and Brokerage keep the state-to-dimension
sync without the Rental app: their older code writes ``state`` and their newer
code writes ``commercial_status``, and the stock lifecycle reads ``state``;
without the bridge those drift apart and a reserved unit reads as sellable.
Occupancy stays in Rental, which extends ``_legacy_state_value`` with the one
precedence rule leasing adds.
"""

from odoo import _, api, fields, models


COMMERCIAL_STATUS = [
    ('unreleased', 'Unreleased'),
    ('available', 'Available'),
    ('held', 'Held'),
    ('reserved', 'Reserved'),
    ('contracted', 'Contracted'),
    ('sold', 'Sold'),
    ('blocked', 'Blocked'),
]

#: Construction status.
#:
#: ``real_estate_developer`` also declares this field as a STORED COMPUTED field
#: driven from the phase state, using ``planning / under_construction / ready /
#: delivered``. This selection is a deliberate superset, spelled identically
#: where the two overlap (``finishing`` is only meaningful without Developer).
#: When Developer is installed it takes ownership of the field and computes it;
#: Odoo logs an "overrides existing selection" warning for it, which is expected
#: because Developer's values are a strict subset.
CONSTRUCTION_STATUS = [
    ('planning', 'Planning'),
    ('under_construction', 'Under Construction'),
    ('finishing', 'Finishing'),
    ('ready', 'Ready'),
    ('delivered', 'Delivered'),
]

HANDOVER_STATUS = [
    ('pending', 'Pending'),
    ('inspection', 'Inspection'),
    ('snagging', 'Snagging'),
    ('handed_over', 'Handed Over'),
]

MAINTENANCE_STATUS = [
    ('normal', 'Normal'),
    ('blocked', 'Blocked'),
    ('maintenance', 'Under Maintenance'),
]

#: Legacy ``state`` value -> the dimension write it actually means.
#:
#: Deliberately *narrow*: each legacy value touches exactly one dimension, so a
#: module releasing its own commercial hold (``state = 'available'``) can never
#: clear an unrelated maintenance block or an active lease.
LEGACY_STATE_TO_DIMENSION = {
    'available': {'commercial_status': 'available'},
    'reserved': {'commercial_status': 'reserved'},
    'sold': {'commercial_status': 'sold'},
    'maintenance': {'maintenance_status': 'maintenance'},
    'inactive': {'commercial_status': 'blocked'},
    # 'rented' is intentionally a no-op: occupancy is computed from leases,
    # which are the authoritative record. Writing it is accepted (so old code
    # does not crash) but ignored.
    'rented': {},
}

#: Context key that lets the bridge's own compute write ``state`` directly.
LEGACY_SYNC_CTX = 're_legacy_state_sync'


class PropertyStatusDimensions(models.Model):
    _inherit = 'realestate.property'

    # ------------------------------------------------------------------
    # Dimension fields
    # ------------------------------------------------------------------
    commercial_status = fields.Selection(
        COMMERCIAL_STATUS, string='Commercial Status',
        default='available', required=True, tracking=True, index=True, copy=False,
        help="Where the unit stands in the SALES pipeline. Owned by the "
             "developer / brokerage modules. Rental must not write this.",
    )
    construction_status = fields.Selection(
        CONSTRUCTION_STATUS, string='Construction Status',
        default='ready', required=True, tracking=True, index=True,
        help="Owned by the construction module. Rental must not write this.",
    )
    handover_status = fields.Selection(
        HANDOVER_STATUS, string='Handover Status',
        default='pending', required=True, tracking=True, index=True,
        help="Owned by the handover module. Rental must not write this.",
    )
    maintenance_status = fields.Selection(
        MAINTENANCE_STATUS, string='Maintenance Status',
        default='normal', required=True, tracking=True, index=True,
        help="Owned by facilities / maintenance requests.",
    )

    # ------------------------------------------------------------------
    # Legacy compatibility bridge
    # ------------------------------------------------------------------
    state = fields.Selection(
        selection=[
            ('available', 'Available'),
            ('reserved', 'Reserved'),
            ('rented', 'Rented'),
            ('sold', 'Sold'),
            ('maintenance', 'Under Maintenance'),
            ('inactive', 'Inactive'),
        ],
        string='Status (legacy)',
        compute='_compute_legacy_state', store=True, index=True, readonly=True,
        tracking=False,
        help="DEPRECATED compatibility bridge, derived from the status "
             "dimensions. Kept because downstream ATMTA modules and the public "
             "API still read it. Write the dimension you own instead.",
    )

    @api.depends('commercial_status', 'maintenance_status', 'active')
    def _compute_legacy_state(self):
        """Collapse the dimensions back into one legacy value."""
        for rec in self:
            rec.state = rec._legacy_state_value()

    def _legacy_state_value(self):
        """Single-record legacy state derivation.

        Precedence: archival and maintenance blocks win (hard blockers), then
        sale, then the commercial pipeline. Rental extends this with occupancy,
        which sits between sale and the pipeline.
        """
        self.ensure_one()
        if not self.active or self.commercial_status == 'blocked':
            return 'inactive'
        if self.maintenance_status in ('maintenance', 'blocked'):
            return 'maintenance'
        if self.commercial_status == 'sold':
            return 'sold'
        if self.commercial_status in ('held', 'reserved', 'contracted'):
            return 'reserved'
        if self.commercial_status == 'available':
            return 'available'
        # 'unreleased' -> hidden from availability counters and the public API.
        return 'inactive'

    # ------------------------------------------------------------------
    # create / write translation of legacy state writes
    # ------------------------------------------------------------------
    @api.model
    def _translate_legacy_state(self, vals):
        """Replace a legacy ``state`` key with the dimension it maps to.

        Returns a new dict; the caller's dict is never mutated.
        """
        if 'state' not in vals:
            return vals
        vals = dict(vals)
        legacy = vals.pop('state')
        mapped = LEGACY_STATE_TO_DIMENSION.get(legacy)
        if mapped is None:
            return vals
        # An explicit dimension write in the same call always wins over the
        # legacy translation -- new code beats old code.
        for key, value in mapped.items():
            vals.setdefault(key, value)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(LEGACY_SYNC_CTX):
            vals_list = [self._translate_legacy_state(v) for v in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get(LEGACY_SYNC_CTX):
            vals = self._translate_legacy_state(vals)
        return super().write(vals)

    # ------------------------------------------------------------------
    # Archival on the dimension it owns
    # ------------------------------------------------------------------
    def set_property_inactive(self):
        """Block the unit commercially, unless it is reserved, rented or under
        maintenance. Rental overrides this to refuse while a lease is live."""
        for rec in self:
            if rec.state in ('reserved', 'rented', 'maintenance'):
                continue
            rec.commercial_status = 'blocked'

    def set_property_available(self):
        for rec in self:
            if rec.commercial_status == 'blocked':
                rec.commercial_status = 'available'

    # ------------------------------------------------------------------
    # Ownership entry points -- surfaced for downstream modules
    # ------------------------------------------------------------------
    def _set_commercial_status(self, status, reason=None):
        """Public entry point for sales-side modules (developer / brokerage)."""
        self.write({'commercial_status': status})
        if reason:
            for rec in self:
                rec.message_post(body=_("Commercial status → %(status)s: %(reason)s",
                                        status=status, reason=reason))
        return True

    def _set_maintenance_status(self, status, reason=None):
        """Public entry point for facilities / maintenance."""
        self.write({'maintenance_status': status})
        if reason:
            for rec in self:
                rec.message_post(body=_("Maintenance status → %(status)s: %(reason)s",
                                        status=status, reason=reason))
        return True
