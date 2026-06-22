"""Shared scaffolding for the public REST controllers.

All public endpoints go through ``json_endpoint`` (decorator) which:

* enforces method allow-list
* parses + validates JSON bodies
* sets CORS headers (origin allowlist from ``ir.config_parameter``)
* applies a per-IP / per-key rate limit
* converts any exception into a strict ``{"error": {...}}`` JSON body
  with the correct HTTP status — **never** a half-baked 200

Strict-empty-results rule (see ``no_silent_fallbacks`` feedback):
controllers raise ``werkzeug.exceptions.NotFound`` on missing records
rather than returning ``[]`` or "the next-best match". Calling code in
the catalog must call ``not_found_if_missing`` after every ``.browse()``.
"""

import functools
import hashlib
import json
import logging

import werkzeug.exceptions

from odoo import _, http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import Response, request


_logger = logging.getLogger(__name__)

API_KEY_HEADER = 'X-API-Key'
AUTHORIZATION_HEADER = 'Authorization'
BEARER_PREFIX = 'Bearer '
API_KEY_SCOPE = 'real_estate_api'

# Status codes the JSON encoder maps Odoo exceptions to.
EXC_STATUS = {
    MissingError: 404,
    AccessError: 403,
    UserError: 400,
    ValidationError: 400,
}


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _config(env, key, default=None):
    return env['ir.config_parameter'].sudo().get_param(key, default)


def _allowed_origins(env):
    raw = _config(env, 'real_estate_api.cors.allowed_origins', '') or ''
    return [o.strip() for o in raw.split(',') if o.strip()]


def _origin_allowed(env, origin):
    if not origin:
        return False
    allowed = _allowed_origins(env)
    if '*' in allowed:
        return True
    return origin in allowed


def _apply_cors_headers(env, response):
    origin = request.httprequest.headers.get('Origin')
    if origin and _origin_allowed(env, origin):
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Methods'] = (
            'GET, POST, OPTIONS'
        )
        response.headers['Access-Control-Allow-Headers'] = (
            f'Content-Type, {API_KEY_HEADER}, {AUTHORIZATION_HEADER}, '
            f'X-Odoo-Database, Idempotency-Key'
        )
        response.headers['Access-Control-Max-Age'] = '600'
    return response


def json_response(payload, status=200, headers=None):
    body = json.dumps(payload, default=str)
    resp = Response(body, status=status, content_type='application/json; charset=utf-8')
    if headers:
        for k, v in headers.items():
            resp.headers[k] = v
    return _apply_cors_headers(request.env, resp)


def json_error(code, message, status=400, details=None):
    payload = {'error': {'code': code, 'message': message}}
    if details:
        payload['error']['details'] = details
    return json_response(payload, status=status)


def not_found_if_missing(records, label='resource'):
    """Raise 404 if ``records`` is empty. Use after every ``.browse()``."""
    if not records.exists():
        raise werkzeug.exceptions.NotFound(_("%s not found.") % label.capitalize())
    return records


# ---------------------------------------------------------------------------
# Auth — API key
# ---------------------------------------------------------------------------

def _key_fingerprint(key):
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:16] if key else ''


def _read_api_key():
    """Extract the API key from the request — accepts either header form.

    Postman and most generic HTTP clients default to ``Authorization:
    Bearer <key>``; our own integrations historically used the
    ``X-API-Key`` header. Both are accepted; ``Authorization`` wins if
    both are present so the caller can't accidentally smuggle two keys.
    """
    auth = request.httprequest.headers.get(AUTHORIZATION_HEADER) or ''
    if auth.startswith(BEARER_PREFIX):
        return auth[len(BEARER_PREFIX):].strip()
    return (request.httprequest.headers.get(API_KEY_HEADER) or '').strip()


def _authenticate_api_key():
    """Validate the API key against ``res.users.apikey``.

    Returns a (user_id, fingerprint) tuple on success. Returns
    ``(None, '')`` if no key is present. Raises ``AccessError`` on a
    present-but-invalid key. Strict: no silent fallback if the scope
    doesn't match.
    """
    key = _read_api_key()
    if not key:
        return None, ''
    # Odoo's API: env['res.users.apikeys']._check_credentials(scope, key)
    # returns the user id or None. We sudo so the lookup itself isn't
    # gated by record rules on the keys model.
    uid = request.env['res.users.apikeys'].sudo()._check_credentials(
        scope=API_KEY_SCOPE, key=key,
    )
    if not uid:
        raise AccessError(_("Invalid API key."))
    return uid, _key_fingerprint(key)


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

