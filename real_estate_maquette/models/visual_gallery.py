# -*- coding: utf-8 -*-
"""M12 / M15 / M16 — finding a unit, keeping it, and comparing it.

### Why filtering is P0

A customer walking into a sales gallery knows "three bedrooms, under 4 million,
high floor, sea view". They do not know "unit B-1104". The 0.4 gallery had no
way to express that: you could orbit a tower and click things.

### Filtering happens in the database

```
    filters ──▶ domain ──▶ search ──▶ ids
                                      │
                            payload ──┴──▶ matching (bright)
                                           everything else (de-emphasised)
```

The brief forbids copying commercial data into frontend-only stores, and the
audit found the whole unit list being shipped to the browser on every open. So
the filter builds a **domain**, the database answers it, and the client is told
which ids matched — never handed a second copy of the pricing to filter itself.

Non-matching units are de-emphasised rather than removed, because a customer
looking at a tower wants to see that the floor above is taken.

### Shortlist and compare belong to Brokerage

Module 4 established `realestate.property.match` as the canonical shortlist,
carrying `shortlisted`, `rank`, `customer_response` and the reason a customer
rejected something. Rebuilding that here would be the exact mistake Rule 3
forbids.

But `real_estate_maquette` does **not** depend on `real_estate_brokerage`, and
making a 3D viewer require the whole brokerage engine would be worse. So the
integration is late-bound: the gallery asks whether the model is in the
registry and uses it when it is. This is the same defensive pattern Brokerage
itself uses to read portal and API fields it does not depend on.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .visual_commercial import AUDIENCE_INTERNAL, AUDIENCE_PUBLIC

_logger = logging.getLogger(__name__)

#: Filters a customer can express, and how each becomes a domain term. Kept as
#: data rather than as a chain of ifs so the set the UI offers and the set the
#: server honours cannot drift apart.
FILTER_SPEC = {
    'visual_state':    ('visual_state', 'in'),
    'project_id':      ('project_id', 'in'),
    'building_id':     ('parent_id', 'in'),
    'property_type_id': ('property_type_id', 'in'),
    'bedrooms_min':    ('bedroom_count', '>='),
    'bedrooms_max':    ('bedroom_count', '<='),
    'bathrooms_min':   ('bathroom_count', '>='),
    'area_min':        ('area_sqm', '>='),
    'area_max':        ('area_sqm', '<='),
    'floor_min':       ('floor_number', '>='),
    'floor_max':       ('floor_number', '<='),
}

#: Price bounds are not domain terms. The price a unit is shown at depends on
#: the audience (`visual_commercial.visual_price`) and is not stored, and
#: searching `base_price` -- the internal figure no audience is ever shown --
#: made results disagree with the displayed price and let a public or broker
#: caller narrow down an unreleased unit's internal price by repeated ranges.
#: They are applied to the audience's own price after the search instead.
PRICE_FILTER_SPEC = {
    'price_min': lambda price, bound: price >= bound,
    'price_max': lambda price, bound: price <= bound,
}

#: The most units a single gallery request will assemble. A tower with 3,000
#: units is real; shipping all of them on every filter keystroke is not.
GALLERY_PAGE_LIMIT = 2000


class VisualGallery(models.AbstractModel):
    """Search, shortlist and compare for the sales gallery."""
    _name = 'realestate.visual.gallery'
    _description = 'Visual Sales Gallery Service'

    # ==================================================================
    # M12 — search and filter
    # ==================================================================
    @api.model
    def filter_domain(self, project, filters=None):
        """Turn a filter dict into a domain. Unknown keys are ignored.

        Ignoring rather than raising is deliberate: a stale client sending a
        filter this version does not know about should get a slightly broader
        result set, not an error dialog in front of a customer.
        """
        domain = [
            ('hierarchy_level', '=', 'unit'),
            ('project_id', '=', project.id),
        ]
        for key, value in (filters or {}).items():
            spec = FILTER_SPEC.get(key)
            if not spec or value in (None, '', [], False):
                continue
            field, operator = spec
            if operator == 'in' and not isinstance(value, (list, tuple)):
                value = [value]
            domain.append((field, operator, value))
        return domain

    @api.model
    def search_units(self, project, filters=None, audience=AUDIENCE_INTERNAL,
                     limit=GALLERY_PAGE_LIMIT):
        """Which units match, and what the whole set looks like.

        Returns both the matching ids and the full payload, because the gallery
        de-emphasises non-matching units rather than hiding them — a customer
        wants to see that the floor above is taken.
        """
        Commercial = self.env['realestate.visual.commercial']
        Property = self.env['realestate.property']

        all_units = Property.search([
            ('hierarchy_level', '=', 'unit'),
            ('project_id', '=', project.id),
        ], limit=limit)
        matching = Property.search(
            self.filter_domain(project, filters), limit=limit)
        price_bounds = {
            key: float(value) for key, value in (filters or {}).items()
            if key in PRICE_FILTER_SPEC and value not in (None, '', False)}
        if price_bounds:
            def within(unit):
                price = Commercial.visual_price(unit, audience)
                # No price for this audience (e.g. not on the market) matches
                # no price range: 0.0 means "not published", not "free".
                return bool(price) and all(
                    PRICE_FILTER_SPEC[key](price, bound)
                    for key, bound in price_bounds.items())
            matching = matching.filtered(within)

        payload = Commercial.unit_payload(all_units, audience=audience)
        matching_ids = set(matching.ids)
        for entry in payload:
            entry['matches_filter'] = entry['id'] in matching_ids

        return {
            'units': payload,
            'matching_ids': sorted(matching_ids),
            'matching_count': len(matching_ids),
            'total_count': len(all_units),
            'truncated': len(all_units) >= limit,
            'facets': self._facets(all_units, audience),
        }

    @api.model
    def _facets(self, units, audience):
        """The ranges and choices a filter bar should offer.

        Built from the units actually present rather than from a hard-coded
        list, so a project with no villas does not offer a villa filter.
        """
        Commercial = self.env['realestate.visual.commercial']
        prices = [Commercial.visual_price(u, audience) for u in units]
        prices = [p for p in prices if p]
        areas = [u.area_sqm for u in units if u.area_sqm]
        floors = [u.floor_number for u in units if u.floor_number is not None]
        beds = sorted({u.bedroom_count for u in units if u.bedroom_count})
        types = units.mapped('property_type_id')
        states = sorted({u.visual_state for u in units if u.visual_state})

        return {
            'price': {'min': min(prices) if prices else 0.0,
                      'max': max(prices) if prices else 0.0},
            'area': {'min': min(areas) if areas else 0.0,
                     'max': max(areas) if areas else 0.0},
            'floor': {'min': min(floors) if floors else 0,
                      'max': max(floors) if floors else 0},
            'bedrooms': beds,
            'property_types': [{'id': t.id, 'name': t.name} for t in types],
            'visual_states': Commercial.legend(states),
        }

    # ==================================================================
    # M15 — shortlist, owned by Brokerage
    # ==================================================================
    @api.model
    def _match_model(self):
        """Brokerage's shortlist model, if this deployment has Brokerage.

        Late-bound on purpose. Module 4 owns the canonical shortlist and Rule 3
        forbids a second one, but a 3D viewer should not drag the whole
        brokerage engine into its dependency graph.
        """
        return self.env.get('realestate.property.match')

    @api.model
    def shortlist_available(self):
        """Installed **and** readable by this user.

        "Available" used to mean only "Brokerage is installed", which is not
        the question the client is asking. A showroom user without Brokerage's
        sales role hit a raw Access Error dialog the moment the gallery tried
        to load a shortlist — the feature was advertised as present and then
        failed in front of a customer. Availability now answers the question
        that was meant: can *this* user use it.
        """
        Match = self._match_model()
        if Match is None:
            return False
        try:
            Match.check_access_rights('read')
        except AccessError:
            return False
        return True

    @api.model
    def shortlist_add(self, crm_lead_id, property_id, source='3d',
                      building_id=None):
        """Favourite a unit against a CRM opportunity.

        Writes Brokerage's `realestate.property.match` — the same rows the
        brokerage shortlist, the match score and the viewing feedback loop all
        use. A visual favourite and an agent's shortlist entry are the same
        thing, and this is what makes them the same row.

        `source` records **where** the customer was standing when they
        favourited it — the 3D maquette, the 2D plan, the unit list or the
        public embed. An agent picking the conversation up later is helped by
        knowing the customer chose it from an interior walkthrough rather than
        from a spreadsheet row; and it is the only way to tell later whether
        the gallery is producing shortlists at all.

        The timestamp comes from `action_shortlist()`, which already stamps
        `shortlisted_on` — not re-implemented here.
        """
        Match = self._match_model()
        if Match is None:
            raise UserError(_(
                "Shortlisting needs the Brokerage module, which owns the "
                "customer's shortlist. Without it the gallery has nowhere to "
                "put a favourite that an agent would ever see again."))
        lead = self.env['crm.lead'].browse(crm_lead_id)
        prop = self.env['realestate.property'].browse(property_id)
        if not lead.exists() or not prop.exists():
            raise UserError(_("Unknown opportunity or unit."))

        existing = Match.search([
            ('crm_lead_id', '=', lead.id),
            ('property_id', '=', prop.id),
        ], limit=1)
        if existing:
            # An existing row may carry a real match score and the customer's
            # own feedback. Favouriting must not discard either — it only
            # raises the flag and notes where the click came from.
            existing.action_shortlist()
            existing.sudo().write(
                {'feedback': self._source_note(source, existing.feedback)})
            return existing.id

        match = Match.create({
            'crm_lead_id': lead.id,
            'property_id': prop.id,
            'score': 0.0,
            'criteria_json': {'_note': {
                'label': 'Gallery',
                'verdict': 'hit',
                'detail': _('Favourited from the %s experience.')
                % self._source_label(source),
            }},
        })
        match.action_shortlist()
        match.sudo().feedback = self._source_note(source, False)
        return match.id

    #: Where a favourite was clicked. Not a new model — a note on the row
    #: Brokerage already owns.
    VISUAL_SOURCES = {
        '3d': 'the 3D maquette',
        '2d': 'the 2D plan',
        'list': 'the unit list',
        'compare': 'the comparison view',
        'embed': 'the public embed',
    }

    @api.model
    def _source_label(self, source):
        return self.VISUAL_SOURCES.get(source, self.VISUAL_SOURCES['list'])

    @api.model
    def _source_note(self, source, existing):
        note = _('Shortlisted from %s.') % self._source_label(source)
        if existing and note in existing:
            return existing
        return '%s %s' % (existing, note) if existing else note

    @api.model
    def shortlist_remove(self, crm_lead_id, property_id):
        Match = self._match_model()
        if Match is None:
            return False
        existing = Match.search([
            ('crm_lead_id', '=', crm_lead_id),
            ('property_id', '=', property_id),
        ], limit=1)
        if existing:
            existing.action_unshortlist()
        return True

    @api.model
    def shortlist_for(self, crm_lead_id, audience=AUDIENCE_INTERNAL):
        """The customer's shortlist, priced and stated as it is right now."""
        if not self.shortlist_available():
            # Empty, explicitly — paired with `shortlist_available: False` in
            # the context so the client hides the feature rather than showing
            # an empty shortlist that looks like the customer saved nothing.
            return []
        Match = self._match_model()
        matches = Match.search([
            ('crm_lead_id', '=', crm_lead_id),
            ('shortlisted', '=', True),
        ])
        return self.env['realestate.visual.commercial'].unit_payload(
            matches.mapped('property_id'), audience=audience)

    @api.model
    def merge_anonymous_shortlist(self, crm_lead_id, property_ids,
                                  source='embed'):
        """Fold a visitor's session shortlist into the canonical one.

        A public visitor has no opportunity yet, so the client keeps their
        favourites locally. The moment they identify themselves, those become
        real shortlist rows — once. Re-sending the same list does not create
        duplicates, because `shortlist_add` finds the existing row first.

        **Nothing is written until an opportunity exists.** The brief is
        explicit that an anonymous visitor clicking Favorite must not create
        permanent CRM data, and this method is the only path from a session
        list into the database — it requires a `crm_lead_id`, so there is no
        way to reach it without an identified customer.
        """
        added = []
        for property_id in (property_ids or []):
            try:
                added.append(
                    self.shortlist_add(crm_lead_id, property_id, source=source))
            except UserError:
                # One unmapped id must not lose the rest of somebody's
                # shortlist.
                continue
        return added

    # ==================================================================
    # Payment plan preview — Developer's calculation, never a second one
    # ==================================================================
    @api.model
    def payment_plans_for(self, property_id, audience=AUDIENCE_INTERNAL):
        """Approved plans for a unit, each with an indicative schedule.

        Every number here comes from Developer:

        ```
            realestate.payment.plan._available_for(unit)   → which plans apply
            plan._generate_schedule(total_price=...)       → the actual rows
        ```

        Nothing is recalculated. The brief is explicit that schedules must not
        be computed in JavaScript, and the same reasoning applies to computing
        them a second time in Python: a plan's residual line, its rounding
        absorption and its date rules are the developer's commercial terms, and
        a second implementation of them is a second set of numbers to reconcile
        when they disagree.

        No new service was needed in Developer. `_generate_schedule` is already
        a pure calculation returning rows, and `_available_for` already answers
        applicability — this only shapes them for an audience.
        """
        unit = self.env['realestate.property'].sudo().browse(
            int(property_id)).exists()
        if not unit:
            return []

        Commercial = self.env['realestate.visual.commercial']
        price = Commercial.visual_price(unit, audience)
        if not price:
            # An unreleased unit has no public price, so it has no public
            # schedule either. Generating one against a zero would produce a
            # table of zeroes that looks like an offer.
            return []

        Plan = self.env['realestate.payment.plan'].sudo()
        handover = (unit.project_id.expected_handover_date
                    if unit.project_id else False)
        previews = []
        for plan in Plan._available_for(unit):
            try:
                rows = plan._generate_schedule(
                    total_price=price,
                    booking_date=fields.Date.context_today(self),
                    handover_date=handover,
                )
            except UserError as exc:
                # Developer refused to generate this schedule and said why —
                # most often a handover-dated line on a project with no
                # expected handover date, where inventing one would produce
                # due dates years too early.
                #
                # The plan is reported as unavailable rather than silently
                # dropped. A plan that vanishes from the panel is a support
                # call nobody can answer; one that says "not available yet"
                # is an answer.
                previews.append(self._plan_unavailable(plan, str(exc)))
            except Exception:
                _logger.warning(
                    "Payment plan %s could not be previewed for unit %s",
                    plan.display_name, unit.display_name, exc_info=True)
                previews.append(self._plan_unavailable(plan, _(
                    "This plan could not be prepared for this unit.")))
            else:
                previews.append(self._plan_preview(plan, rows, price))
        return previews

    @api.model
    def _plan_unavailable(self, plan, reason):
        """A plan that exists but cannot be quoted for this unit yet."""
        return {
            'id': plan.id,
            'name': plan.name,
            'code': plan.code or '',
            'available': False,
            'reason': reason,
            'schedule': [],
        }

    @api.model
    def _plan_preview(self, plan, rows, price):
        """Shape one plan's schedule for display.

        The headline figures a buyer asks for — down payment, duration,
        cadence, handover — are derived from the generated rows rather than
        from the plan's own summary fields, so what is shown is what this
        particular price actually produces.
        """
        def total_of(kinds):
            return sum(r['amount'] for r in rows if r['kind'] in kinds)

        instalments = [r for r in rows if r['kind'] == 'installment']
        return {
            'id': plan.id,
            'name': plan.name,
            'code': plan.code or '',
            'available': True,
            'currency': plan.currency_id.symbol or '',
            'total_price': price,
            'down_payment': total_of(('booking', 'down_payment')),
            'handover_payment': total_of(('handover',)),
            'balloon_payment': total_of(('balloon',)),
            'duration_months': plan.duration_months,
            'installment_count': len(instalments),
            'installment_amount': (instalments[0]['amount']
                                   if instalments else 0.0),
            'cadence_months': self._cadence_of(instalments),
            'schedule': [{
                'sequence': r['sequence'],
                'kind': r['kind'],
                'name': r['name'],
                'percent': r['percent'],
                'amount': r['amount'],
                'date_due': (r['date_due'].isoformat()
                             if r.get('date_due') else False),
            } for r in rows],
        }

    @api.model
    def _cadence_of(self, instalments):
        """Months between instalments, from the dates actually generated.

        Reading the gap rather than the plan's configured rule: a plan can mix
        rules, and what a buyer wants to know is how often they will pay.
        """
        dates = [r['date_due'] for r in instalments if r.get('date_due')]
        if len(dates) < 2:
            return 0
        first, second = sorted(dates)[:2]
        return max(1, round((second - first).days / 30.0))

    # ==================================================================
    # M16 — compare
    # ==================================================================
    #: Comparing two is useful, comparing twenty is a spreadsheet.
    COMPARE_MIN = 2
    COMPARE_MAX = 4

    @api.model
    def compare(self, property_ids, audience=AUDIENCE_INTERNAL):
        """Side-by-side comparison, read fresh every time.

        The brief forbids comparing stale frontend snapshots after a commercial
        refresh, so nothing is cached: the payload is rebuilt from the same
        service the gallery colours from, and a unit reserved thirty seconds
        ago compares as reserved.
        """
        property_ids = list(dict.fromkeys(property_ids or []))
        if not (self.COMPARE_MIN <= len(property_ids) <= self.COMPARE_MAX):
            raise UserError(_(
                "Compare between %(low)s and %(high)s units — fewer is not a "
                "comparison and more is a spreadsheet.",
                low=self.COMPARE_MIN, high=self.COMPARE_MAX))

        units = self.env['realestate.property'].browse(property_ids).exists()
        payload = self.env['realestate.visual.commercial'].unit_payload(
            units, audience=audience)

        # Plans are fetched per unit at compare time, not cached from an
        # earlier page: the brief forbids comparing stale commercial values,
        # and a payment plan is a commercial value.
        for entry in payload:
            entry['payment_plans'] = self.payment_plans_for(
                entry['id'], audience=audience)

        return {
            'units': payload,
            'rows': self._compare_rows(payload),
            'as_of': fields.Datetime.now(),
        }

    @api.model
    def _compare_rows(self, payload):
        """The comparison table, with the best value in each row marked.

        Marking is only done where "better" is unambiguous — a lower price per
        square metre is better, a higher floor is not necessarily.
        """
        def best(key, direction):
            values = [e.get(key) or 0 for e in payload]
            if not any(values):
                return None
            target = min(values) if direction == 'low' else max(values)
            for entry in payload:
                if (entry.get(key) or 0) == target:
                    return entry['id']
            return None

        return [
            {'key': 'price', 'label': _('Price'), 'best': best('price', 'low')},
            {'key': 'price_per_sqm', 'label': _('Price / sqm'),
             'best': best('price_per_sqm', 'low')},
            {'key': 'area_sqm', 'label': _('Area (sqm)'),
             'best': best('area_sqm', 'high')},
            {'key': 'bedrooms', 'label': _('Bedrooms'), 'best': None},
            {'key': 'bathrooms', 'label': _('Bathrooms'), 'best': None},
            {'key': 'floor', 'label': _('Floor'), 'best': None},
            {'key': 'property_type', 'label': _('Type'), 'best': None},
            {'key': 'visual_state', 'label': _('Availability'), 'best': None},
            {'key': 'payment_plans', 'label': _('Payment Plans'),
             'best': None, 'kind': 'plans'},
        ]
