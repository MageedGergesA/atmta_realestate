from odoo import models, fields

class ClinicMedicalRecord(models.Model):
    _name = 'clinic.medical.record'
    _description = 'Clinic Medical Record'
    _rec_name = 'diagnosis'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    doctor_id = fields.Many2one('clinic.doctor', string='Doctor')
    visit_id = fields.Many2one('clinic.visit', string='Visit')
    diagnosis = fields.Text(string='Diagnosis')
    notes = fields.Text(string='Notes')
    attachment_ids = fields.Many2many('ir.attachment', 'clinic_medical_record_attachment_rel', 'record_id', 'attachment_id', string='Attachments')

class ClinicVitalSigns(models.Model):
    _name = 'clinic.vital.signs'
    _description = 'Clinic Vital Signs'
    _rec_name = 'visit_id'

    visit_id = fields.Many2one('clinic.visit', string='Visit', required=True)
    temperature = fields.Float(string='Temperature (C)')
    blood_pressure = fields.Char(string='Blood Pressure')
    heart_rate = fields.Integer(string='Heart Rate')
    respiratory_rate = fields.Integer(string='Respiratory Rate')
    spo2 = fields.Float(string='SpO2 (%)')
    weight = fields.Float(string='Weight (kg)')
    height = fields.Float(string='Height (cm)')

class ClinicVisit(models.Model):
    _name = 'clinic.visit'
    _description = 'Clinic Visit'
    _rec_name = 'visit_reason'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    doctor_id = fields.Many2one('clinic.doctor', string='Doctor')
    date = fields.Datetime(string='Visit Date', required=True)
    visit_reason = fields.Char(string='Visit Reason')
    subjective = fields.Text(string='Subjective')
    objective = fields.Text(string='Objective')
    assessment = fields.Text(string='Assessment')
    plan = fields.Text(string='Plan')
    vital_signs_ids = fields.One2many('clinic.vital.signs', 'visit_id', string='Vital Signs')
    medical_record_ids = fields.One2many('clinic.medical.record', 'visit_id', string='Medical Records')
    attachment_ids = fields.Many2many('ir.attachment', 'clinic_visit_attachment_rel', 'visit_id', 'attachment_id', string='Attachments')
    appointment_id = fields.Many2one('clinic.appointment', string='Appointment')

    def print_visit_summary(self):
        """
        Return the QWeb report action for the visit summary PDF.
        """
        return self.env.ref('atmta_clinic.action_report_visit_summary').report_action(self) 