"""Take ownership of what real_estate_procurement used to declare.

The hand-over Waves 6-8 established: `(module, name)` is unique on
`ir_model_data`, so this is an UPDATE of one column, run at pre-init because
Odoo installs a dependency before it upgrades the module that depends on it.
The guard accepts `to upgrade` as well as `installed`.

`stock.picking`'s three procurement fields come across too. They are columns on
a table Inventory owns and they stay on it; only the declaring module changes.

No business row is read, written or deleted here.
"""
import logging

from odoo.addons.atmta_procurement_request.hooks import hand_over

_logger = logging.getLogger(__name__)

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_receipt'

MODELS = [
    'realestate.procurement.receipt.inspection',
    'realestate.procurement.receipt.inspection.line',
]
EXTRA_FIELDS = [
    ('stock.picking', 're_inspection_id'),
    ('stock.picking', 're_inspection_state'),
    ('stock.picking', 're_inspection_policy'),
]
XMLIDS = ['access_receipt_inspection_inspector',
    'access_receipt_inspection_user',
    'access_receipt_inspection_manager',
    'access_receipt_inspection_line_inspector',
    'access_receipt_inspection_line_user',
    'access_receipt_inspection_line_manager',
    'rule_receipt_inspection_company',
    'rule_receipt_inspection_line_company',
    'seq_receipt_inspection']


def pre_init_hook(env):
    cr = env.cr
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
