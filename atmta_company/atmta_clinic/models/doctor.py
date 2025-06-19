from odoo import models, fields

class ClinicDoctor(models.Model):
    _name = 'clinic.doctor'
    _description = 'Clinic Doctor'
    _rec_name = 'user_id'

    user_id = fields.Many2one('res.users', string='User', required=True)
    specialization = fields.Char(string='Specialization')
    available = fields.Boolean(string='Available', default=True)
    rating = fields.Float(string='Rating')
    feedback = fields.Text(string='Feedback')
    signature = fields.Binary(string='Signature') 