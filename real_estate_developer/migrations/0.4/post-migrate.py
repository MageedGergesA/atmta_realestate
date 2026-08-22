# -*- coding: utf-8 -*-
"""Wave 2 — keep mixin field metadata from falling through the ownership gap.

When this module stopped declaring `realestate.project`, `realestate.phase` and
`realestate.project.boundary.point`, every `ir.model.fields` identifier it owned
for them became stale. For fields that `atmta_project_core` declares, that is
harmless: Core creates its own identifier, and `IrModelData._process_end` keeps
the record and drops only the surplus name (`ir_model.py:2644-2652`).

Three fields per mail-tracked model are not in that group:

    activity_calendar_event_id   -> calendar
    message_has_sms_error        -> sms
    website_message_ids          -> portal

They reach these models through `mail.thread` / `mail.activity.mixin`, but they
are contributed by modules `atmta_project_core` does not depend on and must not:
Project Core sits on `base` and `mail`, and widening that to drag in Calendar,
SMS and Portal would be inventing a dependency to satisfy a bookkeeping detail.

So Core cannot claim those identifiers, this module no longer owns them, and
`_process_end` deletes both the name and the row. The fields keep working — they
are in the registry, and nothing user-facing reads `ir_model_fields` for them —
but their metadata is gone until the contributing modules happen to be reflected
again. Measured on the Wave 2 fixture: six rows lost after `-u
real_estate_developer`, all six restored by a later `-u calendar,sms,portal`.

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

OLD = 'real_estate_developer'
NEW = 'atmta_project_core'

MODELS = (
    'realestate.project',
    'realestate.phase',
    'realestate.project.boundary.point',
)


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

    _logger.info(
        "ATMTA V2 Wave 2: handed %s mixin field identifier(s) to the modules "
        "that declare them, so _process_end does not delete metadata that "
        "neither %s nor %s can own.", rescued, OLD, NEW)
