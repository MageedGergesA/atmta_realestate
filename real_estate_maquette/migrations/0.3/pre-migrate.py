def migrate(cr, version):
    """Slim the building floor model: floor_number becomes integer, drop
    plan_image / has_plan / notes / filename. Also drop floor_id from
    realestate.property (link is now via the existing floor_number int)."""

    # 1) Convert realestate_building_floor.floor_number from Char → Integer.
    #    Map alphas (G, T, B*) to sensible integers.
    cr.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'realestate_building_floor' AND column_name = 'floor_number'
    """)
    row = cr.fetchone()
    if row and row[1] not in ('integer', 'bigint'):
        cr.execute("""
            ALTER TABLE realestate_building_floor
            ADD COLUMN IF NOT EXISTS floor_number_int integer
        """)
        cr.execute("""
            UPDATE realestate_building_floor SET floor_number_int = CASE
                WHEN upper(trim(floor_number)) IN ('G', 'GROUND', 'GF') THEN 0
                WHEN upper(trim(floor_number)) IN ('T', 'TERRACE', 'ROOF', 'R') THEN 99
                WHEN upper(trim(floor_number)) LIKE 'B%' THEN -1
                ELSE NULLIF(regexp_replace(floor_number, '\\D', '', 'g'), '')::integer
            END
        """)
        cr.execute("ALTER TABLE realestate_building_floor DROP COLUMN floor_number")
        cr.execute("""
            ALTER TABLE realestate_building_floor
            RENAME COLUMN floor_number_int TO floor_number
        """)

    # 2) Drop now-removed columns. Idempotent (IF EXISTS).
    for col in (
        'plan_image', 'plan_image_filename', 'has_plan', 'notes',
        'floor_number_int',
    ):
        cr.execute("ALTER TABLE realestate_building_floor DROP COLUMN IF EXISTS %s" % col)

    # 3) realestate_property.floor_id is gone.
    cr.execute("ALTER TABLE realestate_property DROP COLUMN IF EXISTS floor_id")

    # 4) Drop the orphaned wizard table if it was ever created.
    cr.execute("DROP TABLE IF EXISTS realestate_floor_assign_wizard CASCADE")
    cr.execute("DROP TABLE IF EXISTS re_floor_assign_wiz_unit_rel CASCADE")
    cr.execute("DROP TABLE IF EXISTS re_floor_assign_wiz_picked_rel CASCADE")
