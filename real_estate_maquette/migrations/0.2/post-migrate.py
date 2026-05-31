def migrate(cr, version):
    """Back-sync project_id from existing 2D-plan regions onto their linked
    properties. Catches regions created before the auto-sync hook was added."""
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['realestate.building.region']._backfill_property_projects()
