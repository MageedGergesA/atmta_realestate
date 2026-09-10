"""Take the identifiers Wave 11 moved into this module.

`real_estate_procurement` declared these until Wave 11 emptied it. This module
declares them now, so this runs before its data loads: without it Odoo would
look up each identifier under this module's name, not find it, and create a
second record beside the one the shell's identifier still points at. The shell
would then drop its identifier and take the original with it.

Re-pointing the rows first means the load finds them and updates the records
already there. `_process_end` only removes identifiers belonging to the module
being loaded, so once a row names this module the shell leaves it alone.

It runs here rather than in the shell because Odoo loads a dependency before
the module that depends on it: by the time the shell is processed this module
has already loaded its data, and the duplicates would exist.

`(module, name)` is unique on `ir_model_data`, so this is an UPDATE of one
column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_evaluation'
XMLIDS = [
    'action_evaluation_audit_wizard',
    'action_evaluation_plan',
    'action_evaluation_round',
    'action_report_evaluation',
    'action_technical_evaluation',
    'report_evaluation',
    'report_evaluation_document',
    'view_evaluation_audit_wizard_form',
    'view_evaluation_plan_form',
    'view_evaluation_plan_list',
    'view_evaluation_round_form',
    'view_evaluation_round_list',
    'view_technical_evaluation_form',
]


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, XMLIDS))
    _logger.info("Wave 11: %s identifiers taken from %s by %s",
                 cr.rowcount, OLD, NEW)
