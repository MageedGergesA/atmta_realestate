"""Take quality from the monolith that used to declare it.

`atmta_construction_quality` is new in Wave 15, so it has no migration history
to run: a module being *installed* never executes `migrations/`. The hand-over
happens here, in `pre_init_hook`, which fires once, before this module's models
are reflected and its data is loaded.

Without it the move is destructive on an existing database. Reflecting
`realestate.construction.ncr` here would create a second identifier for the
model and for each of its fields while `real_estate_construction`'s identifiers
still point at the originals. That module's data reload would then find them
undeclared and remove them, and removing an `ir.model` identifier cascades to
the model row, taking the table with it -- every inspection, observation and
non-conformance on the project.

Three groups move: the identifiers named below (nineteen access rules,
thirteen company record rules, five sequences); the model and field metadata
Odoo generates for the ten models, matched by what they point at because Odoo
named them; and nothing else. `change_event_id` on the NCR stays with
`real_estate_construction`, which declares it, so the field sweep excludes it
by name -- taking it would leave that module dropping an identifier it no
longer owned, and the column with it.

The sequences are `noupdate`, which `_process_end` skips when it cleans up, so
nothing else would ever hand them over. Wave 6 lost four sequences to exactly
that.

The guard accepts `to upgrade` as well as `installed`: an operator upgrading
the monolith in the same run has already moved it out of `installed`.

`(module, name)` is unique on `ir_model_data`, so all of this is an UPDATE of
one column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_quality'

MODELS = [
    'realestate.construction.itp',
    'realestate.construction.itp.item',
    'realestate.construction.checklist.template',
    'realestate.construction.checklist.template.line',
    'realestate.construction.inspection.request',
    'realestate.construction.inspection',
    'realestate.construction.inspection.line',
    'realestate.construction.quality.observation',
    'realestate.construction.ncr',
    'realestate.construction.reason.wizard',
]

# Declared above this module, on a model it owns. It stays there.
STAYS_ABOVE = ['change_event_id']

XMLIDS = [
    'access_construction_checklist_template_line_manager',
    'access_construction_checklist_template_line_user',
    'access_construction_checklist_template_manager',
    'access_construction_checklist_template_user',
    'access_construction_inspection_line_manager',
    'access_construction_inspection_line_user',
    'access_construction_inspection_manager',
    'access_construction_inspection_request_manager',
    'access_construction_inspection_request_user',
    'access_construction_inspection_user',
    'access_construction_itp_item_manager',
    'access_construction_itp_item_user',
    'access_construction_itp_manager',
    'access_construction_itp_user',
    'access_construction_ncr_manager',
    'access_construction_ncr_user',
    'access_construction_observation_manager',
    'access_construction_observation_user',
    'access_construction_reason_wizard',
    'rule_realestate_construction_checklist_template_company',
    'rule_realestate_construction_inspection_company',
    'rule_realestate_construction_inspection_line_company',
    'rule_realestate_construction_inspection_manager',
    'rule_realestate_construction_inspection_request_company',
    'rule_realestate_construction_inspection_request_manager',
    'rule_realestate_construction_itp_company',
    'rule_realestate_construction_itp_item_company',
    'rule_realestate_construction_itp_manager',
    'rule_realestate_construction_ncr_company',
    'rule_realestate_construction_ncr_manager',
    'rule_realestate_construction_quality_observation_company',
    'rule_realestate_construction_quality_observation_manager',
    'seq_construction_inspection',
    'seq_construction_inspection_request',
    'seq_construction_itp',
    'seq_construction_ncr',
    'seq_construction_observation',
]


def pre_init_hook(env):
    cr = env.cr
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("Wave 15: %s is not installed; nothing to take over", OLD)
        return

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, XMLIDS))
    _logger.info("Wave 15: %s declared identifiers taken from %s",
                 cr.rowcount, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model m
         WHERE d.module = %s AND d.model = 'ir.model'
           AND m.id = d.res_id AND m.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 15: %s ir.model identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields f
         WHERE d.module = %s AND d.model = 'ir.model.fields'
           AND f.id = d.res_id AND f.model = ANY(%s)
           AND f.name <> ALL(%s)
    """, (NEW, OLD, MODELS, STAYS_ABOVE))
    _logger.info("Wave 15: %s field identifiers taken (%s left with %s)",
                 cr.rowcount, STAYS_ABOVE, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.module = %s AND d.model = 'ir.model.fields.selection'
           AND s.id = d.res_id AND f.model = ANY(%s)
           AND f.name <> ALL(%s)
    """, (NEW, OLD, MODELS, STAYS_ABOVE))
    _logger.info("Wave 15: %s selection identifiers taken", cr.rowcount)
