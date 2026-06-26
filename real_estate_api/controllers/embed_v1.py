"""Chromeless HTML routes the 3rd-party website iframes directly.

Each route looks up the token, validates origin + expiry, then renders
a tiny shell page that boots the corresponding embed JS bundle. The JS
fetches everything else via the public ``/api/v1/...`` endpoints — no
session, no cookie auth, no Odoo chrome.

CSP ``frame-ancestors`` is set from the token's allowed_origins. We
ALSO strip ``X-Frame-Options`` because Odoo's default ``ir.http`` adds
``SAMEORIGIN`` which would block embedding from third-party sites.
"""

import json
import logging
import re

import werkzeug.exceptions
from markupsafe import Markup

from odoo import _, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import Response, request


# Bundle URLs emitted by ``t-call-assets`` look like
# ``/web/assets/<hash>/<bundle>.min.<ext>``. On a multi-DB host the
# browser, lacking the X-Odoo-Database header for iframe-loaded
# resources, can't route them — we suffix ``?db=<current>`` so the
# router resolves the same database that served the shell.
_ASSET_URL_RE = re.compile(
    rb'(/web/assets/[A-Za-z0-9]+/[^"\'?\s]+\.(?:min\.)?(?:js|css))'
)


def _safe_inline_json(value):
    """Serialise a server-controlled dict for inlining into a
    ``<script type="application/json">`` block.

    Two HTML quirks force us off the standard ``t-out`` HTML-escaping
    path here:

    * The HTML parser does NOT decode entities inside a ``<script>``
      element — its content is raw text. ``t-out``'s ``&#34;``
      escapes therefore land literally in ``el.textContent``, breaking
      ``JSON.parse``.
    * Without escaping, a JSON string containing ``</script>`` would
      end the block early. We pre-escape ``</`` to ``\\u003c/`` to
      make that impossible.

    The dict itself is fully server-controlled (only API-key holders
    can mint embed tokens, and the fields stored on the token are
    constrained selections / integers / a length-checked origin
    list) — no user-supplied free text ever lands here.
    """
    blob = json.dumps(value)
    blob = blob.replace('</', '<\\/')
    return Markup(blob)


_logger = logging.getLogger(__name__)


def _embed_response(template, **values):
    """Render an embed template with the right security headers.

    ``allowed_origins`` (comma-joined string) is REQUIRED in ``values`` —
    we use it to build ``Content-Security-Policy: frame-ancestors``.
    """
    allowed = values.pop('allowed_origins', '')
    response = request.render(template, values)
    if isinstance(response, Response):
        # Convert comma list to space-separated for CSP.
        origins = [o.strip() for o in (allowed or '').split(',') if o.strip()]
        if '*' in origins:
            origins = ['*']
        ancestors = ' '.join(origins) if origins else "'none'"
        response.headers['Content-Security-Policy'] = (
            f"frame-ancestors {ancestors}"
        )
        # Strip the legacy header — `frame-ancestors` supersedes it but
        # browsers honour the most restrictive of the two.
        response.headers.pop('X-Frame-Options', None)
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = (
            "accelerometer=(), camera=(), microphone=(), geolocation=(), "
            "fullscreen=(self)"
        )
        # Embed routes are stateless — never cache the HTML shell longer
        # than the token expiry, browsers can re-fetch.
        response.headers['Cache-Control'] = 'private, max-age=60'

        # Carry the current database forward into every t-call-assets
        # bundle URL. Browsers can't set X-Odoo-Database on iframe-loaded
        # subresources, and dbfilter alone is not enough when the host
        # serves multiple databases — the only place we can inject the
        # routing hint is the URL itself. ``get_data()`` materialises the
        # body (Odoo's render can leave it as a lazy iterator) and
        # ``set_data()`` recomputes Content-Length.
        if request.db:
            body = response.get_data()
            if body:
                db_qs = f"?db={request.db}".encode('ascii')
                response.set_data(_ASSET_URL_RE.sub(rb'\1' + db_qs, body))
    return response


def _consume_or_error(kind, token):
    """Look up token; map MissingError → 404, AccessError → 403."""
    try:
        return request.env['realestate.embed.token'].sudo()._consume(
            token=token,
            kind=kind,
            origin=request.httprequest.headers.get('Origin')
                   or request.httprequest.headers.get('Referer', '').rstrip('/')
                   or '',
            remote_ip=request.httprequest.remote_addr,
        )
    except MissingError as exc:
        raise werkzeug.exceptions.NotFound(str(exc))
    except AccessError as exc:
        raise werkzeug.exceptions.Forbidden(str(exc))


def _bridge_config(token_record):
    """JSON-serializable config the embed JS needs to bootstrap."""
    return {
        'version': 'v1',
        'kind': token_record.kind,
        'resource_model': token_record.resource_model,
        'resource_id': token_record.resource_id,
        'api_base': '/api/v1',
        # Iframe JS sends no cookies (credentials: 'omit'), so each
        # /api/v1/* call must carry the db forward as ?db=. The mint
        # endpoint already stamps ?db= on the embed_url; we surface the
        # same value here so apiGet can append it to every fetch.
        'db': request.db or '',
        'allowed_origins': [
            o.strip() for o in (token_record.allowed_origins or '').split(',')
            if o.strip()
        ],
    }


class EmbedV1(http.Controller):

    @http.route('/embed/v1/plan-2d/<string:token>', type='http', auth='public',
                website=False, csrf=False, save_session=False, sitemap=False,
                methods=['GET'])
    def plan_2d(self, token, **kw):
        record = _consume_or_error('plan-2d', token)
        theme = record._theme(kw)
        return _embed_response(
            'real_estate_api.embed_plan_2d',
            allowed_origins=record.allowed_origins,
            bridge_config_json=_safe_inline_json(_bridge_config(record)),
            theme_json=_safe_inline_json(theme),
            html_dir='rtl' if (theme.get('lang') == 'ar') else 'ltr',
        )

    @http.route('/embed/v1/maquette-3d/<string:token>', type='http',
                auth='public', website=False, csrf=False, save_session=False,
                sitemap=False, methods=['GET'])
    def maquette_3d(self, token, **kw):
        record = _consume_or_error('maquette-3d', token)
        theme = record._theme(kw)
        return _embed_response(
            'real_estate_api.embed_maquette_3d',
            allowed_origins=record.allowed_origins,
            bridge_config_json=_safe_inline_json(_bridge_config(record)),
            theme_json=_safe_inline_json(theme),
            html_dir='rtl' if (theme.get('lang') == 'ar') else 'ltr',
        )
