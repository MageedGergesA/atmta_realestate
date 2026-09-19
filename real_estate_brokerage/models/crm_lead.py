# -*- coding: utf-8 -*-
"""M1 — `crm.lead` is the customer opportunity spine.

### Why this file exists

The Phase 0 audit answered the module's founding question: **`realestate.lead`
had no consumers outside Brokerage**, while `crm.lead` was already the object
the website form, the public API and the customer portal all created and read.
Brokerage 0.1 built a second pipeline for the same customer without checking
that the suite already had one.

So the spine moves to `crm.lead`, and what Brokerage genuinely owned — the
real-estate *requirements* of a buyer — moves onto it. Nothing else about CRM is
re-implemented here: stages, lost reasons, duplicate detection, merging,
conversion, teams, activities and stage-duration tracking are Odoo's, and
re-creating any of them was exactly the mistake being corrected.

### What is deliberately NOT added

* **No `state` selection.** The pipeline is `stage_id`; loss is
  `active = False` + `lost_reason_id`. A parallel selection is what made the
  legacy model impossible to reconcile with CRM.
* **No lead sequence.** `crm.lead.name` is the opportunity title, as Odoo
  intends. The legacy `LEAD-00001` reference survives on
  `re_legacy_reference` so a document printed in 2024 is still findable.
* **No duplicate-detection engine.** Odoo 18 ships
  `duplicate_lead_ids` (email domain, `phone_sanitized`, commercial entity) and
  it surfaces candidates without ever auto-merging — which is precisely M5's
  requirement.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: What the customer wants to do. Drives which inventory is even relevant.
RE_INTENT = [
    ('buy', 'Buy'),
    ('rent', 'Rent'),
    ('sell', 'Sell'),
    ('lease_out', 'Lease Out'),
]

#: Which inventory channel the opportunity is being served from. A buyer can be
#: open to more than one, so this is the *primary* channel; matching searches
#: whatever the requirement allows.
RE_MARKET = [
    ('developer', 'Developer Inventory'),
    ('resale', 'Resale / Secondary'),
    ('external', 'External Brokerage'),
    ('any', 'Any'),
]

RE_PAYMENT_PREFERENCE = [
    ('cash', 'Cash'),
    ('installment', 'Installments'),
    ('mortgage', 'Mortgage'),
    ('any', 'No Preference'),
]

RE_URGENCY = [
    ('immediate', 'Immediate'),
    ('3_months', 'Within 3 Months'),
    ('6_months', 'Within 6 Months'),
    ('12_months', 'Within a Year'),
    ('exploring', 'Just Exploring'),
]

RE_FINISHING = [
    ('core_shell', 'Core & Shell'),
    ('semi_finished', 'Semi-finished'),
    ('fully_finished', 'Fully Finished'),
    ('furnished', 'Furnished'),
    ('any', 'No Preference'),
]

RE_VIEW = [
    ('garden', 'Garden'),
    ('pool', 'Pool'),
    ('sea', 'Sea'),
    ('landscape', 'Landscape'),
    ('street', 'Street'),
    ('any', 'No Preference'),
]


class CrmLead(models.Model):
    """The real-estate requirements layer.

    Kept deliberately small. Multi-valued concepts (projects, property types,
    locations) are normalised into relations rather than becoming a wall of
    boolean columns, which is what M1 asks for.
    """
    _inherit = 'crm.lead'

    # ------------------------------------------------------------------
    # Intent
    # ------------------------------------------------------------------
    re_intent = fields.Selection(
        RE_INTENT, string='Intent', tracking=True,
        help="What the customer wants to do. Matching only ever looks at "
             "inventory that can satisfy this.")
    re_market = fields.Selection(
        RE_MARKET, string='Inventory Channel', default='any', tracking=True)

    # ------------------------------------------------------------------
    # Budget and size
    # ------------------------------------------------------------------
    # `company_currency` is what every Monetary on `crm.lead` uses — Odoo's own
    # `expected_revenue` included. Naming it explicitly keeps the budget in the
    # same currency as the revenue figures it will be compared against.
    re_budget_min = fields.Monetary(
        string='Budget From', currency_field='company_currency', tracking=True)
    re_budget_max = fields.Monetary(
        string='Budget To', currency_field='company_currency', tracking=True)
    re_area_min = fields.Float(string='Area From (sqm)')
    re_area_max = fields.Float(string='Area To (sqm)')
    re_bedrooms_min = fields.Integer(string='Bedrooms From')
    re_bedrooms_max = fields.Integer(string='Bedrooms To')
    re_bathrooms_min = fields.Integer(string='Bathrooms From')
    re_floor_min = fields.Integer(string='Floor From')
    re_floor_max = fields.Integer(string='Floor To')

    # ------------------------------------------------------------------
    # Qualitative preferences
    # ------------------------------------------------------------------
    re_view_preference = fields.Selection(RE_VIEW, string='Preferred View')
    re_finishing = fields.Selection(RE_FINISHING, string='Finishing')
    re_handover_from = fields.Date(
        string='Handover From',
        help="Earliest acceptable handover. A ready unit satisfies any "
             "handover preference; an off-plan unit must deliver by "
             "'Handover To'.")
    re_handover_to = fields.Date(string='Handover By')
    re_payment_preference = fields.Selection(
        RE_PAYMENT_PREFERENCE, string='Payment Preference', default='any')
    re_urgency = fields.Selection(RE_URGENCY, string='Urgency', tracking=True)
    re_target_move_date = fields.Date(string='Target Move Date')

    # ------------------------------------------------------------------
    # Normalised multi-value preferences
    # ------------------------------------------------------------------
    re_project_ids = fields.Many2many(
        'realestate.project', 'crm_lead_re_project_rel', 'lead_id',
        'project_id', string='Preferred Projects',
        help="Projects the customer is open to. Distinct from 'Real Estate "
             "Project' below, which records the single project a website or "
             "API enquiry arrived through.")
    re_property_type_ids = fields.Many2many(
        'property.type', 'crm_lead_re_property_type_rel', 'lead_id', 'type_id',
        string='Preferred Property Types')
    re_location_ids = fields.One2many(
        'crm.lead.re.location', 'lead_id', string='Preferred Locations',
        help="Normalised: one row per place the customer would consider.")

    # ------------------------------------------------------------------
    # Attribution (M16) — first touch is never overwritten
    # ------------------------------------------------------------------
    re_first_source_id = fields.Many2one(
        'utm.source', string='First-touch Source', readonly=True, copy=False,
        help="The source this opportunity ORIGINALLY arrived through, captured "
             "once at creation. Odoo's own `source_id` is the current/latest "
             "source and may legitimately change; this one never does.")
    re_first_medium_id = fields.Many2one(
        'utm.medium', string='First-touch Medium', readonly=True, copy=False)
    re_first_campaign_id = fields.Many2one(
        'utm.campaign', string='First-touch Campaign', readonly=True,
        copy=False)
    re_first_seen_on = fields.Datetime(
        string='First Seen', readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Legacy bridge (M30)
    # ------------------------------------------------------------------
    re_legacy_lead_id = fields.Many2one(
        'realestate.lead', string='Legacy Brokerage Lead', readonly=True,
        copy=False, index=True, ondelete='set null',
        help="The pre-0.2 `realestate.lead` this opportunity was migrated "
             "from. Kept so the migration is idempotent and so history stays "
             "traceable in both directions.")
    re_legacy_reference = fields.Char(
        string='Legacy Reference', readonly=True, copy=False, index=True,
        help="The legacy LEAD-00000 reference. Preserved because documents "
             "printed before the migration quote it.")

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('re_intent', 're_budget_min', 're_budget_max',
                 're_project_ids', 're_property_type_ids', 're_location_ids')
    def _compute_re_is_realestate(self):
        """Set once anything real-estate is present; never cleared silently.

        A generic CRM pipeline sharing the database must stay untouched, so the
        flag is what every real-estate view, rule and report filters on.
        """
        for lead in self:
            if lead.re_is_realestate:
                continue
            lead.re_is_realestate = bool(
                lead.re_intent or lead.re_budget_min or lead.re_budget_max
                or lead.re_project_ids or lead.re_property_type_ids
                or lead.re_location_ids or lead._re_origin_project())

    re_is_realestate = fields.Boolean(
        string='Real Estate Opportunity', index=True,
        compute='_compute_re_is_realestate', store=True, readonly=False,
        help="Marks this opportunity as carrying real-estate requirements. Set "
             "automatically as soon as any requirement is filled in, so a "
             "generic CRM pipeline in the same database is unaffected.")

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('re_budget_min', 're_budget_max')
    def _check_re_budget_range(self):
        for lead in self:
            if (lead.re_budget_min and lead.re_budget_max
                    and lead.re_budget_max < lead.re_budget_min):
                raise UserError(_(
                    "Budget range is inverted: 'Budget To' (%(high)s) is below "
                    "'Budget From' (%(low)s).",
                    high=lead.re_budget_max, low=lead.re_budget_min))

    @api.constrains('re_area_min', 're_area_max')
    def _check_re_area_range(self):
        for lead in self:
            if (lead.re_area_min and lead.re_area_max
                    and lead.re_area_max < lead.re_area_min):
                raise UserError(_(
                    "Area range is inverted: %(high)s sqm is below %(low)s sqm.",
                    high=lead.re_area_max, low=lead.re_area_min))

    @api.constrains('re_bedrooms_min', 're_bedrooms_max')
    def _check_re_bedroom_range(self):
        for lead in self:
            if (lead.re_bedrooms_min and lead.re_bedrooms_max
                    and lead.re_bedrooms_max < lead.re_bedrooms_min):
                raise UserError(_(
                    "Bedroom range is inverted."))

    @api.constrains('re_handover_from', 're_handover_to')
    def _check_re_handover_range(self):
        for lead in self:
            if (lead.re_handover_from and lead.re_handover_to
                    and lead.re_handover_to < lead.re_handover_from):
                raise UserError(_("Handover window is inverted."))

    # ------------------------------------------------------------------
    # Create / write
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        leads._capture_first_touch()
        leads._seed_preferred_project_from_origin()
        return leads

    def _capture_first_touch(self):
        """M16 — record where an opportunity came from, once.

        Odoo's `source_id` is the *current* attribution and may be re-stamped by
        a later campaign, an import, or a salesperson correcting a value. The
        original is a fact about how this customer was acquired and must not be
        overwritten, so it is copied to its own read-only field at creation and
        never touched again.
        """
        for lead in self:
            if lead.re_first_seen_on:
                continue
            lead.sudo().write({
                're_first_source_id': lead.source_id.id or False,
                're_first_medium_id': lead.medium_id.id or False,
                're_first_campaign_id': lead.campaign_id.id or False,
                're_first_seen_on': lead.create_date or fields.Datetime.now(),
            })

    def _re_origin_project(self):
        """The single project a website or API enquiry arrived through.

        `re_project_id` is added by `real_estate_portal` and
        `realestate_api_project_id` by `real_estate_api`. **Brokerage depends on
        neither**, so both are read defensively: this module must install and
        work in a deployment that has only the CRM half of the suite.
        """
        self.ensure_one()
        for fname in ('re_project_id', 'realestate_api_project_id'):
            if fname in self._fields and self[fname]:
                return self[fname]
        return self.env['realestate.project'].browse()

    def _seed_preferred_project_from_origin(self):
        """A website or API enquiry names one project; treat it as a preference.

        Those fields record where the enquiry came *from*. If the customer
        clicked a project, that project is self-evidently of interest, so it
        seeds the preference list — without erasing anything a salesperson
        later adds.
        """
        for lead in self:
            origin = lead._re_origin_project()
            if origin and origin not in lead.re_project_ids:
                lead.re_project_ids = [(4, origin.id)]

    # ------------------------------------------------------------------
    # Requirements helpers, used by the matching engine (M8)
    # ------------------------------------------------------------------
    def _re_has_requirements(self):
        """Enough to search on? A blank requirement matches everything, which
        is not a match — it is a missing brief."""
        self.ensure_one()
        return bool(
            self.re_budget_max or self.re_budget_min or self.re_area_min
            or self.re_area_max or self.re_bedrooms_min or self.re_project_ids
            or self.re_property_type_ids or self.re_location_ids)

    def action_re_open_requirements(self):
        """Jump straight to the requirements tab from anywhere."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Requirements — %s') % self.display_name,
            'res_model': 'crm.lead',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }


