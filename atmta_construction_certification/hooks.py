"""Take certification from the monolith that used to declare it.

`atmta_construction_certification` is new in Wave 19, so it has no migration
history to run: a module being *installed* never executes `migrations/`. The
hand-over happens here, in `pre_init_hook`, before this module's models are
reflected and its data is loaded.

Without it the move is destructive on an existing database. Reflecting
`realestate.construction.payment.certificate` here would create a second
identifier for the model and for each of its fields while
`real_estate_construction`'s identifiers still point at the originals, and that
module's reload would then drop them -- cascading through `ir.model` to the
tables. On a live project that is every certificate issued, every amount
retained and every advance recovered.

`certification_line_ids` moves *to* this module rather than staying behind.
Wave 18 left it in the monolith because certificates were still there; they
are here now, so the relation comes with them and is swept in with the rest.

The sequences are `noupdate`, which `_process_end` skips when it cleans up.
Wave 6 lost four sequences to exactly that.

The guard accepts `to upgrade` as well as `installed`.

`(module, name)` is unique on `ir_model_data`, so all of this is an UPDATE of
one column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_certification'

MODELS = [
    'realestate.construction.payment.certificate',
    'realestate.construction.payment.certificate.line',
    'realestate.construction.retention',
    'realestate.construction.retention.release',
    'realestate.construction.advance',
    'realestate.construction.advance.recovery',
    'realestate.owner.progress.billing',
    'realestate.owner.progress.billing.deduction',
]

# Declared by this module on a model `atmta_construction_site` owns. Wave 18
# left it above; it belongs here now.
FOREIGN_FIELDS = [
    ('realestate.boq.line', 'certification_line_ids'),
]

# Wave 21 — the screens for these models, moved down from the monolith
# once they were shown to render no field declared above this module.
VIEW_XMLIDS = [
    'action_advance',
    'action_owner_billing',
    'action_payment_certificate',
    'action_retention',
    'action_retention_release',
    'view_advance_form',
    'view_advance_list',
    'view_construction_config_settings',
    'view_owner_billing_form',
    'view_owner_billing_list',
    'view_payment_certificate_form',
    'view_payment_certificate_list',
    'view_retention_list',
    'view_retention_release_form',
    'view_retention_release_list',
]

XMLIDS = [
    'access_construction_advance_manager',
    'access_construction_advance_recovery_manager',
    'access_construction_advance_recovery_user',
    'access_construction_advance_user',
    'access_construction_retention_manager',
    'access_construction_retention_release_manager',
    'access_construction_retention_release_user',
    'access_construction_retention_user',
    'access_owner_billing_mgr',
    'access_owner_billing_user',
    'access_owner_ded_mgr',
    'access_owner_ded_user',
    'access_payment_cert_manager',
    'access_payment_cert_user',
    'access_pc_line_mgr',
    'access_pc_line_user',
    'rule_realestate_construction_advance_company',
    'rule_realestate_construction_advance_manager',
    'rule_realestate_construction_advance_recovery_company',
    'rule_realestate_construction_payment_certificate_company',
    'rule_realestate_construction_payment_certificate_line_company',
    'rule_realestate_construction_payment_certificate_manager',
    'rule_realestate_construction_retention_company',
    'rule_realestate_construction_retention_manager',
    'rule_realestate_construction_retention_release_company',
    'rule_realestate_construction_retention_release_manager',
    'rule_realestate_owner_progress_billing_company',
    'rule_realestate_owner_progress_billing_manager',
    'seq_construction_advance',
    'seq_construction_retention_release',
    'seq_owner_progress_billing',
    'seq_payment_certificate',
]


def pre_init_hook(env):
    cr = env.cr
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("Wave 19: %s is not installed; nothing to take over", OLD)
        return

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, XMLIDS))
    _logger.info("Wave 19: %s declared identifiers taken from %s",
                 cr.rowcount, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model m
         WHERE d.module = %s AND d.model = 'ir.model'
           AND m.id = d.res_id AND m.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 19: %s ir.model identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields f
         WHERE d.module = %s AND d.model = 'ir.model.fields'
           AND f.id = d.res_id AND f.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 19: %s field identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.module = %s AND d.model = 'ir.model.fields.selection'
           AND s.id = d.res_id AND f.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 19: %s selection identifiers taken", cr.rowcount)

    for model, field in FOREIGN_FIELDS:
        cr.execute("""
            UPDATE ir_model_data d SET module = %s
              FROM ir_model_fields f
             WHERE d.module = %s AND d.model = 'ir.model.fields'
               AND f.id = d.res_id AND f.model = %s AND f.name = %s
        """, (NEW, OLD, model, field))
        if cr.rowcount:
            _logger.info("Wave 19: took %s.%s", model, field)

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, VIEW_XMLIDS))
    _logger.info("Wave 21: %s view identifiers taken from %s", cr.rowcount, OLD)
