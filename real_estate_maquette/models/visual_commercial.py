# -*- coding: utf-8 -*-
"""The one place the gallery asks what a unit is worth and whether it is free.

### The defect this replaces

`get_maquette_units_data()` built its payload like this:

```python
'state':      u.state,          # Module 1's legacy physical/sales mix
'base_price': u.base_price,     # the internal number discounts are measured against
```

Both are wrong for a customer-facing gallery, and wrong in opposite directions.

`property.state` predates the Developer engine. It knows nothing about release
batches, commercial blocks, selling windows, or the project's commercial state.
A unit that has never been released reads `available` and is coloured green in
the 3D model, offered to a customer, and clicked — and only then does
Developer's reservation form refuse it. The customer has already been told they
can buy it.

`base_price` is the internal figure. Developer's own pricing module says so in
as many words: *"the internal number every discount is measured against"*, and
it added `_public_price()` precisely because that figure was being served from
a public endpoint.

### What replaces it

One service, consumed by the 3D viewer, the 2D plan, the unit panel, the
filters and the public embed. Every one of them gets the same answer to the
same question, because they all ask the same method.

```
    Developer                          this service                consumers
    ─────────────────                  ────────────────           ──────────
    is_available_for_sale        ┐
    sale_unavailable_reason      ├──▶  visual_state         ──▶   3D tint
    commercial_status            ┘                                2D fill
                                                                  legend
    list_price_developer         ┐                                filters
    _public_price()              ├──▶  price (role-aware)   ──▶   unit panel
    active_price_book_id         ┘                                compare
```

### Role awareness is part of the contract, not a caller's responsibility

M13 and M20 forbid showing a public buyer the confidential minimum, the
discount authority, the internal block reason or internal notes. Leaving that
to each caller means it holds until somebody adds a caller. So the audience is
an argument here, and the payload is built for it.
"""

from odoo import api, fields, models

from .visual_states import (
    VISUAL_STATE,
    VISUAL_STATE_RESERVABLE,
    presentation_for,
    visual_state_from_developer,
)

#: Who is asking. Not a security boundary on its own — record rules and ACLs
#: still apply underneath — but it decides what is even assembled.
AUDIENCE_INTERNAL = 'internal'
AUDIENCE_BROKER = 'broker'
AUDIENCE_PUBLIC = 'public'

AUDIENCES = (AUDIENCE_INTERNAL, AUDIENCE_BROKER, AUDIENCE_PUBLIC)


