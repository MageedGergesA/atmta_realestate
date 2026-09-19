# -*- coding: utf-8 -*-
"""M7 — a link that opens the right unit, and authorises before it does.

### The rule that shapes all of this

**An identifier is not an authorisation.** A deep link may carry whichever
reference is convenient; what it may never do is cause a resource to be served
*because* the reference was well-formed. Internal links resolve through Odoo's
own access rules; public links carry a grant, exactly as assets do.

```
    /visual/go/unit/1234                internal — access rules decide
    /visual/p/<grant>/unit/1234         public   — the grant decides
```

### And a price in a URL is a number somebody typed

The link says *which* unit. It never says what that unit costs or whether it is
available: those are read fresh at open time from
`realestate.visual.commercial`, which is the only thing in this module that
answers them. A shared link that was accurate on Tuesday shows Thursday's
truth on Thursday.

### Resolution order

```
    1. authorise the context           (rules, or the grant)
    2. resolve the target              (unit → floor → building → project)
    3. hand back a navigation path     the viewer walks
    4. the viewer fetches fresh commercial metadata
```

Step 2 walks *upwards* from the unit rather than trusting a project id in the
URL, so a link cannot claim a unit belongs to a project it does not.
"""

from odoo import _, api, models
from odoo.exceptions import AccessError

#: What a deep link may point at.
LINK_TARGETS = ('project', 'building', 'floor', 'unit')


class VisualDeepLink(models.AbstractModel):
    """Build and resolve gallery navigation links."""
    _name = 'realestate.visual.deeplink'
    _description = 'Visual Gallery Deep Links'

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------
    @api.model
    def internal_url(self, target, record_id):
        """A link for a salesperson, resolved under Odoo access rules."""
        if target not in LINK_TARGETS:
            raise ValueError('Unknown deep-link target: %s' % target)
        return '/visual/go/%s/%s' % (target, int(record_id))

    @api.model
    def public_url(self, target, record_id, grant):
        """A link for a customer, carrying its own authorisation.

        The grant is in the path rather than the query string so it survives
        the copy-paste into WhatsApp that strips query parameters, and so a QR
        code encodes one atomic thing.
        """
        if target not in LINK_TARGETS:
            raise ValueError('Unknown deep-link target: %s' % target)
        return '/visual/p/%s/%s/%s' % (grant.token, target, int(record_id))

    @api.model
    def share_links(self, unit, audience='internal', origin=None):
        """Every safe URL for a unit, for a human to copy.

        Produces URLs and nothing else. Sending them — email, WhatsApp, SMS —
        is deliberately not built here; the brief asks for the safe link, not
        an outbound messaging feature, and a module that quietly grew one would
        be a module that quietly grew a compliance surface.
        """
        unit = unit.sudo()
        project = unit.project_id
        links = {'internal': self.internal_url('unit', unit.id)}

        if audience == 'internal' or not project:
            return links

        if not (project.visual_public_enabled and project.visual_is_live):
            # No public link exists for a project nobody published. Returning
            # one that 404s would look like a bug rather than a decision.
            links['public'] = False
            links['public_reason'] = _(
                "This project is not published publicly.")
            return links

        grant = self.env['realestate.visual.access'].grant_for_public_project(
            project,
            kinds=['maquette_glb', 'maquette_hdr', 'master_plan_2d',
                   'plan_image', 'floor_plan_image', 'gallery_image'],
            origin=origin, source='portal_page',
            source_ref='share/unit/%s' % unit.id)
        links['public'] = self.public_url('unit', unit.id, grant)
        links['expires_at'] = grant.expires_at
        return links

    # ------------------------------------------------------------------
    # Resolving
    # ------------------------------------------------------------------
    @api.model
    def resolve(self, target, record_id, grant_token=None, origin=None):
        """Authorise, then describe where the viewer should navigate.

        Returns the whole ancestry so the client can open the project, walk to
        the building and floor, and select the unit — rather than dropping the
        user into a unit panel with no context around it.
        """
        if target not in LINK_TARGETS:
            raise AccessError(_("Not found."))

        model = ('realestate.project' if target == 'project'
                 else 'realestate.building.floor' if target == 'floor'
                 else 'realestate.property')
        record = self.env[model].sudo().browse(int(record_id)).exists()
        if not record:
            raise AccessError(_("Not found."))

        project = self._project_of(target, record)
        if not project:
            raise AccessError(_("Not found."))

        if grant_token:
            self._authorize_public(project, grant_token, origin)
        else:
            self._authorize_internal(model, record_id)

        return self._navigation_path(target, record, project)

    @api.model
    def _project_of(self, target, record):
        """Walk upwards. Never trust a project id supplied by the caller."""
        if target == 'project':
            return record
        if target == 'floor':
            return record.project_id
        return record.project_id or (
            record.parent_id.project_id if record.parent_id else False)

    @api.model
    def _authorize_internal(self, model, record_id):
        try:
            record = self.env[model].browse(int(record_id))
            record.check_access_rights('read')
            record.check_access_rule('read')
            record.read(['id'])
        except Exception as exc:
            raise AccessError(_("Not found.")) from exc
        return True

    @api.model
    def _authorize_public(self, project, grant_token, origin):
        """The same grant that authorises assets authorises navigation.

        Deliberately one mechanism: a second one for links would be a second
        thing to expire, revoke and scope, and the two would drift.
        """
        return self.env['realestate.visual.access'].check_public_grant(
            project, grant_token, origin=origin)

    @api.model
    def _navigation_path(self, target, record, project):
        """The ancestry, plus nothing commercial.

        No price, no availability, no status. The viewer fetches those from
        `realestate.visual.commercial` once it has navigated, so a link that
        was accurate on Tuesday shows Thursday's truth on Thursday.
        """
        path = {
            'target': target,
            'project_id': project.id,
            'building_id': False,
            'floor_number': False,
            'unit_id': False,
        }
        if target == 'floor':
            path['building_id'] = record.building_id.id
            path['floor_number'] = record.floor_number
        elif target == 'building':
            path['building_id'] = record.id
        elif target == 'unit':
            path['unit_id'] = record.id
            parent = record.parent_id
            if parent:
                path['building_id'] = parent.id
            path['floor_number'] = record.floor_number
        return path
