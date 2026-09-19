from collections import defaultdict

from odoo import api, fields, models


class ResPartnerInherit(models.Model):
    _inherit = 'res.partner'

    id_type = fields.Selection([('national_id','National ID'),('passport','Passport'),('driving_licence','Driving Licence')], string='ID Type', default='national_id')
    id_number = fields.Char(string='ID Number')
    is_lessor = fields.Boolean(string='IS Lessor')
    organization_type_id = fields.Many2one('organization.type', string='Organization Type')
    unified_number = fields.Char(string='Unified Number')
    cr_number = fields.Char(string='CR Number')
    cr_date = fields.Date(string='CR Date')
    issued_by = fields.Char(string='Issued By')

    # _sql_constraints = [
    #     ('unique_id_number', 'unique(id_number)', 'ID Number must be unique.'),
    # ]

    # ------------------------------------------------------------------
    # Tenants (RENTAL_UX_SPEC.md §9): a tenant is an ordinary contact that
    # holds a lease. No tenant model -- only the inverse of the lease's tenant.
    # ------------------------------------------------------------------
    rental_lease_ids = fields.One2many(
        'realestate.contract', 'partner_id', string='Leases')
    is_rental_tenant = fields.Boolean(
        string='Rental Tenant', compute='_compute_is_rental_tenant',
        store=True, index=True,
        help="Whether this contact is the tenant of at least one lease.")
    rental_current_lease_id = fields.Many2one(
        'realestate.contract', string='Current Lease',
        compute='_compute_rental_summary',
        help="The most recent live lease, or else the most recent lease awaiting signature.")
    rental_balance_due = fields.Monetary(
        string='Balance Due', compute='_compute_rental_summary',
        currency_field='rental_currency_id',
        help="Outstanding balance of this tenant's invoiced billing obligations.")
    rental_currency_id = fields.Many2one(
        'res.currency', compute='_compute_rental_summary')
    rental_outstanding_obligation_ids = fields.Many2many(
        'realestate.contract.payment', string='Outstanding Billing Obligations',
        compute='_compute_rental_summary')
    rental_deposit_ids = fields.Many2many(
        'realestate.contract.deposit', string='Security Deposits',
        compute='_compute_rental_summary')

    @api.depends('rental_lease_ids')
    def _compute_is_rental_tenant(self):
        for partner in self:
            partner.is_rental_tenant = bool(partner.rental_lease_ids)

    def _compute_rental_summary(self):
        """One query per related model for the whole recordset, so the Tenants
        list costs the same for one row as for eighty."""
        Obligation = self.env['realestate.contract.payment']
        Deposit = self.env['realestate.contract.deposit']
        partner_ids = [partner.id for partner in self if partner.id]
        obligations = defaultdict(lambda: Obligation)
        deposits = defaultdict(lambda: Deposit)
        if partner_ids:
            for obligation in Obligation.search([
                    ('contract_id.partner_id', 'in', partner_ids),
                    ('state', '=', 'invoiced'),
                    ('amount_residual', '>', 0)], order='date_due'):
                obligations[obligation.contract_id.partner_id.id] |= obligation
            for deposit in Deposit.search([('partner_id', 'in', partner_ids)]):
                deposits[deposit.partner_id.id] |= deposit
        company = self.env.company
        currency = company.currency_id
        today = fields.Date.context_today(self)
        for partner in self:
            leases = partner.rental_lease_ids.sorted('start_date', reverse=True)
            current = (leases.filtered(lambda lease: lease.lifecycle_state in ('active', 'notice'))
                       or leases.filtered(lambda lease: lease.lifecycle_state == 'pending_signature'))
            partner.rental_current_lease_id = current[:1]
            partner.rental_outstanding_obligation_ids = obligations[partner.id]
            # Shown in the company currency, so each lease's outstanding amount
            # is converted at today's rate first; adding amounts in different
            # currencies gave a meaningless figure.
            partner.rental_balance_due = sum(
                obligation.currency_id._convert(
                    obligation.amount_residual, currency, company, today)
                if obligation.currency_id and obligation.currency_id != currency
                else obligation.amount_residual
                for obligation in obligations[partner.id])
            partner.rental_deposit_ids = deposits[partner.id]
            partner.rental_currency_id = currency


class OrganizationType(models.Model):
    _name = 'organization.type'

    name = fields.Char(string='Name')
