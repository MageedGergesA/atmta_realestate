from odoo import fields, models, api


class ResPartnerInherit(models.Model):
    _inherit = 'res.partner'

    id_type = fields.Selection([('national_id','National ID'),('passport','Passport'),('driving_licence','Driving Licence')], string='ID Type', default='national_id')
    id_number = fields.Char(string='ID Number')
    is_lessor = fields.Boolean(string='IS Lessor')
    organization_type_id = fields.Many2one('organization.type', string='Organization Type')
    unified_number = fields.Char(string='Unified Number')
    cr_number = fields.Char(string='CR Number')
    cr_date = fields.Date(string='CR Date')
    issued_by = fields.Char(string='Issued By')

    # _sql_constraints = [
    #     ('unique_id_number', 'unique(id_number)', 'ID Number must be unique.'),
    # ]

class OrganizationType(models.Model):
    _name = 'organization.type'

    name = fields.Char(string='Name')