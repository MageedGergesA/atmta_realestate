# -*- coding: utf-8 -*-
"""The one route that serves a visual asset.

Every other route that used to stream a GLB, an HDR, a floor plan or a plan
image now delegates here, so there is exactly one place where authorisation
happens and exactly one place to get it wrong.

```
    GET /visual/asset/<kind>/<id>[?t=<grant>]
             │
             ▼
     realestate.visual.access.authorize()      ← mandatory, always
             │  success                  │ failure
             ▼                           ▼
      sudo() → stream bytes            404 (never 403)
```

`sudo()` appears exactly once in this file, on the line after authorisation
succeeded. Failure is always 404: answering 403 for a record that exists and
404 for one that does not would turn the route into an existence oracle, and
enumeration is one of the things being defended against.
"""

import base64
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import Response, request

from ..models.visual_access import VISUAL_ASSET_KINDS

_logger = logging.getLogger(__name__)

#: Content types per asset kind. A GLB served as `application/octet-stream`
#: still loads, but the correct type is what lets a proxy or a browser make
#: sensible decisions about it.
CONTENT_TYPES = {
    'maquette_glb': 'model/gltf-binary',
    'interior_glb': 'model/gltf-binary',
    'maquette_hdr': 'image/vnd.radiance',
}
DEFAULT_CONTENT_TYPE = 'application/octet-stream'

#: The origin allow-list is read straight from its configuration parameter
#: rather than by importing `real_estate_api`'s helper: the API module depends
#: on this one, so the import would only run the dependency backwards. The
#: parameter stays the single source of truth either way.
CORS_ORIGINS_PARAM = 'real_estate_api.cors.allowed_origins'


def _cors_headers(env):
    """CORS headers for the requesting origin, or none if it is not allowed.

    Every response on this route needs them, 304 and 404 included. A browser
    that cannot read the status of a cross-origin response cannot tell a
    revalidation from a failure, and the old image proxy this route replaced
    applied the same policy to everything it returned.
    """
    origin = request.httprequest.headers.get('Origin')
    if not origin:
        return []
    raw = env['ir.config_parameter'].sudo().get_param(
        CORS_ORIGINS_PARAM, '') or ''
    allowed = {o.strip().rstrip('/') for o in raw.split(',') if o.strip()}
    if '*' not in allowed and origin.strip().rstrip('/') not in allowed:
        return []
    return [
        ('Access-Control-Allow-Origin', origin),
        ('Vary', 'Origin'),
        ('Access-Control-Allow-Methods', 'GET, OPTIONS'),
    ]


#: Leads this browser session actually produced, by id.
#:
#: A lead id is an identifier, not an authorisation. `/visual/public/convert`
#: takes one from an anonymous caller and writes a shortlist onto it under
#: `sudo()`, so without this the route lets anybody rewrite the favourites of
#: any opportunity in the database by counting. The id is recorded here at the
#: moment the visitor's own form creates the lead, and the route will accept
#: no other.
PUBLIC_LEAD_SESSION_KEY = 're_public_lead_ids'

#: Enough for a visitor who enquires about several projects in one visit,
#: bounded so a scripted caller cannot grow the session indefinitely.
PUBLIC_LEAD_SESSION_MAX = 10


def remember_public_lead(lead_id):
    """Record that this session created `lead_id`. Called at creation only."""
    if not lead_id:
        return
    held = [i for i in request.session.get(PUBLIC_LEAD_SESSION_KEY) or []
            if isinstance(i, int)]
    if int(lead_id) not in held:
        held.append(int(lead_id))
    request.session[PUBLIC_LEAD_SESSION_KEY] = held[-PUBLIC_LEAD_SESSION_MAX:]


def session_holds_lead(lead_id):
    """Whether this session is the one that produced `lead_id`."""
    if not lead_id:
        return False
    try:
        wanted = int(lead_id)
    except (TypeError, ValueError):
        return False
    return wanted in (request.session.get(PUBLIC_LEAD_SESSION_KEY) or [])


