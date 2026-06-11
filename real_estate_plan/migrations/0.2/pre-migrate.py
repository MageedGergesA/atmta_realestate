"""Convert legacy rectangle regions (x_pct/y_pct/w_pct/h_pct) into the new
polygon JSON format, then drop the old columns. Safe to run on a fresh
table where the old columns never existed."""
import json
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'realestate_plan_region'
    """)
    cols = {r[0] for r in cr.fetchall()}
    if 'polygon' not in cols:
        cr.execute(
            "ALTER TABLE realestate_plan_region "
            "ADD COLUMN polygon character varying"
        )
        cols.add('polygon')
    if 'color' not in cols:
        cr.execute(
            "ALTER TABLE realestate_plan_region "
            "ADD COLUMN color character varying"
        )
        cols.add('color')

    rect_cols = {'x_pct', 'y_pct', 'w_pct', 'h_pct'}
    if rect_cols.issubset(cols):
        cr.execute("""
            SELECT id, x_pct, y_pct, w_pct, h_pct
              FROM realestate_plan_region
             WHERE polygon IS NULL OR polygon = ''
        """)
        rows = cr.fetchall()
        for rid, x, y, w, h in rows:
            x, y, w, h = float(x or 0), float(y or 0), float(w or 0), float(h or 0)
            poly = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
            cr.execute(
                "UPDATE realestate_plan_region SET polygon = %s WHERE id = %s",
                (json.dumps(poly), rid),
            )
        _logger.info("Converted %s legacy rectangle regions to polygons.", len(rows))
        for col in rect_cols:
            cr.execute(
                f"ALTER TABLE realestate_plan_region DROP COLUMN IF EXISTS {col}"
            )

    cr.execute(
        "UPDATE realestate_plan_region SET color = %s WHERE color IS NULL OR color = ''",
        ("#3b82f6",),
    )
