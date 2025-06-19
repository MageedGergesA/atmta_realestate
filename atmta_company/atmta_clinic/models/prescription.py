from odoo import models, fields

class ClinicMedicine(models.Model):
    _name = 'clinic.medicine'
    _description = 'Clinic Medicine'
    _rec_name = 'name'

    name = fields.Char(string='Medicine Name', required=True)
    description = fields.Text(string='Description')
    code = fields.Char(string='Code')
    is_active = fields.Boolean(string='Active', default=True)

class ClinicPrescription(models.Model):
    _name = 'clinic.prescription'
    _description = 'Clinic Prescription'
    _rec_name = 'patient_id'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    doctor_id = fields.Many2one('clinic.doctor', string='Doctor')
    visit_id = fields.Many2one('clinic.visit', string='Visit')
    date = fields.Datetime(string='Date', default=fields.Datetime.now)
    medicine_ids = fields.One2many('clinic.prescription.line', 'prescription_id', string='Medicines')
    notes = fields.Text(string='Notes')
    prescription_pdf = fields.Binary(string='Prescription PDF')
    sent_to_email = fields.Boolean(string='Sent to Email')

class ClinicPrescriptionLine(models.Model):
    _name = 'clinic.prescription.line'
    _description = 'Clinic Prescription Line'

    prescription_id = fields.Many2one('clinic.prescription', string='Prescription', required=True, ondelete='cascade')
    medicine_id = fields.Many2one('clinic.medicine', string='Medicine', required=True)
    dosage = fields.Char(string='Dosage')
    frequency = fields.Char(string='Frequency')
    duration = fields.Char(string='Duration')
    instructions = fields.Text(string='Instructions') 