from odoo import models, fields

class ClinicProgressNote(models.Model):
    _name = 'clinic.progress.note'
    _description = 'Clinic Progress Note (SOAP)'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True, ondelete='cascade')
    date = fields.Datetime(string='Date', default=fields.Datetime.now)
    subjective = fields.Text(string='Subjective')
    objective = fields.Text(string='Objective')
    assessment = fields.Text(string='Assessment')
    plan = fields.Text(string='Plan') 