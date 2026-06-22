"""Public image proxy.

Odoo's ``/web/image/<model>/<id>/<field>`` checks read ACL on the parent
record, which means anonymous visitors get the placeholder PNG for any
record they can't read — including the publicly-listed projects and
properties our catalog exposes.

This controller serves those images directly, with two strict guards
that keep it from becoming a generic file leak:

1.  Only a fixed whitelist of (model, field) pairs is reachable.
2.  Each whitelisted entry has a ``visibility check`` that re-applies
    the same public-state filter the catalog uses. A record that
    wouldn't appear in ``/api/v1/projects`` won't have its image
    served either — no silent bypass.

Resize parameters mirror ``/web/image``: ``?w=`` and ``?h=`` (zero or
absent means "natural size"). The ``Content-Type`` header is set by
Odoo's stream helper; we add a 5-minute browser cache and the same
``Vary: Origin`` discipline the JSON routes use.
"""

import logging

import werkzeug.exceptions

from odoo import _, http
from odoo.http import request

from ._base import _apply_cors_headers, _int_arg


_logger = logging.getLogger(__name__)


def _public_project(record):
    Project = request.env['realestate.project'].sudo()
    return record.exists() and record.state in Project._api_public_states()


def _public_property(record):
    return (record.exists()
            and record.state in ('available', 'reserved', 'sold'))


def _public_property_image(record):
    # property.image rows ride on their parent property's visibility.
    return record.exists() and _public_property(record.property_id)


def _public_developer_partner(record):
    if not record.exists():
        return False
    Project = request.env['realestate.project'].sudo()
    return Project.search_count([
        ('developer_id', '=', record.id),
        ('state', 'in', Project._api_public_states()),
    ]) > 0


# (model, field): (visibility-check callable taking the record)
WHITELIST = {
    ('realestate.project',  'master_plan_2d'): _public_project,
    ('realestate.project',  'maquette_glb'):   _public_project,
    ('realestate.project',  'maquette_env_hdr'): _public_project,
    ('realestate.property', 'plan_image'):     _public_property,
    ('realestate.property', 'floor_plan_image'): _public_property,
    ('realestate.property', 'image_1920'):     _public_property,
    ('realestate.property', 'elevation_sheet'): _public_property,
    ('realestate.property', 'interior_glb'):   _public_property,
    ('property.image',      'image_1920'):     _public_property_image,
    ('property.image',      'image_1024'):     _public_property_image,
    ('property.image',      'image_256'):      _public_property_image,
    ('res.partner',         'image_256'):      _public_developer_partner,
    ('res.partner',         'image_128'):      _public_developer_partner,
}


class ImageProxyApiV1(http.Controller):

    @http.route('/api/v1/image/<string:model>/<int:rec_id>/<string:field>',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    def image(self, model, rec_id, field, **kw):
        if request.httprequest.method == 'OPTIONS':
            from odoo.http import Response
            return _apply_cors_headers(request.env, Response('', status=204))

        env = request.env
        check = WHITELIST.get((model, field))
        if not check:
            raise werkzeug.exceptions.NotFound(_("Image not exposed."))

        record = env[model].sudo().browse(rec_id)
        if not check(record):
            # Strict: no placeholder fallback — the caller asked for a
            # specific image that doesn't exist in our public view.
            raise werkzeug.exceptions.NotFound(_("Image not found."))

        width = _int_arg(kw, 'w', 0, lo=0, hi=4096)
        height = _int_arg(kw, 'h', 0, lo=0, hi=4096)

        # ``ir.binary._get_image_stream_from`` does the heavy lifting:
        # resize, mimetype, ETag/Last-Modified header generation.
        try:
            stream = env['ir.binary']._get_image_stream_from(
                record, field, width=width, height=height,
            )
        except Exception:
            _logger.exception("Error streaming %s/%s/%s", model, rec_id, field)
            raise werkzeug.exceptions.NotFound(_("Image not available."))

        response = stream.get_response(as_attachment=False)
        response.headers['Cache-Control'] = 'public, max-age=300'
        return _apply_cors_headers(env, response)
