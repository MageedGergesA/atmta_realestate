from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_realestate_vendor = fields.Boolean(
        compute='_compute_is_realestate_vendor', store=True,
        help='True when the partner carries any real-estate vendor tag.',
    )

    _RE_VENDOR_TAG_XMLIDS = (
        'real_estate_procurement.tag_vendor_contractor',
        'real_estate_procurement.tag_vendor_material_supplier',
        'real_estate_procurement.tag_vendor_service',
        'real_estate_procurement.tag_vendor_marketing',
        'real_estate_procurement.tag_vendor_consultant',
        'real_estate_procurement.tag_vendor_utility',
        'real_estate_procurement.tag_vendor_landowner',
    )

    @api.depends('category_id')
    def _compute_is_realestate_vendor(self):
        tag_ids = {
            tag.id for tag in (
                self.env.ref(xmlid, raise_if_not_found=False)
                for xmlid in self._RE_VENDOR_TAG_XMLIDS
            ) if tag
        }
        for rec in self:
            rec.is_realestate_vendor = bool(tag_ids & set(rec.category_id.ids))
