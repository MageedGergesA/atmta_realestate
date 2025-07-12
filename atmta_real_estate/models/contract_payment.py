from email.policy import default

from dateutil.relativedelta import relativedelta

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from hijridate import Hijri, Gregorian
from datetime import datetime, timedelta


class RealEstateContractPayment(models.Model):
    _name = 'realestate.contract.payment'
    _order = 'date_due ASC'
    _description = 'Scheduled Contract Payment'

    contract_id = fields.Many2one('realestate.contract', string="Contract", required=True, ondelete='cascade')
    is_single_property = fields.Boolean(related="contract_id.is_single_property")
    is_multi_property = fields.Boolean(related="contract_id.is_multi_property")
    partner_id = fields.Many2one(related='contract_id.partner_id', string='Tenant/Partner')
    contract_line_id = fields.Many2one('realestate.contract.line', string="Contract Line", ondelete='cascade')
    date_due = fields.Date(string="Due Date", required=True)
    date_due_deadline = fields.Date(string="Due Date Deadline", compute="_compute_date_due_deadline", store=True)
    hijri_date_due = fields.Char(
        string="Hijri Due Date",
        compute='_compute_hijri_date',
        store=True,
        help="Stores in format: '15 Ramadan 1445 هـ'"
    )

    @api.depends('date_due')
    def _compute_hijri_date(self):
        for rec in self:
            if rec.date_due:
                greg_date = fields.Date.from_string(rec.date_due)
                hijri = Gregorian(greg_date.year, greg_date.month, greg_date.day).to_hijri()
                # Format as dd/month_name/yyyy with Arabic month names
                # arabic_months = {
                #     1: "محرم",
                #     2: "صفر",
                #     3: "ربيع الأول",
                #     4: "ربيع الثاني",
                #     5: "جمادى الأولى",
                #     6: "جمادى الآخرة",
                #     7: "رجب",
                #     8: "شعبان",
                #     9: "رمضان",
                #     10: "شوال",
                #     11: "ذو القعدة",
                #     12: "ذو الحجة"
                # }
                # month_name_ar = arabic_months.get(hijri.month, "")
                # rec.hijri_date_due = f"{hijri.day}/\u200E{month_name_ar}/\u200E{hijri.year}"
                rec.hijri_date_due = f"{hijri.day}/{hijri.month}/{hijri.year}"
            else:
                rec.hijri_date_due = False

    from datetime import timedelta

    hijri_date_due_deadline = fields.Char(string='Hijri Deadline (30 days)', compute='_compute_hijri_deadline', store=True)

    @api.depends('hijri_date_due')
    def _compute_hijri_deadline(self):
        for rec in self:
            if rec.hijri_date_due:
                try:
                    # Parse the existing Hijri date (format: dd/mm/yyyy)
                    parts = rec.hijri_date_due.split('/')
                    print(rec.hijri_date_due,parts)
                    if len(parts) == 3:
                        day = int(parts[0])
                        month = int(parts[1])  # Directly use month number
                        year = int(parts[2])
                        print('------==32-=4=32-4=23-4=23-4=23-4=32-4')

                        # Convert to Gregorian
                        greg_date = Hijri(year, month, day).to_gregorian()

                        # Add 30 days
                        deadline_date = greg_date + timedelta(days=30)

                        # Convert back to Hijri
                        hijri_deadline = Gregorian(deadline_date.year, deadline_date.month,
                                                   deadline_date.day).to_hijri()

                        # Format the result as dd/mm/yyyy
                        rec.hijri_date_due_deadline = f"{hijri_deadline.day:02d}/{hijri_deadline.month:02d}/{hijri_deadline.year}"
                    else:
                        rec.hijri_date_due_deadline = False
                except:
                    rec.hijri_date_due_deadline = False
            else:
                rec.hijri_date_due_deadline = False    # hijri_date_due_deadline = fields.Date(string="Hijri Start Date")


    @api.depends('date_due')
    def _compute_date_due_deadline(self):
        for rec in self:
            if rec.date_due:
                rec.date_due_deadline = rec.date_due + relativedelta(days=30)

    amount = fields.Float(string="Amount", required=True)
    state = fields.Selection([
        ('draft', 'Unpaid'),
        ('invoiced', 'Invoiced'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ], default='draft', string="Status")
    move_id = fields.Many2one('account.move', string="Invoice")
    move_state = fields.Selection(related='move_id.state', string="Invoice Status", store=True)
    increase_amount = fields.Float(string="Increase Amount", readonly=True)
    discount_amount = fields.Float(string="Discount Amount", readonly=True)
    property_id = fields.Many2one('product.product', string="Property", domain="[('is_property', '=', True)]")

    payment_plan_id = fields.Many2one(
        'realestate.payment.plan',
        string="Payment Plan Rule",
        ondelete='set null'
    )
    increment_rule_ids = fields.Many2many(
        'realestate.contract.increment.rule',
        'rel_payment_increment_rule',
        'payment_id',
        'rule_id',
        string="Applied Increment Rules",
        domain=[('discount', '=', False)]
    )

    discount_rule_ids = fields.Many2many(
        'realestate.contract.increment.rule',
        'rel_payment_discount_rule',
        'payment_id',
        'rule_id',
        string="Applied Discount Rules",
        domain=[('discount', '=', True)]
    )
    discount_rule_id = fields.Many2one(
        'realestate.contract.increment.rule',
        string="Applied Discount Rule",
        domain="[('discount', '=', True)]",
        ondelete='set null'
    )
    label = fields.Char(string="Label", compute="_compute_name", store=True)
    name = fields.Char(
        string="Reference",
        copy=False,
        help="Used during invoice creation to link payments to invoices"
    )

    _sql_constraints = [('contract_payment_name_unique', 'unique(name)', 'Contract Payment already exists')]

    @api.constrains('name')
    def _check_unique_code(self):
        for rec in self:
            if rec.name:
                existing = self.search([
                    ('name', '=', rec.name),
                    ('id', '!=', rec.id)
                ], limit=1)
                if existing:
                    raise ValidationError(_("Name Code '%s' already exists.") % rec.name)



    @api.depends('property_id', 'date_due')
    def _compute_name(self):
        for rec in self:
            prop_name = rec.property_id.display_name or "Property"
            date_str = rec.date_due.strftime('%Y-%m-%d') if rec.date_due else 'N/A'
            rec.label = f"Rent for {prop_name} on {date_str}"

    @api.depends('move_id.payment_state')
    def _compute_state(self):
        for rec in self:
            if rec.move_id:
                if rec.move_id.payment_state == 'paid':
                    rec.state = 'paid'
                elif rec.move_id.payment_state == 'not_paid':
                    rec.state = 'invoiced'
                else:
                    rec.state = 'draft'
            else:
                rec.state = 'draft'


    @api.depends('move_id.state', 'move_id.payment_state')
    def _compute_payment_state(self):
        for rec in self:
            if not rec.move_id:
                rec.state = 'draft'
            elif rec.move_id.payment_state == 'paid':
                rec.state = 'paid'
            elif rec.move_id.state == 'posted':
                rec.state = 'invoiced'
            else:
                rec.state = 'draft'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _("New")) == _("New"):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.contract.payment') or 'New'
        return super().create(vals_list)

