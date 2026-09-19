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
        'realestate.contract', string="Bill to Lease", tracking=True,
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
            if not rec.scheduled_date:
                rec.scheduled_date = fields.Date.context_today(rec)

    def action_start_progress(self):
        for rec in self:
            rec.state = 'in_progress'
            rec._apply_maintenance_block()

    def action_complete(self):
        for rec in self:
            rec.state = 'done'
            if not rec.completion_date:
                rec.completion_date = fields.Date.context_today(rec)
            rec._release_maintenance_block()

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancelled'
            rec._release_maintenance_block()

    # ------------------------------------------------------------------
    # Property status -- maintenance owns ONE dimension, not the whole state
    # ------------------------------------------------------------------
    def _apply_maintenance_block(self):
        """Flag the property as under maintenance.

        Writes ``maintenance_status`` only. Before the Phase 2 split this wrote
        ``state = 'maintenance'``, which destroyed the rented / reserved /sold
        value it happened to overwrite.
        """
        self.ensure_one()
        if self.property_id:
            self.property_id._set_maintenance_status('maintenance', reason=_(
                "Maintenance request %s started.") % self.display_name)

    def _release_maintenance_block(self):
        """Clear the maintenance flag once no request is still working on the
        unit.

        Critically, this restores ``maintenance_status`` to normal and nothing
        else: a unit that was rented before the maintenance is still rented
        afterwards. The pre-upgrade code set ``state = 'available'`` here,
        silently evicting a sitting tenant from the property record.
        """
        self.ensure_one()
        prop = self.property_id
        if not prop:
            return
        still_working = self.search_count([
            ('property_id', '=', prop.id),
            ('id', '!=', self.id),
            ('state', '=', 'in_progress'),
        ])
        if still_working:
            return
        # A unit turn is a separate, deliberate gate on re-letting; it clears
        # its own block when the unit passes final inspection.
        if prop.active_turn_id:
            return
        prop._set_maintenance_status('normal', reason=_(
            "Maintenance request %s closed.") % self.display_name)

    def action_reset_to_draft(self):
        for rec in self:
            was_in_progress = rec.state == 'in_progress'
            rec.state = 'draft'
            # Starting the work flagged the unit as under maintenance. Back in
            # draft nothing is being worked on, so the flag has to go too, or
            # the unit stays blocked with no open request to clear it.
            if was_in_progress:
                rec._release_maintenance_block()

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
                ('date_due', '>=', fields.Date.context_today(rec)),
            ], order='date_due asc', limit=1)
            if not payment:
                payment = Payment.create({
                    'contract_id': rec.contract_id.id,
                    'property_id': rec.property_id.id,
                    'date_due': fields.Date.context_today(rec),
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
