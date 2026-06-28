"""``POST /api/v1/partners`` — upsert a customer contact from the 3rd-party site.

The 3rd-party website calls this whenever a visitor signs up or edits
their profile. The integration's API key authenticates the call; the
``external_ref`` field (a stable identifier from the 3rd-party user
table) is the upsert key.

Behaviour:

* No partner with ``(realestate_api_source=True, external_ref=ref)``
  exists yet → CREATE one, return 201 + ``{"id", "external_ref",
  "created": true}``.
* One exists → UPDATE the writable fields (name, email, phone, address)
  from the request, return 200 + ``{"id", "external_ref",
  "created": false}``.
* The ref is already held by a partner NOT created via the API → 409
  ``conflict``. We never silently take over a backend-created contact.

Strict input validation, no fallbacks: missing required field, bad
email/phone format, or unknown country code → 400.

Concurrency: two parallel POSTs with the same ``external_ref`` may
both pass the in-Python "does it exist?" check and race into
``create()``. The partial unique index on
``(realestate_api_external_ref) WHERE realestate_api_source`` then
rejects the loser with ``psycopg2.errors.UniqueViolation``. We wrap
the create in a savepoint so the failure doesn't poison the request
transaction, and translate it into a clean ``409 conflict`` instead
of a 500.
"""

import logging
import re

import psycopg2
import werkzeug.exceptions

from odoo import _, http
from odoo.http import request

from ._base import (
    json_endpoint,
    json_response,
)


_logger = logging.getLogger(__name__)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9\s\-()]{6,20}$")
EXTERNAL_REF_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")

MAX_NAME = 120
MAX_STREET = 255
MAX_CITY = 96


