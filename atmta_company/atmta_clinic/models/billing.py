from odoo import models, fields, api

class ClinicBilling(models.Model):
    _name = 'clinic.billing'
    _description = 'Clinic Billing'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    amount = fields.Float(string='Amount', required=True, compute='_compute_amount', store=True)
    due_date = fields.Date(string='Due Date')
    status = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue')
    ], string='Status', default='draft')
    payment_link = fields.Char(string='Payment Link')
    claim_id = fields.Many2one('clinic.claim', string='Insurance Claim')
    line_ids = fields.One2many('clinic.billing.line', 'billing_id', string='Invoice Lines')
    # Fallback fields for Odoo view validation (not for business logic)
    product_id = fields.Many2one('product.product', string='Service/Medicine', compute='_get_first_line_field', readonly=True)
    quantity = fields.Float(string='Quantity', compute='_get_first_line_field', readonly=True)
    price_unit = fields.Float(string='Unit Price', compute='_get_first_line_field', readonly=True)
    subtotal = fields.Float(string='Subtotal', compute='_get_first_line_field', readonly=True)

    @api.depends('line_ids.subtotal')
    def _compute_amount(self):
        for rec in self:
            rec.amount = sum(line.subtotal for line in rec.line_ids)

    def _get_first_line_field(self):
        for rec in self:
            first_line = rec.line_ids[:1]
            rec.product_id = first_line.product_id if first_line else False
            rec.quantity = first_line.quantity if first_line else 0.0
            rec.price_unit = first_line.price_unit if first_line else 0.0
            rec.subtotal = first_line.subtotal if first_line else 0.0

    def print_invoice(self):
        return self.env.ref('atmta_clinic.action_report_invoice').report_action(self)

    def send_invoice(self):
        for rec in self:
            if not rec.patient_id.email:
                raise Exception('No email found for the patient.')
            template = self.env.ref('atmta_clinic.email_template_invoice', raise_if_not_found=False)
            if template:
                template.send_mail(rec.id, force_send=True)
            else:
                # fallback: send a simple email
                mail_values = {
                    'subject': 'Your Invoice',
                    'body_html': '<p>Your invoice is attached.</p>',
                    'email_to': rec.patient_id.email,
                }
                self.env['mail.mail'].create(mail_values).send()

class ClinicBillingLine(models.Model):
    _name = 'clinic.billing.line'
    _description = 'Clinic Billing Line'

    billing_id = fields.Many2one('clinic.billing', string='Invoice', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Service/Medicine', required=True)
    quantity = fields.Float(string='Quantity', default=1.0)
    price_unit = fields.Float(string='Unit Price', required=True)
    subtotal = fields.Float(string='Subtotal', compute='_compute_subtotal', store=True)

    @api.depends('quantity', 'price_unit')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.quantity * line.price_unit

class ClinicClaim(models.Model):
    _name = 'clinic.claim'
    _description = 'Clinic Insurance Claim'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    billing_id = fields.Many2one('clinic.billing', string='Billing')
    insurance_company = fields.Char(string='Insurance Company')
    claim_amount = fields.Float(string='Claim Amount')
    status = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('denied', 'Denied'),
        ('paid', 'Paid')
    ], string='Status', default='draft')
    denial_reason = fields.Text(string='Denial Reason') 