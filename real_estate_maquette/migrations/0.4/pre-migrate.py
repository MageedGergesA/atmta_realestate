def _table_exists(cr, name):
    cr.execute("SELECT to_regclass(%s)", [name])
    return cr.fetchone()[0] is not None


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, [table, column])
    return cr.fetchone() is not None


def migrate(cr, version):
    """Floor row is now unit-driven: pick a unit on the floor, and the row's
    floor_number / property_usage_id flow from it. Drop manual `name`, `use`,
    `sequence`. Backfill `unit_id` from each row's (building_id, floor_number)
    match. Dedupe duplicate (building, floor) pairs first to honour the new
    UNIQUE constraint. Rows that can't be matched are deleted.

    All steps are guarded for fresh-install / restored-backup DBs where the
    table may not yet exist."""

    if _table_exists(cr, 'realestate_building_floor'):
        # 0) Add the new unit_id column if not already created.
        cr.execute("""
            ALTER TABLE realestate_building_floor
            ADD COLUMN IF NOT EXISTS unit_id integer
        """)

        # 1) Dedupe (building_id, floor_number): keep the lowest id per pair.
        cr.execute("""
            DELETE FROM realestate_building_floor f
            USING realestate_building_floor g
            WHERE f.building_id = g.building_id
              AND f.floor_number IS NOT DISTINCT FROM g.floor_number
              AND f.id > g.id
        """)

        # 2) Backfill unit_id where the building has exactly one unit
        #    matching the row's floor_number.
        if _table_exists(cr, 'realestate_property'):
            cr.execute("""
                UPDATE realestate_building_floor f
                SET unit_id = sub.unit_id
                FROM (
                    SELECT DISTINCT ON (p.parent_id, p.floor_number)
                           p.parent_id AS building_id,
                           p.floor_number,
                           p.id AS unit_id
                    FROM realestate_property p
                    WHERE p.hierarchy_level = 'unit'
                    ORDER BY p.parent_id, p.floor_number, p.id
                ) sub
                WHERE f.building_id = sub.building_id
                  AND f.floor_number IS NOT DISTINCT FROM sub.floor_number
                  AND f.unit_id IS NULL
            """)

        # 3) Delete rows we couldn't backfill — unit_id is required from 0.4 on.
        cr.execute("DELETE FROM realestate_building_floor WHERE unit_id IS NULL")

        # 4) Drop now-unused columns. Idempotent.
        for col in ('use', 'name', 'sequence'):
            cr.execute("ALTER TABLE realestate_building_floor DROP COLUMN IF EXISTS %s" % col)

    # 5) Force recompute of has_elevation — its dep list dropped `floor_ids`,
    #    so previously a building with floors but no sheet image would have
    #    been True. Reset to NULL; ORM recomputes on next access.
    if _table_exists(cr, 'realestate_property') and _column_exists(cr, 'realestate_property', 'has_elevation'):
        cr.execute("UPDATE realestate_property SET has_elevation = NULL "
                   "WHERE has_elevation IS NOT NULL")
