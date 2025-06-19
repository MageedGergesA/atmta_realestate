from odoo import models, fields

class ClinicInsuranceProvider(models.Model):
    _name = 'clinic.insurance.provider'
    _description = 'Clinic Insurance Provider'
    _rec_name = 'name'

    name = fields.Char(string='Provider Name', required=True)
    plan = fields.Char(string='Plan')
    contact = fields.Char(string='Contact Info')
    notes = fields.Text(string='Notes') 