class CrmLeadRealEstateLocation(models.Model):
    """One place a customer would consider.

    A child model rather than four columns on the lead, because "preferred
    locations" is genuinely multi-valued: a buyer will look in New Cairo *and*
    Sheikh Zayed, and flattening that into a single city field loses half the
    brief.
    """
    _name = 'crm.lead.re.location'
    _description = 'CRM Lead Preferred Location'
    _order = 'lead_id, sequence, id'

    lead_id = fields.Many2one(
        'crm.lead', string='Opportunity', required=True, ondelete='cascade',
        index=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        related='lead_id.company_id', store=True, index=True, readonly=True)

    country_id = fields.Many2one('res.country', string='Country')
    state_id = fields.Many2one(
        'res.country.state', string='State / Governorate',
        domain="[('country_id', '=?', country_id)]")
    city = fields.Char()
    district = fields.Char()
    note = fields.Char()

    display_name = fields.Char(compute='_compute_display_name', store=True)

    @api.depends('country_id', 'state_id', 'city', 'district')
    def _compute_display_name(self):
        for rec in self:
            parts = [rec.district, rec.city, rec.state_id.name,
                     rec.country_id.name]
            rec.display_name = ', '.join(p for p in parts if p) or _('Anywhere')

    @api.constrains('country_id', 'state_id', 'city', 'district')
    def _check_not_empty(self):
        for rec in self:
            if not (rec.country_id or rec.state_id or rec.city or rec.district):
                raise UserError(_(
                    "A preferred location must name at least a country, a "
                    "state, a city or a district."))
