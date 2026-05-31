def migrate(cr, version):
    """The legacy realestate.installment.plan model is removed. Drop the inbound
    FK columns so Odoo can drop its table without constraint errors."""
    cr.execute("ALTER TABLE IF EXISTS realestate_sale_contract DROP COLUMN IF EXISTS installment_plan_id")
    cr.execute("ALTER TABLE IF EXISTS realestate_unit_reservation DROP COLUMN IF EXISTS installment_plan_id")
