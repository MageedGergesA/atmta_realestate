# -*- coding: utf-8 -*-
"""M6 — who is looking, and what that permits.

Three audiences, one authorisation model. The mode decides what is *assembled*;
`realestate.visual.access` still decides what may be *fetched*, and record
rules still decide what may be *read*. Hiding Odoo's chrome does not remove a
permission check, and a mode is not a security boundary on its own.

```
    PRESENTATION   internal, authenticated, full-screen showroom
                   → same server permissions as the backend
                   → optional crm.lead context

    PUBLIC         anonymous, token-authorised
                   → released inventory, public prices, approved plans
                   → never an internal price, a block reason or a customer

    BROKER         Brokerage decides everything
                   → this module asks; it does not re-implement
```

### Broker mode consumes, it does not rebuild

Module 4 owns broker agreements, authorised projects, licence and KYC standing,
and lead protection. Every one of those questions is asked of Brokerage:

| Question | Answered by |
|---|---|
| may this broker transact at all? | `res.partner._check_may_transact()` |
| may they see this project? | `realestate.broker.agreement._covers_project()` |
| is their agreement live? | `agreement._check_still_valid()` |
| whose customer is this? | `realestate.lead.registration` |

There is no second broker model here, no second agreement, no second
project-authorisation rule. `real_estate_maquette` does not even depend on
`real_estate_brokerage` — the integration is late-bound through the registry,
the same pattern the shortlist uses, so a deployment without Brokerage simply
has no broker mode rather than a broken one.
"""

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError

from .visual_commercial import (
    AUDIENCE_BROKER,
    AUDIENCE_INTERNAL,
    AUDIENCE_PUBLIC,
)

#: The three experiences. `mode` is presentation; `audience` is data exposure.
#: They are deliberately separate names: presentation mode is a *layout*
#: decision and internal is a *data* decision, and conflating them is how a
#: full-screen customer-facing view ends up shipped with internal pricing in it.
MODE_BACKEND = 'backend'
MODE_PRESENTATION = 'presentation'
MODE_PUBLIC = 'public'
MODE_BROKER = 'broker'

MODE_AUDIENCE = {
    MODE_BACKEND: AUDIENCE_INTERNAL,
    MODE_PRESENTATION: AUDIENCE_INTERNAL,
    MODE_PUBLIC: AUDIENCE_PUBLIC,
    MODE_BROKER: AUDIENCE_BROKER,
}


