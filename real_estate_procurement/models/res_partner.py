from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_realestate_vendor = fields.Boolean(
        compute='_compute_is_realestate_vendor', store=True,
        help='True when the partner carries any real-estate vendor tag.',
    )

    @api.depends('category_id')
    def _compute_is_realestate_vendor(self):
        tag_xmlids = (
            'real_estate_procurement.tag_vendor_contractor',
            'real_estate_procurement.tag_vendor_material_supplier',
            'real_estate_procurement.tag_vendor_service',
            'real_estate_procurement.tag_vendor_marketing',
            'real_estate_procurement.tag_vendor_consultant',
            'real_estate_procurement.tag_vendor_utility',
            'real_estate_procurement.tag_vendor_landowner',
        )
        tag_ids = set()
        for xmlid in tag_xmlids:
            tag = self.env.ref(xmlid, raise_if_not_found=False)
            if tag:
                tag_ids.add(tag.id)
        for rec in self:
            rec.is_realestate_vendor = bool(set(rec.category_id.ids) & tag_ids)
