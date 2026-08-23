"""Take ownership of the sourcing identifiers real_estate_procurement declared.

Same hand-over the Wave 6 capabilities perform, and for the same reason: Odoo
installs a dependency before it upgrades the module that depends on it, so by
the time the old module's migration could run this module would already have
created a second sequence for every code it owns. A pre-init hook runs earlier,
re-points the existing rows, and lets the data load find and update them.

The guard accepts `to upgrade` as well as `installed`. On an upgrade run Odoo
has already moved the old module out of `installed`, and treating that as
"nothing to take over" is exactly the defect Wave 6 found — it left four
duplicate sequences behind while still exiting 0.
"""
import logging

from odoo.addons.atmta_procurement_request.hooks import hand_over

_logger = logging.getLogger(__name__)

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_sourcing'

MODELS = [
    'realestate.procurement.sourcing.event',
    'realestate.procurement.sourcing.version',
    'realestate.procurement.sourcing.line',
    'realestate.procurement.sourcing.demand.allocation',
    'realestate.procurement.sourcing.invitation',
    'realestate.procurement.sourcing.acknowledgement',
    'realestate.procurement.bid.response',
    'realestate.procurement.bid.response.line',
    'realestate.procurement.sourcing.clarification',
    'realestate.procurement.sourcing.audit',
]
# Nothing on a legacy-owned model: the solicitation field this module now
# declares was Request's, not the monolith's, and is handed over separately.
EXTRA_FIELDS = []

# Wave 6 gave `vendor_category_id` to atmta_procurement_request. Wave 7 moves
# the whole solicitation surface up here, so the identifier has to come from
# Request rather than from the monolith the other identifiers come from.
FROM_REQUEST = 'atmta_procurement_request'
FROM_REQUEST_FIELDS = [
    ('realestate.material.request.line', 'vendor_category_id'),
]
XMLIDS = [
    'access_sourcing_event_user', 'access_sourcing_event_manager',
    'access_sourcing_version_user', 'access_sourcing_version_manager',
    'access_sourcing_line_user', 'access_sourcing_line_manager',
    'access_sourcing_allocation_user', 'access_sourcing_allocation_manager',
    'access_sourcing_invitation_user', 'access_sourcing_invitation_manager',
    'access_bid_response_user', 'access_bid_response_manager',
    'access_bid_response_line_user', 'access_bid_response_line_manager',
    'access_sourcing_clarification_user', 'access_sourcing_clarification_manager',
    'rule_sourcing_event_company', 'rule_sourcing_version_company',
    'rule_sourcing_line_company', 'rule_sourcing_allocation_company',
    'rule_sourcing_invitation_company', 'rule_bid_response_company',
    'rule_bid_response_line_company', 'rule_sourcing_clarification_company',
    'rule_bid_response_own_event', 'rule_bid_response_manager',
    'rule_bid_response_line_own_event', 'rule_bid_response_line_manager',
    'seq_sourcing_event', 'seq_bid_response', 'seq_sourcing_clarification',
]


def pre_init_hook(env):
    cr = env.cr
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("%s: %s is not installed, nothing to take over.", NEW, OLD)
        return
    moved = hand_over(cr, OLD, NEW, MODELS, EXTRA_FIELDS, XMLIDS)
    cr.execute("SELECT 1 FROM ir_module_module WHERE name=%s AND state IN ('installed','to upgrade')",
               (FROM_REQUEST,))
    if cr.fetchone():
        from_req = hand_over(cr, FROM_REQUEST, NEW, [], FROM_REQUEST_FIELDS, [])
        moved['field'] += from_req['field']
        moved['discarded'] += from_req['discarded']
    _logger.info(
        "%s took ownership from %s: %s model(s), %s field(s), %s other "
        "identifier(s); %s stale duplicate(s) discarded.",
        NEW, OLD, moved['model'], moved['field'], moved['other'],
        moved['discarded'])
