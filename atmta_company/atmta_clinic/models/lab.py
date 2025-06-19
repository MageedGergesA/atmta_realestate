from odoo import models, fields, api
from odoo.exceptions import UserError

class ClinicLabTestRequest(models.Model):
    _name = 'clinic.lab.test.request'
    _description = 'Clinic Lab Test Request'
    _rec_name = 'test_type'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    visit_id = fields.Many2one('clinic.visit', string='Visit')
    doctor_id = fields.Many2one('clinic.doctor', string='Doctor')
    test_type = fields.Char(string='Test Type')
    status = fields.Selection([
        ('requested', 'Requested'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected')
    ], string='Status', default='requested')
    result_id = fields.Many2one('clinic.lab.result', string='Result')
    attachment_ids = fields.Many2many('ir.attachment', 'clinic_lab_test_request_attachment_rel', 'request_id', 'attachment_id', string='Attachments')

class ClinicLabResult(models.Model):
    _name = 'clinic.lab.result'
    _description = 'Clinic Lab Result'
    _rec_name = 'result'

    request_id = fields.Many2one('clinic.lab.test.request', string='Test Request', required=True)
    result = fields.Text(string='Result')
    result_file = fields.Binary(string='Result PDF')
    approved = fields.Boolean(string='Approved')
    approved_by = fields.Many2one('clinic.doctor', string='Approved By')
    approval_date = fields.Datetime(string='Approval Date')
    sent_to_patient = fields.Boolean(string='Sent to Patient', default=False)

    def send_to_patient(self):
        for record in self:
            patient_email = record.request_id.patient_id.email
            if not patient_email:
                raise UserError('No email found for the patient.')
            template = self.env.ref('atmta_clinic.email_template_lab_result_to_patient', raise_if_not_found=False)
            if template:
                template.send_mail(record.id, force_send=True)
                record.sent_to_patient = True
            else:
                # fallback: send a simple email
                mail_values = {
                    'subject': 'Lab Result',
                    'body_html': '<p>Your lab result is ready.</p>',
                    'email_to': patient_email,
                    'attachment_ids': [(6, 0, [record.result_file.id]) if record.result_file else []],
                }
                self.env['mail.mail'].create(mail_values).send()
                record.sent_to_patient = True 