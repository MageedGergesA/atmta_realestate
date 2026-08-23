"""Take ownership of what real_estate_procurement used to declare.

Wave 6 moves demand out of the monolith. The records themselves — the
requisitions, their lines, the sequence that numbers them — are staying exactly
where they are; what changes is which module Odoo believes declares them.

That belief lives in `ir_model_data`, and it has to be corrected **before** this
module's own data is loaded. Odoo installs a dependency before it upgrades the
module that depends on it, so by the time the old module's migration script
could run, this module would already have created a second sequence with the
same code and a second copy of every access rule. A pre-init hook runs earlier
than that: it re-points the existing rows, and the data load that follows finds
them and updates them in place instead of creating rivals.

`(module, name)` is unique on `ir_model_data`, so a hand-over is an UPDATE of
one column. No business row is read, written or deleted here.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_request'

MODELS = [
    'realestate.material.request',
    'realestate.material.request.line',
    'realestate.material.request.revise',
    'realestate.material.request.revision',
    'realestate.material.request.rfq',
    'realestate.procurement.plan',
    'realestate.procurement.plan.line',
]
# Demand linkage this module now declares on a model it does not own.
EXTRA_FIELDS = [
    ('purchase.order.line', 're_material_request_line_id'),
    ('purchase.order.line', 're_material_request_id'),
]
XMLIDS = [
    # access rules
    'access_material_request_user', 'access_material_request_approver',
    'access_material_request_manager', 'access_material_request_line_user',
    'access_material_request_line_manager', 'access_procurement_plan_user',
    'access_procurement_plan_manager', 'access_procurement_plan_line_user',
    'access_procurement_plan_line_manager', 'access_material_request_requester',
    'access_material_request_line_requester', 'access_request_revision_requester',
    'access_request_revision_user', 'access_request_revision_manager',
    'access_plan_requester', 'access_plan_line_requester',
    'access_wizard_revise_user', 'access_wizard_rfq_user',
    # record rules
    'rule_material_request_company', 'rule_material_request_line_company',
    'rule_request_revision_company', 'rule_procurement_plan_company',
    'rule_procurement_plan_line_company', 'rule_material_request_own',
    'rule_material_request_buyer',
    # sequences — noupdate, so nothing reassigns these automatically
    'seq_material_request', 'seq_procurement_plan',
]


def hand_over(cr, old, new, models, extra_fields, xmlids):
    """Re-point every identifier this module is taking over. Returns counts."""
    moved = {'model': 0, 'field': 0, 'other': 0, 'discarded': 0}

    def repoint(kind, ids):
        if not ids:
            return 0
        # A name already claimed under the new module means a previous, partial
        # run got there first. Keep the row that is already correct and drop the
        # stale duplicate rather than colliding with the unique index.
        cr.execute("""
            DELETE FROM ir_model_data old_row
             USING ir_model_data new_row
             WHERE old_row.module = %s AND new_row.module = %s
               AND old_row.name = new_row.name
               AND old_row.model = new_row.model
               AND old_row.id = ANY(%s)
        """, (old, new, list(ids)))
        moved['discarded'] += cr.rowcount
        cr.execute("""
            UPDATE ir_model_data SET module = %s
             WHERE id = ANY(%s) AND module = %s
        """, (new, list(ids), old))
        return cr.rowcount

    for model in models:
        cr.execute("SELECT id FROM ir_model WHERE model = %s", (model,))
        row = cr.fetchone()
        if not row:
            continue
        cr.execute("""SELECT id FROM ir_model_data
                       WHERE module=%s AND model='ir.model' AND res_id=%s""",
                   (old, row[0]))
        moved['model'] += repoint('ir.model', [r[0] for r in cr.fetchall()])
        cr.execute("""SELECT d.id FROM ir_model_data d
                        JOIN ir_model_fields f ON f.id = d.res_id
                       WHERE d.module=%s AND d.model='ir.model.fields'
                         AND f.model=%s""", (old, model))
        moved['field'] += repoint('ir.model.fields', [r[0] for r in cr.fetchall()])

    for model, fname in extra_fields:
        cr.execute("""SELECT d.id FROM ir_model_data d
                        JOIN ir_model_fields f ON f.id = d.res_id
                       WHERE d.module=%s AND d.model='ir.model.fields'
                         AND f.model=%s AND f.name=%s""", (old, model, fname))
        moved['field'] += repoint('ir.model.fields', [r[0] for r in cr.fetchall()])

    cr.execute("""SELECT id FROM ir_model_data
                   WHERE module=%s AND name = ANY(%s)""", (old, xmlids))
    moved['other'] += repoint('mixed', [r[0] for r in cr.fetchall()])
    return moved


def pre_init_hook(env):
    cr = env.cr
    # 'to upgrade' matters as much as 'installed': on an upgrade run Odoo has
    # already moved the old module out of 'installed' by the time this hook
    # fires, and treating that as "nothing to take over" is what leaves a
    # second sequence behind for every code the old module owned.
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("%s: %s is not installed, nothing to take over.", NEW, OLD)
        return
    moved = hand_over(cr, OLD, NEW, MODELS, EXTRA_FIELDS, XMLIDS)
    _logger.info(
        "%s took ownership from %s: %s model(s), %s field(s), %s other "
        "identifier(s); %s stale duplicate(s) discarded.",
        NEW, OLD, moved['model'], moved['field'], moved['other'],
        moved['discarded'])
