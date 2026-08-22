# -*- coding: utf-8 -*-
"""The one Vendor Governance wizard that did NOT move in Wave 1.

`realestate.procurement.restriction.lift` is assigned to
`atmta_procurement_control` by `05_MODEL_TO_MODULE_MAP.csv`, not to
`atmta_procurement_vendor`, because lifting a restriction is an act of
procurement control rather than a description of vendor state. It therefore
stays here until the control capability is extracted.

(`16_FIRST_EXTRACTION_RECOMMENDATION.md` lists it among the vendor models while
its own heading says fifteen and it names sixteen; the CSV is the machine-
generated per-model assignment and was taken as authoritative. Recorded in the
Wave 1 evidence as an architecture-package inconsistency, not resolved silently.)

It reaches `realestate.procurement.vendor.restriction` through the ORM registry,
which now lives in `atmta_procurement_vendor` — a normal downward reference from
control to vendor.
"""

from odoo import fields, models


class RestrictionLift(models.TransientModel):
    _name = 'realestate.procurement.restriction.lift'
    _description = 'Lift Vendor Restriction'

    restriction_id = fields.Many2one(
        'realestate.procurement.vendor.restriction', required=True,
        default=lambda self: self.env.context.get('active_id'))
    reason = fields.Text(required=True, string='Why')

    def action_lift(self):
        self.ensure_one()
        self.restriction_id.action_lift(reason=self.reason)
        return {'type': 'ir.actions.act_window_close'}
