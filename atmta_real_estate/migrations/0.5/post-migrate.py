# -*- coding: utf-8 -*-
"""Wave 3 — keep mixin field metadata from falling through the ownership gap.

When this module stopped declaring `realestate.project`, `realestate.phase` and
`realestate.project.boundary.point`, every `ir.model.fields` identifier it owned
for them became stale. For fields that `atmta_property_core` declares, that is
harmless: Core creates its own identifier, and `IrModelData._process_end` keeps
the record and drops only the surplus name (`ir_model.py:2644-2652`).

Three fields per mail-tracked model are not in that group:

    activity_calendar_event_id   -> calendar
    message_has_sms_error        -> sms
    website_message_ids          -> portal

They reach these models through `mail.thread` / `mail.activity.mixin`, but they
are contributed by modules `atmta_property_core` does not depend on and must not:
Property Core sits on `base`, `mail`, `product` and `web_editor`, and widening
that to drag in Calendar and SMS would be inventing a dependency to satisfy a
bookkeeping detail.

So Core cannot claim those identifiers, this module no longer owns them, and
`_process_end` deletes both the name and the row. The fields keep working — they
are in the registry, and nothing user-facing reads `ir_model_fields` for them —
but their metadata is gone until the contributing modules happen to be reflected
again. Measured on the Wave 3 fixture: six rows lost after `-u
atmta_real_estate`, all six restored by a later `-u calendar,sms,portal`.

"Wrong until something unrelated is upgraded" is not a state to ship. So the
identifiers are handed to the modules that actually declare the fields, before
`_process_end` runs. Those modules are not part of this upgrade, so their
identifiers are never examined as stale, and both the name and the row survive.
This also puts ownership where it belongs: these were only ever attributed to
this module because it happened to be the one declaring the model.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

OLD = 'atmta_real_estate'
NEW = 'atmta_property_core'

MODELS = (
    'realestate.property',
    'property.type',
    'property.usage',
    'property.image',
)


def _protect_delegated_field_identifiers(cr):
    """Keep the delegated `product.template` field metadata from being dropped.

    `realestate.property` delegates to `product.template`, so every field that
    `stock`, `account`, `sale` and friends add to a product also appears on a
    property: `incoming_qty`, `cost_method`, `expense_policy`, `valuation` and
    around fifty more. Odoo attributes those to whichever module declares
    `_inherits` -- which used to be `atmta_real_estate`, and is now
    `atmta_property_core`.

    That worked before Wave 3 by accident: `atmta_real_estate` depends on
    `sale`, `account` and `stock`, so re-reflecting it re-registered them.
    Property Core depends on `base`, `mail`, `product` and `web_editor`, and
    must keep doing so -- a core module that drags in the accounting and
    warehouse stack is not a core module. So on any later
    `-u atmta_real_estate,atmta_property_core` those identifiers are not
    re-registered, `_process_end` finds them stale and deletes both the name and
    the row. Measured: 50 rows lost on the retry, out of 353.

    The fix is to give each of those rows a second identifier belonging to the
    module that actually declares the field on `product.template`. Those modules
    are not part of this upgrade, so their identifiers are never examined as
    stale, and `ir_model.py:2644-2652` then keeps the record when core's own
    copy is dropped. This is the same shape Odoo already uses for the mail
    mixin fields, which carry `mail.field_realestate_property__activity_ids`.
    """
    cr.execute("""
        INSERT INTO ir_model_data (module, name, model, res_id, noupdate)
        SELECT parent_d.module,
               'field_realestate_property__' || child_f.name,
               'ir.model.fields', child_f.id, false
        FROM ir_model_fields child_f
        JOIN ir_model child_m ON child_m.id = child_f.model_id
                             AND child_m.model = 'realestate.property'
        JOIN ir_model_fields parent_f ON parent_f.name = child_f.name
        JOIN ir_model parent_m ON parent_m.id = parent_f.model_id
                              AND parent_m.model = 'product.template'
        JOIN ir_model_data parent_d ON parent_d.model = 'ir.model.fields'
                                   AND parent_d.res_id = parent_f.id
        WHERE parent_d.module NOT IN (%s, %s)
          AND NOT EXISTS (
              SELECT 1 FROM ir_model_data existing
              WHERE existing.module = parent_d.module
                AND existing.name = 'field_realestate_property__' || child_f.name)
        ON CONFLICT DO NOTHING
    """, (OLD, NEW))
    return cr.rowcount

def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    cr.execute("""
        SELECT d.id, d.name, m.model, f.name
        FROM ir_model_data d
        JOIN ir_model_fields f ON f.id = d.res_id
        JOIN ir_model m ON m.id = f.model_id
        WHERE d.module = %s AND d.model = 'ir.model.fields'
          AND m.model = ANY(%s)
    """, (OLD, list(MODELS)))
    rows = cr.fetchall()

    rescued = 0
    for imd_id, imd_name, model, field_name in rows:
        field = env[model]._fields.get(field_name)
        if field is None:
            continue
        owners = [m for m in (field._modules or ()) if m not in (OLD, NEW)]
        if not owners or NEW in (field._modules or ()) or OLD in (field._modules or ()):
            # Core (or this module) declares it; the normal de-duplication path
            # already handles those correctly.
            continue
        owner = owners[-1]
        cr.execute("""
            SELECT 1 FROM ir_model_data WHERE module = %s AND name = %s
        """, (owner, imd_name))
        if cr.fetchone():
            continue
        cr.execute("UPDATE ir_model_data SET module = %s WHERE id = %s",
                   (owner, imd_id))
        rescued += 1

    delegated = _protect_delegated_field_identifiers(cr)

    _logger.info(
        "ATMTA V2 Wave 3: handed %s mixin field identifier(s) to the modules "
        "that declare them, and gave %s delegated product.template field(s) a "
        "second identifier owned by their declaring module, so _process_end "
        "does not delete metadata that neither %s nor %s can own.",
        rescued, delegated, OLD, NEW)
