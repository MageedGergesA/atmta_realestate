from odoo import api, fields, models


class RealEstatePropertyRental(models.Model):
    """Rental-specific extensions to the base property model."""
    _inherit = 'realestate.property'

    contract_history_ids = fields.One2many(
        'realestate.contract.line', 'property_id', string='Contract History',
    )
    rental_history_ids = fields.One2many(
        'realestate.property.rental.history', 'property_id', string='Rental History',
    )
    single_contract_ids = fields.One2many(
        'realestate.contract', 'property_id', string='Single-unit Contracts',
    )

    for_rent = fields.Boolean(
        string='For Rent',
        help='Mark this unit as available to rent. Used by listing/search filters.',
    )
    rental_status = fields.Selection([
        ('not_rented', 'Not Rented'),
        ('for_rent', 'For Rent'),
        ('rented', 'Rented'),
    ], string='Rental Status', compute='_compute_rental_status', store=True,
        help='Where this unit stands in the rental pipeline.')

    @api.depends('contract_history_ids.state',
                 'single_contract_ids.state', 'for_rent')
    def _compute_rental_status(self):
        ACTIVE = ('confirmed', 'invoiced', 'active')
        for rec in self:
            multi_active = rec.contract_history_ids.filtered(lambda l: l.state in ACTIVE)
            single_active = rec.single_contract_ids.filtered(lambda c: c.state in ACTIVE)
            if multi_active or single_active:
                rec.rental_status = 'rented'
            elif rec.for_rent:
                rec.rental_status = 'for_rent'
            else:
                rec.rental_status = 'not_rented'

    rent_count = fields.Integer(
        string="Times Rented",
        compute="_compute_rent_statistics", store=True, compute_sudo=True,
    )
    total_rent_months = fields.Float(
        string="Total Rental Duration (Months)",
        compute="_compute_rent_statistics", store=True, compute_sudo=True,
    )
    actual_rent_months = fields.Integer(
        string="Actual Rent Months",
        compute="_compute_rent_statistics", store=True, compute_sudo=True,
    )
    revenue_collected = fields.Monetary(
        string="Revenue Collected",
        compute="_compute_rent_statistics", store=True, compute_sudo=True,
    )
    revenue_expected = fields.Monetary(
        string="Total Expected Revenue",
        compute="_compute_rent_statistics", store=True, compute_sudo=True,
    )
    total_amount_due = fields.Monetary(
        string="Total Amount Due",
        compute="_compute_rent_statistics", store=True, compute_sudo=True,
    )

    @api.depends(
        'rental_history_ids.start_date',
        'rental_history_ids.end_date',
        'rental_history_ids.contract_id.contract_payment_ids.state',
        'rental_history_ids.contract_id.contract_payment_ids.amount',
        'rental_history_ids.contract_id.contract_payment_ids.date_due',
    )
    def _compute_rent_statistics(self):
        for prop in self:
            rent_count = 0
            total_months = 0
            actual_months = 0
            revenue_collected = 0.0
            revenue_expected = 0.0

            history_lines = prop.rental_history_ids.filtered(
                lambda l: l.contract_id.state not in ('draft', 'terminated')
            )

            for line in history_lines:
                rent_count += 1

                if line.start_date and line.end_date:
                    months = (line.end_date.year - line.start_date.year) * 12 + (
                        line.end_date.month - line.start_date.month
                    )
                    if line.end_date.day >= line.start_date.day:
                        months += 1
                    total_months += max(0, months)

                payments = line.contract_id.contract_payment_ids
                paid_payments = payments.filtered(lambda p: p.state == 'paid')
                paid_dates = paid_payments.mapped('date_due')
                actual_months += len({(d.year, d.month) for d in paid_dates if d})

                revenue_collected += sum(paid_payments.mapped('amount'))
                revenue_expected += sum(payments.mapped('amount'))

            prop.rent_count = rent_count
            prop.total_rent_months = total_months
            prop.actual_rent_months = actual_months
            prop.revenue_collected = revenue_collected
            prop.revenue_expected = revenue_expected
            prop.total_amount_due = revenue_expected - revenue_collected
