from odoo import models, fields, _, api
from odoo.exceptions import UserError, ValidationError
from dateutil.relativedelta import relativedelta
import datetime
from hijri.core import Hijriah


class RealEstateContract(models.Model):
    _name = 'realestate.contract'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Real Estate Contract'

    name = fields.Char(string="Contract Reference", required=True, copy=False, readonly=False,
                       index='trigram',
                       default=lambda self: _('New'))
    partner_id = fields.Many2one('res.partner', string="Tenant", required=True, tracking=True)
    start_date = fields.Date(string="Start Date", required=True, tracking=True)
    # hijri_start_date = fields.Date(string="Hijri Start Date", compute='_compute_hijri_date', store=True)
    end_date = fields.Date(string="End Date", required=True, tracking=True)
    # hijri_end_date = fields.Date(string="Hijri End Date", compute='_compute_hijri_date', store=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('ready', 'Ready'),
        ('confirmed', 'Confirmed'),
        ('invoiced', 'Invoiced'),
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('terminated', 'Terminated'),
    ], string='Status', default='draft', tracking=True, readonly=True)
    notes = fields.Text(string="Terms and Conditions", tracking=True)
    # fields from the tenancy contract
    main_contract_no = fields.Char(string='Main Contract No')
    country_id = fields.Many2one(related='partner_id.country_id')
    contract_type = fields.Many2one('contract.type',string='Contract Type') #
    contract_sealing_location_id = fields.Many2one(comodel_name='res.country.state', string='Contract Sealing Location', domain="[('country_id', '=', country_id)]")
    contract_sealing_date = fields.Date(string='Contract Sealing Date')
    contract_no = fields.Char(string='Contract No')
    lessor_rep_id = fields.Many2one('res.partner', string='Lessor Representative')
    lessor_id = fields.Many2one('res.partner', string='Lessor')
    # fields from the tenancy contract
    issuer = fields.Char(string="Issuer")
    title_need_no = fields.Char(string='Title Need No')
    place_of_issue = fields.Char(string='Place Of Issue')
    issue_Date = fields.Date(string='Issue Date')
    # fields from the tenancy contract
    use_manual_payment = fields.Boolean(string='Generate Payment Schedule Lines')
    line_ids = fields.One2many('realestate.contract.line', 'contract_id', string="Contract Lines")
    payment_count = fields.Integer(compute='get_payment_count', default=0)
    move_ids = fields.One2many('account.move', 'contract_id', string='Invoices')
    invoice_count = fields.Integer(compute='get_invoice_count', default=0)
    last_generated = fields.Datetime(string="Last Payment Generation", readonly=True)
    contract_payment_ids = fields.One2many('realestate.contract.payment', 'contract_id', string="Payments")
    attachment_ids = fields.Many2many('ir.attachment', 'contract_attachment_rel', 'contract_id',
                                      'attachment_contract_id', 'Attachments',
                                      help="You may attach files to this template, to be added to all "
                                           "emails created from this template")
    is_renewed = fields.Boolean(string='Is Renewed Contract')
    old_contract_id = fields.Many2one('realestate.contract', string='Old Contract')
    # New computed fields
    total_scheduled = fields.Monetary(
        string="Total Scheduled",
        compute="_compute_totals",
        store=True
    )
    total_paid = fields.Monetary(
        string="Total Paid",
        compute="_compute_totals",
        store=True
    )
    balance_due = fields.Monetary(
        string="Remaining Balance",
        compute="_compute_totals",
        store=True
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id.id,
        required=True
    )

    utility_line_ids = fields.One2many('realestate.contract.utility.line', 'contract_id', string="Utilities")
    total_utilities = fields.Monetary(string="Total Utilities", compute='_compute_utilities')
    paid_utilities = fields.Monetary(string="Paid Utilities", compute='_compute_utilities')
    net_income = fields.Monetary(string="Net Income", compute='_compute_utilities')

    # is_single unit contract, will diable the contract lines, and unit,price,start and end date will be on the contract level
    is_single_property = fields.Boolean(string='Is Single Unit Contract',
                                        default=lambda self: self.env['ir.config_parameter'].sudo().get_param(
                                            'atmta_real_estate.single_property_contract') == 'True')
    property_id = fields.Many2one('product.product', string="Property", domain="[('is_property', '=', True)]")
    property_type_id = fields.Many2one(related='property_id.property_type_id', string='Property Type', store=True)
    price = fields.Float(string="Base Rent")
    payment_plan_ids = fields.Many2many(
        'realestate.payment.plan',
        'rel_contract_payment_plan',  # relation table name
        'contract_id',  # this model's column
        'contract_payment_plan_id',  # related model's column
        string='Payment Plans'
    )
    increment_rule_ids = fields.Many2many(
        'realestate.contract.increment.rule',
        'rel_contract_increment_rule_rel',
        'contract_id',
        'increment_rule_id',
        string="Increment Rules",
        domain=[('discount', '=', False)]
    )
    discount_rule_ids = fields.Many2many(
        'realestate.contract.increment.rule',
        'rel_contract_discount_rule_rel',
        'contract_id',  # your model
        'increment_rule_id',  # related model (must match id in target model)
        string="Discount Rules",
        domain=[('discount', '=', True)]
    )

    # is_multi unit contract, will enable the contract lines, and unit,price,start and end date will be on the contract level
    is_multi_property = fields.Boolean(string='Is Multi Unit Contract',
                                       default=lambda self: self.env['ir.config_parameter'].sudo().get_param(
                                           'atmta_real_estate.multi_property_contract') == 'True')
    _sql_constraints = [('contract_name_unique', 'unique(name)', 'Contract name already exists')]

    # @api.depends('start_date', 'end_date')
    # def _compute_hijri_date(self):
    #     for rec in self:
    #         if rec.start_date:
    #             rec.hijri_start_date = Hijriah(rec.start_date)
    #         if rec.end_date:
    #             rec.hijri_end_date = Hijriah(rec.end_date)
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


    @api.onchange('is_single_property')
    def _onchange_is_single(self):
        for rec in self:
            if rec.is_single_property:
                rec.is_multi_property = False
                rec.line_ids = False

    @api.onchange('is_multi_property')
    def _onchange_is_multi(self):
        for rec in self:
            if rec.is_multi_property:
                rec.is_single_property = False
                rec.discount_rule_ids = False
                rec.increment_rule_ids = False
                rec.payment_plan_ids = False
                rec.property_id = False
                rec.price = 0

    @api.depends('utility_line_ids.amount', 'utility_line_ids.bill_paid')
    def _compute_utilities(self):
        for contract in self:
            utilities = contract.utility_line_ids
            contract.total_utilities = sum(utilities.mapped('amount'))
            contract.paid_utilities = sum(utilities.filtered(lambda l: l.bill_paid).mapped('amount'))
            contract.net_income = contract.total_paid - contract.paid_utilities

    def action_generate_payment_lines(self):
        self.ensure_one()
        self.action_generate_payment_schedule()
        if self.contract_payment_ids:
            self.state = 'ready'
            if self.is_multi_property:
                self.line_ids.write({'state': 'ready'})

    def action_reset_to_draft(self):
        for contract in self:
            contract.state = 'draft'
            if self.is_multi_property:
                contract.line_ids.write({'state': 'draft'})

    def action_confirm(self):
        self.ensure_one()
        if self.state != 'ready':
            raise UserError("You must generate payment lines before confirming the contract.")
        self.state = 'confirmed'
        if self.is_multi_property:
            self.line_ids.write({'state': 'confirmed'})

    def action_generate_invoices(self):
        self.ensure_one()
        if self.state != 'confirmed':
            raise UserError("You must confirm the contract before generating invoices.")
        self.action_create_invoices()
        if self.move_ids:
            self.state = 'invoiced'
            if self.is_multi_property:
                self.line_ids.write({'state': 'invoiced'})

    def action_activate(self):
        for contract in self:
            if contract.state != 'invoiced':
                raise UserError("You must generate invoices before activating the contract.")
            contract.state = 'active'
            if self.is_multi_property:
                # Auto-activate lines that match contract start date
                for line in contract.line_ids:
                    if (
                            line.state not in ['terminated', 'expired']
                            and line.start_date == contract.start_date
                    ):
                        line.state = 'active'

    def action_terminate(self):
        for contract in self:
            if contract.state not in ['confirmed', 'invoiced', 'active']:
                raise UserError("Only confirmed, invoiced, or active contracts can be terminated.")

            # Cancel only draft invoices
            draft_moves = contract.contract_payment_ids.mapped('move_id').filtered(lambda m: m.state == 'draft')
            for move in draft_moves:
                move.button_cancel()

            # Update contract and its lines
            contract.state = 'terminated'
            if self.is_multi_property:
                contract.line_ids.filtered(lambda l: l.state != 'expired').write({'state': 'terminated'})

    def check_contract_expiry(self):
        today = fields.Date.today()
        expired = self.search([('state', '=', 'active'), ('end_date', '<', today)])
        expired.write({'state': 'expired'})

    @api.depends('contract_payment_ids.amount',
                 'contract_payment_ids.move_id.state')
    def _compute_totals(self):
        if not self:
            return

        # First get all payment amounts (total scheduled)
        payment_totals = self.env['realestate.contract.payment'].read_group(
            [('contract_id', 'in', self.ids)],
            ['amount', 'contract_id'],
            ['contract_id']
        )
        scheduled_map = {x['contract_id'][0]: x['amount'] for x in payment_totals}

        # Then get only posted payments
        posted_totals = self.env['realestate.contract.payment'].read_group(
            [('contract_id', 'in', self.ids),
             ('move_id.state', '=', 'posted')],
            ['amount', 'contract_id'],
            ['contract_id']
        )
        paid_map = {x['contract_id'][0]: x['amount'] for x in posted_totals}

        # Compute values
        for contract in self:
            total = scheduled_map.get(contract.id, 0.0)
            paid = paid_map.get(contract.id, 0.0)
            contract.update({
                'total_scheduled': total,
                'total_paid': paid,
                'balance_due': total - paid
            })

    @api.depends('contract_payment_ids')
    def get_payment_count(self):
        for rec in self:
            rec.payment_count = len(rec.contract_payment_ids)

    @api.depends('move_ids')
    def get_invoice_count(self):
        for rec in self:
            rec.invoice_count = len(rec.move_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _("New")) == _("New"):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.contract') or 'New'
        contracts = super().create(vals_list)
        contracts._sync_contract_history()
        return contracts

    def write(self, vals):
        result = super().write(vals)
        self._sync_contract_history()
        return result

    def action_open_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Scheduled Payments',
            'res_model': 'realestate.contract.payment',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {
                'default_contract_id': self.id,
                # 'group_by': 'contract_line_id',  # 👈 This triggers default grouping
            }, }

    def action_open_report_wizard(self):
        return {
            'name': 'Contract Report',
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.contracts.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_id': self.id},
        }

    def action_generate_payment_schedule(self):
        now = datetime.datetime.now()

        UNIT_TO_DAYS = {
            'day': 1,
            'week': 7,
            'month': 30,
            'year': 365
        }

        UNIT_TO_RELATIVEDELTA = {
            'day': 'days',
            'week': 'weeks',
            'month': 'months',
            'year': 'years'
        }

        def apply_rules(price, months_passed, rules, is_discount=False):
            applied_ids = []
            total = 0.0
            for rule in rules.filtered(
                    lambda r: r.start_month <= months_passed and (
                            r.duration_months == 0 or months_passed < r.start_month + r.duration_months)
            ).sorted('priority'):
                val = abs(rule.increase_value)
                if rule.increase_type == 'percent':
                    val = price * val / 100
                if is_discount:
                    price -= val
                else:
                    price += val
                total += val
                applied_ids.append(rule.id)
            return price, total, applied_ids

        for contract in self:
            if contract.last_generated and contract.write_date <= contract.last_generated:
                continue

            if contract.is_multi_property:
                if contract.last_generated:
                    if all(line.write_date <= contract.last_generated for line in contract.line_ids):
                        continue

            # Remove previous non-invoiced payments
            contract.contract_payment_ids.filtered(lambda p: not p.move_id).unlink()

            payments_to_create = []

            contract_lines = contract.line_ids if contract.is_multi_property else [contract]
            for line in contract_lines:
                plans = line.payment_plan_ids if contract.is_multi_property else contract.payment_plan_ids
                if not plans:
                    continue

                start = line.start_date or contract.start_date
                end = line.end_date or contract.end_date
                current_date = start

                while current_date <= end:
                    base_price = line.price if contract.is_multi_property else contract.price
                    delta = relativedelta(current_date, start)
                    months_passed = delta.years * 12 + delta.months
                    days_passed = (current_date - start).days

                    valid_plans = [
                        p for p in plans
                        if UNIT_TO_DAYS.get(p.start_after_unit, 0) * p.start_after <= days_passed
                    ]

                    if not valid_plans:
                        current_date += relativedelta(days=1)
                        continue

                    plan = max(valid_plans, key=lambda p: UNIT_TO_DAYS.get(p.start_after_unit, 0) * p.start_after)
                    unit_key = UNIT_TO_RELATIVEDELTA.get(plan.unit)

                    if not unit_key:
                        raise UserError(f"Invalid interval unit '{plan.unit}' in payment plan.")

                    increment_rules = line.increment_rule_ids if contract.is_multi_property else contract.increment_rule_ids
                    discount_rules = line.discount_rule_ids if contract.is_multi_property else contract.discount_rule_ids

                    # Apply increment
                    price_with_increments, inc_total, inc_ids = apply_rules(base_price, months_passed, increment_rules)

                    # Apply discount (subtracts from total)
                    final_price, disc_total, disc_ids = apply_rules(price_with_increments, months_passed,
                                                                    discount_rules, is_discount=True)

                    search_domain = [
                        ('contract_id', '=', contract.id),
                        ('date_due', '=', current_date)
                    ]
                    if contract.is_multi_property:
                        search_domain.append(('contract_line_id', '=', line.id))

                    exists = self.env['realestate.contract.payment'].search_count(search_domain, limit=1)

                    if not exists:
                        ref = f"CNT-{contract.id}-PAY-{line.id if contract.is_multi_property else '0'}-{current_date}"
                        payments_to_create.append({
                            'contract_id': contract.id,
                            'contract_line_id': line.id if contract.is_multi_property else False,
                            'property_id': contract.property_id.id if contract.is_single_property else line.property_id.id,
                            'date_due': current_date,
                            # 'name': ref,
                            'amount': final_price,
                            'payment_plan_id': plan.id,
                            'increase_amount': inc_total,
                            'discount_amount': disc_total,
                            'increment_rule_ids': [(6, 0, inc_ids)],
                            'discount_rule_ids': [(6, 0, disc_ids)],
                        })

                    current_date += relativedelta(**{unit_key: plan.interval})

            if payments_to_create:
                self.env['realestate.contract.payment'].create(payments_to_create)

            contract.last_generated = now

    def action_create_invoices(self):
        # Prepare all invoice data
        invoices_to_create = []

        # Get all unpaid payments with prefetch
        payments = self.env['realestate.contract.payment'].search([
            ('contract_id', 'in', self.ids),
            ('move_id', '=', False)
        ])

        if not payments:
            raise UserError(_("No unpaid payments found"))

        # Prepare invoice vals
        for payment in payments:
            product = payment.contract_line_id.property_id if payment.contract_line_id else payment.contract_id.property_id
            account_id = product.categ_id.property_account_income_categ_id.id

            invoices_to_create.append({
                'move_type': 'out_invoice',
                'partner_id': payment.contract_id.partner_id.id,
                'contract_id': payment.contract_id.id,
                'invoice_date': payment.date_due,
                'payment_reference': payment.name,
                'invoice_line_ids': [(0, 0, {
                    'name': f'Rent for {product.display_name} on {payment.date_due}',
                    'product_id': product.id,
                    'quantity': 1,
                    'price_unit': payment.amount,
                    'account_id': account_id,
                })]
            })

        # Batch create invoices
        invoices = self.env['account.move'].create(invoices_to_create)

        # Link payments to invoices
        invoice_map = {
            inv.payment_reference: inv
            for inv in invoices
        }

        # Update payments in bulk
        for payment in payments:
            if payment.name in invoice_map:
                payment.write({
                    'move_id': invoice_map[payment.name].id,
                    'state': 'invoiced',
                })
        return invoices

    def action_get_invoices(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Invoices',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
        }

    def _sync_contract_history(self):
        ContractLineHistory = self.env['realestate.property.rental.history']

        for contract in self:
            # Delete old history for this contract
            ContractLineHistory.sudo().search([('contract_id', '=', contract.id)]).unlink()

            if contract.is_single_property and contract.property_id:
                ContractLineHistory.create({
                    'contract_id': contract.id,
                    'property_id': contract.property_id.id,
                    'start_date': contract.start_date,
                    'end_date': contract.end_date,
                    'is_multi': False,
                })
            elif contract.is_multi_property:
                for line in contract.line_ids.filtered(lambda l: l.property_id):
                    ContractLineHistory.create({
                        'contract_id': contract.id,
                        'property_id': line.property_id.id,
                        'start_date': line.start_date,
                        'end_date': line.end_date,
                        'contract_line_id': line.id,
                        'is_multi': True,
                    })



class ContractType(models.Model):
    _name = 'contract.type'

    name = fields.Char(string='Name')