class VisualCommercial(models.AbstractModel):
    """Commercial truth for the visual layer. One service, one answer."""
    _name = 'realestate.visual.commercial'
    _description = 'Visual Commercial Truth Service'

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    @api.model
    def visual_state(self, unit):
        """Developer's answer, mapped to one of the gallery's states."""
        unit = unit.sudo()
        return visual_state_from_developer(
            is_available=bool(unit.is_available_for_sale),
            unavailable_reason=unit.sale_unavailable_reason or False,
            commercial_status=unit.commercial_status or False,
        )

    @api.model
    def legend(self, states=None):
        """The legend, built from the same table the colours come from.

        M25: status must not be conveyed by colour alone, so every entry
        carries a translated label and an icon. Passing `states` narrows it to
        what is actually on screen — a legend listing states no unit is in
        teaches nothing.
        """
        labels = dict(VISUAL_STATE)
        wanted = set(states) if states else set(labels)
        entries = []
        for key in labels:
            if key not in wanted:
                continue
            presentation = presentation_for(key)
            entries.append({
                'state': key,
                'label': labels[key],
                'color': presentation['color'],
                'icon': presentation['icon'],
                'order': presentation['order'],
            })
        return sorted(entries, key=lambda e: e['order'])

    # ------------------------------------------------------------------
    # Price
    # ------------------------------------------------------------------
    @api.model
    def visual_price(self, unit, audience=AUDIENCE_INTERNAL):
        """The price this audience may be shown.

        Public and broker callers get Developer's `_public_price()`, which
        deliberately returns 0.0 for inventory that is not on the market rather
        than leaking the pricing of an unlaunched tower. Internal callers get
        the developer list price, which is what a salesperson quotes from.

        `base_price` is never returned to anybody. It is the internal figure
        discounts are measured against, and the gallery has no use for it.
        """
        unit = unit.sudo()
        if audience in (AUDIENCE_PUBLIC, AUDIENCE_BROKER):
            return unit._public_price()
        return unit.list_price_developer

    # ------------------------------------------------------------------
    # The payload
    # ------------------------------------------------------------------
    @api.model
    def unit_payload(self, units, audience=AUDIENCE_INTERNAL):
        """One dict per unit, assembled for the audience that asked.

        Built in bulk: `units` is a recordset and every field read here is
        either stored or already prefetched, so a 3,000-unit tower costs a
        handful of queries rather than one per unit.
        """
        if audience not in AUDIENCES:
            audience = AUDIENCE_PUBLIC
        units = units.sudo()
        payload = []
        for unit in units:
            state = self.visual_state(unit)
            presentation = presentation_for(state)
            entry = {
                'id': unit.id,
                'property_code': unit.property_code or '',
                'name': unit.name or '',
                'mesh_name': unit.maquette_mesh_name or '',
                'visual_state': state,
                'color': unit.maquette_color_override or presentation['color'],
                'icon': presentation['icon'],
                'reservable': state in VISUAL_STATE_RESERVABLE,
                'price': self.visual_price(unit, audience),
                'currency': unit.currency_id.symbol or '',
                'area_sqm': unit.area_sqm or 0.0,
                'bedrooms': unit.bedroom_count or 0,
                'bathrooms': unit.bathroom_count or 0,
                'floor': unit.floor_number or 0,
                'property_type': (unit.property_type_id.name
                                  if unit.property_type_id else ''),
                'has_floor_plan': bool(unit.has_floor_plan_effective),
                'has_interior': bool(unit.interior_glb),
            }
            entry['price_per_sqm'] = (
                round(entry['price'] / entry['area_sqm'], 2)
                if entry['area_sqm'] else 0.0)

            if audience == AUDIENCE_INTERNAL:
                # Only an internal audience learns *why* something is not
                # available. "Blocked — VIP hold for Mr X" is exactly the kind
                # of sentence M20 forbids a public visitor from seeing.
                entry['unavailable_reason'] = unit.sale_unavailable_reason or ''
                entry['commercial_status'] = unit.commercial_status or ''
                entry['is_released'] = bool(unit.is_released_for_sale)
            payload.append(entry)
        return payload

    # ------------------------------------------------------------------
    # Reservation eligibility
    # ------------------------------------------------------------------
    @api.model
    def may_start_reservation(self, unit):
        """Whether the gallery should offer to start a reservation.

        Deliberately advisory. Developer's `create()` takes an advisory lock and
        re-checks availability inside the transaction, and that is what actually
        decides. This only stops the gallery from offering a button that is
        certain to fail — which the audit found it doing, because it gated on
        `property.state` instead.
        """
        return self.visual_state(unit) in VISUAL_STATE_RESERVABLE


class PropertyVisualCommercial(models.Model):
    """Stored visual state on the unit, so it can be searched and grouped."""
    _inherit = 'realestate.property'

    visual_state = fields.Selection(
        VISUAL_STATE, string='Gallery Status',
        compute='_compute_visual_state', store=True, index=True,
        help="What the sales gallery shows for this unit, derived from "
             "Developer's authoritative availability. Stored so filters and "
             "reports can use it; never written by hand.")

    @api.depends('is_available_for_sale', 'sale_unavailable_reason',
                 'commercial_status')
    def _compute_visual_state(self):
        service = self.env['realestate.visual.commercial']
        for rec in self:
            if rec.hierarchy_level != 'unit':
                rec.visual_state = 'not_for_sale'
                continue
            rec.visual_state = service.visual_state(rec)
