from odoo import models, fields

class ClinicAuditLog(models.Model):
    _name = 'clinic.audit.log'
    _description = 'Clinic Audit Log'
    _rec_name = 'action'

    user_id = fields.Many2one('res.users', string='User')
    action = fields.Char(string='Action')
    model = fields.Char(string='Model')
    record_id = fields.Integer(string='Record ID')
    timestamp = fields.Datetime(string='Timestamp', default=fields.Datetime.now)
    details = fields.Text(string='Details')

class ClinicDataEncryption(models.Model):
    _name = 'clinic.data.encryption'
    _description = 'Clinic Data Encryption (Stub)'

    encrypted = fields.Boolean(string='Encrypted', default=True)
    encryption_method = fields.Char(string='Encryption Method')
    last_encrypted = fields.Datetime(string='Last Encrypted') 