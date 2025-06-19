from odoo import models, fields

class ClinicAppointmentRequest(models.Model):
    _name = 'clinic.appointment.request'
    _description = 'Clinic Appointment Request'
    _rec_name = 'patient_id'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    requested_date = fields.Datetime(string='Requested Date')
    status = fields.Selection([
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected')
    ], string='Status', default='pending')
    notes = fields.Text(string='Notes')

class ClinicPortalMessage(models.Model):
    _name = 'clinic.portal.message'
    _description = 'Clinic Portal Message'
    _rec_name = 'patient_id'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    message = fields.Text(string='Message')
    date_sent = fields.Datetime(string='Date Sent', default=fields.Datetime.now)

class ClinicDocumentUpload(models.Model):
    _name = 'clinic.document.upload'
    _description = 'Clinic Document Upload'
    _rec_name = 'filename'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    document = fields.Binary(string='Document')
    filename = fields.Char(string='Filename')
    upload_date = fields.Datetime(string='Upload Date', default=fields.Datetime.now)

class ClinicBillPay(models.Model):
    _name = 'clinic.bill.pay'
    _description = 'Clinic Bill Pay'
    _rec_name = 'billing_id'

    billing_id = fields.Many2one('clinic.billing', string='Billing', required=True)
    payment_date = fields.Datetime(string='Payment Date', default=fields.Datetime.now)
    amount = fields.Float(string='Amount')
    payment_method = fields.Selection([
        ('card', 'Card'),
        ('bank', 'Bank Transfer'),
        ('cash', 'Cash')
    ], string='Payment Method') 