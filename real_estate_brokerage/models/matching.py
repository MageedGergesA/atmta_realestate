# -*- coding: utf-8 -*-
"""M8 — deterministic, explainable property matching.

### Two rules, and they shape everything

**Pre-filter in the database.** M29 assumes hundreds of thousands of leads and
tens of thousands of listings. Loading every property into Python and scoring it
there is the pattern the brief forbids, so the hard constraints — the ones where
a mismatch means "not a candidate at all" — become a `search` domain, and only
the survivors are scored.

**Explainable, never opaque.** A score is useless to an agent who cannot say why
a unit scored 88%. Every criterion returns a verdict of `hit`, `partial` or
`miss` with a human sentence, and the score is their weighted sum. No AI, no
learned weights, nothing a salesperson cannot repeat to a customer.

```
MATCH SCORE: 88%
  ✓ Budget      within 800,000 – 1,200,000
  ✓ Bedrooms    3 (wanted 2+)
  ✓ Area        120 sqm (wanted 100 – 150)
  ✓ Project     Palm Heights
  △ Floor       5 (wanted 8+)
  ✗ View        street (wanted garden)
```

### Hard versus soft

A hard criterion is one where a miss disqualifies: the transaction type, and
availability. Everything else is scored, because a buyer who said "3 bedrooms"
will still look at a 4-bedroom unit priced right — and a matching engine that
hides it is not helping anybody.
"""

from odoo import _, api, fields, models

#: Weight per criterion. They need not sum to 100: the score is
#: `earned / available`, so a criterion the customer did not express simply
#: drops out of both sides rather than silently costing them points.
CRITERION_WEIGHTS = {
    'budget': 30,
    'bedrooms': 15,
    'area': 15,
    'project': 15,
    'property_type': 10,
    'location': 5,
    'bathrooms': 5,
    'floor': 3,
    'view': 2,
}

VERDICT_HIT = 'hit'
VERDICT_PARTIAL = 'partial'
VERDICT_MISS = 'miss'

#: What a partial verdict is worth. A near miss is worth something — a unit
#: 3% over budget is a conversation, not a rejection.
PARTIAL_CREDIT = 0.5


class PropertyMatch(models.Model):
    """What was actually shown to a customer, and why.

    A match is a **record**, not a computed list, because the question an agent
    is asked six months later is "what did you offer them in March?" — and a
    recomputed list answers a different question. The criteria snapshot freezes
    the reasoning at the time it was made.
    """
    _name = 'realestate.property.match'
    _description = 'CRM Requirement ↔ Property Match'
    _order = 'crm_lead_id, score desc, id'
    _check_company_auto = True

    crm_lead_id = fields.Many2one(
        'crm.lead', string='Opportunity', required=True, index=True,
        ondelete='cascade')
    company_id = fields.Many2one(
        related='crm_lead_id.company_id', store=True, index=True, readonly=True)
    partner_id = fields.Many2one(
        related='crm_lead_id.partner_id', store=True, readonly=True,
        string='Customer')

    property_id = fields.Many2one(
        'realestate.property', string='Property', required=True, index=True,
        ondelete='cascade')
    listing_id = fields.Many2one(
        'realestate.listing', string='Listing', index=True,
        ondelete='set null',
        help="Set when the match came from an external brokerage listing "
             "rather than straight from Developer inventory.")
    project_id = fields.Many2one(
        related='property_id.project_id', store=True, index=True,
        readonly=True)

    score = fields.Float(string='Match Score (%)', index=True)
    criteria_json = fields.Json(
        string='Criteria Snapshot',
        help="Why this scored what it scored, frozen at the moment the match "
             "was made. Recomputing it later would answer a different "
             "question.")
    explanation = fields.Text(
        string='Explanation', compute='_compute_explanation')
    matched_on = fields.Datetime(
        string='Matched On', default=fields.Datetime.now, readonly=True,
        index=True)

    # ------------------------------------------------------------------
    # M9 — shortlist and customer response
    # ------------------------------------------------------------------
    shortlisted = fields.Boolean(string='Shortlisted', index=True)
    shortlisted_on = fields.Datetime(readonly=True)
    rank = fields.Integer(string='Rank', help="Customer's own ordering.")
    customer_response = fields.Selection([
        ('pending', 'Not Shown Yet'),
        ('favorite', 'Favourite'),
        ('interested', 'Interested'),
        ('maybe', 'Maybe'),
        ('rejected', 'Rejected'),
    ], default='pending', required=True, index=True, string='Response')
    rejection_reason = fields.Selection([
        ('price', 'Price'),
        ('layout', 'Layout'),
        ('location', 'Location'),
        ('view', 'View'),
        ('condition', 'Condition'),
        ('payment_plan', 'Payment Plan'),
        ('timing', 'Timing'),
        ('floor', 'Floor'),
        ('other', 'Other'),
    ], string='Rejection Reason')
    feedback = fields.Char()

    _sql_constraints = [
        ('match_unique_per_lead_property',
         'unique(crm_lead_id, property_id)',
         'This property has already been matched to this opportunity.'),
    ]

    # ------------------------------------------------------------------
    # Explanation
    # ------------------------------------------------------------------
    @api.depends('criteria_json')
    def _compute_explanation(self):
        marks = {VERDICT_HIT: '✓', VERDICT_PARTIAL: '△', VERDICT_MISS: '✗'}
        for rec in self:
            criteria = rec.criteria_json or {}
            lines = []
            for key, entry in criteria.items():
                if key.startswith('_'):
                    continue
                lines.append('%s %-14s %s' % (
                    marks.get(entry.get('verdict'), '?'),
                    entry.get('label', key), entry.get('detail', '')))
            rec.explanation = '\n'.join(lines) or _('No criteria were expressed.')

    # ------------------------------------------------------------------
    # Shortlist actions (M9)
    # ------------------------------------------------------------------
    def action_shortlist(self):
        for rec in self:
            rec.write({'shortlisted': True,
                       'shortlisted_on': fields.Datetime.now()})
        return True

    def action_unshortlist(self):
        self.write({'shortlisted': False, 'shortlisted_on': False})
        return True

    def action_open_property(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.property',
            'res_id': self.property_id.id,
            'view_mode': 'form',
        }


