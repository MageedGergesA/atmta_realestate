# -*- coding: utf-8 -*-
"""Wave 18 — what certification consumes from a BOQ line.

The bill of quantities moved to `atmta_construction_site` with the rest of
site execution. Payment certificates did not: they are the money, and they
stay here with retention, advances and owner billing.

So the relation from a BOQ line to the certificate lines that consume it is
declared here, and the seam the line exposes is answered here. The rule is
unchanged and still stated once: only certificates that reached certified,
invoiced or paid count against the authorised quantity. What over-certification
means is unchanged too, and it is still visible rather than floored at zero.
"""
from odoo import api, fields, models


class BoqLineCertification(models.Model):
    _inherit = 'realestate.boq.line'

    certification_line_ids = fields.One2many(
        'realestate.construction.payment.certificate.line',
        'boq_line_id',
        string='Certification Lines',
    )

    def _certified_totals(self):
        self.ensure_one()
        live = self.certification_line_ids.filtered(
            lambda cl: cl.certificate_id.state in ('certified', 'invoiced', 'paid')
        )
        return sum(live.mapped('qty')), sum(live.mapped('amount'))

    @api.depends(
        'certification_line_ids.qty',
        'certification_line_ids.amount',
        'certification_line_ids.certificate_id.state',
        'authorised_quantity',
    )
    def _compute_certified(self):
        return super()._compute_certified()
