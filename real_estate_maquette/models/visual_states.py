# -*- coding: utf-8 -*-
"""Shared vocabulary for the Interactive Sales Gallery.

No ORM imports, deliberately — the same pattern `commercial_states.py` and
`check_states.py` use elsewhere in the suite. Tests, migrations, controllers and
the JSON payloads can all import these without dragging a registry along, and
there is exactly one spelling of every state.

### Why a *visual* state vocabulary exists at all

The audit found the viewer colouring units from `realestate.property.state` —
Module 1's legacy field, which mixes physical condition with sales meaning.
Developer already owns the authoritative answer in `is_available_for_sale` plus
`sale_unavailable_reason` plus `commercial_status`, but that is three fields and
eleven reason codes: correct for an engine, far too much for a colour.

So this maps Developer's answer down to the seven states a customer looking at a
building can actually distinguish, without inventing any of them:

```
    Developer                                  Visual
    ──────────────────────────────────────     ─────────────
    is_available_for_sale = True           →   available
    reason = committed, status = held      →   held
    reason = committed, status = reserved  →   reserved
    reason = committed, status = contracted→   contracted
    reason = committed, status = sold      →   sold
    reason = blocked | maintenance         →   blocked
    reason = not_released | release_window
           | project_not_selling
           | phase_not_selling             →   unreleased
    reason = archived | not_a_unit
           | no_project                    →   not_for_sale
```

The mapping is one-way and lossy on purpose. Nothing reads back from a visual
state into a commercial decision.

### Colour is never the only signal

M25 requires that status is not conveyed by colour alone, so every entry
carries a label and an icon as well, and the legend is built from this one
table rather than from anything hard-coded in a template.
"""

#: What a unit can look like in the gallery.
VISUAL_STATE = [
    ('available', 'Available'),
    ('held', 'On Hold'),
    ('reserved', 'Reserved'),
    ('contracted', 'Contracted'),
    ('sold', 'Sold'),
    ('blocked', 'Blocked'),
    ('unreleased', 'Not Yet Released'),
    ('not_for_sale', 'Not for Sale'),
    ('unmapped', 'No Commercial Data'),
]

#: Presentation for each state: colour, label key, icon. One table, consumed by
#: the 3D material tint, the 2D polygon fill and the legend, so those three can
#: never drift apart.
VISUAL_STATE_PRESENTATION = {
    'available':    {'color': '#22c55e', 'icon': 'fa-check-circle',  'order': 10},
    'held':         {'color': '#f59e0b', 'icon': 'fa-hourglass-half', 'order': 20},
    'reserved':     {'color': '#f97316', 'icon': 'fa-bookmark',      'order': 30},
    'contracted':   {'color': '#8b5cf6', 'icon': 'fa-file-text',     'order': 40},
    'sold':         {'color': '#ef4444', 'icon': 'fa-times-circle',  'order': 50},
    'blocked':      {'color': '#64748b', 'icon': 'fa-ban',           'order': 60},
    'unreleased':   {'color': '#94a3b8', 'icon': 'fa-clock-o',       'order': 70},
    'not_for_sale': {'color': '#cbd5e1', 'icon': 'fa-minus-circle',  'order': 80},
    'unmapped':     {'color': '#e2e8f0', 'icon': 'fa-question-circle', 'order': 90},
}

#: Developer's `sale_unavailable_reason` → visual state, for the reasons that do
#: not depend on `commercial_status`. Anything absent falls through to the
#: committed-status lookup and then to `not_for_sale`.
UNAVAILABLE_REASON_TO_VISUAL = {
    'blocked': 'blocked',
    'maintenance': 'blocked',
    'not_released': 'unreleased',
    'release_window': 'unreleased',
    'project_not_selling': 'unreleased',
    'phase_not_selling': 'unreleased',
    'archived': 'not_for_sale',
    'not_a_unit': 'not_for_sale',
    'no_project': 'not_for_sale',
}

#: Module 1 `commercial_status` → visual state, used when Developer's reason is
#: `committed`. These are the four values `COMMITTED_COMMERCIAL_STATUS` holds.
COMMERCIAL_STATUS_TO_VISUAL = {
    'held': 'held',
    'reserved': 'reserved',
    'contracted': 'contracted',
    'sold': 'sold',
}

#: The only visual state from which the gallery offers to start a reservation.
#: A single name, so the 2D panel, the 3D panel and the public embed cannot
#: disagree about when the button appears.
VISUAL_STATE_RESERVABLE = ('available',)

#: How the gallery states roll up into the three counters a building or floor
#: shows (Available / Reserved / Sold). Everything else -- blocked, not yet
#: released, not for sale -- counts towards the total only: a unit nobody may
#: buy is not "available", whatever its legacy `property.state` says.
VISUAL_STATE_COUNTED_AS = {
    'available': ('available',),
    'reserved': ('held', 'reserved', 'contracted'),
    'sold': ('sold',),
}


# ---------------------------------------------------------------------------
# Publication lifecycle (M1 / M2 / M3)
# ---------------------------------------------------------------------------

#: A project's visual experience is not live because somebody uploaded a file.
VISUAL_PUBLICATION_STATE = [
    ('draft', 'Draft'),
    ('validating', 'Validating'),
    ('ready', 'Ready'),
    ('published', 'Published'),
    ('archived', 'Archived'),
]

#: States in which the experience may be shown to a customer.
VISUAL_PUBLICATION_LIVE = ('published',)

#: Which experience a project opens in.
VISUAL_DEFAULT_EXPERIENCE = [
    ('auto', 'Best Available'),
    ('3d', '3D Maquette'),
    ('2d', '2D Master Plan'),
    ('list', 'Unit List'),
]

#: Device capability tiers (M9). Detected from a real WebGL probe, never from a
#: user-agent string.
CAPABILITY_TIER = [
    ('high', 'Full 3D'),
    ('medium', 'Optimised 3D'),
    ('low', 'Reduced 3D'),
    ('none', '2D Only'),
]


# ---------------------------------------------------------------------------
# Asset validation (M2)
# ---------------------------------------------------------------------------

VALIDATION_SEVERITY = [
    ('critical', 'Critical'),
    ('warning', 'Warning'),
    ('info', 'Information'),
]

#: Publication is refused while any of these are outstanding. Everything else
#: is advisory — a warning should never stop a sale from going live.
VALIDATION_BLOCKING = ('critical',)


def presentation_for(visual_state):
    """Colour/icon/order for a visual state, with a defined fallback."""
    return VISUAL_STATE_PRESENTATION.get(
        visual_state, VISUAL_STATE_PRESENTATION['unmapped'])


def visual_state_from_developer(is_available, unavailable_reason,
                                commercial_status):
    """Map Developer's authoritative answer onto one visual state.

    Pure function of three inputs, so it can be tested exhaustively without a
    database and reasoned about without reading a viewer.
    """
    if is_available:
        return 'available'
    if unavailable_reason == 'committed':
        return COMMERCIAL_STATUS_TO_VISUAL.get(commercial_status, 'sold')
    if unavailable_reason in UNAVAILABLE_REASON_TO_VISUAL:
        return UNAVAILABLE_REASON_TO_VISUAL[unavailable_reason]
    # A unit that is not available for a reason nobody has mapped is shown as
    # not for sale rather than as available. Failing towards "you cannot buy
    # this" is the safe direction.
    return 'not_for_sale'
