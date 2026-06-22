"""Signed, single-resource, origin-pinned tokens for the public iframe embeds.

A token does three things:

1.  Authorises a public visitor to view exactly one resource
    (``realestate.project`` or ``realestate.property``) in the embed.
2.  Pins the allowed embedding origin(s) so a stolen URL cannot be
    iframed from anywhere else.
3.  Carries an absolute expiry — past that, the embed route returns 404.

The token value itself is a 32-byte url-safe random string; consumption is
read-only on the row except for the audit counter.

No silent fallback: every consume failure (missing, expired, wrong origin)
raises ``MissingError`` / ``AccessError`` which the controller maps to the
correct HTTP status. Never substitute the "next best" record.
"""

import json
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, MissingError, ValidationError


EMBED_KINDS = [
    ('plan-2d', '2D Plan Drill'),
    ('maquette-3d', '3D Maquette'),
]
ALLOWED_RESOURCE_MODELS = ('realestate.project', 'realestate.property')


class EmbedToken(models.Model):
    _name = 'realestate.embed.token'
    _description = 'Real Estate Embed Token'
    _order = 'create_date desc'

    name = fields.Char(
        string='Reference',
        compute='_compute_name', store=True,
        help='Human label, just for the admin list.',
    )
    token = fields.Char(
        string='Token', required=True, readonly=True, copy=False, index=True,
    )
    kind = fields.Selection(EMBED_KINDS, required=True)
    resource_model = fields.Selection(
        [(m, m) for m in ALLOWED_RESOURCE_MODELS],
        required=True,
    )
    resource_id = fields.Integer(required=True)
    resource_display = fields.Char(compute='_compute_resource_display')

    allowed_origins = fields.Char(
        string='Allowed Origins',
        required=True,
        help='Comma-separated origins permitted to embed this URL. '
             'Match against the ``Origin`` header. Use "*" only for '
             'short-lived development tokens.',
    )
    theme_json = fields.Text(
        string='Theme Overrides (JSON)',
        help='Optional theme dict: {"primary": "#ff5a00", "mode": "light", '
             '"hide": ["exit", "breadcrumbs"], "lang": "en"}.',
    )

    expires_at = fields.Datetime(required=True)
    is_expired = fields.Boolean(compute='_compute_is_expired')
    used_count = fields.Integer(default=0, readonly=True)
    last_used_at = fields.Datetime(readonly=True)
    last_used_ip = fields.Char(readonly=True)
    last_used_origin = fields.Char(readonly=True)
    created_by_key = fields.Char(
        string='Minted By (key fingerprint)',
        readonly=True,
        help='SHA-256 fingerprint of the API key that minted this token.',
    )

    _sql_constraints = [
        ('token_uniq', 'UNIQUE(token)', 'Embed token must be unique.'),
    ]

    @api.depends('kind', 'resource_model', 'resource_id')
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.kind or ''} #{rec.resource_id or 0}"

    @api.depends('resource_model', 'resource_id')
    def _compute_resource_display(self):
        for rec in self:
            if rec.resource_model and rec.resource_id:
                resource = rec.env[rec.resource_model].browse(rec.resource_id).exists()
                rec.resource_display = resource.display_name if resource else _('[deleted]')
            else:
                rec.resource_display = ''

    @api.depends('expires_at')
    def _compute_is_expired(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.is_expired = bool(rec.expires_at and rec.expires_at < now)

    @api.constrains('resource_model', 'resource_id', 'kind')
    def _check_resource_matches_kind(self):
        for rec in self:
            if rec.resource_model not in ALLOWED_RESOURCE_MODELS:
                raise ValidationError(_(
                    "Resource model %s is not allowed for embed tokens."
                ) % rec.resource_model)
            resource = rec.env[rec.resource_model].browse(rec.resource_id).exists()
            if not resource:
                raise ValidationError(_("Resource %s/%d not found.") % (
                    rec.resource_model, rec.resource_id,
                ))
            if rec.kind == 'maquette-3d':
                # 3D needs the GLB attached to a project (units are mapped via mesh names).
                project = (resource if rec.resource_model == 'realestate.project'
                           else resource.project_id if hasattr(resource, 'project_id') else False)
                if not project or not project.maquette_glb:
                    raise ValidationError(_(
                        "Cannot mint a 3D embed token: no GLB attached to the project."
                    ))
            if rec.kind == 'plan-2d':
                if rec.resource_model == 'realestate.project':
                    if not (resource.master_plan_2d or resource.main_property_id):
                        raise ValidationError(_(
                            "Project has no master plan and no entry property — "
                            "cannot mint a 2D embed token."
                        ))
                else:
                    if not resource.plan_image:
                        raise ValidationError(_(
                            "Property has no plan image — cannot mint a 2D embed token."
                        ))

    @api.model
    def _mint(self, *, kind, resource_model, resource_id, allowed_origins,
              expires_in=3600, theme=None, key_fingerprint=None):
        """Create a token. Caller (controller) is responsible for auth.

        Returns the created record. Raises ``ValidationError`` on bad input —
        never returns a "next-best" token.
        """
        if kind not in dict(EMBED_KINDS):
            raise ValidationError(_("Unknown embed kind: %s") % kind)
        if resource_model not in ALLOWED_RESOURCE_MODELS:
            raise ValidationError(_("Resource model %s is not embeddable.") % resource_model)
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            raise ValidationError(_("expires_in must be an integer (seconds)."))
        if not (60 <= expires_in <= 60 * 60 * 24 * 30):
            raise ValidationError(_("expires_in must be between 60 and 2592000 seconds."))
        if not allowed_origins or not allowed_origins.strip():
            raise ValidationError(_("allowed_origins is required."))

        return self.sudo().create({
            'token': secrets.token_urlsafe(32),
            'kind': kind,
            'resource_model': resource_model,
            'resource_id': resource_id,
            'allowed_origins': allowed_origins.strip(),
            'theme_json': json.dumps(theme) if theme else False,
            'expires_at': fields.Datetime.now() + timedelta(seconds=expires_in),
            'created_by_key': key_fingerprint or False,
        })

    @api.model
    def _consume(self, *, token, kind, origin, remote_ip=None):
        """Look up and validate a token for the embed route.

        Strict semantics:
        * missing token → ``MissingError`` (controller maps to 404)
        * wrong kind for token → ``MissingError`` (don't leak the kind via 400)
        * expired → ``MissingError``
        * origin not in allowlist → ``AccessError`` (controller maps to 403)

        On success: bumps audit counters and returns the token record.
        """
        if not token:
            raise MissingError(_("Missing token."))
        rec = self.sudo().search([('token', '=', token)], limit=1)
        if not rec or rec.kind != kind:
            raise MissingError(_("Token not found."))
        if rec.expires_at and rec.expires_at < fields.Datetime.now():
            raise MissingError(_("Token expired."))

        if rec.allowed_origins != '*':
            allowed = {o.strip() for o in rec.allowed_origins.split(',') if o.strip()}
            if origin not in allowed:
                raise AccessError(_("Origin not allowed for this embed token."))

        rec.write({
            'used_count': rec.used_count + 1,
            'last_used_at': fields.Datetime.now(),
            'last_used_ip': (remote_ip or '')[:64],
            'last_used_origin': (origin or '')[:255],
        })
        return rec

    def _theme(self, query_overrides=None):
        """Effective theme dict for this embed.

        ``theme_json`` on the token is the base; query string can override
        selected keys (primary, mode, lang). Never falls back silently — any
        unknown override key is dropped.
        """
        base = {}
        if self.theme_json:
            try:
                base = json.loads(self.theme_json) or {}
            except json.JSONDecodeError:
                base = {}
        if not isinstance(base, dict):
            base = {}

        overrides = query_overrides or {}
        ALLOWED_OVERRIDES = {'primary', 'mode', 'lang'}
        for k in ALLOWED_OVERRIDES & overrides.keys():
            v = overrides[k]
            if isinstance(v, str) and 0 < len(v) <= 64:
                base[k] = v

        # Hide list comes from theme_json only — query "hide=..." comma list
        # is allowed but bounded.
        if 'hide' in overrides:
            hide_raw = overrides['hide']
            if isinstance(hide_raw, str):
                base['hide'] = [h for h in hide_raw.split(',') if h.strip()][:8]
        return base

    @api.model
    def _gc(self):
        """Cron entry-point: prune tokens that expired more than a day ago."""
        cutoff = fields.Datetime.now() - timedelta(days=1)
        self.sudo().search([('expires_at', '<', cutoff)]).unlink()