class VisualAssetController(http.Controller):

    @http.route('/visual/asset/<string:kind>/<int:record_id>',
                type='http', auth='public', methods=['GET'], csrf=False,
                save_session=False)
    def visual_asset(self, kind, record_id, t=None, **kw):
        origin = request.httprequest.headers.get('Origin') \
            or request.httprequest.headers.get('Referer')
        remote_ip = request.httprequest.remote_addr

        try:
            record, field_name = request.env[
                'realestate.visual.access'].authorize(
                    kind, record_id, token=t, origin=origin,
                    remote_ip=remote_ip)
        except AccessError:
            # Deliberately indistinguishable from a genuinely missing record.
            return Response('Not Found', status=404,
                            headers=_cors_headers(request.env))

        # Authorised. Only now.
        raw = record.sudo()[field_name]
        if not raw:
            return Response('Not Found', status=404,
                            headers=_cors_headers(request.env))
        binary = base64.b64decode(raw)

        # Revalidation is keyed on the gallery version rather than the
        # project's `write_date`, so editing a project's phone number does not
        # invalidate every browser's copy of a multi-megabyte model.
        #
        # The asset's own bytes have to be in the key too. `visual_version`
        # moves when the project is published, not when somebody replaces a
        # floor plan, so on that alone a viewer who had already loaded the old
        # plan kept being told 304 and kept seeing it. The asset record's
        # `write_date` changes whenever the field is rewritten, which is
        # exactly the event a cache needs to hear about.
        project = request.env['realestate.visual.access']._project_of(
            record.sudo())
        version = project.visual_version if project else 0
        asset_stamp = record.sudo().write_date
        asset_stamp = int(asset_stamp.timestamp()) if asset_stamp else 0
        etag = '"visual-%s-%s-%s-%s"' % (kind, record.id, version, asset_stamp)
        if request.httprequest.headers.get('If-None-Match') == etag:
            return Response(status=304, headers=[
                ('ETag', etag), ('Cache-Control', 'private, max-age=0'),
            ] + _cors_headers(request.env))

        return Response(binary, headers=[
            ('Content-Type', CONTENT_TYPES.get(kind, DEFAULT_CONTENT_TYPE)),
            ('Content-Length', str(len(binary))),
            ('ETag', etag),
            # `private` matters: these are authorised responses and a shared
            # proxy must not hand one visitor's authorised asset to another.
            ('Cache-Control', 'private, max-age=0, must-revalidate'),
            ('X-Content-Type-Options', 'nosniff'),
        ] + _cors_headers(request.env))

    @http.route('/visual/kinds', type='json', auth='user', methods=['POST'])
    def visual_kinds(self, **kw):
        """What kinds exist. Internal only; useful for the authoring UI."""
        return sorted(VISUAL_ASSET_KINDS)


class VisualDeepLinkController(http.Controller):
    """M7 — deep links. An identifier is never an authorisation.

    Two routes, one resolver. The internal one resolves under Odoo's access
    rules; the public one carries the same grant that authorises assets, so
    there is one thing to expire, revoke and scope rather than two that drift.
    """

    @http.route('/visual/go/<string:target>/<int:record_id>',
                type='http', auth='user', methods=['GET'], website=False)
    def internal_deeplink(self, target, record_id, **kw):
        try:
            path = request.env['realestate.visual.deeplink'].resolve(
                target, record_id)
        except AccessError:
            return request.not_found()
        return request.redirect(
            '/odoo/action-real_estate_maquette.action_visual_gallery'
            '?project_id=%s&unit_id=%s&building_id=%s' % (
                path['project_id'], path['unit_id'] or '',
                path['building_id'] or ''))

    @http.route('/visual/p/<string:token>/<string:target>/<int:record_id>',
                type='http', auth='public', methods=['GET'], website=True,
                sitemap=False)
    def public_deeplink(self, token, target, record_id, **kw):
        origin = request.httprequest.headers.get('Origin') \
            or request.httprequest.headers.get('Referer')
        try:
            path = request.env['realestate.visual.deeplink'].resolve(
                target, record_id, grant_token=token, origin=origin)
        except AccessError:
            return request.not_found()
        # The public gallery page, told where to navigate. No price or status
        # travels in the URL — the page reads those fresh.
        return request.redirect(
            '/projects/%s?unit=%s&t=%s' % (
                path['project_id'], path['unit_id'] or '', token))

    @http.route('/visual/resolve', type='json', auth='public',
                methods=['POST'])
    def resolve_deeplink(self, target, record_id, token=None, **kw):
        """The same resolution, for a client that is already open."""
        origin = request.httprequest.headers.get('Origin')
        try:
            return request.env['realestate.visual.deeplink'].resolve(
                target, record_id, grant_token=token, origin=origin)
        except AccessError:
            return {'error': 'not_found'}


