"""Configurable recurring charges (Phase 10).

A lease is rarely "rent and nothing else". Service charge, parking, chilled
water, internet, a furnished premium, insurance, a management fee, municipality
fees -- each has its own amount basis, its own frequency and its own tax
treatment, and each has to appear as its own invoice line so the tenant can see
what they are paying for.

Rules are **declarative**. They describe what is owed; the billing engine
(:mod:`billing_engine`) asks each rule what it is worth for a given period.
Nothing is "applied" in a mutating step, so re-running schedule generation can
never double-charge.

Odoo's product / tax / account machinery does the accounting -- this module
never re-implements tax logic.
"""

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

CHARGE_CATEGORIES = [
    ('rent', 'Rent'),
    ('maintenance', 'Maintenance'),
    ('service_charge', 'Service Charge'),
    ('parking', 'Parking'),
    ('utilities', 'Utilities'),
    ('internet', 'Internet'),
    ('furnished_premium', 'Furnished Premium'),
    ('insurance', 'Insurance'),
    ('management_fee', 'Management Fee'),
    ('municipality', 'Municipality Fees'),
    ('other', 'Other'),
]

CALCULATION_TYPES = [
    ('fixed', 'Fixed Amount'),
    ('percentage', '% of Base Rent'),
    ('per_sqm', 'Per m² of Leased Area'),
    ('meter_consumption', 'Metered Consumption'),
]

FREQUENCIES = [
    ('one_time', 'One Time'),
    ('monthly', 'Monthly'),
    ('quarterly', 'Quarterly'),
    ('semiannual', 'Semi-Annual'),
    ('annual', 'Annual'),
    ('custom', 'Custom Interval'),
]

#: frequency -> months between occurrences. ``one_time`` and ``custom`` are
#: handled separately.
FREQUENCY_MONTHS = {
    'monthly': 1,
    'quarterly': 3,
    'semiannual': 6,
    'annual': 12,
}


