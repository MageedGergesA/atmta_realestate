# -*- coding: utf-8 -*-
"""What a requisition looked like when somebody approved it.

The smallest history that survives a materially changed request: one immutable
row per revision, holding the basis as it stood. Not a second requisition, not
a copy of the record — those would need their own lifecycle, their own links
and their own way of going wrong. A snapshot only has to be readable later.
"""

import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MaterialRequestRevision(models.Model):
    _name = 'realestate.material.request.revision'
    _description = 'Procurement Requisition Revision'
    _order = 'request_id, revision desc'

    request_id = fields.Many2one(
        'realestate.material.request', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='request_id.company_id', store=True, readonly=True, index=True)
    revision = fields.Integer(required=True, readonly=True)
    reason = fields.Text(required=True, readonly=True)
    revised_by_id = fields.Many2one('res.users', readonly=True)
    revised_on = fields.Datetime(readonly=True)
    state_at_revision = fields.Char(readonly=True)
    estimated_total = fields.Monetary(readonly=True)
    currency_id = fields.Many2one(
        related='request_id.currency_id', readonly=True)
    basis = fields.Text(
        readonly=True,
        help="The lines as they stood, in JSON: quantity, unit of measure, "
             "estimate, coding and required date. Stored as text because it "
             "must stay readable after the products, cost codes or WBS nodes "
             "it names have been renamed or archived.")

    def write(self, vals):
        """A snapshot that can be edited is not a snapshot."""
        if not self.env.context.get('re_revision_snapshot'):
            raise UserError(_(
                "A revision record is history. Revise the requisition again "
                "instead of editing what it used to say."))
        return super().write(vals)

    @api.model
    def _snapshot(self, request, reason):
        lines = []
        for line in request.line_ids:
            entry = {
                'product': line.product_id.display_name or '',
                'description': line.description or '',
                'qty': line.qty,
                'uom': line.uom_id.display_name or '',
                'estimated_unit_cost': line.estimated_unit_cost,
                'estimated_cost': line.estimated_cost,
                'required_on_site_date': str(
                    line.required_on_site_date or ''),
            }
            # Coding exists only where Construction is installed; the
            # snapshot records what there was, not what there should be.
            for field_name, key in (('wbs_id', 'wbs'),
                                    ('cost_code_id', 'cost_code')):
                if field_name in line._fields:
                    entry[key] = line[field_name].display_name or ''
            lines.append(entry)
        return self.create({
            'request_id': request.id,
            'revision': request.revision,
            'reason': reason,
            'revised_by_id': self.env.user.id,
            'revised_on': fields.Datetime.now(),
            'state_at_revision': request.state,
            'estimated_total': request.estimated_total,
            'basis': json.dumps(lines, indent=1, default=str),
        })
