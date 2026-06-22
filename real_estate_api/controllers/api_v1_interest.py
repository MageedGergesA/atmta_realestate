"""``POST /api/v1/interests`` — lead capture from the 3rd-party website.

Strict input validation, no fallbacks: missing required fields or unknown
project/unit ids → 400; known-good payload → ``crm.lead`` row + 201.

Anti-abuse: tighter rate limit on top of the default (5/hour per IP) plus
``Idempotency-Key`` header support so retries don't double-create.
"""

import re

import werkzeug.exceptions

from odoo import _, http
from odoo.http import request

from ._base import (
    _config,
    _rate_limit,
    json_endpoint,
    json_response,
    not_found_if_missing,
)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9\s\-()]{6,20}$")
MAX_NAME = 120
MAX_MESSAGE = 2000


class InterestApiV1(http.Controller):

    @http.route('/api/v1/interests', type='http', auth='public',
                methods=['POST', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['POST'])
    def submit_interest(self, _body=None, _auth_uid=None, _key_fp=None, **kw):
        env = request.env
        body = _body or {}
        name = (body.get('name') or '').strip()[:MAX_NAME]
        email = (body.get('email') or '').strip()
        phone = (body.get('phone') or '').strip()
        message = (body.get('message') or '').strip()[:MAX_MESSAGE]
        project_id = body.get('project_id')
        unit_id = body.get('unit_id') or body.get('property_id')

        # ---- validation (strict, no defaults that hide bad input)
        if not name:
            raise werkzeug.exceptions.BadRequest(_("'name' is required."))
        if not email and not phone:
            raise werkzeug.exceptions.BadRequest(
                _("Provide at least one of 'email' or 'phone'.")
            )
        if email and not EMAIL_RE.match(email):
            raise werkzeug.exceptions.BadRequest(_("Invalid email format."))
        if phone and not PHONE_RE.match(phone):
            raise werkzeug.exceptions.BadRequest(_("Invalid phone format."))

        project = None
        prop = None
        if project_id is not None:
            try:
                project_id = int(project_id)
            except (TypeError, ValueError):
                raise werkzeug.exceptions.BadRequest(_("project_id must be int."))
            Project = env['realestate.project'].sudo()
            project = Project.browse(project_id)
            not_found_if_missing(project, 'project')
            if project.state not in Project._api_public_states():
                raise werkzeug.exceptions.NotFound(_("Project not found."))
        if unit_id is not None:
            try:
                unit_id = int(unit_id)
            except (TypeError, ValueError):
                raise werkzeug.exceptions.BadRequest(_("unit_id must be int."))
            prop = env['realestate.property'].sudo().browse(unit_id)
            not_found_if_missing(prop, 'unit')
            if prop.state not in ('available', 'reserved'):
                raise werkzeug.exceptions.NotFound(_("Unit not available."))
            # If a project is also given, cross-check consistency.
            if project and prop.project_id and prop.project_id.id != project.id:
                raise werkzeug.exceptions.BadRequest(
                    _("Unit does not belong to the given project.")
                )
            if not project:
                project = prop.project_id

        # ---- anti-abuse: stricter per-IP throttle on top of the default
        ip = request.httprequest.remote_addr or 'unknown'
        per_hour = int(_config(env, 'real_estate_api.rate_limit.interest.per_hour', 5))
        ok = _rate_limit(env, bucket_prefix='interest',
                         key=ip, limit=per_hour, window_seconds=3600)
        if not ok:
            return json_response(
                {'error': {'code': 'rate_limited',
                           'message': _("Too many interest submissions. Try later.")}},
                status=429,
            )

        # ---- idempotency
        idemp_key = request.httprequest.headers.get('Idempotency-Key') or ''
        idemp_key = idemp_key.strip()[:64]
        if idemp_key:
            existing = env['crm.lead'].sudo().search([
                ('realestate_api_source', '=', True),
                ('description', 'like', f"\nIdempotency-Key: {idemp_key}\n"),
            ], limit=1)
            if existing:
                return json_response(
                    {'id': existing.id, 'reference': existing.name,
                     'status': 'received', 'idempotent_replay': True},
                    status=200,
                )

        # ---- create the lead
        title_parts = [_("Website Interest")]
        if project:
            title_parts.append(project.name or '')
        if prop:
            title_parts.append(prop.name or prop.property_code or '')
        title_parts.append(name)
        lead_name = " — ".join(p for p in title_parts if p)

        description = message or ''
        if idemp_key:
            description = (description + f"\nIdempotency-Key: {idemp_key}\n").strip()

        vals = {
            'name': lead_name[:255],
            'contact_name': name,
            'email_from': email or False,
            'phone': phone or False,
            'description': description,
            'realestate_api_source': True,
            'realestate_api_project_id': project.id if project else False,
            'realestate_api_property_id': prop.id if prop else False,
        }
        lead = env['crm.lead'].sudo().create(vals)

        return json_response(
            {'id': lead.id, 'reference': lead.name, 'status': 'received'},
            status=201,
        )
