"""Take document control from the monolith that used to declare it.

`atmta_construction_documents` is new in Wave 14, so it has no migration
history to run: a module being *installed* never executes `migrations/`. The
hand-over happens here, in `pre_init_hook`, which fires once, before this
module's models are reflected and its data is loaded.

Without it the move is destructive on an existing database. Reflecting
`realestate.construction.document` here would create a second identifier for
the model and for each of its fields while `real_estate_construction`'s
identifiers still point at the originals. That module's data reload would then
find them undeclared and remove them, and removing an `ir.model` identifier
cascades to the model row -- taking the table with it, and with it every
drawing, submittal, transmittal and RFI on the project.

Three groups move:

* the identifiers this module now declares by name: the views and their
  actions, the twenty access rules, the fourteen company record rules, and the
  three sequences. The sequences are `noupdate`, which `_process_end` skips
  when it cleans up, so nothing else would ever hand them over -- Wave 6 lost
  four sequences to exactly that;
* the model and field metadata Odoo generates for the ten models, matched by
  what they point at because Odoo, not this module, named them;
* nothing else. `change_event_id` on the submittal and the RFI stays with
  `real_estate_construction`, which declares it, so the field sweep excludes
  it by name. Taking it would leave that module's next data reload dropping an
  identifier it no longer owned, and the column with it.

The guard accepts `to upgrade` as well as `installed`: an operator upgrading
the monolith in the same run has already moved it out of `installed`.

`(module, name)` is unique on `ir_model_data`, so all of this is an UPDATE of
one column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_documents'

MODELS = [
    'realestate.construction.document',
    'realestate.construction.document.revision',
    'realestate.construction.submittal',
    'realestate.construction.submittal.package',
    'realestate.construction.submittal.review',
    'realestate.construction.submittal.revision',
    'realestate.construction.transmittal',
    'realestate.construction.transmittal.line',
    'realestate.construction.rfi',
    'realestate.construction.rfi.response',
]

# Declared above this module, on models it owns. It stays there.
STAYS_ABOVE = ['change_event_id']

# Wave 23 — the screens that came down once the change-event link did,
# and the link itself. Both were declared by `real_estate_construction`
# until change events got their own module.
W23_XMLIDS = [
    'action_document',
    'action_document_revision',
    'action_rfi',
    'action_submittal',
    'action_transmittal',
    'view_document_form',
    'view_document_list',
    'view_document_revision_list',
    'view_rfi_form',
    'view_rfi_list',
    'view_submittal_form',
    'view_submittal_list',
    'view_transmittal_form',
    'view_transmittal_list',
]

W23_FIELDS = [
    ('realestate.construction.submittal', 'change_event_id'),
    ('realestate.construction.rfi', 'change_event_id'),
]

XMLIDS = [
    'access_construction_document_manager',
    'access_construction_document_revision_manager',
    'access_construction_document_revision_user',
    'access_construction_document_user',
    'access_construction_rfi_manager',
    'access_construction_rfi_response_manager',
    'access_construction_rfi_response_user',
    'access_construction_rfi_user',
    'access_construction_submittal_manager',
    'access_construction_submittal_package_manager',
    'access_construction_submittal_package_user',
    'access_construction_submittal_review_manager',
    'access_construction_submittal_review_user',
    'access_construction_submittal_revision_manager',
    'access_construction_submittal_revision_user',
    'access_construction_submittal_user',
    'access_construction_transmittal_line_manager',
    'access_construction_transmittal_line_user',
    'access_construction_transmittal_manager',
    'access_construction_transmittal_user',
    'rule_realestate_construction_document_company',
    'rule_realestate_construction_document_manager',
    'rule_realestate_construction_document_revision_company',
    'rule_realestate_construction_document_revision_manager',
    'rule_realestate_construction_rfi_company',
    'rule_realestate_construction_rfi_manager',
    'rule_realestate_construction_submittal_company',
    'rule_realestate_construction_submittal_manager',
    'rule_realestate_construction_submittal_package_company',
    'rule_realestate_construction_submittal_package_manager',
    'rule_realestate_construction_submittal_revision_company',
    'rule_realestate_construction_transmittal_company',
    'rule_realestate_construction_transmittal_line_company',
    'rule_realestate_construction_transmittal_manager',
    'seq_construction_rfi',
    'seq_construction_submittal',
    'seq_construction_transmittal',
]


def pre_init_hook(env):
    cr = env.cr
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("Wave 14: %s is not installed; nothing to take over", OLD)
        return

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, XMLIDS))
    _logger.info("Wave 14: %s declared identifiers taken from %s",
                 cr.rowcount, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model m
         WHERE d.module = %s AND d.model = 'ir.model'
           AND m.id = d.res_id AND m.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 14: %s ir.model identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields f
         WHERE d.module = %s AND d.model = 'ir.model.fields'
           AND f.id = d.res_id AND f.model = ANY(%s)
           AND f.name <> ALL(%s)
    """, (NEW, OLD, MODELS, STAYS_ABOVE))
    _logger.info("Wave 14: %s field identifiers taken (%s left with %s)",
                 cr.rowcount, STAYS_ABOVE, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.module = %s AND d.model = 'ir.model.fields.selection'
           AND s.id = d.res_id AND f.model = ANY(%s)
           AND f.name <> ALL(%s)
    """, (NEW, OLD, MODELS, STAYS_ABOVE))
    _logger.info("Wave 14: %s selection identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, W23_XMLIDS))
    _logger.info("Wave 23: %s screen identifiers taken from %s", cr.rowcount, OLD)

    for model, field in W23_FIELDS:
        cr.execute("""
            UPDATE ir_model_data d SET module = %s
              FROM ir_model_fields f
             WHERE d.module = %s AND d.model = 'ir.model.fields'
               AND f.id = d.res_id AND f.model = %s AND f.name = %s
        """, (NEW, OLD, model, field))
        if cr.rowcount:
            _logger.info("Wave 23: took %s.%s", model, field)

# Wave 25 -- the project-team and manager rules, moved down from
# `real_estate_construction` once that module stopped declaring
# `construction_member_ids`. The identifiers keep their original names, so the
# hand-over is an UPDATE of the module column and the loader then updates the
# existing rows in place instead of creating second copies.
W25_RULE_XMLIDS = [
    'rule_realestate_construction_document_project_member',
    'rule_realestate_construction_document_revision_project_member',
    'rule_realestate_construction_rfi_project_member',
    'rule_realestate_construction_submittal_package_project_member',
    'rule_realestate_construction_submittal_project_member',
    'rule_realestate_construction_transmittal_project_member',
]
