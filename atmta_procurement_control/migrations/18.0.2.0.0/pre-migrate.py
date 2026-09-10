"""Take the requisition and control screens Wave 11 moved out of the shell.

`real_estate_procurement` declared these until Wave 11 emptied it. This module
declares them now, so this runs before its data loads: otherwise Odoo would not
find them under this module's name, create a second record for each, and let
the shell's data reload drop the original.

The requisition screens are here rather than in `atmta_procurement_request`
because they render nine columns this module declares on the request --
reservations, approval steps, control status. Control is the lowest module that
can validate them.

The restriction-lift wizard moved the other way, to
`atmta_procurement_vendor`, whose model it acts on. That hand-over is done by
vendor's own pre-migrate, because Odoo may load vendor before this module and
the receiving side has to claim the rows first.

`(module, name)` is unique on `ir_model_data`, so this is an UPDATE of one
column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

SHELL = 'real_estate_procurement'
SHELL_XMLIDS = [
    'view_material_request_list',
    'view_material_request_form',
    'view_material_request_search',
    'action_material_request',
    'action_approval_register',
    'action_approval_rule',
    'action_control_exception',
    'action_procurement_reservation',
    'view_approval_rule_list',
    'view_approval_step_list',
    'view_approval_step_pivot',
    'view_approval_step_search',
    'view_company_form_procurement',
    'view_control_exception_form',
    'view_control_exception_list',
    'view_control_exception_search',
    'view_procurement_reservation_form',
    'view_procurement_reservation_list',
    'view_procurement_reservation_pivot',
    'view_procurement_reservation_search',
]

OLD = 'atmta_procurement_control'


def migrate(cr, version):
    if not version:
        return
    # Wave 11 also moved the requisition and control screens out of the shell.
    # Same reasoning as the other capabilities: this module declares them now,
    # so the rows must name it before its data loads.
    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (OLD, SHELL, SHELL_XMLIDS))
    _logger.info("Wave 11: %s identifiers taken from %s by %s",
                 cr.rowcount, SHELL, OLD)
