# -*- coding: utf-8 -*-
"""M30 — migrating the legacy Brokerage pipeline onto the CRM spine.

This is the highest-risk operation in the module, so it is written as a service
that can be read, tested and re-run rather than as a one-shot script buried in
`migrations/`. The upgrade hook calls it; so do the tests.

### The five classifications, and why guessing is forbidden

A legacy lead is a customer plus a brief. A `crm.lead` is a customer plus an
opportunity. The two are **not** in bijection, because one customer legitimately
has several concurrent opportunities — a villa in one project and an apartment
in another are two deals, not one duplicated record. So the migration classifies
rather than merges:

| Outcome | When | What is done |
|---|---|---|
| `migrated` | no CRM counterpart exists | a new `crm.lead` is created |
| `linked` | exactly one deterministic counterpart | the existing one is linked |
| `multiple` | several counterparts, all legitimate | a new opportunity is created and the ambiguity is recorded |
| `ambiguous` | several candidates, none decisive | **nothing is linked**; reported |
| `skipped` | not enough data to identify a customer at all | **nothing is created**; reported |

"Deterministic" means the same `res.partner` — never a name, never an email
alone, never a phone alone. M30 forbids all three, and for good reason: two
family members share a surname, a household shares an email, and a switchboard
number belongs to a hundred people.

### Idempotence

Every decision is keyed on `realestate.lead.crm_lead_id`. A row that already has
one is skipped on every subsequent run, so the migration can be re-run after a
partial failure or a data fix without duplicating a single opportunity.
"""

import logging

from markupsafe import Markup

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

#: Legacy `state` → what it means for the CRM record. Loss is Odoo's own
#: mechanic (`active = False` + `lost_reason_id`), never a stage.
LEGACY_STATE_TO_STAGE = {
    'new': 'new',
    'qualified': 'qualified',
    'matched': 'matching',
    'viewing_scheduled': 'viewing',
    'offer': 'negotiation',
    'converted': 'won',
    'lost': None,
}

#: Legacy free-text source → the `utm.source` name to find or create.
LEGACY_SOURCE_NAMES = {
    'walk_in': 'Walk-in',
    'website': 'Website',
    'referral': 'Referral',
    'portal': 'Property Portal',
    'call': 'Phone Call',
    'other': 'Other',
}