class VisualModes(models.AbstractModel):
    """What each mode is allowed to assemble."""
    _name = 'realestate.visual.modes'
    _description = 'Visual Gallery Modes'

    # ------------------------------------------------------------------
    @api.model
    def audience_for(self, mode):
        """Presentation mode is internal data in a customer-facing layout."""
        return MODE_AUDIENCE.get(mode, AUDIENCE_PUBLIC)

    # ==================================================================
    # Presentation mode
    # ==================================================================
    @api.model
    def presentation_context(self, project_id, crm_lead_id=None):
        """Everything a showroom session needs, assembled once.

        The CRM opportunity is **optional**. A salesperson walking a visitor
        through a tower before anybody has taken a name must be able to open
        the gallery, and requiring an opportunity first would either block them
        or make them create a junk lead — which is the thing the public-session
        rule exists to prevent, arriving through the internal door.
        """
        if not self.env.user._is_internal():
            raise AccessError(_("Presentation mode is for internal users."))

        project = self.env['realestate.project'].browse(int(project_id))
        # As the real user. Hiding the chrome changes nothing about who may
        # read what.
        project.check_access_rights('read')
        project.check_access_rule('read')

        Gallery = self.env['realestate.visual.gallery']
        lead = self._presentation_lead(crm_lead_id)

        return {
            'mode': MODE_PRESENTATION,
            'project_id': project.id,
            'project_name': project.display_name,
            'audience': AUDIENCE_INTERNAL,
            'crm_lead_id': lead.id if lead else False,
            'crm_lead_name': lead.display_name if lead else '',
            'partner_name': (lead.partner_id.display_name
                             if lead and lead.partner_id else ''),
            'shortlist': (Gallery.shortlist_for(lead.id, AUDIENCE_INTERNAL)
                          if lead else []),
            'shortlist_available': Gallery.shortlist_available(),
            'can_reserve': True,
            'fallback': project.sudo()._visual_fallback_descriptor(),
        }

    @api.model
    def _presentation_lead(self, crm_lead_id):
        """The opportunity, if one was supplied and may be read.

        Read as the real user, so a salesperson cannot pull up somebody else's
        opportunity by passing its id into the URL.
        """
        if not crm_lead_id:
            return None
        lead = self.env['crm.lead'].browse(int(crm_lead_id))
        try:
            lead.check_access_rights('read')
            lead.check_access_rule('read')
            lead.read(['id'])
        except Exception as exc:
            raise AccessError(_("That opportunity is not available.")) from exc
        return lead

    # ==================================================================
    # Public mode — a visitor nobody has met yet
    # ==================================================================
    @api.model
    def public_context(self, project_id, grant_token=None, origin=None):
        """What an anonymous visitor may be shown, assembled in one place.

        ### Authorisation first, always

        A public route runs as the shared Public user, whose access is broad by
        design — so "the ORM let me read it" is not authorisation here. The
        project must be *published for the public*, and the caller must present
        the same grant that authorises assets. Only then does anything get
        assembled, and only from the public audience.

        ### What is deliberately absent

        No internal price basis, no premium-rule internals, no minimum
        acceptable price, no discount authority, no approval metadata, no
        commercial block or its reason, no customer, no commission. Those are
        not filtered out at the end; they are never fetched, because
        `unit_payload(audience='public')` is the only source used and it does
        not read them.

        ### The shortlist a visitor keeps

        `shortlist_available` is **False** and `session_shortlist_only` is True.
        A visitor who taps a heart is holding a list in their own browser: this
        module writes no CRM record for somebody who has not identified
        themselves, and there is no route here that would. The list becomes
        real only when the visitor submits an enquiry — an explicit act — at
        which point `merge_anonymous_shortlist` folds it onto the lead that act
        created.
        """
        project = self.env['realestate.project'].sudo().browse(
            int(project_id)).exists()
        if not project:
            raise AccessError(_("Not found."))
        if not (project.visual_public_enabled and project.visual_is_live):
            raise AccessError(_("Not found."))

        Access = self.env['realestate.visual.access']
        Access.check_public_grant(project, grant_token, origin=origin)

        # Authorised. Only now, and only to assemble the public audience.
        #
        # A public route runs as the Public user, who cannot read a property —
        # so this cannot be left to the ORM either way: without `sudo()` the
        # gallery is empty for everybody, and with it *before* the grant check
        # the gallery is open to everybody. The order is the whole point.
        Gallery = self.env['realestate.visual.gallery'].sudo()
        result = Gallery.search_units(project, audience=AUDIENCE_PUBLIC)
        return {
            'mode': MODE_PUBLIC,
            'project_id': project.id,
            'project_name': project.display_name,
            'audience': AUDIENCE_PUBLIC,
            'units': result['units'],
            'facets': result['facets'],
            'fallback': project._visual_fallback_descriptor(),
            # No CRM shortlist for somebody nobody has met.
            'shortlist': [],
            'shortlist_available': False,
            'session_shortlist_only': True,
            # Reserving is not a thing an anonymous visitor does. They register
            # interest; a salesperson reserves.
            'can_reserve': False,
            'enquiry_url': '/projects/%s/eoi' % project.id,
        }

    @api.model
    def convert_public_session(self, crm_lead_id, property_ids,
                               source='public_embed'):
        """The one path from a visitor's private list into the database.

        Called after an explicit act — an enquiry or a viewing request — has
        already produced a lead. It refuses to invent one: without an
        opportunity there is nothing to attach a favourite to, and creating a
        contact from a browsing session is precisely what the rule forbids.
        """
        if not crm_lead_id:
            raise UserError(_(
                "A shortlist becomes real only once a customer has "
                "identified themselves."))
        return self.env['realestate.visual.gallery'].merge_anonymous_shortlist(
            int(crm_lead_id), property_ids, source=source)

    # ==================================================================
    # Broker mode — every answer comes from Brokerage
    # ==================================================================
    @api.model
    def _brokerage_available(self):
        return self.env.get('realestate.broker.agreement') is not None

    @api.model
    def broker_may_view_project(self, broker_partner, project):
        """Ask Brokerage. Do not re-implement its rules.

        Returns `(allowed, reason)` rather than raising, because the gallery
        needs to render a refusal rather than a traceback — and the reason is
        Brokerage's own wording, not a second description of the same rule.
        """
        if not self._brokerage_available():
            return False, _("Broker access requires the Brokerage module.")

        broker = broker_partner.sudo()
        try:
            broker._check_may_transact()
        except Exception as exc:
            # Suspended, unlicensed, KYC-incomplete — Brokerage's own message.
            return False, str(exc)

        agreement = broker.active_broker_agreement_id
        if not agreement:
            return False, _("No active broker agreement.")
        try:
            agreement._check_still_valid()
        except Exception as exc:
            return False, str(exc)

        if not agreement._covers_project(project.sudo()):
            return False, _(
                "%(broker)s's agreement does not cover %(project)s.",
                broker=broker.display_name, project=project.display_name)
        return True, ''

    @api.model
    def broker_context(self, broker_partner_id, project_id):
        """A broker's view of a project, or a refusal with a reason."""
        broker = self.env['res.partner'].sudo().browse(int(broker_partner_id))
        project = self.env['realestate.project'].sudo().browse(int(project_id))
        allowed, reason = self.broker_may_view_project(broker, project)
        if not allowed:
            return {'mode': MODE_BROKER, 'allowed': False, 'reason': reason,
                    'units': []}

        Gallery = self.env['realestate.visual.gallery']
        # Broker pricing is the external audience — Developer's public price.
        # A broker never sees the internal basis, and this is the same call the
        # public embed makes rather than a parallel one that could drift.
        result = Gallery.search_units(
            project, filters={'visual_state': ['available']},
            audience=AUDIENCE_BROKER)
        return {
            'mode': MODE_BROKER,
            'allowed': True,
            'reason': '',
            'project_id': project.id,
            'project_name': project.display_name,
            'audience': AUDIENCE_BROKER,
            'units': result['units'],
            'facets': result['facets'],
            # Reservation for a broker's customer goes through Brokerage's
            # registration and protection workflow, not through a second
            # "visual broker customer" of our own.
            'reservation_via': 'brokerage_registration',
        }

    @api.model
    def broker_may_reserve(self, broker_partner, project, customer_phone=None,
                           customer_email=None):
        """Whether a broker may start a commercial action for a customer.

        Delegates to Brokerage's lead registration, which owns protection and
        collision detection. This module deliberately does not learn who holds
        a lead — that answer, and its careful silence about *who* holds it,
        belongs to Module 4.
        """
        allowed, reason = self.broker_may_view_project(broker_partner, project)
        if not allowed:
            return False, reason
        Registration = self.env.get('realestate.lead.registration')
        if Registration is None:
            return False, _("Broker registration requires the Brokerage "
                            "module.")
        return True, _(
            "Register the customer through the broker lead-registration "
            "workflow before reserving.")
