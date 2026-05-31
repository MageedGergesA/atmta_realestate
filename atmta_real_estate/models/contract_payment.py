from dateutil.relativedelta import relativedelta

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
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
                    if len(parts) == 3:
                        day = int(parts[0])
                        month = int(parts[1])  # Directly use month number
                        year = int(parts[2])

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
                except (ValueError, TypeError):
                    rec.hijri_date_due_deadline = False
            else:
                rec.hijri_date_due_deadline = False    # hijri_date_due_deadline = fields.Date(string="Hijri Start Date")


    @api.depends('date_due')
    def _compute_date_due_deadline(self):
        for rec in self:
            if rec.date_due:
                rec.date_due_deadline = rec.date_due + relativedelta(days=30)

    amount = fields.Float(string="Base Amount", required=True,
                          help="Base (rent) amount for this due date, before additional charges.")
    charge_line_ids = fields.One2many(
        'realestate.contract.payment.line', 'payment_id',
        string="Additional Charges",
        help="Maintenance, utility or other charges billed together with this payment.")
    amount_total = fields.Float(
        string="Total Due", compute="_compute_amount_total", store=True,
        help="Base amount plus all additional charges.")

    @api.depends('amount', 'charge_line_ids.amount')
    def _compute_amount_total(self):
        for rec in self:
            rec.amount_total = rec.amount + sum(rec.charge_line_ids.mapped('amount'))

    state = fields.Selection([
        ('draft', 'Unpaid'),
        ('invoiced', 'Invoiced'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ], default='draft', string="Status",
        compute='_compute_state', store=True, readonly=True,
        help="Derived from the linked invoice: posted -> Invoiced, "
             "reconciled -> Paid, cancelled invoice -> Cancelled.")
    move_id = fields.Many2one('account.move', string="Invoice")
    move_state = fields.Selection(related='move_id.state', string="Invoice Status", store=True)
    payment_state = fields.Selection(
        related='move_id.payment_state', string="Payment Status", store=True)
    increase_amount = fields.Float(string="Increase Amount", readonly=True)
    discount_amount = fields.Float(string="Discount Amount", readonly=True)
    property_id = fields.Many2one('realestate.property', string="Property")

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

    @api.depends('move_id', 'move_id.state', 'move_id.payment_state')
    def _compute_state(self):
        for rec in self:
            move = rec.move_id
            if not move:
                rec.state = 'draft'
            elif move.state == 'cancel':
                rec.state = 'cancelled'
            elif move.payment_state in ('paid', 'in_payment', 'reversed'):
                rec.state = 'paid'
            elif move.state == 'posted':
                rec.state = 'invoiced'
            else:
                rec.state = 'draft'

    def action_register_payment(self):
        """Register and reconcile a real payment for the linked invoice(s)."""
        moves = self.mapped('move_id').filtered(lambda m: m.state == 'posted')
        if not moves:
            raise UserError(_("There is no posted invoice to pay yet. "
                              "Generate and post the invoice first."))
        return self.env['realestate.account.tools'].register_payment(moves)

    def _get_invoice_line_commands(self, prop, account_id):
        """Build the (0, 0, vals) command list for one payment's invoice:
        a base/rent line plus one line per additional charge. Shared by the
        manual invoicing action and the auto-invoicing cron."""
        self.ensure_one()
        charge_labels = dict(
            self.env['realestate.contract.payment.line']._fields['charge_type'].selection)
        base_line = {
            'name': f'Rent for {prop.display_name} on {self.date_due}',
            'product_id': prop.product_variant_id.id,
            'quantity': 1,
            'price_unit': self.amount,
        }
        # Only pin the account when one is configured; otherwise let Odoo derive
        # it from the product so the invoice can still post.
        if account_id:
            base_line['account_id'] = account_id
        lines = [(0, 0, base_line)]
        for charge in self.charge_line_ids:
            charge_product = charge.product_id or prop.product_variant_id
            charge_account = (
                charge.product_id.categ_id.property_account_income_categ_id.id
                if charge.product_id else False) or account_id
            charge_line = {
                'name': charge.name or charge_labels.get(charge.charge_type, 'Charge'),
                'product_id': charge_product.id,
                'quantity': 1,
                'price_unit': charge.amount,
            }
            if charge_account:
                charge_line['account_id'] = charge_account
            lines.append((0, 0, charge_line))
        return lines

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _("New")) == _("New"):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.contract.payment') or 'New'
        return super().create(vals_list)