class VisualPublicController(http.Controller):
    """M6 — Public Mode.

    `auth='public'`, which in Odoo means these run as the shared Public user
    whose access is broad by design. Nothing here relies on that: every route
    presents its grant to `realestate.visual.modes`, which authorises before it
    assembles, and answers 404 on every failure so the route cannot be used to
    discover which project ids exist.
    """

    @http.route('/visual/public/context', type='json', auth='public',
                methods=['POST'])
    def public_context(self, project_id, t=None, **kw):
        origin = request.httprequest.headers.get('Origin') \
            or request.httprequest.headers.get('Referer')
        try:
            return request.env['realestate.visual.modes'].public_context(
                project_id, grant_token=t, origin=origin)
        except AccessError:
            return {'error': 'not_found'}

    @http.route('/visual/public/convert', type='json', auth='public',
                methods=['POST'])
    def convert_session(self, crm_lead_id, property_ids, t=None, **kw):
        """Fold a visitor's browser-held shortlist onto a lead they created.

        Reachable only with a lead id that an explicit enquiry just produced
        **in this session**, and it will not create one: a browsing session
        must never become a CRM record on its own. Anything else answers 404
        rather than explaining which half was wrong.

        The session check is the authorisation. The call below runs under
        `sudo()` and the model deliberately only verifies that the lead and
        the properties exist, so without it the lead id -- a small integer --
        would be the only thing standing between an anonymous caller and any
        opportunity's shortlist.
        """
        if not session_holds_lead(crm_lead_id):
            return {'error': 'not_found'}
        try:
            request.env['realestate.visual.modes'].sudo(
                ).convert_public_session(crm_lead_id, property_ids)
        except (AccessError, UserError):
            return {'error': 'not_found'}
        return {'converted': True}


class VisualGalleryController(http.Controller):
    """M6 — the endpoints Presentation Mode calls.

    All `auth='user'`: Presentation Mode is an internal showroom, and hiding
    Odoo's chrome does not make it public. Public visitors use the portal and
    the token-gated routes, which are a different surface entirely.
    """

    @http.route('/visual/gallery/projects', type='json', auth='user',
                methods=['POST'])
    def gallery_projects(self, **kw):
        """Projects this user may present, as they may read them."""
        projects = request.env['realestate.project'].search(
            [('visual_publication_state', 'in', ('ready', 'published'))],
            order='name')
        return [{
            'id': p.id,
            'name': p.display_name,
            'has_3d': bool(p.maquette_glb) and p.visual_3d_enabled,
            'has_2d': bool(p.master_plan_2d) and p.visual_2d_enabled,
            'readiness': p.visual_readiness_score,
        } for p in projects]

    @http.route('/visual/gallery/context', type='json', auth='user',
                methods=['POST'])
    def gallery_context(self, project_id, crm_lead_id=None, **kw):
        try:
            return request.env['realestate.visual.modes'].presentation_context(
                project_id, crm_lead_id)
        except AccessError as exc:
            return {'error': str(exc)}

    @http.route('/visual/gallery/payment_plans', type='json', auth='user',
                methods=['POST'])
    def gallery_payment_plans(self, property_id, **kw):
        Gallery = request.env['realestate.visual.gallery']
        return {'plans': Gallery.payment_plans_for(
            property_id, audience='internal')}

    @http.route('/visual/gallery/shortlist', type='json', auth='user',
                methods=['POST'])
    def gallery_shortlist(self, crm_lead_id, property_id, source='3d',
                          remove=False, **kw):
        Gallery = request.env['realestate.visual.gallery']
        if remove:
            Gallery.shortlist_remove(crm_lead_id, property_id)
            return {'shortlisted': False}
        Gallery.shortlist_add(crm_lead_id, property_id, source=source)
        return {'shortlisted': True}

    @http.route('/visual/gallery/compare', type='json', auth='user',
                methods=['POST'])
    def gallery_compare(self, property_ids, **kw):
        try:
            return request.env['realestate.visual.gallery'].compare(
                property_ids, audience='internal')
        except Exception as exc:
            return {'error': str(exc)}

    @http.route('/visual/gallery/search', type='json', auth='user',
                methods=['POST'])
    def gallery_search(self, project_id, filters=None, **kw):
        project = request.env['realestate.project'].browse(int(project_id))
        project.check_access_rights('read')
        project.check_access_rule('read')
        return request.env['realestate.visual.gallery'].search_units(
            project, filters=filters, audience='internal')
