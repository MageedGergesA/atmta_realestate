# -*- coding: utf-8 -*-
"""Shared commercial vocabulary for the developer sales engine.

Kept in one module with no ORM imports so every other file agrees on the
spelling of a state, and so the values can be imported by tests and by
migrations without dragging a model registry along.

**Commercial is not construction.** ``realestate.project.state`` already exists
and is read by `real_estate_construction`, `real_estate_api`,
`real_estate_maquette`, `real_estate_plan` and the dashboard. It mixes physical
progress (`construction`, `handover`) with a sales notion (`marketing`), and it
cannot be changed without breaking those modules. So it is left exactly as it
is and treated as the *construction/physical* dimension, and the commercial
lifecycle below is added alongside it — the same orthogonal-dimension pattern
Module 1 used when it split ``property.state``.
"""

#: Commercial lifecycle of a project or phase. Orthogonal to construction.
COMMERCIAL_STATE = [
    ('planning', 'Planning'),
    ('pre_launch', 'Pre-Launch'),
    ('selling', 'Selling'),
    ('sold_out', 'Sold Out'),
    ('closed', 'Closed'),
]

#: Commercial states in which a project/phase will accept new sales activity.
#: ``pre_launch`` is included on purpose: pre-launch is when developers take
#: expressions of interest and soft holds, which is a real commercial activity.
COMMERCIAL_STATE_SELLING = ('pre_launch', 'selling')

#: Release batch lifecycle.
RELEASE_STATE = [
    ('draft', 'Draft'),
    ('approved', 'Approved'),
    ('released', 'Released'),
    ('closed', 'Closed'),
    ('cancelled', 'Cancelled'),
]

#: The only release state that makes inventory sellable.
RELEASE_STATE_LIVE = 'released'

#: Why a unit is commercially blocked.
BLOCK_REASON = [
    ('management', 'Management Hold'),
    ('legal', 'Legal'),
    ('owner', 'Owner Request'),
    ('vip', 'VIP / Reserved for Client'),
    ('pricing_review', 'Pricing Review'),
    ('model_unit', 'Model / Show Unit'),
    ('maintenance', 'Maintenance'),
    ('internal_use', 'Internal Use'),
    ('regulatory', 'Regulatory'),
    ('other', 'Other'),
]

#: Values of ``realestate.property.commercial_status`` (Module 1) that mean a
#: live commercial commitment exists on the unit. Availability must never
#: override one of these.
COMMITTED_COMMERCIAL_STATUS = ('held', 'reserved', 'contracted', 'sold')

#: Reasons ``is_available_for_sale`` can be False, most specific first. The
#: order is the order they are evaluated in, so the reason a user sees is the
#: one they can actually act on.
UNAVAILABLE_REASON = [
    ('archived', 'Archived'),
    ('not_a_unit', 'Not a Sellable Unit'),
    ('no_project', 'Not Assigned to a Project'),
    ('project_not_selling', 'Project Not Selling'),
    ('phase_not_selling', 'Phase Not Selling'),
    ('not_released', 'Not Released for Sale'),
    ('release_window', 'Outside Release Window'),
    ('blocked', 'Commercially Blocked'),
    ('maintenance', 'Under Maintenance'),
    ('committed', 'Held, Reserved or Sold'),
]

#: Reservation states that no longer claim a use of a capped promotion. Shared
#: by the promotion's use count and the reservation's cap check, so the two
#: cannot disagree about who holds a use.
PROMOTION_RELEASING_STATES = ('cancelled', 'expired', 'rejected')

#: Sale contract states in which the deal is over and commits no unit.
CONTRACT_ENDED_STATES = ('cancelled', 'terminated', 'transferred')