class ContractChargeRule(models.Model):
    _name = 'realestate.contract.charge.rule'
    _description = 'Lease Recurring Charge Rule'
    _inherit = ['mail.thread']
    _order = 'contract_id, sequence, id'

    sequence = fields.Integer(default=10)
    name = fields.Char(
        string='Description', required=True,
        help="Appears on the invoice line the tenant sees.",
    )
    active = fields.Boolean(default=True)

    contract_id = fields.Many2one(
        'realestate.contract', string='Lease', required=True,
        ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related='contract_id.currency_id', store=True, readonly=True,
    )
    property_line_id = fields.Many2one(
        'realestate.contract.property.line', string='Specific Property',
        ondelete='cascade', index=True,
        domain="[('contract_id', '=', contract_id)]",
        help="Leave empty to charge across the whole lease. Set it to bill a "
             "charge that belongs to one unit only (e.g. parking).",
    )

    charge_category = fields.Selection(
        CHARGE_CATEGORIES, string='Category', required=True,
        default='service_charge', index=True, tracking=True,
    )

    # ---------------- Amount basis ----------------
    calculation_type = fields.Selection(
        CALCULATION_TYPES, string='Calculation', required=True,
        default='fixed', tracking=True,
    )
    amount = fields.Monetary(
        string='Amount', tracking=True,
        help="Fixed amount per occurrence.",
    )
    percentage = fields.Float(
        string='Percentage (%)', tracking=True,
        help="Percentage of the base rent for the period.",
    )
    rate_per_sqm = fields.Monetary(
        string='Rate / m²', tracking=True,
        help="Multiplied by the leased area for the period.",
    )
    meter_type = fields.Selection(
        [('electricity', 'Electricity'), ('water', 'Water'),
         ('gas', 'Gas'), ('other', 'Other')],
        string='Meter Type',
        help="Which meter's consumption to bill.",
    )
    unit_rate = fields.Monetary(
        string='Rate / Unit', tracking=True,
        help="Price per kWh / m³ of measured consumption.",
    )

    # ---------------- Accounting ----------------
    product_id = fields.Many2one(
        'product.product', string='Product',
        domain="[('type', '=', 'service')]",
        check_company=True,
        help="Drives the income account and default taxes. Strongly "
             "recommended -- without it the invoice line falls back to the "
             "lease's default rent product.",
    )
    tax_ids = fields.Many2many(
        'account.tax', string='Taxes',
        domain="[('type_tax_use', '=', 'sale'), ('company_id', '=', company_id)]",
        check_company=True,
        help="Leave empty to inherit the product's customer taxes. Odoo "
             "computes the tax -- this module never does.",
    )

    # ---------------- Schedule ----------------
    frequency = fields.Selection(
        FREQUENCIES, string='Frequency', required=True, default='monthly',
        tracking=True,
    )
    custom_interval_months = fields.Integer(
        string='Every N Months', default=1,
        help="Used when Frequency is 'Custom Interval'.",
    )
    start_date = fields.Date(
        string='From', tracking=True,
        help="Defaults to the lease start date when empty.",
    )
    end_date = fields.Date(
        string='Until', tracking=True,
        help="Defaults to the lease end date when empty.",
    )
    prorate_partial_periods = fields.Boolean(
        string='Prorate Partial Periods', default=True,
        help="When off, a partial period is charged in full regardless of the "
             "company proration method.",
    )

    notes = fields.Text()

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('calculation_type', 'amount', 'percentage',
                    'rate_per_sqm', 'unit_rate', 'meter_type')
    def _check_calculation_inputs(self):
        """Reject a rule that cannot produce a number.

        A silently-zero charge is worse than an error: it looks configured and
        bills nothing.
        """
        for rec in self:
            kind = rec.calculation_type
            if kind == 'fixed' and not rec.amount:
                raise ValidationError(_(
                    "Charge '%s' is a fixed amount but no amount is set.", rec.name))
            if kind == 'percentage' and not rec.percentage:
                raise ValidationError(_(
                    "Charge '%s' is percentage-based but no percentage is set.",
                    rec.name))
            if kind == 'per_sqm' and not rec.rate_per_sqm:
                raise ValidationError(_(
                    "Charge '%s' is per-m² but no rate is set.", rec.name))
            if kind == 'meter_consumption':
                if not rec.unit_rate:
                    raise ValidationError(_(
                        "Charge '%s' is metered but no unit rate is set.", rec.name))
                if not rec.meter_type:
                    raise ValidationError(_(
                        "Charge '%s' is metered but no meter type is selected.",
                        rec.name))

    @api.constrains('start_date', 'end_date')
    def _check_charge_dates(self):
        for rec in self:
            if rec.start_date and rec.end_date and rec.start_date > rec.end_date:
                raise ValidationError(_(
                    "Charge '%s' ends before it starts.", rec.name))

    @api.constrains('custom_interval_months', 'frequency')
    def _check_custom_interval(self):
        for rec in self:
            if rec.frequency == 'custom' and rec.custom_interval_months < 1:
                raise ValidationError(_(
                    "Charge '%s' uses a custom interval, which must be at "
                    "least 1 month.", rec.name))

    # ------------------------------------------------------------------
    # Schedule helpers
    # ------------------------------------------------------------------
    def _interval_months(self):
        """Months between occurrences, or ``None`` for a one-time charge."""
        self.ensure_one()
        if self.frequency == 'one_time':
            return None
        if self.frequency == 'custom':
            return max(self.custom_interval_months or 1, 1)
        return FREQUENCY_MONTHS.get(self.frequency, 1)

    def _effective_window(self):
        """The rule's active window, defaulted from the lease term."""
        self.ensure_one()
        contract = self.contract_id
        start = self.start_date or contract.start_date
        end = self.end_date or contract.end_date
        return start, end

    def _applies_to_period(self, period_start, period_end):
        """Whether this rule contributes to a billing period at all."""
        self.ensure_one()
        return self._occurrences_in_period(period_start, period_end) > 0

    def _occurrences_in_period(self, period_start, period_end):
        """How many times this rule falls due inside one billing period.

        Not a boolean. When the lease bills quarterly and the charge is
        monthly, the charge is due three times in the period, and answering
        "yes it applies" made the engine bill it once -- a monthly 100 service
        charge came out as 100 on a quarterly invoice instead of 300.

        Bounded by the rule's own window, so a charge that ends mid-period
        stops being counted after it ends.
        """
        self.ensure_one()
        if not self.active:
            return 0
        start, end = self._effective_window()
        if start and start > period_end:
            return 0
        if end and end < period_start:
            return 0
        if self.frequency == 'one_time':
            # A one-time charge lands in the period containing its start date.
            return 1 if (start and period_start <= start <= period_end) else 0
        interval = self._interval_months()
        if not start or not interval:
            return 0
        # Occurrence dates march forward from the rule start; a period bills
        # the rule once for each occurrence that falls inside it.
        cursor = start
        # Fast-forward without looping month by month over long leases.
        months_elapsed = ((period_start.year - start.year) * 12
                          + (period_start.month - start.month))
        if months_elapsed > 0:
            steps = months_elapsed // interval
            cursor = start + relativedelta(months=steps * interval)
        while cursor < period_start:
            cursor += relativedelta(months=interval)
        last = period_end if not end else min(period_end, end)
        occurrences = 0
        while cursor <= last:
            occurrences += 1
            cursor += relativedelta(months=interval)
        return occurrences

    # ------------------------------------------------------------------
    # Amount for a period
    # ------------------------------------------------------------------
    def _amount_for_period(self, period_start, period_end, base_rent,
                           property_line=None):
        """What this rule is worth for one billing period, before proration.

        :param base_rent: rent for the period, used by percentage rules
        :param property_line: the allocation being billed, used by per-m² and
            metered rules. Falls back to the rule's own ``property_line_id``.
        :returns: float amount (never negative)
        """
        self.ensure_one()
        occurrences = self._occurrences_in_period(period_start, period_end)
        if not occurrences:
            return 0.0

        line = property_line or self.property_line_id
        kind = self.calculation_type

        # `fixed` and `per_sqm` are priced per occurrence, so a period holding
        # three of them is worth three. `percentage` and `meter_consumption`
        # are already period quantities -- the rent passed in and the
        # consumption read are both for this period -- so multiplying them by
        # the occurrence count would bill the same money several times.
        if kind == 'fixed':
            return (self.amount or 0.0) * occurrences
        if kind == 'percentage':
            return (base_rent or 0.0) * (self.percentage or 0.0) / 100.0
        if kind == 'per_sqm':
            area = self._area_for(line)
            return (self.rate_per_sqm or 0.0) * area * occurrences
        if kind == 'meter_consumption':
            consumption = self._consumption_for(line, period_start, period_end)
            return (self.unit_rate or 0.0) * consumption
        return 0.0

    def _area_for(self, property_line):
        """Leased area backing a per-m² charge."""
        self.ensure_one()
        if property_line:
            return property_line.area_sqm or 0.0
        return self.contract_id.total_area_sqm or 0.0

    def _consumption_for(self, property_line, period_start, period_end):
        """Measured consumption in the period for the configured meter type.

        Returns ``0.0`` when there is no reading -- deliberately, rather than
        estimating. An invented consumption figure on a tenant invoice is a
        dispute waiting to happen; a zero is visible and can be corrected.
        """
        self.ensure_one()
        properties = (property_line.property_id if property_line
                      else self.contract_id.property_line_ids.mapped('property_id'))
        if not properties:
            return 0.0
        # sudo(): a metered charge must bill the true consumption. Meter
        # readings are operational records a leasing agent may not have
        # read access to, and silently billing 0 because of an access rule
        # would be a wrong invoice rather than a permission error.
        readings = self.env['realestate.property.meter.reading'].sudo().search([
            ('property_id', 'in', properties.ids),
            ('meter_type', '=', self.meter_type),
            ('reading_date', '>=', period_start),
            ('reading_date', '<=', period_end),
        ])
        return sum(readings.mapped('consumption'))

    # ------------------------------------------------------------------
    # Invoice line contribution
    # ------------------------------------------------------------------
    def _invoice_line_vals(self, amount):
        """Invoice line values for this charge.

        Product and taxes come from Odoo; we only supply the label, quantity
        and price so the standard engine can do the rest.
        """
        self.ensure_one()
        product = self.product_id or self.contract_id.company_id.re_rent_product_id
        vals = {
            'name': self.name or dict(CHARGE_CATEGORIES).get(self.charge_category),
            'quantity': 1.0,
            'price_unit': amount,
        }
        if product:
            vals['product_id'] = product.id
        if self.tax_ids:
            vals['tax_ids'] = [(6, 0, self.tax_ids.ids)]
        return vals


