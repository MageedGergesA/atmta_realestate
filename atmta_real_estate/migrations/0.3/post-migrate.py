def migrate(cr, version):
    """Backfill stock for units that existed before the inventory lifecycle:
    flag them storable and place their on-hand quant per state."""
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['realestate.property']._backfill_unit_stock()
