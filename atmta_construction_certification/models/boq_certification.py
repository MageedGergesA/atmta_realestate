# -*- coding: utf-8 -*-
"""What certification consumes from a BOQ line.

Wave 18 moved the bill of quantities to `atmta_construction_site` and left
this relation in the monolith, because payment certificates were still there.
Wave 19 moved the certificates here, so the relation comes with them: the
module that owns the certificate line is the one that should say which BOQ
line it consumes.

The rule is unchanged and still stated once: only certificates that reached
certified, invoiced or paid count against the authorised quantity, and
over-certification stays visible rather than floored at zero.
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