def _rate_limit(env, *, bucket_prefix, key, limit, window_seconds):
    bucket_key = f"{bucket_prefix}:{key}"[:255]
    return env['realestate.api.ratelimit'].sudo()._check_and_increment(
        bucket_key=bucket_key,
        limit=int(limit),
        window_seconds=int(window_seconds),
    )


def _apply_default_rate_limit(env, *, authed_uid, fingerprint):
    """Apply the default per-minute throttle. Authenticated calls get a
    higher cap than anonymous ones."""
    ip = request.httprequest.remote_addr or 'unknown'
    if authed_uid:
        limit = int(_config(env, 'real_estate_api.rate_limit.authed.per_minute', 300))
        bucket = f"authed:{fingerprint or authed_uid}"
    else:
        limit = int(_config(env, 'real_estate_api.rate_limit.public.per_minute', 60))
        bucket = f"public:{ip}"
    return _rate_limit(env, bucket_prefix='req', key=bucket,
                       limit=limit, window_seconds=60)


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def json_endpoint(*, methods, auth='public', require_key=False):
    """Decorate a controller method to enforce the conventions above.

    Use ``require_key=True`` for endpoints that mutate state (POST /interests,
    POST /embed-tokens). Read endpoints stay anonymous by default.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            env = request.env
            try:
                # Preflight
                if request.httprequest.method == 'OPTIONS':
                    return _apply_cors_headers(env, Response('', status=204))

                if request.httprequest.method not in methods:
                    return json_error('method_not_allowed',
                                      _("Method not allowed."), status=405)

                # Auth
                try:
                    uid, fingerprint = _authenticate_api_key()
                except AccessError as e:
                    return json_error('unauthorized', str(e), status=401)
                if require_key and not uid:
                    return json_error('unauthorized',
                                      _("API key required."), status=401)
                if uid:
                    # Re-enter the request as the api user — sudo() still works
                    # for catalog lookups, but writes go through the real uid.
                    request.update_env(user=uid)
                    env = request.env

                # Rate limit
                ok = _apply_default_rate_limit(env,
                                               authed_uid=uid,
                                               fingerprint=fingerprint)
                if not ok:
                    return json_error('rate_limited',
                                      _("Too many requests."), status=429)

                # Parse JSON body (POST/PUT only)
                body = None
                if request.httprequest.method in ('POST', 'PUT'):
                    raw = request.httprequest.get_data(as_text=True) or ''
                    if raw:
                        try:
                            body = json.loads(raw)
                        except json.JSONDecodeError as exc:
                            return json_error('bad_request',
                                              _("Invalid JSON: %s") % exc,
                                              status=400)
                    if not isinstance(body, dict):
                        body = body if body is not None else {}

                kwargs['_body'] = body
                kwargs['_auth_uid'] = uid
                kwargs['_key_fp'] = fingerprint
                return func(self, *args, **kwargs)

            except werkzeug.exceptions.HTTPException as exc:
                return json_error(
                    code=exc.name.lower().replace(' ', '_'),
                    message=exc.description,
                    status=exc.code,
                )
            except tuple(EXC_STATUS.keys()) as exc:
                status = next(s for t, s in EXC_STATUS.items() if isinstance(exc, t))
                return json_error(type(exc).__name__.lower(), str(exc),
                                  status=status)
            except Exception as exc:  # noqa: BLE001
                _logger.exception("Unhandled error in %s", func.__name__)
                return json_error('internal_error',
                                  _("Internal server error."), status=500)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Common query-param parsing
# ---------------------------------------------------------------------------

def _int_arg(kwargs, name, default, *, lo=None, hi=None):
    raw = kwargs.get(name)
    if raw is None:
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise werkzeug.exceptions.BadRequest(_("'%s' must be an integer.") % name)
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


def parse_pagination(kwargs, default_limit=20, max_limit=100):
    return (
        _int_arg(kwargs, 'limit', default_limit, lo=1, hi=max_limit),
        _int_arg(kwargs, 'offset', 0, lo=0),
    )


class CorsPreflight(http.Controller):
    """Catch-all OPTIONS handler for the API + embed URL prefixes.

    Browsers send a preflight before any cross-origin POST or any request
    with a non-simple header (we use ``X-API-Key`` everywhere). Without
    this we'd 404 every preflight.
    """

    @http.route(['/api/v1/<path:_subpath>', '/embed/v1/<path:_subpath>'],
                type='http', auth='public', methods=['OPTIONS'],
                csrf=False, save_session=False)
    def preflight(self, _subpath=None, **kw):
        resp = Response('', status=204)
        return _apply_cors_headers(request.env, resp)
