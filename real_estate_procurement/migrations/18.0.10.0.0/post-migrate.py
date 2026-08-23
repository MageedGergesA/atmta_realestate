# -*- coding: utf-8 -*-
"""Wave 5 — protect field metadata as procurement policy changes owner.

No model moves in this wave. `atmta_procurement_core` owns none; it carries two
extension files, so what changes hands is roughly thirty `ir.model.fields`
identifiers on `res.company`, `realestate.project`, `product.template` and
`product.product`.

That is precisely where Waves 2 and 3 both lost metadata. `_process_end` drops an
identifier the old module no longer produces, and the new owner can only reclaim
it if it declares the field itself — which, for fields contributed by *other*
modules on those same shared models, it does not. Wave 3 measured 50 rows lost on
a combined `-u old,new`.

The guard is the one proven there: hand any orphaned identifier to the module
that actually declares the field. Those modules are not part of this upgrade, so
their identifiers are never examined as stale, and `ir_model.py:2644-2652` keeps
the record when the surplus copy is dropped.

Idempotent: after the first run nothing matches.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_core'

# The shared models the two moved extension files touch.
MODELS = ['res.company', 'realestate.project', 'product.template', 'product.product']


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    cr.execute("""
        SELECT d.id, d.name, m.model, f.name
        FROM ir_model_data d
        JOIN ir_model_fields f ON f.id = d.res_id
        JOIN ir_model m ON m.id = f.model_id
        WHERE d.module = %s AND d.model = 'ir.model.fields' AND m.model = ANY(%s)
    """, (OLD, MODELS))

    rescued = 0
    for imd_id, imd_name, model, field_name in cr.fetchall():
        field = env[model]._fields.get(field_name)
        if field is None:
            continue
        modules = field._modules or ()
        if OLD in modules or NEW in modules:
            # One of the two declares it; the ordinary de-duplication path is
            # correct for those.
            continue
        owners = [m for m in modules if m not in (OLD, NEW)]
        if not owners:
            continue
        owner = owners[-1]
        cr.execute("SELECT 1 FROM ir_model_data WHERE module = %s AND name = %s",
                   (owner, imd_name))
        if cr.fetchone():
            continue
        cr.execute("UPDATE ir_model_data SET module = %s WHERE id = %s",
                   (owner, imd_id))
        rescued += 1

    # -- assert the policy and classification fields still exist -----------
    expected = {
        'res.company': ['procurement_budget_policy', 'procurement_po_governance',
                        'procurement_allow_self_approval', 'procurement_self_approval_limit'],
        'realestate.project': ['procurement_budget_policy', 'procurement_po_governance',
                               'procurement_vendor_policy'],
        'product.template': ['is_construction_material', 'is_realestate_product'],
        'product.product': ['is_construction_material', 'is_realestate_product'],
    }
    for model, names in expected.items():
        cr.execute("""
            SELECT f.name FROM ir_model_fields f JOIN ir_model m ON m.id = f.model_id
            WHERE m.model = %s AND f.name = ANY(%s)
        """, (model, names))
        found = {r[0] for r in cr.fetchall()}
        missing = set(names) - found
        if missing:
            raise AssertionError(
                "Wave 5 lost field metadata on %s: %s" % (model, sorted(missing)))

    _logger.info(
        "ATMTA V2 Wave 5: procurement policy and product classification now "
        "declared by %s; handed %s orphaned field identifier(s) to the modules "
        "that declare them.", NEW, rescued)
