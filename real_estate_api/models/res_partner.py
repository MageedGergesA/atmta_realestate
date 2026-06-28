"""Developer / company exposure + 3rd-party contact sync.

Two roles for ``res.partner`` in this API:

1. READ-ONLY ``developer`` records exposed through the catalog. These
   are partners that already exist as ``realestate.project.developer_id``
   targets — we don't flag them, the relevant set is the distinct
   ``developer_id`` values across projects.

2. WRITABLE ``customer`` records created by the 3rd-party website via
   ``POST /api/v1/partners`` whenever a visitor signs up. The 3rd party
   sends its own user identifier in ``external_ref``; we use
   ``(realestate_api_source=True, realestate_api_external_ref=ref)`` as
   the upsert key. Strict: a ref already used by a non-API partner is
   refused — we never silently take over an existing contact.
"""

from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    realestate_api_source = fields.Boolean(
        string='Created via Public API',
        readonly=True, copy=False, index=True,
    )
    realestate_api_external_ref = fields.Char(
        string='3rd-Party External Reference',
        readonly=True, copy=False, index=True,
        help="Identifier supplied by the 3rd-party website when it "
             "registered this contact via POST /api/v1/partners. Used "
             "as the upsert key on subsequent calls.",
    )

    _sql_constraints = [
        # Unique only among API-created rows. We can't add a global
        # UNIQUE because the column is nullable and most existing
        # partners have no ref. Partial unique index on (ref) WHERE
        # realestate_api_source is the right shape; Odoo's _sql_constraints
        # don't support WHERE clauses, so we enforce in Python (see
        # api_v1_partners.py) and add the partial index here for speed.
    ]

    def init(self):
        super().init()
        # Partial index speeds up the upsert lookup AND enforces unicity
        # at the DB level for API-sourced rows (defence in depth — the
        # controller already serialises).
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                res_partner_realestate_api_external_ref_uniq
                ON res_partner (realestate_api_external_ref)
                WHERE realestate_api_source = TRUE
                  AND realestate_api_external_ref IS NOT NULL;
        """)

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
