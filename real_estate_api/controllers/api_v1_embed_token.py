"""``POST /api/v1/embed-tokens`` — mint a short-lived embed URL.

The 3rd-party server calls this from its own backend (with its API key)
each time it renders a page that contains an iframe. The returned URL is
single-resource, origin-pinned, and time-limited.

Anonymous callers are rejected (401). Returning the embed URL to a
browser would defeat the point — only the trusted server should know
the API key.
"""

from urllib.parse import urlencode

import werkzeug.exceptions

from odoo import _, http
from odoo.http import request

from ._base import json_endpoint, json_response


VALID_KINDS = {'plan-2d', 'maquette-3d'}
VALID_MODELS = {'realestate.project', 'realestate.property'}


class EmbedTokenApiV1(http.Controller):

    @http.route('/api/v1/embed-tokens', type='http', auth='public',
                methods=['POST', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['POST'], require_key=True)
    def mint(self, _body=None, _auth_uid=None, _key_fp=None, **kw):
        body = _body or {}
        kind = body.get('kind')
        resource_model = body.get('resource_model')
        resource_id = body.get('resource_id')
        allowed_origins = body.get('allowed_origins')
        expires_in = body.get('expires_in', 3600)
        theme = body.get('theme')

        if kind not in VALID_KINDS:
            raise werkzeug.exceptions.BadRequest(_("Unknown 'kind'."))
        if resource_model not in VALID_MODELS:
            raise werkzeug.exceptions.BadRequest(_("Unknown 'resource_model'."))
        try:
            resource_id = int(resource_id)
        except (TypeError, ValueError):
            raise werkzeug.exceptions.BadRequest(_("'resource_id' must be int."))
        if theme is not None and not isinstance(theme, dict):
            raise werkzeug.exceptions.BadRequest(_("'theme' must be an object."))

        # Confirm the resource actually exists and is publicly visible —
        # we don't mint tokens for hidden/internal records.
        env = request.env
        resource = env[resource_model].sudo().browse(resource_id).exists()
        if not resource:
            raise werkzeug.exceptions.NotFound(_("Resource not found."))
        if resource_model == 'realestate.project':
            if resource.state not in env['realestate.project']._api_public_states():
                raise werkzeug.exceptions.NotFound(_("Resource not found."))
        else:
            if resource.state not in ('available', 'reserved', 'sold'):
                raise werkzeug.exceptions.NotFound(_("Resource not found."))

        token = env['realestate.embed.token'].sudo()._mint(
            kind=kind,
            resource_model=resource_model,
            resource_id=resource_id,
            allowed_origins=allowed_origins,
            expires_in=expires_in,
            theme=theme,
            key_fingerprint=_key_fp,
        )

        # Build the public URL the 3rd-party site will iframe.
        base = env['ir.config_parameter'].sudo().get_param(
            'real_estate_api.public.base_url') or request.httprequest.host_url.rstrip('/')
        path = f"/embed/v1/{kind}/{token.token}"
        qs = {}
        # Carry the current database forward into the URL so the browser
        # (which cannot set X-Odoo-Database on iframe loads) still routes
        # to the correct db. db_router.py honours ?db= the same way it
        # honours the header.
        if request.db:
            qs['db'] = request.db
        if theme and isinstance(theme, dict):
            if theme.get('lang'):
                qs['lang'] = theme['lang']
            if theme.get('mode'):
                qs['mode'] = theme['mode']
        embed_url = f"{base.rstrip('/')}{path}"
        if qs:
            embed_url += '?' + urlencode(qs)

        return json_response({
            'token': token.token,
            'embed_url': embed_url,
            'expires_at': token.expires_at.isoformat() if token.expires_at else None,
            'kind': kind,
            'resource_model': resource_model,
            'resource_id': resource_id,
        }, status=201)
