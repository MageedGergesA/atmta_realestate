# -*- coding: utf-8 -*-
"""M4.5-A — one gate in front of every visual asset.

### What the audit found, and what was worse than the audit found

`/api/v1/image/<model>/<id>/<field>` served `maquette_glb`, `interior_glb`,
`plan_image`, `floor_plan_image` and `elevation_sheet` with `auth='public'`,
`sudo()`, sequential integer ids and **no token**. That was documented.

Looking for every route that serves a visual asset turned up a second family
that had not been:

```
    /projects/<id>/glb                              → the whole GLB
    /projects/<id>/units.json                       → every unit
    /projects/<id>/regions.json                     → the polygon map
    /projects/portal/property/<id>/floor_plan       → floor plans
    /projects/portal/property/<id>/images.json      → the gallery
```

all `auth='public'`, all `sudo()`, and all authorised by exactly one test:
*the project exists and is not cancelled*. Fixing only the API route would have
left every one of these as a bypass — which is precisely what the brief warns
against.

### The model

Authorisation is **mandatory and resource-level**, whoever is asking:

```
   request for (kind, record)
        │
        ├── internal user   → check_access_rights + check_access_rule
        │                     as the REAL user, before any sudo()
        │
        ├── public + grant  → grant must match company, resource, kind,
        │                     be inside its window, unrevoked, origin-allowed
        │
        └── public, no grant → refused. Always.
                               (This is the hole being closed.)
```

`sudo()` happens **only after** one of those has succeeded, and only to stream
bytes. Nothing depends on the public user's own access rights, because relying
on the public user being suitably restricted is how this became a hole in the
first place.

### Grants, and why they are not a second token system

`real_estate_api` already owns `realestate.embed.token`, which authorises an
embed *page*: opaque, expiring, origin-checked, usage-tracked. That contract is
kept and is still the public entry point.

What was missing is a capability at the *resource* level. A grant is that: it is
minted **by** a page that has already been authorised — an embed token being
consumed, or a portal project page that passed its own public check — and it
carries only what that page legitimately needs. A page cannot mint a grant for a
project it was not itself authorised for.

### Not-found rather than forbidden

A wrong-project or wrong-kind request returns 404. Answering 403 for a record
that exists and 404 for one that does not turns the route into an existence
oracle, and enumeration is one of the things this is defending against.
"""

import hashlib
import logging
import secrets

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

#: Visual asset kinds, and the (model, field) each may serve. A request naming
#: a model/field pair that is not in here is refused before anything else
#: happens — the whitelist is the first gate, not the last.
VISUAL_ASSET_KINDS = {
    'maquette_glb':     ('realestate.project', 'maquette_glb'),
    'maquette_hdr':     ('realestate.project', 'maquette_env_hdr'),
    'master_plan_2d':   ('realestate.project', 'master_plan_2d'),
    'plan_image':       ('realestate.property', 'plan_image'),
    'floor_plan_image': ('realestate.property', 'floor_plan_image'),
    'elevation_sheet':  ('realestate.property', 'elevation_sheet'),
    'interior_glb':     ('realestate.property', 'interior_glb'),
    'unit_image':       ('realestate.property', 'image_1920'),
    'gallery_image':    ('property.image', 'image_1920'),
}

#: How long a grant minted for a page lives. Long enough to load a large model
#: on a slow connection and browse for a while; short enough that a leaked URL
#: is not a permanent one.
DEFAULT_GRANT_HOURS = 8