class PartnersApiV1(http.Controller):

    @http.route('/api/v1/partners', type='http', auth='public',
                methods=['POST', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['POST'], require_key=True)
    def upsert(self, _body=None, _auth_uid=None, _key_fp=None, **kw):
        env = request.env
        body = _body or {}

        # --- required: external_ref + name --------------------------------
        external_ref = (body.get('external_ref') or '').strip()
        if not external_ref:
            raise werkzeug.exceptions.BadRequest(_("'external_ref' is required."))
        if not EXTERNAL_REF_RE.match(external_ref):
            raise werkzeug.exceptions.BadRequest(
                _("'external_ref' must be 1-128 chars of [A-Za-z0-9._:-].")
            )

        name = (body.get('name') or '').strip()[:MAX_NAME]
        if not name:
            raise werkzeug.exceptions.BadRequest(_("'name' is required."))

        # --- optional: email, phone (validated if present) ----------------
        email = (body.get('email') or '').strip() or False
        phone = (body.get('phone') or '').strip() or False
        if email and not EMAIL_RE.match(email):
            raise werkzeug.exceptions.BadRequest(_("Invalid email format."))
        if phone and not PHONE_RE.match(phone):
            raise werkzeug.exceptions.BadRequest(_("Invalid phone format."))

        # --- optional: address -------------------------------------------
        street = (body.get('street') or '').strip()[:MAX_STREET] or False
        city = (body.get('city') or '').strip()[:MAX_CITY] or False
        country_id = False
        country_code = (body.get('country') or '').strip().upper()
        if country_code:
            if not re.match(r'^[A-Z]{2}$', country_code):
                raise werkzeug.exceptions.BadRequest(
                    _("'country' must be a 2-letter ISO code (e.g. 'EG').")
                )
            country = env['res.country'].sudo().search(
                [('code', '=', country_code)], limit=1)
            if not country:
                raise werkzeug.exceptions.BadRequest(
                    _("Unknown country code '%s'.") % country_code
                )
            country_id = country.id

        # --- upsert --------------------------------------------------------
        Partner = env['res.partner'].sudo()

        # Case A: existing API-sourced partner with this ref → update.
        existing = Partner.search([
            ('realestate_api_source', '=', True),
            ('realestate_api_external_ref', '=', external_ref),
        ], limit=1)

        # Case B: ref is held by a backend-created partner → refuse.
        # Strict: never silently take over a contact we didn't mint.
        clash = Partner.search([
            ('realestate_api_source', '=', False),
            ('realestate_api_external_ref', '=', external_ref),
        ], limit=1)
        if clash:
            return json_response(
                {'error': {
                    'code': 'conflict',
                    'message': _("external_ref %s is already held by a "
                                 "non-API contact (id=%d). Pick a "
                                 "different ref or have an admin clear it.")
                               % (external_ref, clash.id),
                }},
                status=409,
            )

        vals = {
            'name': name,
            'email': email,
            'phone': phone,
            'street': street,
            'city': city,
            'country_id': country_id,
        }
        # Drop keys whose value is False so we don't overwrite a populated
        # address with empty on a partial update.
        vals = {k: v for k, v in vals.items() if v is not False or k == 'name'}

        if existing:
            existing.write(vals)
            return json_response({
                'id': existing.id,
                'external_ref': external_ref,
                'created': False,
            }, status=200)

        vals.update({
            'realestate_api_source': True,
            'realestate_api_external_ref': external_ref,
            # Customer-type partners; not a company.
            'company_type': 'person',
        })
        # Savepoint so a UniqueViolation from a concurrent insert doesn't
        # break the outer transaction (we still want to return a clean
        # JSON 409 to the caller).
        try:
            with env.cr.savepoint():
                partner = Partner.create(vals)
        except psycopg2.errors.UniqueViolation:
            # Another request beat us to it. The winning row IS the
            # partner the caller wanted, so retry the lookup and return
            # it as an idempotent 200 (NOT 201 — we didn't create it).
            _logger.info(
                "real_estate_api partners: lost create race for "
                "external_ref=%r, returning the winner.", external_ref,
            )
            winner = Partner.search([
                ('realestate_api_source', '=', True),
                ('realestate_api_external_ref', '=', external_ref),
            ], limit=1)
            if not winner:
                # The unique index fired but no row visible — DB state
                # is genuinely surprising. Refuse strictly.
                return json_response(
                    {'error': {
                        'code': 'conflict',
                        'message': _("Concurrent write conflict on "
                                     "external_ref %s, please retry.") % external_ref,
                    }},
                    status=409,
                )
            return json_response({
                'id': winner.id,
                'external_ref': external_ref,
                'created': False,
            }, status=200)

        return json_response({
            'id': partner.id,
            'external_ref': external_ref,
            'created': True,
        }, status=201)

    @http.route('/api/v1/partners/by-ref/<string:external_ref>',
                type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'], require_key=True)
    def by_ref(self, external_ref, _body=None, _auth_uid=None, _key_fp=None, **kw):
        """Look up an API-created partner by its 3rd-party reference.

        Used by the 3rd-party site when it has the visitor's external_ref
        (always known: it's their own user id) but has lost the Odoo
        partner_id (cache wipe, fresh deploy, …). Strict 404 on miss —
        callers MUST handle missing as "not synced yet" and call POST
        /api/v1/partners. Never returns a non-API partner — refs held by
        backend-created contacts read as not-found.
        """
        external_ref = (external_ref or '').strip()
        if not external_ref or not EXTERNAL_REF_RE.match(external_ref):
            raise werkzeug.exceptions.BadRequest(
                _("'external_ref' must be 1-128 chars of [A-Za-z0-9._:-].")
            )

        partner = request.env['res.partner'].sudo().search([
            ('realestate_api_source', '=', True),
            ('realestate_api_external_ref', '=', external_ref),
        ], limit=1)
        if not partner:
            raise werkzeug.exceptions.NotFound(
                _("No API-created partner with external_ref=%s.") % external_ref
            )

        return json_response({
            'id': partner.id,
            'external_ref': external_ref,
            'name': partner.name or '',
            'email': partner.email or '',
            'phone': partner.phone or '',
            'street': partner.street or '',
            'city': partner.city or '',
            'country': partner.country_id.code or '',
        }, status=200)
