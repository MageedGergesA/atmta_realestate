"""Take claims from the monolith that used to declare them.

`atmta_construction_claims` is new in Wave 17, so it has no migration history
to run: a module being *installed* never executes `migrations/`. The hand-over
happens here, in `pre_init_hook`, before this module's models are reflected and
its data is loaded.

Without it the move is destructive on an existing database. Reflecting
`realestate.construction.claim` here would create a second identifier for the
model and for each of its fields while `real_estate_construction`'s identifiers
still point at the originals, and that module's reload would then drop them --
cascading through `ir.model` to the tables, taking every claim, every
determination and every notice with them. On a contract, those records are the
argument.

`daily_delay_ids` stays above. It is the relation from a delay event to the
daily records evidencing it, and daily reporting did not move, so
`real_estate_construction` keeps declaring it. A blanket field sweep would take
it and the monolith's next reload would drop the column.

The sequences are `noupdate`, which `_process_end` skips when it cleans up.
Wave 6 lost four sequences to exactly that.

The guard accepts `to upgrade` as well as `installed`.

`(module, name)` is unique on `ir_model_data`, so all of this is an UPDATE of
one column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_claims'

MODELS = [
    'realestate.construction.claim',
    'realestate.construction.claim.cost.line',
    'realestate.construction.claim.determination',
    'realestate.construction.claim.evidence',
    'realestate.construction.claim.submission',
    'realestate.construction.delay.event',
    'realestate.construction.eot',
    'realestate.construction.notice',
]

# Declared above this module, on a model it owns.
STAYS_ABOVE = ['daily_delay_ids']

XMLIDS = [
    'access_claim_commercial',
    'access_claim_cost_line_commercial',
    'access_claim_cost_line_manager',
    'access_claim_determination_commercial',
    'access_claim_determination_manager',
    'access_claim_evidence_commercial',
    'access_claim_evidence_manager',
    'access_claim_manager',
    'access_claim_submission_commercial',
    'access_claim_submission_manager',
    'access_delay_event_manager',
    'access_delay_event_user',
    'access_eot_commercial',
    'access_eot_manager',
    'access_notice_manager',
    'access_notice_user',
    'rule_realestate_construction_claim_company',
    'rule_realestate_construction_claim_cost_line_company',
    'rule_realestate_construction_claim_determination_company',
    'rule_realestate_construction_claim_evidence_company',
    'rule_realestate_construction_claim_manager',
    'rule_realestate_construction_claim_submission_company',
    'rule_realestate_construction_delay_event_company',
    'rule_realestate_construction_delay_event_manager',
    'rule_realestate_construction_eot_company',
    'rule_realestate_construction_eot_manager',
    'rule_realestate_construction_notice_company',
    'rule_realestate_construction_notice_manager',
    'seq_construction_claim',
    'seq_construction_claim_determination',
    'seq_construction_delay_event',
    'seq_construction_eot',
    'seq_construction_notice',
]


def pre_init_hook(env):
    cr = env.cr
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("Wave 17: %s is not installed; nothing to take over", OLD)
        return

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, XMLIDS))
    _logger.info("Wave 17: %s declared identifiers taken from %s",
                 cr.rowcount, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model m
         WHERE d.module = %s AND d.model = 'ir.model'
           AND m.id = d.res_id AND m.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 17: %s ir.model identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields f
         WHERE d.module = %s AND d.model = 'ir.model.fields'
           AND f.id = d.res_id AND f.model = ANY(%s)
           AND f.name <> ALL(%s)
    """, (NEW, OLD, MODELS, STAYS_ABOVE))
    _logger.info("Wave 17: %s field identifiers taken (%s left with %s)",
                 cr.rowcount, STAYS_ABOVE, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.module = %s AND d.model = 'ir.model.fields.selection'
           AND s.id = d.res_id AND f.model = ANY(%s)
           AND f.name <> ALL(%s)
    """, (NEW, OLD, MODELS, STAYS_ABOVE))
    _logger.info("Wave 17: %s selection identifiers taken", cr.rowcount)

# Wave 25 -- the project-team and manager rules, moved down from
# `real_estate_construction` once that module stopped declaring
# `construction_member_ids`. The identifiers keep their original names, so the
# hand-over is an UPDATE of the module column and the loader then updates the
# existing rows in place instead of creating second copies.
W25_RULE_XMLIDS = [
    'rule_realestate_construction_claim_project_member',
    'rule_realestate_construction_delay_event_project_member',
    'rule_realestate_construction_eot_project_member',
    'rule_realestate_construction_notice_project_member',
]