class ContractChargeRuleMixin(models.Model):
    _inherit = 'realestate.contract'

    charge_rule_ids = fields.One2many(
        'realestate.contract.charge.rule', 'contract_id',
        string='Recurring Charges', copy=True,
    )
    charge_rule_count = fields.Integer(compute='_compute_charge_rule_count')
    monthly_charges_estimate = fields.Monetary(
        string='Monthly Charges (est.)',
        compute='_compute_monthly_charges_estimate', store=True,
        help="Indicative monthly total of fixed and percentage charges. "
             "Metered charges are excluded because they are not knowable in "
             "advance.",
    )

    @api.depends('charge_rule_ids')
    def _compute_charge_rule_count(self):
        for rec in self:
            rec.charge_rule_count = len(rec.charge_rule_ids)

    @api.depends('charge_rule_ids.amount', 'charge_rule_ids.percentage',
                 'charge_rule_ids.calculation_type', 'charge_rule_ids.frequency',
                 'charge_rule_ids.rate_per_sqm', 'charge_rule_ids.active',
                 'price', 'total_area_sqm')
    def _compute_monthly_charges_estimate(self):
        for rec in self:
            total = 0.0
            for rule in rec.charge_rule_ids.filtered('active'):
                interval = rule._interval_months()
                if not interval:
                    continue  # one-time charges are not a monthly run rate
                if rule.calculation_type == 'fixed':
                    value = rule.amount or 0.0
                elif rule.calculation_type == 'percentage':
                    value = (rec.price or 0.0) * (rule.percentage or 0.0) / 100.0
                elif rule.calculation_type == 'per_sqm':
                    value = (rule.rate_per_sqm or 0.0) * (rec.total_area_sqm or 0.0)
                else:
                    continue  # metered: unknowable ahead of time
                total += value / interval
            rec.monthly_charges_estimate = total

    def action_view_charge_rules(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Recurring Charges'),
            'res_model': 'realestate.contract.charge.rule',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }
