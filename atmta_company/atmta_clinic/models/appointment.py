from odoo import models, fields

class ClinicAppointment(models.Model):
    _name = 'clinic.appointment'
    _description = 'Clinic Appointment'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True, ondelete='cascade')
    provider_id = fields.Many2one('res.users', string='Provider')
    date_start = fields.Datetime(string='Start Time', required=True)
    date_end = fields.Datetime(string='End Time')
    room_id = fields.Many2one('clinic.resource', string='Room')
    equipment_ids = fields.Many2many('clinic.resource', 'clinic_appointment_equipment_rel', 'appointment_id', 'resource_id', string='Equipment')
    status = fields.Selection([
        ('scheduled', 'Scheduled'),
        ('checked_in', 'Checked In'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('no_show', 'No Show')
    ], string='Status', default='scheduled')
    notes = fields.Text(string='Notes')
    invoice_id = fields.Many2one('clinic.billing', string='Invoice')

    def action_create_invoice(self):
        for appt in self:
            if appt.invoice_id:
                return {
                    'type': 'ir.actions.act_window',
                    'res_model': 'clinic.billing',
                    'res_id': appt.invoice_id.id,
                    'view_mode': 'form',
                }
            invoice = self.env['clinic.billing'].create({
                'patient_id': appt.patient_id.id,
                'amount': 0.0,  # To be filled by user
                'due_date': appt.date_start.date() if appt.date_start else False,
                'status': 'draft',
            })
            appt.invoice_id = invoice.id
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'clinic.billing',
                'res_id': invoice.id,
                'view_mode': 'form',
            } 