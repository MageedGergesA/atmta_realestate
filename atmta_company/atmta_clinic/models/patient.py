from odoo import models, fields

class ClinicPatient(models.Model):
    _name = 'clinic.patient'
    _description = 'Clinic Patient'
    _rec_name = 'name'

    name = fields.Char(string='Full Name', required=True)
    dob = fields.Date(string='Date of Birth')
    gender = fields.Selection([
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other')
    ], string='Gender')
    phone = fields.Char(string='Phone')
    email = fields.Char(string='Email')
    address = fields.Text(string='Address')
    medical_history = fields.Text(string='Medical History')
    allergies = fields.Text(string='Allergies')
    immunizations = fields.Text(string='Immunizations')
    family_history = fields.Text(string='Family History')
    social_history = fields.Text(string='Social History')
    progress_note_ids = fields.One2many('clinic.progress.note', 'patient_id', string='Progress Notes')
    family_id = fields.Many2one('clinic.patient', string='Family/Group')
    tag_ids = fields.Many2many('res.partner.category', 'clinic_patient_category_rel', 'patient_id', 'category_id', string='Tags')
    insurance_provider_id = fields.Many2one('clinic.insurance.provider', string='Insurance Provider')
    insurance_plan = fields.Char(string='Insurance Plan')
    insurance_id_number = fields.Char(string='Insurance ID Number')
    insurance_eligibility = fields.Selection([
        ('unknown', 'Unknown'),
        ('eligible', 'Eligible'),
        ('ineligible', 'Ineligible')
    ], string='Eligibility', default='unknown')
    notes = fields.Text(string='Notes')
    document_upload_ids = fields.One2many('clinic.document.upload', 'patient_id', string='Attachments')
    user_id = fields.Many2one('res.users', string='Related User', ondelete='set null', default=lambda self: self.env.user)

    def create(self, vals):
        if 'user_id' not in vals or not vals['user_id']:
            vals['user_id'] = self.env.user.id
        return super(ClinicPatient, self).create(vals) 