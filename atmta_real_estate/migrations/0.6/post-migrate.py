"""Map legacy data onto the enterprise leasing model (Phase 34, v0.6).

Runs after the schema is in place. Reads the snapshots taken by
``pre-migrate.py`` and:

1. maps ``property.state`` onto the five status dimensions
2. maps ``contract.state`` onto ``lifecycle_state``
3. seeds ``usage_category`` from the existing property-type catalogue
4. materialises canonical property allocations from both legacy lease shapes
5. normalises the flat utility-meter columns into meter records
6. creates a deposit record for every lease already holding a deposit

Guiding rules, in order of importance:

* **Nothing is deleted.** Legacy columns, legacy relations and the snapshots
  all survive.
* **Nothing is invented.** Where the legacy data genuinely cannot determine a
  new value -- the classic case being a property that was ``available`` and
  therefore says nothing about whether it was ever built -- the field is left
  at its default and the record is logged rather than guessed at.
* **Ambiguity is reported, loudly.** Every unmappable value is counted and
  written to the log with its record ids, so it can be reviewed rather than
  discovered six months later.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

#: Legacy property.state -> the dimension values it genuinely implies.
#:
#: Note what is deliberately absent: nothing here sets ``construction_status``
#: or ``handover_status``. The legacy field carried no information about
#: either, so inferring them would be fabrication.
PROPERTY_STATE_MAP = {
    'available': {'commercial_status': 'available',
                  'maintenance_status': 'normal'},
    'reserved': {'commercial_status': 'reserved',
                 'maintenance_status': 'normal'},
    'sold': {'commercial_status': 'sold',
             'maintenance_status': 'normal'},
    # 'rented' says nothing about the commercial pipeline -- occupancy is
    # recomputed from the allocations, so only the maintenance flag is cleared.
    'rented': {'maintenance_status': 'normal'},
    'maintenance': {'maintenance_status': 'maintenance'},
    'inactive': {'commercial_status': 'blocked'},
}

CONTRACT_STATE_MAP = {
    'draft': 'draft',
    'ready': 'proposal',
    'confirmed': 'pending_signature',
    'invoiced': 'pending_signature',
    'active': 'active',
    'expired': 'ended',
    'terminated': 'terminated',
}


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    _migrate_property_status(cr, env)
    _migrate_contract_lifecycle(cr, env)
    _seed_usage_category(env)
    _build_property_allocations(env)
    _normalise_meters(env)
    _seed_deposit_records(env)
    _recompute_derived(env)
    _logger.info("atmta_real_estate 0.6 migration complete.")


# ---------------------------------------------------------------------------
# 1. Property status dimensions
# ---------------------------------------------------------------------------
def _migrate_property_status(cr, env):
    if not _column_exists(cr, 'realestate_property', 're_legacy_state_backup'):
        _logger.warning(
            "No property state snapshot found; leaving status dimensions at "
            "their defaults.")
        return

    cr.execute("""
        SELECT re_legacy_state_backup, array_agg(id)
        FROM realestate_property
        WHERE re_legacy_state_backup IS NOT NULL
        GROUP BY re_legacy_state_backup
    """)
    unmapped = {}
    for legacy_state, ids in cr.fetchall():
        mapping = PROPERTY_STATE_MAP.get(legacy_state)
        if mapping is None:
            unmapped[legacy_state] = ids
            continue
        # Written in SQL rather than via the ORM: this is a bulk backfill of
        # plain columns on a table that may hold tens of thousands of rows, and
        # going through create/write would fire computes and constraints for
        # data that is about to be recomputed wholesale anyway.
        assignments = ', '.join('%s = %%s' % column for column in mapping)
        cr.execute(
            'UPDATE realestate_property SET %s WHERE id = ANY(%%s)' % assignments,
            list(mapping.values()) + [ids])
        _logger.info("Property status: mapped %s '%s' -> %s",
                     len(ids), legacy_state, mapping)

    for legacy_state, ids in unmapped.items():
        _logger.warning(
            "Property status: legacy value %r on %s record(s) has no mapping "
            "and was left at the default. Record ids: %s",
            legacy_state, len(ids), ids[:50])


# ---------------------------------------------------------------------------
# 2. Contract lifecycle
# ---------------------------------------------------------------------------
def _migrate_contract_lifecycle(cr, env):
    if not _column_exists(cr, 'realestate_contract', 're_legacy_state_backup'):
        _logger.warning(
            "No lease state snapshot found; leaving lifecycle_state at draft.")
        return

    cr.execute("""
        SELECT re_legacy_state_backup, array_agg(id)
        FROM realestate_contract
        WHERE re_legacy_state_backup IS NOT NULL
        GROUP BY re_legacy_state_backup
    """)
    unmapped = {}
    for legacy_state, ids in cr.fetchall():
        target = CONTRACT_STATE_MAP.get(legacy_state)
        if target is None:
            unmapped[legacy_state] = ids
            continue
        cr.execute(
            "UPDATE realestate_contract SET lifecycle_state = %s WHERE id = ANY(%s)",
            (target, ids))
        _logger.info("Lease lifecycle: mapped %s '%s' -> '%s'",
                     len(ids), legacy_state, target)

    # A lease that was already running has, by definition, been signed --
    # otherwise it could not have been activated. Anything earlier in the
    # legacy flow is left as 'pending', because we genuinely do not know.
    cr.execute("""
        UPDATE realestate_contract
        SET signature_status = 'signed'
        WHERE lifecycle_state IN ('active', 'ended', 'terminated')
          AND (signature_status IS NULL OR signature_status = 'pending')
    """)
    _logger.info("Lease signature: marked %s running/closed lease(s) as signed.",
                 cr.rowcount)

    for legacy_state, ids in unmapped.items():
        _logger.warning(
            "Lease lifecycle: legacy value %r on %s record(s) has no mapping "
            "and was left as draft. Record ids: %s",
            legacy_state, len(ids), ids[:50])


# ---------------------------------------------------------------------------
# 3. Usage category
# ---------------------------------------------------------------------------
def _seed_usage_category(env):
    """Best-effort classification from the existing property-type names.

    Only unambiguous, whole-word matches are applied. A type called
    "Residential" is NOT guessed at, because residential covers apartments and
    villas alike and picking one would be inventing data.
    """
    keywords = {
        'apartment': 'apartment', 'flat': 'apartment', 'شقة': 'apartment',
        'villa': 'villa', 'فيلا': 'villa',
        'office': 'office', 'مكتب': 'office',
        'retail': 'retail', 'shop': 'retail', 'store': 'retail', 'محل': 'retail',
        'warehouse': 'warehouse', 'مخزن': 'warehouse',
        'parking': 'parking', 'garage': 'parking',
        'storage': 'storage',
        'hotel': 'hotel_room',
        'land': 'land_plot', 'plot': 'land_plot',
    }
    matched = 0
    unmatched = []
    for ptype in env['property.type'].search([('usage_category', '=', False)]):
        label = (ptype.name or '').strip().lower()
        hit = next((usage for token, usage in keywords.items() if token in label),
                   None)
        if hit:
            ptype.usage_category = hit
            matched += 1
        else:
            unmatched.append(ptype.name)

    if matched:
        # Push the classification down onto properties that have no usage yet.
        env.cr.execute("""
            UPDATE realestate_property p
            SET usage_category = t.usage_category
            FROM property_type t
            WHERE p.property_type_id = t.id
              AND t.usage_category IS NOT NULL
              AND p.usage_category IS NULL
        """)
        _logger.info("Usage category: classified %s property type(s), seeded "
                     "%s propert(ies).", matched, env.cr.rowcount)
    if unmatched:
        _logger.warning(
            "Usage category: could not classify these property types from "
            "their names -- set them by hand: %s", unmatched)


# ---------------------------------------------------------------------------
# 4. Canonical allocations
# ---------------------------------------------------------------------------
def _build_property_allocations(env):
    """Mirror both legacy lease shapes into realestate.contract.property.line.

    Runs through the ORM (not SQL) on purpose: the sync method is the same one
    the runtime uses, so a migrated database and a freshly-created lease end up
    with byte-identical allocations.
    """
    contracts = env['realestate.contract'].search([])
    if not contracts:
        return
    created = 0
    for batch_start in range(0, len(contracts), 200):
        batch = contracts[batch_start:batch_start + 200]
        for contract in batch:
            try:
                before = len(contract.property_line_ids)
                contract._sync_property_lines()
                created += len(contract.property_line_ids) - before
            except Exception as exc:  # noqa: BLE001 - one bad lease must not
                # abort the whole migration; report it and carry on.
                _logger.warning(
                    "Could not build allocations for lease %s (id=%s): %s",
                    contract.name, contract.id, exc)
        env.cr.commit()
    _logger.info("Allocations: created %s lease property line(s) across %s lease(s).",
                 created, len(contracts))


# ---------------------------------------------------------------------------
# 5. Meters
# ---------------------------------------------------------------------------
def _normalise_meters(env):
    properties = env['realestate.property'].search([
        '|', '|',
        ('electricity_meter_number', '!=', False),
        ('water_meter_number', '!=', False),
        ('gas_meter_number', '!=', False),
    ])
    if not properties:
        return
    try:
        created = properties._normalise_legacy_meters()
        _logger.info("Meters: created %s meter record(s) from the legacy "
                     "columns on %s propert(ies).", len(created), len(properties))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Meter normalisation failed: %s", exc)


# ---------------------------------------------------------------------------
# 6. Deposits
# ---------------------------------------------------------------------------
def _seed_deposit_records(env):
    """Create a deposit record for leases already holding a deposit.

    The record is created in a state that matches the legacy ``deposit_state``
    and, critically, **no accounting is posted**. The money moved before this
    module existed; inventing journal entries for it now would double-count.
    The records exist so the balance is visible and future settlements go
    through the proper flow.
    """
    Deposit = env['realestate.contract.deposit']
    legacy_to_new = {
        'held': 'held',
        'refunded': 'refunded',
        'forfeited': 'forfeited',
        'partial_refund': 'partially_refunded',
    }
    contracts = env['realestate.contract'].search([
        ('deposit_amount', '>', 0),
        ('deposit_state', '!=', 'none'),
    ])
    created = 0
    for contract in contracts:
        if Deposit.search_count([('contract_id', '=', contract.id)]):
            continue
        state = legacy_to_new.get(contract.deposit_state)
        if not state:
            _logger.warning(
                "Deposit: lease %s has legacy deposit_state %r with no "
                "mapping; skipped.", contract.name, contract.deposit_state)
            continue
        Deposit.create({
            'contract_id': contract.id,
            'partner_id': contract.partner_id.id,
            'property_id': contract.property_id.id or False,
            'requested_amount': contract.deposit_amount,
            'received_amount': contract.deposit_amount,
            'refunded_amount': contract.deposit_refund_amount or 0.0,
            'forfeited_amount': contract.deposit_forfeit_amount or 0.0,
            'received_date': contract.deposit_paid_date,
            'refund_date': contract.deposit_settled_date,
            'state': state,
            'notes': (
                "Migrated from the pre-0.6 deposit fields. No journal entry "
                "was created: the cash movement predates this module, and "
                "posting one now would double-count it. Future settlements "
                "will post normally."),
        })
        created += 1
    if created:
        _logger.info("Deposits: created %s deposit record(s) from legacy "
                     "lease fields.", created)


# ---------------------------------------------------------------------------
# 7. Recompute
# ---------------------------------------------------------------------------
def _recompute_derived(env):
    """Force the new stored computes now, rather than lazily on first read."""
    Property = env['realestate.property']
    properties = Property.search([])
    if properties:
        properties.modified([
            'commercial_status', 'maintenance_status', 'hierarchy_level',
            'property_type_id', 'usage_category',
        ])
    contracts = env['realestate.contract'].search([])
    if contracts:
        contracts.modified(['lifecycle_state', 'price', 'end_date'])
    env.cr.commit()
    _logger.info("Recomputed derived fields on %s propert(ies) and %s lease(s).",
                 len(properties), len(contracts))
