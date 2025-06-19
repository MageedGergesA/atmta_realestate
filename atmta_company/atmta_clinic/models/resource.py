from odoo import models, fields

class ClinicResource(models.Model):
    _name = 'clinic.resource'
    _description = 'Clinic Resource (Room/Equipment)'
    _rec_name = 'name'

    name = fields.Char(string='Name', required=True)
    resource_type = fields.Selection([
        ('room', 'Room'),
        ('equipment', 'Equipment')
    ], string='Type', required=True)
    description = fields.Text(string='Description') 