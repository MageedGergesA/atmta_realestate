# -*- coding: utf-8 -*-
"""Wave 1 — hand the Vendor Governance models over to atmta_procurement_vendor.

WHY THIS LIVES HERE AND NOT IN THE NEW MODULE
---------------------------------------------
Odoo runs `migrations/<version>/pre-migrate.py` only when a module is
**upgraded**, never when it is **installed for the first time**. A script placed
in the brand-new `atmta_procurement_vendor` would therefore never execute on the
very databases that need it — the hazard Odoo's SaaS tooling works around with
`force_upgrade_of_fresh_module()`.

That helper is not available here: `odoo/upgrade/` on this deployment is only the
four-line `pkgutil.extend_path` namespace stub, `odoo.upgrade.util` does not
import, `upgrade_path` is empty, and there is no `openupgradelib`. So
`move_model()`, `move_field()` and `rename_xmlid()` cannot be called, and this is
the hand-written minimum equivalent.

Putting it in `real_estate_procurement` — which *is* being upgraded, 18.0.8.0.0
to 18.0.9.0.0 — sidesteps the problem entirely. It is guaranteed to run on every
populated database, and `real_estate_procurement` now depends on
`atmta_procurement_vendor`, so the new module is always loaded first and in the
same transaction.

WHAT IT DOES, AND WHAT IT REFUSES TO DO
---------------------------------------
It moves **ownership metadata only**: the `ir.model.data` rows that say which
module declared each model and each field. It does not rename a model, rename a
table, create a table, copy a row, or touch a single business record. The
existing tables stay authoritative; every id, create_date and value is left
exactly where it is.

Two cases, because by the time this runs the new module has already reflected
the models and may have created its own XML-IDs:

  * target XML-ID absent  -> rename the source row's module (the cheap path)
  * target XML-ID present -> delete the now-redundant source row

Both leave exactly one XML-ID per record. Idempotent: after the first run there
is nothing left matching `module = 'real_estate_procurement'`, so a second run
moves nothing.

WHY DELETING THE STALE ROW IS SAFE
----------------------------------
`IrModelData._process_end()` would remove it anyway at the end of the load, and
its own source is explicit that a record with another XML-ID keeps the record and
drops only the identifier:

    # if the record has other associated xids, only remove the xid
    if self.search_count([("model","=",model), ("res_id","=",res_id), ...]):
        bad_imd_ids.append(id); continue

Doing it here makes the hand-over deterministic instead of dependent on that
ordering, and lets the result be asserted before anything else runs.
"""

import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_vendor'

#: The fifteen models assigned to the capability by
#: ~/atmta_v2_architecture/05_MODEL_TO_MODULE_MAP.csv. Listed literally rather
#: than pattern-matched, so a model can never be swept along by accident.
MODELS = (
    'realestate.procurement.avl.report',
    'realestate.procurement.avl.report.line',
    'realestate.procurement.qualification.area',
    'realestate.procurement.qualification.condition',
    'realestate.procurement.qualification.reject',
    'realestate.procurement.qualification.requirement',
    'realestate.procurement.qualification.response',
    'realestate.procurement.qualification.template',
    'realestate.procurement.vendor.audit',
    'realestate.procurement.vendor.audit.line',
    'realestate.procurement.vendor.category',
    'realestate.procurement.vendor.eligibility',
    'realestate.procurement.vendor.profile',
    'realestate.procurement.vendor.qualification',
    'realestate.procurement.vendor.restriction',
)

#: The res.partner fields the vendor extension adds. res.partner itself stays
#: Odoo's — only these field declarations change hands.
PARTNER_FIELDS = (
    'is_realestate_vendor',
    'procurement_profile_id',
    'procurement_qualification_ids',
    'procurement_qualification_count',
    'procurement_restriction_count',
    'procurement_vendor_class',
)


def _hand_over(cr, names):
    """Move `ir_model_data` rows OLD.<name> -> NEW.<name>, collision-safe."""
    if not names:
        return 0, 0
    cr.execute("""
        SELECT o.id, o.name, n.id
          FROM ir_model_data o
          LEFT JOIN ir_model_data n ON n.module = %s AND n.name = o.name
         WHERE o.module = %s AND o.name = ANY(%s)
    """, (NEW, OLD, list(names)))
    renamed = dropped = 0
    for old_id, name, new_id in cr.fetchall():
        if new_id:
            cr.execute("DELETE FROM ir_model_data WHERE id = %s", (old_id,))
            dropped += 1
        else:
            cr.execute("UPDATE ir_model_data SET module = %s WHERE id = %s", (NEW, old_id))
            renamed += 1
    return renamed, dropped


def migrate(cr, version):
    if not version:
        # Fresh install of real_estate_procurement: there is no prior ownership
        # to hand over, because atmta_procurement_vendor declared the models
        # from the start.
        return

    # -- 1. the ir.model rows -------------------------------------------
    model_xmlids = ['model_' + m.replace('.', '_') for m in MODELS]
    m_ren, m_drop = _hand_over(cr, model_xmlids)

    # -- 2. every field on those models ---------------------------------
    cr.execute("""
        SELECT d.name
          FROM ir_model_data d
          JOIN ir_model_fields f ON f.id = d.res_id
         WHERE d.module = %s AND d.model = 'ir.model.fields' AND f.model = ANY(%s)
    """, (OLD, list(MODELS)))
    field_xmlids = [r[0] for r in cr.fetchall()]
    f_ren, f_drop = _hand_over(cr, field_xmlids)

    # -- 3. the res.partner vendor fields -------------------------------
    cr.execute("""
        SELECT d.name
          FROM ir_model_data d
          JOIN ir_model_fields f ON f.id = d.res_id
         WHERE d.module = %s AND d.model = 'ir.model.fields'
           AND f.model = 'res.partner' AND f.name = ANY(%s)
    """, (OLD, list(PARTNER_FIELDS)))
    partner_xmlids = [r[0] for r in cr.fetchall()]
    p_ren, p_drop = _hand_over(cr, partner_xmlids)

    # -- 4. assert the hand-over, rather than hope ----------------------
    cr.execute("""
        SELECT count(*) FROM ir_model_data d
          JOIN ir_model m ON m.id = d.res_id
         WHERE d.model = 'ir.model' AND d.module = %s AND m.model = ANY(%s)
    """, (OLD, list(MODELS)))
    leftover = cr.fetchone()[0]
    if leftover:
        raise AssertionError(
            "Wave 1: %s still owns %s Vendor Governance ir.model XML-ID(s) after "
            "the hand-over. Refusing to continue rather than let the module "
            "loader decide what to delete." % (OLD, leftover))

    cr.execute("SELECT count(*) FROM ir_model WHERE model = ANY(%s)", (list(MODELS),))
    present = cr.fetchone()[0]
    if present != len(MODELS):
        raise AssertionError(
            "Wave 1: expected %s Vendor Governance models in ir_model, found %s."
            % (len(MODELS), present))

    _logger.info(
        "Wave 1 Vendor Governance hand-over %s -> %s: "
        "models %s renamed / %s de-duplicated, "
        "model fields %s / %s, res.partner fields %s / %s. "
        "No table, column or business record was touched.",
        OLD, NEW, m_ren, m_drop, f_ren, f_drop, p_ren, p_drop)
