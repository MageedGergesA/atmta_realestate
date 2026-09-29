from odoo import models, fields, _, api
from odoo.exceptions import UserError, ValidationError

from .lease_states import LEGACY_TO_LIFECYCLE
from dateutil.relativedelta import relativedelta
import datetime


class RealEstateContract(models.Model):
    _name = 'realestate.contract'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Lease'

    name = fields.Char(string="Lease Number", required=True, copy=False, readonly=False,
                       index='trigram',
                       default=lambda self: _('New'))
    partner_id = fields.Many2one('res.partner', string="Tenant", required=True, tracking=True)
    start_date = fields.Date(string="Start Date", required=True, tracking=True)
    end_date = fields.Date(string="End Date", required=True, tracking=True)
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
    main_contract_no = fields.Char(string='Main Lease No')
    country_id = fields.Many2one(related='partner_id.country_id')
    contract_type = fields.Many2one('contract.type',string='Lease Type') #
    contract_sealing_location_id = fields.Many2one(comodel_name='res.country.state', string='Sealing Location', domain="[('country_id', '=', country_id)]")
    contract_sealing_date = fields.Date(string='Sealing Date')
    contract_no = fields.Char(string='Lease No')
    lessor_rep_id = fields.Many2one('res.partner', string='Lessor Representative')
    lessor_id = fields.Many2one('res.partner', string='Lessor')
    # fields from the tenancy contract
    issuer = fields.Char(string="Issuer")
    title_need_no = fields.Char(string='Title Need No')
    place_of_issue = fields.Char(string='Place Of Issue')
    issue_Date = fields.Date(string='Issue Date')
    # fields from the tenancy contract
    use_manual_payment = fields.Boolean(string='Generate Payment Schedule Lines')
    payment_count = fields.Integer(compute='get_payment_count', default=0)
    move_ids = fields.One2many('account.move', 'contract_id', string='Invoices')
    invoice_count = fields.Integer(compute='get_invoice_count', default=0)
    last_generated = fields.Datetime(string="Last Payment Generation", readonly=True)
    contract_payment_ids = fields.One2many('realestate.contract.payment', 'contract_id', string="Payments")
    sale_order_id = fields.Many2one('sale.order', string='Sale Order', readonly=True, copy=False)
    invoice_id = fields.Many2one('account.move', string='Rent Invoice', readonly=True, copy=False)
    payment_term_id = fields.Many2one(
        'account.payment.term', string='Payment Terms', readonly=True, copy=False,
        help="Per-contract payment term derived from the rent schedule.")
    attachment_ids = fields.Many2many('ir.attachment', 'contract_attachment_rel', 'contract_id',
                                      'attachment_contract_id', 'Attachments',
                                      help="You may attach files to this template, to be added to all "
                                           "emails created from this template")
    is_renewed = fields.Boolean(string='Renewal Lease')
    old_contract_id = fields.Many2one('realestate.contract', string='Renews Lease')
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

    # Single-unit lease: the unit, rent and dates live on the lease and mirror into one allocation.
    is_single_property = fields.Boolean(string='Single-Unit Lease',
                                        default=lambda self: self.env['ir.config_parameter'].sudo().get_param(
                                            'atmta_real_estate.single_property_contract') == 'True')
    property_id = fields.Many2one('realestate.property', string="Property",
                                  domain="[('is_leasable', '=', True)]")
    property_type_id = fields.Many2one(related='property_id.property_type_id', string='Property Type', store=True)
    price = fields.Float(string="Base Rent")
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

    # Multi-unit lease: units are entered as allocations on the Properties tab.
    is_multi_property = fields.Boolean(string='Several Units',
                                       default=lambda self: self.env['ir.config_parameter'].sudo().get_param(
                                           'atmta_real_estate.multi_property_contract') == 'True')
    _sql_constraints = [('contract_name_unique', 'unique(name)', 'Contract name already exists')]

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

    @api.onchange('is_multi_property')
    def _onchange_is_multi(self):
        for rec in self:
            if rec.is_multi_property:
                rec.is_single_property = False
                rec.discount_rule_ids = False
                rec.increment_rule_ids = False
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
        """Retired with the legacy payment schedule it generated."""
        return self.action_generate_payment_schedule()

    def action_reset_to_draft(self):
        # Reopening is a Rental Manager decision in the lifecycle engine. The
        # legacy button let any user do it, from any state.
        self.action_reopen_draft()

    def action_confirm(self):
        """Legacy "Confirm Contract", routed through the lifecycle rules.

        It used to move a proposal, or a lease awaiting approval, straight to
        signature -- skipping the Rental Manager approval and the self-approval
        rule. Now a lease awaiting approval is approved through
        ``action_approve_lease``; a proposal goes to approval when the company
        requires it, and otherwise to signature after the same role and data
        checks the lifecycle buttons apply.
        """
        self.ensure_one()
        if self.state != 'ready':
            raise UserError("You must generate payment lines before confirming the contract.")
        if self.lifecycle_state == 'pending_approval':
            self.action_approve_lease()
        elif self.company_id.sudo().re_require_lease_approval:
            self.action_submit_for_approval()
        else:
            self._require_group('atmta_real_estate.group_rental_agent')
            self._validate_ready_for_proposal()
            self._do_transition('pending_signature')

    def action_generate_invoices(self):
        """One sale order + one invoice for the whole lease, split into the rent
        schedule via a per-contract payment term (increments preserved)."""
        self.ensure_one()
        if self.state != 'confirmed':
            raise UserError("You must confirm the contract before generating invoices.")
        if self.invoice_id:
            raise UserError("The rent invoice has already been generated.")
        # One billing path per lease. A lease billed per period -- by the
        # engine, or with any obligation already invoiced -- must not also get
        # one invoice for its whole term: that bills the same rent twice.
        if self.use_billing_engine:
            raise UserError(_(
                "Lease '%s' is billed per period by the billing engine. Use "
                "Invoice Due instead of one invoice for the whole term.",
                self.display_name))
        if self.contract_payment_ids.filtered('move_id'):
            raise UserError(_(
                "Lease '%s' already has billing obligations invoiced per period. "
                "Invoicing its whole term now would bill that rent twice.",
                self.display_name))
        payments = self.contract_payment_ids.sorted(lambda p: p.date_due or fields.Date.today())
        if not payments:
            raise UserError("Generate the payment schedule first.")
        total = sum(payments.mapped('amount'))
        if total <= 0:
            raise UserError("The scheduled rent total must be positive.")
        line_vals = self._rent_so_line_vals(total)
        if not line_vals:
            raise UserError("The unit has no linked product to invoice.")
        term = self._build_rent_payment_term(payments, total)
        order = self.env['sale.order']._create_re_bridge_order(
            partner=self.partner_id, origin=self.name, source=self, line_vals=line_vals)
        if not order:
            return
        order.payment_term_id = term.id
        self.sale_order_id = order.id
        self.payment_term_id = term.id
        invoices = order._create_invoices()
        if invoices:
            invoices.write({'invoice_payment_term_id': term.id, 'contract_id': self.id})
            self.env['realestate.account.tools'].post_moves(invoices)
            self.invoice_id = invoices[:1].id
        self._do_transition(LEGACY_TO_LIFECYCLE['invoiced'])

    def _build_rent_payment_term(self, payments, total):
        """Turn the dated rent schedule into a per-contract payment term:
        one percent line per scheduled payment, at its day offset."""
        start = self.start_date
        cmds = []
        allocated = 0.0
        for p in payments:
            pct = round((p.amount / total) * 100.0, 6) if total else 0.0
            days = (p.date_due - start).days if (p.date_due and start) else 0
            cmds.append((0, 0, {
                'value': 'percent', 'value_amount': pct,
                'delay_type': 'days_after', 'nb_days': max(days, 0),
            }))
            allocated += pct
        if cmds:
            cmds[-1][2]['value_amount'] = round(cmds[-1][2]['value_amount'] + (100.0 - allocated), 6)
        return self.env['account.payment.term'].create({
            'name': _('Rent schedule — %s') % self.name,
            're_is_realestate': True,
            'line_ids': cmds,
        })

    def _rent_so_line_vals(self, total):
        """SO/invoice lines: one per property (multi) or the unit (single)."""
        if self.is_multi_property:
            by_prop = {}
            for p in self.contract_payment_ids:
                if p.property_id:
                    by_prop[p.property_id] = by_prop.get(p.property_id, 0.0) + p.amount
            return [{
                'product_id': prop.product_variant_id.id,
                'name': _('Rent — %s') % prop.display_name,
                'product_uom_qty': 1, 'price_unit': amt,
            } for prop, amt in by_prop.items() if prop.product_variant_id]
        prop = self.property_id
        if not prop or not prop.product_variant_id:
            return []
        return [{
            'product_id': prop.product_variant_id.id,
            'name': _('Rent — %s') % prop.display_name,
            'product_uom_qty': 1, 'price_unit': total,
        }]

    def action_activate(self):
        for contract in self:
            if contract.state != 'invoiced':
                raise UserError("You must generate invoices before activating the contract.")
            # The lifecycle action, not a raw transition: activation requires a
            # signed lease with at least one unit, and the Property Manager role.
            contract.action_activate_lease()
            # Occupancy is no longer written here. Since the Phase 2 status
            # split it is COMPUTED from the lease allocations, so activating a
            # lease makes its properties occupied automatically -- and, unlike
            # the old direct write, it cannot clobber a maintenance block or a
            # sales-side reservation held by another module.

    def action_terminate(self):
        """Legacy "Terminate", routed through the termination process.

        A live lease (active or on notice) is terminated through a termination
        record, so notice, settlement, credit notes, the deposit and the
        move-out are handled; this opens that record instead of ending the lease
        on the spot. A lease that never went live has nothing to settle, and
        ending it is a Rental Manager decision.
        """
        self.ensure_one()
        if self.state not in ['confirmed', 'invoiced', 'active']:
            raise UserError("Only confirmed, invoiced, or active contracts can be terminated.")
        if self.lifecycle_state in ('active', 'notice'):
            return self.action_start_termination()
        self._require_group('atmta_real_estate.group_rental_manager')
        # Cancel the rent invoice if it's still a draft.
        if self.invoice_id and self.invoice_id.state == 'draft':
            self.invoice_id.button_cancel()
        self._do_transition(LEGACY_TO_LIFECYCLE['terminated'])
        # The unit is NOT marked available here. Occupancy follows the
        # allocations automatically, and whether the unit can be re-let is
        # the unit turn's decision -- a vacated unit that still needs
        # cleaning or repair must not go back on the market.

    def check_contract_expiry(self):
        """Compatibility entry point for the retired legacy expiry job.

        The legacy scheduled action ended every lease whose legacy ``state``
        read ``active`` once its end date passed. That included leases on
        notice, leases with arrears and leases with a termination in progress,
        and because it ran daily beside ``_cron_expire_leases`` it overrode
        every safeguard that job applies.

        There is now one expiry authority. The legacy cron record is gone; this
        method stays so that any server action or script still calling it gets
        the safeguarded behaviour rather than an error.
        """
        return self._cron_expire_leases()

    @api.depends('contract_payment_ids.amount_due',
                 'contract_payment_ids.amount_paid',
                 'contract_payment_ids.amount_residual',
                 'contract_payment_ids.state',
                 'contract_payment_ids.payment_state',
                 'invoice_id.amount_total', 'invoice_id.amount_residual',
                 'invoice_id.payment_state', 'invoice_id.state')
    def _compute_totals(self):
        for contract in self:
            inv = contract.invoice_id
            if inv and inv.state == 'posted':
                # Once invoiced, track the actual invoice (tax-inclusive) so
                # scheduled / paid / balance stay on the same basis.
                contract.total_scheduled = inv.amount_total
                contract.total_paid = inv.amount_total - inv.amount_residual
                contract.balance_due = inv.amount_residual
            else:
                # Billed per period: each obligation already reads its money
                # from its own invoice (tax-inclusive once posted, a forecast
                # before). Summing base rent here ignored every payment, so a
                # lease that had been paid showed nothing paid and its whole
                # term still due. Cancelled obligations, and invoices reversed
                # by a credit note (a terminated period), are neither owed nor
                # paid.
                obligations = contract.contract_payment_ids.filtered(
                    lambda p: p.state != 'cancelled'
                    and p.payment_state != 'reversed')
                contract.total_scheduled = sum(obligations.mapped('amount_due'))
                contract.total_paid = sum(obligations.mapped('amount_paid'))
                contract.balance_due = sum(obligations.mapped('amount_residual'))

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
            'name': _('Billing Obligations'),
            'res_model': 'realestate.contract.payment',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {
                'default_contract_id': self.id,
            }, }

    def action_open_report_wizard(self):
        return {
            'name': _('Lease Report'),
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.contracts.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_id': self.id},
        }

    def action_generate_payment_schedule(self):
        """Retired legacy payment-schedule generator.

        It built rows from Rental's own payment plans, which no longer exist:
        ``realestate.payment.plan`` belongs to Development & Sales. Billing
        schedules are generated by the billing engine.
        """
        raise UserError(_(
            "The legacy payment schedule has been retired. Turn on "
            "'Use Advanced Billing' on the lease and use Generate Billing "
            "Schedule."))

    def action_create_invoices(self):
        """Retired legacy per-payment invoicing.

        It invoiced every uninvoiced payment of the lease at once, including
        future periods, without the lease's company or currency, and without
        checking whether the lease was already billed another way. Billing
        obligations are invoiced through ``action_invoice_due_obligations``.
        """
        raise UserError(_(
            "Per-payment invoicing has been retired. Invoice billing obligations "
            "from the lease with Invoice Due."))

    def action_get_invoices(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Invoices',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
        }

    def action_view_sale_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': self.sale_order_id.id,
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
                # Units are entered as allocations.
                for allocation in contract.property_line_ids:
                    ContractLineHistory.create({
                        'contract_id': contract.id,
                        'property_id': allocation.property_id.id,
                        'start_date': allocation.start_date,
                        'end_date': allocation.end_date,
                        'is_multi': True,
                    })



class ContractType(models.Model):
    _name = 'contract.type'

    name = fields.Char(string='Name')