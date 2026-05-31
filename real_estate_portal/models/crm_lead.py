from odoo import fields, models


class CrmLeadRealestate(models.Model):
    """Public website visitors submit EOI / visit requests against a project
    or a specific property. Those land here as leads, tagged with the source
    references so sales can follow up in the CRM pipeline."""
    _inherit = 'crm.lead'

    re_project_id = fields.Many2one(
        'realestate.project', string='Real Estate Project',
        index=True, ondelete='set null',
        help='Project the visitor expressed interest in.',
    )
    re_property_id = fields.Many2one(
        'realestate.property', string='Real Estate Property',
        index=True, ondelete='set null',
        domain="[('project_id', '=', re_project_id)]",
        help='Specific unit / villa the visitor clicked on (if any).',
    )
    re_visit_requested = fields.Boolean(string='Visit Requested')
    re_visit_date = fields.Datetime(string='Preferred Visit Date')
    re_source = fields.Selection([
        ('eoi', 'Expression of Interest'),
        ('visit', 'Visit Request'),
    ], string='RE Source', help='Which website form produced this lead.')