class VisualAccessGrant(models.Model):
    """A capability to fetch specific visual assets of one specific resource."""
    _name = 'realestate.visual.grant'
    _description = 'Visual Asset Access Grant'
    _order = 'create_date desc, id desc'
    _rec_name = 'token'

    token = fields.Char(
        required=True, index=True, copy=False, readonly=True,
        help="Opaque, unguessable, and never derived from a record id.")
    company_id = fields.Many2one(
        'res.company', required=True, index=True, readonly=True)
    project_id = fields.Many2one(
        'realestate.project', required=True, index=True, readonly=True,
        ondelete='cascade',
        help="Every grant is scoped to one project. A grant for project A can "
             "never serve an asset belonging to project B, whatever ids the "
             "caller supplies.")

    kinds = fields.Char(
        required=True, readonly=True,
        help="Comma-separated asset kinds this grant covers. A grant for a "
             "master plan does not also hand over interior models.")
    allowed_origins = fields.Char(
        readonly=True,
        help="Comma-separated origins. Empty means the grant is not "
             "origin-restricted — which is correct for a first-party portal "
             "page and wrong for a third-party embed.")

    expires_at = fields.Datetime(required=True, readonly=True, index=True)
    revoked = fields.Boolean(index=True, copy=False)
    revoked_on = fields.Datetime(readonly=True, copy=False)

    source = fields.Selection([
        ('embed_token', 'Embed Token'),
        ('portal_page', 'Portal Page'),
        ('internal', 'Internal Session'),
    ], required=True, readonly=True)
    source_ref = fields.Char(
        readonly=True, help="The embed token or page that minted this.")

    used_count = fields.Integer(readonly=True, copy=False)
    last_used_at = fields.Datetime(readonly=True, copy=False)
    last_used_ip = fields.Char(readonly=True, copy=False)

    is_valid = fields.Boolean(compute='_compute_is_valid')

    _sql_constraints = [
        ('token_unique', 'unique(token)', 'Grant tokens must be unique.'),
    ]

    @api.depends('revoked', 'expires_at')
    def _compute_is_valid(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.is_valid = bool(
                not rec.revoked and rec.expires_at and rec.expires_at > now)

    # ------------------------------------------------------------------
    @api.model
    def _mint(self, project, kinds, *, source, source_ref=None,
              allowed_origins=None, hours=DEFAULT_GRANT_HOURS):
        """Create a grant. Callers must already have authorised themselves.

        Deliberately not a public route: a grant is minted *by* a page that has
        passed its own check, never requested directly by a client.
        """
        if isinstance(kinds, str):
            kinds = [kinds]
        unknown = set(kinds) - set(VISUAL_ASSET_KINDS)
        if unknown:
            raise ValueError('Unknown visual asset kinds: %s' % sorted(unknown))
        return self.sudo().create({
            'token': secrets.token_urlsafe(32),
            'company_id': project.company_id.id or self.env.company.id,
            'project_id': project.id,
            'kinds': ','.join(sorted(kinds)),
            'allowed_origins': allowed_origins or False,
            'expires_at': fields.Datetime.now() + timedelta(hours=hours),
            'source': source,
            'source_ref': source_ref or '',
        })

    def _covers(self, kind):
        self.ensure_one()
        return kind in (self.kinds or '').split(',')

    def _origin_allowed(self, origin):
        """Empty allow-list means unrestricted; a set one is enforced exactly."""
        self.ensure_one()
        if not self.allowed_origins:
            return True
        if not origin:
            return False
        allowed = {o.strip().rstrip('/').lower()
                   for o in self.allowed_origins.split(',') if o.strip()}
        return origin.strip().rstrip('/').lower() in allowed

    def _record_use(self, remote_ip=None):
        self.ensure_one()
        self.sudo().write({
            'used_count': self.used_count + 1,
            'last_used_at': fields.Datetime.now(),
            'last_used_ip': (remote_ip or '')[:64],
        })

    def action_revoke(self):
        """Revoke these grants, under the caller's own rights.

        The `sudo()` was doing two jobs: writing the audit columns, and
        deciding who may revoke. Only the first is legitimate. Any signed-in
        caller, a portal user included, could pass guessed grant ids to this
        method and invalidate visual access belonging to another company.

        `check_access` applies the model's access rules and record rules to
        this exact recordset first; the elevated write then only spares the
        caller needing write rights on audit columns they should not be
        editing by hand.
        """
        self.check_access('write')
        self.sudo().write({'revoked': True,
                           'revoked_on': fields.Datetime.now()})
        return True

    @api.model
    def _gc(self):
        """Drop grants that expired more than a day ago."""
        cutoff = fields.Datetime.now() - timedelta(days=1)
        stale = self.sudo().search([('expires_at', '<', cutoff)])
        count = len(stale)
        stale.unlink()
        return count


class VisualAccess(models.AbstractModel):
    """The single gate. Every asset route calls this before serving bytes."""
    _name = 'realestate.visual.access'
    _description = 'Visual Asset Authorisation'

    # ------------------------------------------------------------------
    # The gate
    # ------------------------------------------------------------------
    @api.model
    def authorize(self, kind, record_id, *, token=None, origin=None,
                  remote_ip=None):
        """Authorise one asset request. Returns the record, or raises.

        Raises `AccessError` for everything that fails, and callers turn that
        into a **404**. Distinguishing "forbidden" from "not found" would make
        the route an existence oracle, and enumeration is one of the things
        this exists to defend against.
        """
        spec = VISUAL_ASSET_KINDS.get(kind)
        if not spec:
            raise AccessError(_("Unknown asset kind."))
        model_name, field_name = spec

        # Browse as sudo ONLY to resolve the record for the checks below.
        # Nothing is served from this recordset until authorisation succeeds.
        record = self.env[model_name].sudo().browse(int(record_id)).exists()
        if not record or not record[field_name]:
            raise AccessError(_("Not found."))

        if token:
            self._authorize_grant(kind, record, token, origin, remote_ip)
        elif self.env.user._is_internal():
            self._authorize_internal(model_name, record_id)
        else:
            # The hole, closed. A public caller with no grant gets nothing,
            # whatever the project's state says.
            raise AccessError(_("Not found."))

        return record, field_name

    # ------------------------------------------------------------------
    @api.model
    def _authorize_internal(self, model_name, record_id):
        """As the real user, before any sudo.

        `check_access_rule` is what applies record rules — company isolation
        included — so an internal user of company A asking for company B's
        model is refused here rather than by a later check that might be
        forgotten.
        """
        try:
            record = self.env[model_name].browse(int(record_id))
            record.check_access_rights('read')
            record.check_access_rule('read')
            record.read(['id'])
        except Exception as exc:      # AccessError, MissingError, anything
            raise AccessError(_("Not found.")) from exc
        return True

    @api.model
    def _authorize_grant(self, kind, record, token, origin, remote_ip):
        Grant = self.env['realestate.visual.grant'].sudo()
        grant = Grant.search([('token', '=', token)], limit=1)
        if not grant or not grant.is_valid:
            raise AccessError(_("Not found."))
        if not grant._covers(kind):
            raise AccessError(_("Not found."))
        if not grant._origin_allowed(origin):
            raise AccessError(_("Not found."))

        project = self._project_of(record)
        if not project or project.id != grant.project_id.id:
            # A grant for project A asking for project B's asset.
            raise AccessError(_("Not found."))
        if project.company_id and project.company_id != grant.company_id:
            raise AccessError(_("Not found."))

        # Public assets belong to a project that has been deliberately
        # published for public consumption. A grant cannot outlive that
        # decision: unpublishing a gallery stops its assets being served even
        # while grants minted earlier are still inside their window.
        if not (project.visual_public_enabled and project.visual_is_live):
            raise AccessError(_("Not found."))

        grant._record_use(remote_ip)
        return True

    @api.model
    def check_public_grant(self, project, token, origin=None,
                           remote_ip=None):
        """Does this grant authorise *this project* for a public caller?

        The project-level counterpart of `_authorize_grant`, which answers the
        same question for one asset. Pages, navigation and JSON contexts all
        ask through here so there is one definition of "a valid public grant"
        to expire, revoke and scope — three copies of it would drift, and the
        one that drifted would be the one still serving bytes.

        Raises `AccessError` on every failure, with the same message in each
        case: a caller must not be able to tell a revoked grant from a wrong
        project from a project that was never published.
        """
        if not token:
            raise AccessError(_("Not found."))
        Grant = self.env['realestate.visual.grant'].sudo()
        grant = Grant.search([('token', '=', token)], limit=1)
        if not grant or not grant.is_valid:
            raise AccessError(_("Not found."))
        if not grant._origin_allowed(origin):
            raise AccessError(_("Not found."))
        if not project or grant.project_id.id != project.id:
            raise AccessError(_("Not found."))
        if project.company_id and project.company_id != grant.company_id:
            raise AccessError(_("Not found."))
        # A grant cannot outlive the decision to publish.
        if not (project.visual_public_enabled and project.visual_is_live):
            raise AccessError(_("Not found."))
        grant._record_use(remote_ip)
        return True

    @api.model
    def _project_of(self, record):
        """The project an asset belongs to, whatever kind of record it is."""
        model = record._name
        if model == 'realestate.project':
            return record
        if model == 'realestate.property':
            return record.project_id
        if model == 'property.image':
            return record.property_id.project_id
        return self.env['realestate.project'].browse()

    # ------------------------------------------------------------------
    # Minting, for pages that have already authorised themselves
    # ------------------------------------------------------------------
    @api.model
    def grant_for_public_project(self, project, kinds=None, origin=None,
                                 source='portal_page', source_ref=None):
        """Mint a grant for a project that is genuinely public.

        The check is re-done here rather than trusted from the caller. A page
        that forgets to check must not be able to mint a capability by asking
        nicely.
        """
        project = project.sudo()
        if not (project.visual_public_enabled and project.visual_is_live):
            raise AccessError(_("This project is not published publicly."))
        kinds = kinds or list(VISUAL_ASSET_KINDS)
        if isinstance(kinds, str):
            kinds = [kinds]
        Grant = self.env['realestate.visual.grant'].sudo()
        # Public pages and the public API ask for a grant for every gated URL
        # they render, and minting each time let one anonymous request write
        # hundreds of rows. A live grant for exactly the same scope, with at
        # least half its lifetime left, is handed out again instead. Revoked,
        # deactivated (not found by search) and nearly expired grants are not.
        reusable = Grant.search([
            ('project_id', '=', project.id),
            ('kinds', '=', ','.join(sorted(kinds))),
            ('source', '=', source),
            ('source_ref', '=', source_ref or False),
            ('allowed_origins', '=', origin or False),
            ('revoked', '=', False),
            ('expires_at', '>', fields.Datetime.now()
             + timedelta(hours=DEFAULT_GRANT_HOURS / 2)),
        ], order='expires_at desc', limit=1)
        if reusable:
            return reusable
        return Grant._mint(
            project,
            kinds,
            source=source,
            source_ref=source_ref,
            allowed_origins=origin,
        )