class CrmLeadMatching(models.Model):
    """The engine, on the opportunity that owns the requirement."""
    _inherit = 'crm.lead'

    match_ids = fields.One2many(
        'realestate.property.match', 'crm_lead_id', string='Matches')
    match_count = fields.Integer(compute='_compute_match_stats')
    shortlist_count = fields.Integer(compute='_compute_match_stats')

    def _compute_match_stats(self):
        """Grouped, never one query per lead (M29)."""
        Match = self.env['realestate.property.match']
        totals = dict(Match._read_group(
            [('crm_lead_id', 'in', self.ids)],
            groupby=['crm_lead_id'], aggregates=['__count']))
        shortlisted = dict(Match._read_group(
            [('crm_lead_id', 'in', self.ids), ('shortlisted', '=', True)],
            groupby=['crm_lead_id'], aggregates=['__count']))
        for lead in self:
            lead.match_count = totals.get(lead, 0)
            lead.shortlist_count = shortlisted.get(lead, 0)

    # ==================================================================
    # Running a match
    # ==================================================================
    def action_run_matching(self):
        """Find candidates, score them, and record what was found."""
        self.ensure_one()
        matches = self._re_run_matching()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Matches — %s') % self.display_name,
            'res_model': 'realestate.property.match',
            'view_mode': 'list,form',
            'domain': [('id', 'in', matches.ids)],
            'context': {'default_crm_lead_id': self.id},
        }

    def _re_run_matching(self, limit=200):
        """Pre-filter in SQL, score in Python, record the result.

        Returns the match records, existing ones updated in place so a customer
        response already captured is not thrown away by a re-run.
        """
        self.ensure_one()
        candidates = self._re_candidate_properties(limit=limit)
        Match = self.env['realestate.property.match']
        existing = {m.property_id.id: m for m in self.match_ids}

        results = Match.browse()
        for prop in candidates:
            score, criteria = self._re_score_property(prop)
            listing = self._re_listing_for(prop)
            vals = {
                'score': score,
                'criteria_json': criteria,
                'listing_id': listing.id if listing else False,
                'matched_on': fields.Datetime.now(),
            }
            match = existing.get(prop.id)
            if match:
                # Re-scoring is fine; the customer's own answer is theirs.
                match.write(vals)
            else:
                match = Match.create(dict(
                    vals, crm_lead_id=self.id, property_id=prop.id))
            results |= match
        return results

    # ------------------------------------------------------------------
    # The database pre-filter (M29)
    # ------------------------------------------------------------------
    def _re_candidate_properties(self, limit=200):
        """Hard constraints only, as a domain.

        A hard constraint is one where a miss disqualifies. Everything softer
        is scored, because a buyer who said "3 bedrooms" will still look at a
        well-priced 4-bedroom unit, and an engine that hides it is not helping.
        """
        self.ensure_one()
        Property = self.env['realestate.property']
        domain = [
            ('hierarchy_level', '=', 'unit'),
            ('company_id', 'in', self.env.companies.ids),
        ]

        # Availability is hard, and its authority differs by inventory channel.
        if self.re_market in ('developer', False):
            domain.append(('is_available_for_sale', '=', True))
        elif self.re_market == 'any':
            # Each unit answers to its own authority: Developer's engine for
            # project inventory, the property's state for resale and external
            # stock (which Developer never releases, so its flag is always
            # False there). A unit inside a project is Developer inventory —
            # the same rule the listing uses — so an unreleased project unit
            # whose state still reads `available` stays out.
            domain += ['|', ('is_available_for_sale', '=', True),
                       '&', ('project_id', '=', False),
                       ('state', '=', 'available')]
        else:
            domain.append(('state', '=', 'available'))

        # A generous budget band: the score decides how good a fit is, the
        # filter only removes what is out of the question. 20% over the stated
        # ceiling is still a conversation; triple is not.
        if self.re_budget_max:
            domain.append(('base_price', '<=', self.re_budget_max * 1.2))
        if self.re_budget_min:
            domain.append(('base_price', '>=', self.re_budget_min * 0.8))

        # Project preference is hard when expressed — a customer who named
        # three projects does not want a fourth.
        if self.re_project_ids:
            domain.append(('project_id', 'in', self.re_project_ids.ids))
        if self.re_property_type_ids:
            domain.append(
                ('property_type_id', 'in', self.re_property_type_ids.ids))

        # Team project authority (M20) narrows the candidate set at source, so
        # an agent never even sees inventory they may not sell.
        allowed = self.env['crm.team']._re_allowed_project_ids_for_user()
        if allowed is not None:
            domain.append(('project_id', 'in', allowed))

        return Property.search(domain, limit=limit)

    def _re_listing_for(self, prop):
        return self.env['realestate.listing'].search([
            ('property_id', '=', prop.id),
            ('state', 'in', ('active', 'under_offer')),
        ], limit=1)

    # ------------------------------------------------------------------
    # Scoring — deterministic and explainable
    # ------------------------------------------------------------------
    def _re_score_property(self, prop):
        """Return `(score_percent, criteria_dict)`.

        The denominator is only the criteria the customer actually expressed,
        so a sparse brief does not drag every score down and a detailed one is
        not easier to satisfy.
        """
        self.ensure_one()
        criteria = {}
        for name, checker in self._re_criteria_checkers().items():
            entry = checker(prop)
            if entry:
                criteria[name] = entry

        available = sum(CRITERION_WEIGHTS[name] for name in criteria)
        if not available:
            return 0.0, {'_note': {
                'label': 'No brief',
                'verdict': VERDICT_MISS,
                'detail': _('The opportunity expresses no requirements yet.')}}

        earned = 0.0
        for name, entry in criteria.items():
            weight = CRITERION_WEIGHTS[name]
            if entry['verdict'] == VERDICT_HIT:
                earned += weight
            elif entry['verdict'] == VERDICT_PARTIAL:
                earned += weight * PARTIAL_CREDIT
        return round(earned / available * 100.0, 1), criteria

    def _re_criteria_checkers(self):
        return {
            'budget': self._re_check_budget,
            'bedrooms': self._re_check_bedrooms,
            'area': self._re_check_area,
            'project': self._re_check_project,
            'property_type': self._re_check_property_type,
            'location': self._re_check_location,
            'bathrooms': self._re_check_bathrooms,
            'floor': self._re_check_floor,
            'view': self._re_check_view,
        }

    # -- individual criteria; each returns None when not expressed --
    def _re_check_budget(self, prop):
        if not (self.re_budget_min or self.re_budget_max):
            return None
        price = prop.base_price or 0.0
        low, high = self.re_budget_min or 0.0, self.re_budget_max or 0.0
        if (not low or price >= low) and (not high or price <= high):
            verdict, detail = VERDICT_HIT, _(
                "%(price)s is within budget", price=price)
        elif high and price <= high * 1.1:
            verdict, detail = VERDICT_PARTIAL, _(
                "%(price)s is just over the %(high)s ceiling",
                price=price, high=high)
        else:
            verdict, detail = VERDICT_MISS, _(
                "%(price)s is outside %(low)s – %(high)s",
                price=price, low=low or _('any'), high=high or _('any'))
        return {'label': _('Budget'), 'verdict': verdict, 'detail': detail}

    def _re_check_bedrooms(self, prop):
        if not (self.re_bedrooms_min or self.re_bedrooms_max):
            return None
        actual = prop.bedroom_count or 0
        low, high = self.re_bedrooms_min or 0, self.re_bedrooms_max or 0
        if (not low or actual >= low) and (not high or actual <= high):
            verdict, detail = VERDICT_HIT, _("%s bedrooms", actual)
        elif low and actual == low - 1:
            verdict, detail = VERDICT_PARTIAL, _(
                "%(actual)s bedrooms, one short of %(low)s",
                actual=actual, low=low)
        else:
            verdict, detail = VERDICT_MISS, _(
                "%(actual)s bedrooms, wanted %(low)s+", actual=actual, low=low)
        return {'label': _('Bedrooms'), 'verdict': verdict, 'detail': detail}

    def _re_check_area(self, prop):
        if not (self.re_area_min or self.re_area_max):
            return None
        actual = prop.area_sqm or 0.0
        low, high = self.re_area_min or 0.0, self.re_area_max or 0.0
        if (not low or actual >= low) and (not high or actual <= high):
            verdict, detail = VERDICT_HIT, _("%s sqm", actual)
        elif low and actual >= low * 0.9:
            verdict, detail = VERDICT_PARTIAL, _(
                "%(actual)s sqm, slightly under %(low)s", actual=actual, low=low)
        else:
            verdict, detail = VERDICT_MISS, _(
                "%(actual)s sqm, wanted %(low)s – %(high)s",
                actual=actual, low=low or _('any'), high=high or _('any'))
        return {'label': _('Area'), 'verdict': verdict, 'detail': detail}

    def _re_check_project(self, prop):
        if not self.re_project_ids:
            return None
        hit = prop.project_id in self.re_project_ids
        return {
            'label': _('Project'),
            'verdict': VERDICT_HIT if hit else VERDICT_MISS,
            'detail': prop.project_id.display_name or _('no project'),
        }

    def _re_check_property_type(self, prop):
        if not self.re_property_type_ids:
            return None
        hit = prop.property_type_id in self.re_property_type_ids
        return {
            'label': _('Type'),
            'verdict': VERDICT_HIT if hit else VERDICT_MISS,
            'detail': prop.property_type_id.display_name or _('unspecified'),
        }

    def _re_check_location(self, prop):
        if not self.re_location_ids:
            return None
        for pref in self.re_location_ids:
            if pref.city and prop.city and \
                    pref.city.strip().lower() == (prop.city or '').strip().lower():
                return {'label': _('Location'), 'verdict': VERDICT_HIT,
                        'detail': prop.city}
            if pref.state_id and prop.state_id == pref.state_id:
                return {'label': _('Location'), 'verdict': VERDICT_PARTIAL,
                        'detail': _("same governorate (%s)",
                                    prop.state_id.display_name)}
        return {'label': _('Location'), 'verdict': VERDICT_MISS,
                'detail': prop.city or _('unspecified')}

    def _re_check_bathrooms(self, prop):
        if not self.re_bathrooms_min:
            return None
        actual = prop.bathroom_count or 0
        hit = actual >= self.re_bathrooms_min
        return {
            'label': _('Bathrooms'),
            'verdict': VERDICT_HIT if hit else VERDICT_MISS,
            'detail': _("%(actual)s, wanted %(low)s+",
                        actual=actual, low=self.re_bathrooms_min),
        }

    def _re_check_floor(self, prop):
        if not (self.re_floor_min or self.re_floor_max):
            return None
        actual = prop.floor_number or 0
        low, high = self.re_floor_min or 0, self.re_floor_max or 0
        if (not low or actual >= low) and (not high or actual <= high):
            verdict = VERDICT_HIT
        elif low and actual >= low - 2:
            verdict = VERDICT_PARTIAL
        else:
            verdict = VERDICT_MISS
        return {'label': _('Floor'), 'verdict': verdict,
                'detail': _("floor %(actual)s, wanted %(low)s+",
                            actual=actual, low=low or _('any'))}

    def _re_check_view(self, prop):
        """Scored only if the inventory actually records a view.

        No model in the frozen suite has a view/orientation field today. Rather
        than score every unit a MISS — which would quietly punish inventory for
        a data point the system does not hold, and make every score wrong in
        the same direction — the criterion drops out of both the numerator and
        the denominator, exactly as if the customer had not expressed it.

        The lookup is by field name so that the day Developer adds
        `view_type`, matching starts using it with no change here. Until then
        the gap is real and is recorded as such in the implementation report,
        not papered over.
        """
        if not self.re_view_preference or self.re_view_preference == 'any':
            return None
        if 'view_type' not in prop._fields:
            return None
        actual = (prop.view_type or '').lower()
        if not actual:
            return None
        return {
            'label': _('View'),
            'verdict': (VERDICT_HIT if self.re_view_preference in actual
                        else VERDICT_MISS),
            'detail': actual,
        }
