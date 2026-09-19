"""Augments ``crm.lead`` with an API source marker.

The marker is set automatically when a lead is created via the public
``/api/v1/interests`` endpoint, so sales can filter / route incoming
website interests separately from leads created in the backend.
"""

from odoo import fields, models


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    realestate_api_source = fields.Boolean(
        string='Captured via Public API',
        readonly=True, copy=False, index=True,
    )
    realestate_api_project_id = fields.Many2one(
        'realestate.project',
        string='Interested Project',
        readonly=True, copy=False,
    )
    # The Idempotency-Key of the submission that created the lead. It used to
    # be appended to the description and searched back with `like`, but the
    # description is Html: the stored value is wrapped in markup, so the
    # search never matched and every retry created a duplicate lead.
    realestate_api_idempotency_key = fields.Char(
        string='API Idempotency Key',
        readonly=True, copy=False, index=True,
    )
    realestate_api_property_id = fields.Many2one(
        'realestate.property',
        string='Interested Property',
        readonly=True, copy=False,
    )