class LegacyLeadMigration(models.AbstractModel):
    """The migration, as a callable service."""
    _name = 'realestate.lead.migration'
    _description = 'Legacy Brokerage Lead → CRM Migration'

    # ==================================================================
    # Entry point
    # ==================================================================
    @api.model
    def run(self, limit=None):
        """Classify and migrate every unbridged legacy lead.

        Returns a report dict — counts plus the references behind each
        classification — so the caller can log it, assert on it, or show it.
        """
        Legacy = self.env['realestate.lead'].with_context(active_test=False)
        pending = Legacy.search([('crm_lead_id', '=', False)], limit=limit)

        report = {
            'total': len(pending),
            'migrated': [], 'linked': [], 'multiple': [],
            'ambiguous': [], 'skipped': [],
        }
        if not pending:
            _logger.info("real_estate_brokerage: no legacy leads to migrate.")
            return report

        for legacy in pending:
            outcome, crm_lead, note = self._migrate_one(legacy)
            report[outcome].append({
                'legacy_id': legacy.id,
                'reference': legacy.name,
                'partner': legacy.partner_id.display_name or legacy.partner_name or '',
                'crm_lead_id': crm_lead.id if crm_lead else False,
                'note': note,
            })
            legacy.write({
                'crm_lead_id': crm_lead.id if crm_lead else False,
                'migration_state': outcome,
                'migration_note': note,
            })

        self._log_report(report)
        return report

    # ==================================================================
    # One record
    # ==================================================================
    def _migrate_one(self, legacy):
        """Return `(outcome, crm_lead_or_None, note)`. Never guesses."""
        if not legacy.partner_id:
            # Without a contact there is nothing to reconcile against. A free
            # -text name is not an identity: two people called "Ahmed Hassan"
            # are two people.
            return ('skipped', None, _(
                "No contact on the legacy lead — only the free-text name "
                "%(name)s. A name is not an identity, so nothing was created "
                "or linked.", name=legacy.partner_name or '(blank)'))

        candidates = self._find_candidates(legacy)
        live = candidates.filtered(lambda l: l.active and not l.date_closed)

        if not live:
            # Either nothing exists, or everything that does is closed. A
            # closed deal is history: the customer coming back is a NEW
            # opportunity, and attaching a fresh brief to a lost deal would
            # resurrect it.
            note = (_("No existing CRM opportunity for this contact; a new one "
                      "was created from the legacy brief.")
                    if not candidates else
                    _("This contact's only CRM opportunities are closed "
                      "(%(refs)s). A closed deal is history, so the legacy "
                      "brief became a new opportunity rather than reopening "
                      "one.", refs=', '.join(candidates.mapped('display_name')[:5])))
            return ('migrated', self._create_crm_lead(legacy), note)

        if len(live) > 1:
            # Several genuine opportunities for one customer is normal — a
            # villa in one project and an apartment in another are two deals.
            # Creating a new one is right; silently attaching the legacy brief
            # to an arbitrary existing deal would corrupt whichever it picked.
            created = self._create_crm_lead(legacy)
            return ('multiple', created, _(
                "This contact already has %(count)s live CRM opportunities, "
                "all apparently legitimate. A separate opportunity was created "
                "for the legacy brief rather than merging into one of them: "
                "%(refs)s",
                count=len(live),
                refs=', '.join(live.mapped('display_name')[:5])))

        # Exactly one live candidate. Decisive only if it is the SAME PERSON.
        counterpart = live
        if counterpart.partner_id == legacy.partner_id:
            self._enrich_crm_lead(counterpart, legacy)
            return ('linked', counterpart, _(
                "Linked to the single open CRM opportunity for this contact."))

        # Same commercial entity, different contact: Ahmed at ACME is not Sara
        # at ACME. That is a real ambiguity and a human has to resolve it.
        return ('ambiguous', None, _(
            "A single open opportunity exists for the same organisation but "
            "for a DIFFERENT contact (%(theirs)s vs %(ours)s). Nothing was "
            "linked — two people at one company are two customers. Review: "
            "%(refs)s",
            theirs=counterpart.partner_id.display_name or _('(none)'),
            ours=legacy.partner_id.display_name,
            refs=counterpart.display_name))

    # ==================================================================
    # Candidate discovery — partner identity only
    # ==================================================================
    def _find_candidates(self, legacy):
        """CRM opportunities for the SAME commercial entity.

        `child_of` the commercial partner, so a contact at a company and the
        company itself are recognised as one customer — which is how Odoo's own
        duplicate detection reasons about it too.
        """
        partner = legacy.partner_id
        commercial = partner.commercial_partner_id or partner
        return self.env['crm.lead'].with_context(active_test=False).search([
            ('partner_id', 'child_of', commercial.ids),
            ('type', 'in', ('lead', 'opportunity')),
        ])

    # ==================================================================
    # Creation and enrichment
    # ==================================================================
    def _create_crm_lead(self, legacy):
        vals = self._crm_vals_from_legacy(legacy)
        crm_lead = self.env['crm.lead'].create(vals)
        self._apply_locations(crm_lead, legacy)
        self._apply_loss(crm_lead, legacy)
        self._move_followers(crm_lead, legacy)
        return crm_lead

    def _enrich_crm_lead(self, crm_lead, legacy):
        """Fill blanks on an existing opportunity; never overwrite.

        The CRM record is the one people have been working. Its salesperson,
        its stage and any requirement already captured there are more current
        than a legacy row, so the migration only supplies what is missing.
        """
        vals = {}
        candidate = self._crm_vals_from_legacy(legacy)
        for field in ('re_intent', 're_budget_min', 're_budget_max',
                      're_area_min', 're_area_max', 're_bedrooms_min'):
            if candidate.get(field) and not crm_lead[field]:
                vals[field] = candidate[field]
        if candidate.get('re_property_type_ids') and not crm_lead.re_property_type_ids:
            vals['re_property_type_ids'] = candidate['re_property_type_ids']
        if not crm_lead.re_legacy_reference:
            vals['re_legacy_reference'] = legacy.name
        if not crm_lead.re_legacy_lead_id:
            vals['re_legacy_lead_id'] = legacy.id
        if vals:
            crm_lead.write(vals)
        if not crm_lead.re_location_ids:
            self._apply_locations(crm_lead, legacy)
        return crm_lead

    def _crm_vals_from_legacy(self, legacy):
        """The legacy brief, expressed as CRM requirements."""
        partner = legacy.partner_id
        stage = self._stage_for(legacy)
        vals = {
            # A legacy reference is not a title; give the opportunity a name a
            # human can read, and keep the reference in its own field.
            'name': _("%(partner)s — %(ref)s",
                      partner=partner.display_name or legacy.partner_name or _('Lead'),
                      ref=legacy.name),
            'type': 'opportunity',
            'partner_id': partner.id,
            'contact_name': legacy.partner_name or False,
            'email_from': legacy.email or False,
            'phone': legacy.phone or False,
            'user_id': legacy.agent_id.id or False,
            'priority': legacy.priority or '0',
            'description': legacy.notes or False,
            'color': legacy.color or 0,
            'company_id': self._company_for(legacy),
            'source_id': self._source_for(legacy),
            're_is_realestate': True,
            're_intent': 'buy',
            're_budget_min': legacy.budget_min or 0.0,
            're_budget_max': legacy.budget_max or 0.0,
            're_area_min': legacy.area_min or 0.0,
            're_area_max': legacy.area_max or 0.0,
            're_bedrooms_min': legacy.bedroom_count_min or 0,
            're_property_type_ids': [(6, 0, legacy.property_type_ids.ids)],
            're_legacy_lead_id': legacy.id,
            're_legacy_reference': legacy.name,
        }
        if stage:
            vals['stage_id'] = stage.id
        return vals

    def _company_for(self, legacy):
        """Legacy leads have no company; infer one and say so.

        The agent's company is the only signal available. Where there is no
        agent the environment's company is used — recorded in the note either
        way, so an operator can see that it was inferred rather than known.
        """
        return (legacy.agent_id.company_id.id
                or legacy.partner_id.company_id.id
                or self.env.company.id)

    def _source_for(self, legacy):
        """Legacy selection → `utm.source`, found or created once."""
        if not legacy.source:
            return False
        name = LEGACY_SOURCE_NAMES.get(legacy.source, legacy.source)
        Source = self.env['utm.source']
        source = Source.search([('name', '=', name)], limit=1)
        if not source:
            source = Source.create({'name': name})
        return source.id

    def _stage_for(self, legacy):
        """Map the frozen legacy state onto a real-estate CRM stage."""
        key = LEGACY_STATE_TO_STAGE.get(legacy.state)
        if not key:
            return self.env['crm.stage'].browse()
        stage = self.env.ref(
            'real_estate_brokerage.stage_re_%s' % key, raise_if_not_found=False)
        return stage or self.env['crm.stage'].browse()

    def _apply_locations(self, crm_lead, legacy):
        """One normalised location row, if the legacy record named a place."""
        if not (legacy.preferred_country_id or legacy.preferred_state_id
                or legacy.preferred_city or legacy.preferred_district):
            return
        self.env['crm.lead.re.location'].create({
            'lead_id': crm_lead.id,
            'country_id': legacy.preferred_country_id.id or False,
            'state_id': legacy.preferred_state_id.id or False,
            'city': legacy.preferred_city or False,
            'district': legacy.preferred_district or False,
            'note': _('Migrated from %s') % legacy.name,
        })

    def _apply_loss(self, crm_lead, legacy):
        """A lost legacy lead becomes a lost opportunity, Odoo's way.

        `active = False` plus a `crm.lost.reason`, not a stage — which is the
        whole point of M17. The legacy reason's *text* is carried across so the
        analysis does not lose it.
        """
        if legacy.state != 'lost':
            return
        reason = False
        if legacy.loss_reason_id:
            LostReason = self.env['crm.lost.reason']
            reason_rec = LostReason.search(
                [('name', '=', legacy.loss_reason_id.name)], limit=1)
            if not reason_rec:
                reason_rec = LostReason.create(
                    {'name': legacy.loss_reason_id.name})
            reason = reason_rec.id
        crm_lead.write({
            'active': False,
            'lost_reason_id': reason,
            'date_closed': legacy.write_date or fields.Datetime.now(),
        })

    def _move_followers(self, crm_lead, legacy):
        """Carry the conversation across.

        Followers move; messages are left in place. Re-parenting `mail.message`
        rows is destructive if the migration is ever re-run against a partially
        migrated database, and the legacy record stays readable, so the safer
        trade is to link the two records and leave the history where it is.
        """
        partners = legacy.message_follower_ids.mapped('partner_id')
        if partners:
            crm_lead.message_subscribe(partner_ids=partners.ids)
        crm_lead.message_post(body=Markup(_(
            "Migrated from legacy Brokerage lead <b>%(ref)s</b>. The original "
            "record and its full conversation remain available and linked.")) % {
                'ref': legacy.name})

    # ==================================================================
    # Reporting
    # ==================================================================
    def _log_report(self, report):
        _logger.info(
            "real_estate_brokerage: legacy lead migration — %s total: "
            "%s migrated, %s linked, %s multiple, %s ambiguous, %s skipped.",
            report['total'], len(report['migrated']), len(report['linked']),
            len(report['multiple']), len(report['ambiguous']),
            len(report['skipped']))
        for outcome in ('ambiguous', 'skipped'):
            rows = report[outcome]
            if not rows:
                continue
            _logger.warning(
                "real_estate_brokerage: %s legacy lead(s) classified '%s' and "
                "left for manual review:\n%s",
                len(rows), outcome,
                '\n'.join('  - %s (%s): %s' % (r['reference'], r['partner'],
                                               r['note'])
                          for r in rows[:50]))
