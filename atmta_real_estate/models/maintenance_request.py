from odoo import fields, models, api, _
from odoo.exceptions import UserError


class MaintenanceRequest(models.Model):
    _name = 'realestate.maintenance.request'
    _description = 'Maintenance Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Request", required=True, default="New", tracking=True)
    property_id = fields.Many2one('realestate.property', required=True, string="Property", tracking=True)
    description = fields.Text(string="Issue Description", tracking=True)
    request_date = fields.Date(default=fields.Date.today, string="Request Date", tracking=True)
    scheduled_date = fields.Date(string="Scheduled Date", tracking=True)
    completion_date = fields.Date(string="Completion Date", tracking=True)
    cost = fields.Float(string="Estimated Cost", tracking=True)
    actual_cost = fields.Float(string="Actual Cost", tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('scheduled', 'Scheduled'),
        ('in_progress', 'In Progress'),
        ('done', 'Completed'),
        ('cancelled', 'Cancelled')
    ], default='draft', tracking=True)
    attachment_ids = fields.Many2many('ir.attachment', 'request_attachment_rel', 'request_id',
                                      'maintenance_request_id', 'Attachments',
                                      help="You may attach files to this template, to be added to all "
                                           "emails created from this template", tracking=True)


    assigned_to = fields.Many2one('res.users', string="Assigned Technician", tracking=True)

    # --- Charge-back to tenant ---
    contract_id = fields.Many2one(
        'realestate.contract', string="Bill to Contract", tracking=True,
        help="Rental contract whose tenant should be charged for this maintenance.")
    charge_to_tenant = fields.Boolean(string="Charged to Tenant", readonly=True, copy=False)
    charge_amount = fields.Float(
        string="Amount to Charge",
        help="Amount billed to the tenant. Defaults to the actual cost (or estimated cost).")
    payment_line_id = fields.Many2one(
        'realestate.contract.payment.line', string="Tenant Charge",
        readonly=True, copy=False,
        help="The payment charge line created when this request was billed to the tenant.")

    def action_schedule(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError("Can only schedule from draft.")
            rec.state = 'scheduled'

    def action_start_progress(self):
        for rec in self:
            rec.state = 'in_progress'
            if rec.property_id:
                rec.property_id.state = 'maintenance'

    def action_complete(self):
        for rec in self:
            rec.state = 'done'
            if rec.property_id:
                other_active = self.search([
                    ('property_id', '=', rec.property_id.id),
                    ('id', '!=', rec.id),
                    ('state', '=', 'in_progress')
                ])
                if not other_active:
                    rec.property_id.state = 'available'

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancelled'
            if rec.property_id:
                other_active = self.search([
                    ('property_id', '=', rec.property_id.id),
                    ('id', '!=', rec.id),
                    ('state', '=', 'in_progress')
                ])
                if not other_active:
                    rec.property_id.state = 'available'

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = 'draft'

    def action_bill_to_tenant(self):
        """Charge this maintenance to the tenant: append a charge line to the
        contract's next un-invoiced payment, creating a one-off charge payment
        if none is pending."""
        Payment = self.env['realestate.contract.payment']
        PaymentLine = self.env['realestate.contract.payment.line']
        for rec in self:
            if rec.payment_line_id:
                raise UserError(_("This request has already been billed to the tenant."))
            if not rec.contract_id:
                raise UserError(_("Set 'Bill to Contract' before billing the tenant."))
            amount = rec.charge_amount or rec.actual_cost or rec.cost
            if amount <= 0:
                raise UserError(_("Set a positive amount to charge the tenant."))

            payment = Payment.search([
                ('contract_id', '=', rec.contract_id.id),
                ('move_id', '=', False),
                ('state', '=', 'draft'),
                ('date_due', '>=', fields.Date.today()),
            ], order='date_due asc', limit=1)
            if not payment:
                payment = Payment.create({
                    'contract_id': rec.contract_id.id,
                    'property_id': rec.property_id.id,
                    'date_due': fields.Date.today(),
                    'amount': 0.0,
                })

            description = rec.name
            if rec.description:
                description = f"{rec.name}: {rec.description}"
            rec.payment_line_id = PaymentLine.create({
                'payment_id': payment.id,
                'charge_type': 'maintenance',
                'name': description,
                'amount': amount,
                'maintenance_request_id': rec.id,
            }).id
            rec.charge_to_tenant = True
            rec.message_post(body=_(
                "Billed %(amount)s to tenant on payment %(ref)s.",
                amount=amount, ref=payment.name or payment.display_name))
        return True
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _("New")) == _("New"):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.maintenance.request') or 'New'
        return super().create(vals_list)
