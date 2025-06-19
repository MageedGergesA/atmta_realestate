from odoo import models, fields

class ClinicTelehealthSession(models.Model):
    _name = 'clinic.telehealth.session'
    _description = 'Clinic Telehealth Session'

    appointment_id = fields.Many2one('clinic.appointment', string='Appointment', required=True)
    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    provider_id = fields.Many2one('res.users', string='Provider')
    start_time = fields.Datetime(string='Start Time')
    end_time = fields.Datetime(string='End Time')
    video_url = fields.Char(string='Video URL')
    status = fields.Selection([
        ('scheduled', 'Scheduled'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='scheduled')

class ClinicVirtualWaitingRoom(models.Model):
    _name = 'clinic.virtual.waiting.room'
    _description = 'Clinic Virtual Waiting Room'

    session_id = fields.Many2one('clinic.telehealth.session', string='Telehealth Session')
    patient_id = fields.Many2one('clinic.patient', string='Patient')
    check_in_time = fields.Datetime(string='Check-in Time', default=fields.Datetime.now)

class ClinicEConsent(models.Model):
    _name = 'clinic.econsent'
    _description = 'Clinic e-Consent Form'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    appointment_id = fields.Many2one('clinic.appointment', string='Appointment')
    consent_text = fields.Text(string='Consent Text')
    signed = fields.Boolean(string='Signed')
    signed_date = fields.Datetime(string='Signed Date') 