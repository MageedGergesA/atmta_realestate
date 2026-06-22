"""Developer / company exposure.

In this codebase ``realestate.project.developer_id`` points at
``res.partner`` — so the "company" API resource is just a partner that
has at least one project. We don't add a flag column; the relevant set
is the distinct ``developer_id`` values across projects.
"""

from odoo import api, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def _api_image_url(self, field='image_256'):
        self.ensure_one()
        if not self[field if field in self._fields else 'image_128']:
            return None
        unique = self.write_date.strftime('%Y%m%d%H%M%S') if self.write_date else ''
        return f"/api/v1/image/res.partner/{self.id}/{field}?unique={unique}"

    def _to_api_dict_developer_v1(self, depth='summary'):
        """Serialize a partner as a "developer" for the public API.

        ``depth='summary'`` for list endpoints, ``depth='detail'`` for the
        single-record endpoint. Sensitive fields (``vat``, banks, internal
        categories, full address) are never included.
        """
        self.ensure_one()
        project_count = self.env['realestate.project'].sudo().search_count(
            [('developer_id', '=', self.id)]
        )
        data = {
            'id': self.id,
            'name': self.name or '',
            'logo_url': self._api_image_url('image_256'),
            'city': self.city or '',
            'country': self.country_id.name if self.country_id else '',
            'project_count': project_count,
        }
        if depth == 'detail':
            data.update({
                'website': self.website or '',
                'email': self.email or '',
                'phone': self.phone or '',
                'mobile': self.mobile or '',
                'description': self.comment or '',
            })
        return data
