"""Shared lease lifecycle vocabulary (Phase 8 support).

Kept in its own module with no Odoo model definitions so every other file --
models, wizards, tests, migrations -- can import the constants without import
cycles.
"""

# ---------------------------------------------------------------------------
# New lifecycle (Phase 8)
# ---------------------------------------------------------------------------

LIFECYCLE_STATES = [
    ('draft', 'Draft'),
    ('proposal', 'Proposal'),
    ('pending_approval', 'Pending Approval'),
    ('pending_signature', 'Pending Signature'),
    ('active', 'Active'),
    ('notice', 'Notice Given'),
    ('ended', 'Ended'),
    ('terminated', 'Terminated'),
    ('cancelled', 'Cancelled'),
]

#: Allowed transitions. Enforced server-side by ``_assert_transition``; the UI
#: only mirrors it. Anything not listed here is rejected.
#:
#: The map is deliberately a little wider than the "ideal" lifecycle because
#: the pre-upgrade buttons (``action_confirm`` -> confirmed,
#: ``action_terminate`` from any committed state) must keep working on live
#: databases. Where a shortcut exists, it is called out below:
#:
#: * ``proposal -> pending_signature`` skips approval. Enabled by default so
#:   the legacy ``action_confirm`` path is unchanged; switch on the company
#:   setting ``re_require_lease_approval`` to force the approval step.
#: * ``pending_signature -> terminated`` mirrors the legacy ``action_terminate``,
#:   which accepted confirmed/invoiced/active.
LIFECYCLE_TRANSITIONS = {
    'draft': ('proposal', 'pending_approval', 'pending_signature', 'cancelled'),
    'proposal': ('draft', 'pending_approval', 'pending_signature', 'cancelled'),
    'pending_approval': ('draft', 'proposal', 'pending_signature', 'cancelled'),
    'pending_signature': ('proposal', 'pending_approval', 'active',
                          'cancelled', 'terminated'),
    'active': ('notice', 'ended', 'terminated'),
    'notice': ('active', 'ended', 'terminated'),
    'ended': ('active',),          # reopened by an approved amendment only
    'terminated': (),
    'cancelled': ('draft',),
}

#: Transitions that require the approval step to have happened, when the
#: company setting ``re_require_lease_approval`` is on.
APPROVAL_GATED_TRANSITIONS = {
    ('draft', 'pending_signature'),
    ('proposal', 'pending_signature'),
}

#: Lifecycle states in which the lease reserves the property against other
#: leases. Draft and proposal deliberately do NOT block, so sales can quote
#: several options on one unit.
BLOCKING_LIFECYCLE = ('pending_signature', 'active', 'notice')

#: Lifecycle states in which the tenant physically holds the property.
OCCUPYING_LIFECYCLE = ('active', 'notice')

#: Terminal states -- no further billing is generated.
CLOSED_LIFECYCLE = ('ended', 'terminated', 'cancelled')


# ---------------------------------------------------------------------------
# Legacy lifecycle (pre-upgrade ``realestate.contract.state``)
# ---------------------------------------------------------------------------

LEGACY_STATES = [
    ('draft', 'Draft'),
    ('ready', 'Ready'),
    ('confirmed', 'Confirmed'),
    ('invoiced', 'Invoiced'),
    ('active', 'Active'),
    ('expired', 'Expired'),
    ('terminated', 'Terminated'),
]

LEGACY_BLOCKING = ('confirmed', 'invoiced', 'active')
LEGACY_OCCUPYING = ('active',)

#: legacy -> new. Used by the migration and by the runtime bridge.
#:
#: ``invoiced`` is the ambiguous one: it was simultaneously a lifecycle state
#: and a billing state. It maps to ``pending_signature`` because that is what it
#: meant operationally (committed, not yet running), and the billing half is
#: carried by the new ``billing_status`` field instead.
LEGACY_TO_LIFECYCLE = {
    'draft': 'draft',
    'ready': 'proposal',
    'confirmed': 'pending_signature',
    'invoiced': 'pending_signature',
    'active': 'active',
    'expired': 'ended',
    'terminated': 'terminated',
}

#: new -> legacy, for keeping the deprecated field readable by downstream code.
LIFECYCLE_TO_LEGACY = {
    'draft': 'draft',
    'proposal': 'ready',
    'pending_approval': 'ready',
    'pending_signature': 'confirmed',
    'active': 'active',
    'notice': 'active',
    'ended': 'expired',
    'terminated': 'terminated',
    'cancelled': 'terminated',
}


# ---------------------------------------------------------------------------
# Orthogonal status dimensions carried alongside the lifecycle (Phase 8)
# ---------------------------------------------------------------------------

BILLING_STATUS = [
    ('not_started', 'Not Started'),
    ('scheduled', 'Scheduled'),
    ('partially_invoiced', 'Partially Invoiced'),
    ('fully_invoiced', 'Fully Invoiced'),
]

PAYMENT_STATUS = [
    ('not_paid', 'Not Paid'),
    ('partial', 'Partially Paid'),
    ('paid', 'Paid'),
    ('overdue', 'Overdue'),
]

SIGNATURE_STATUS = [
    ('not_required', 'Not Required'),
    ('pending', 'Pending'),
    ('signed', 'Signed'),
]

#: Overdue ageing buckets exposed for a future Collections module (Phase 16).
OVERDUE_BUCKETS = [
    ('current', 'Current'),
    ('1_30', '1-30 Days'),
    ('31_60', '31-60 Days'),
    ('61_90', '61-90 Days'),
    ('91_120', '91-120 Days'),
    ('120_plus', '120+ Days'),
]


def overdue_bucket(days):
    """Map a day count to its ageing bucket key."""
    if days is None or days <= 0:
        return 'current'
    if days <= 30:
        return '1_30'
    if days <= 60:
        return '31_60'
    if days <= 90:
        return '61_90'
    if days <= 120:
        return '91_120'
    return '120_plus'
