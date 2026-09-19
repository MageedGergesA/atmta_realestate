"""Lease incentives -- rent-free periods, fit-out, discounts (Phase 12).

The rule this model exists to enforce: **a free period is never represented by
deleting payment lines.** Deleting the line destroys the commercial story --
six months later nobody can answer "why did this tenant pay nothing in March?",
the rent roll under-reports contracted value, and the discount is invisible to
management reporting.

Instead every incentive is a declared record, and the billing engine emits the
obligation at full rent with an explicit discount alongside it. The schedule
stays explainable line by line.

Supported shapes:

* first month free / first 60 days free  -> ``rent_free`` with a date window
* fit-out period                          -> ``fit_out`` (free, and flagged as
  a works period rather than a commercial concession)
* promotional discount                    -> ``percent_discount`` for the whole
  term or a window
* temporary fixed discount                -> ``fixed_discount``
"""

from dateutil.relativedelta import relativedelta

from markupsafe import Markup
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

INCENTIVE_TYPES = [
    ('rent_free', 'Rent-Free Period'),
    ('fit_out', 'Fit-Out Period'),
    ('percent_discount', 'Percentage Discount'),
    ('fixed_discount', 'Fixed Discount'),
]

#: Types that zero the rent entirely for their window.
FULL_RELIEF_TYPES = ('rent_free', 'fit_out')


class ContractIncentive(models.Model):
    _name = 'realestate.contract.incentive'
    _description = 'Lease Incentive'
    _inherit = ['mail.thread']
    _order = 'contract_id, start_date, id'

    name = fields.Char(
        string='Description', required=True,
        help="Shown on the invoice next to the discount, so the tenant sees "
             "what they were granted.",
    )
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
        ondelete='cascade', domain="[('contract_id', '=', contract_id)]",
        help="Leave empty to apply across the whole lease.",
    )

    incentive_type = fields.Selection(
        INCENTIVE_TYPES, string='Type', required=True, default='rent_free',
        tracking=True, index=True,
    )
    start_date = fields.Date(string='From', required=True, tracking=True, index=True)
    end_date = fields.Date(string='Until', required=True, tracking=True, index=True)

    percentage = fields.Float(
        string='Discount (%)', tracking=True,
        help="Used by Percentage Discount.",
    )
    amount = fields.Monetary(
        string='Discount Amount', tracking=True,
        help="Used by Fixed Discount -- deducted per billing period.",
    )

    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False)
    approval_date = fields.Datetime(readonly=True, copy=False)
    reason = fields.Text(
        string='Commercial Reason',
        help="Why the concession was granted. Kept for management reporting "
             "on discounting.",
    )
    active = fields.Boolean(default=True)

    total_value = fields.Monetary(
        string='Value Given Away', compute='_compute_total_value', store=True,
        help="Indicative total concession over the incentive window, at the "
             "rent in force. Lets management see the real cost of discounting.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('incentive_type', 'start_date', 'end_date', 'percentage',
                 'amount', 'contract_id.price')
    def _compute_total_value(self):
        for rec in self:
            if not rec.start_date or not rec.end_date or not rec.contract_id:
                rec.total_value = 0.0
                continue
            months = max(
                (rec.end_date.year - rec.start_date.year) * 12
                + (rec.end_date.month - rec.start_date.month) + 1, 0)
            rent = rec.contract_id._rent_on(rec.start_date)
            if rec.incentive_type in FULL_RELIEF_TYPES:
                rec.total_value = rent * months
            elif rec.incentive_type == 'percent_discount':
                rec.total_value = rent * months * (rec.percentage or 0.0) / 100.0
            else:
                rec.total_value = (rec.amount or 0.0) * months

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('start_date', 'end_date', 'contract_id')
    def _check_window(self):
        for rec in self:
            if rec.start_date > rec.end_date:
                raise ValidationError(_(
                    "Incentive '%s' ends before it starts.", rec.name))
            contract = rec.contract_id
            if contract.start_date and rec.start_date < contract.start_date:
                raise ValidationError(_(
                    "Incentive '%(name)s' starts before the lease (%(start)s).",
                    name=rec.name, start=contract.start_date))
            if contract.end_date and rec.end_date > contract.end_date:
                raise ValidationError(_(
                    "Incentive '%(name)s' ends after the lease (%(end)s).",
                    name=rec.name, end=contract.end_date))

    @api.constrains('incentive_type', 'percentage', 'amount')
    def _check_inputs(self):
        for rec in self:
            if rec.incentive_type == 'percent_discount':
                if not 0 < (rec.percentage or 0.0) <= 100:
                    raise ValidationError(_(
                        "Incentive '%s' needs a discount percentage between 0 "
                        "and 100.", rec.name))
            if rec.incentive_type == 'fixed_discount' and not rec.amount:
                raise ValidationError(_(
                    "Incentive '%s' needs a discount amount.", rec.name))

    @api.constrains('contract_id', 'property_line_id', 'start_date', 'end_date',
                    'incentive_type')
    def _check_no_stacked_full_relief(self):
        """Two overlapping rent-free periods on the same unit would relieve the
        rent twice and produce a negative invoice."""
        for rec in self:
            if rec.incentive_type not in FULL_RELIEF_TYPES:
                continue
            clash = self.search([
                ('id', '!=', rec.id),
                ('contract_id', '=', rec.contract_id.id),
                ('incentive_type', 'in', FULL_RELIEF_TYPES),
                ('active', '=', True),
                ('start_date', '<=', rec.end_date),
                ('end_date', '>=', rec.start_date),
                ('property_line_id', '=', rec.property_line_id.id),
            ], limit=1)
            if clash:
                raise ValidationError(_(
                    "Incentive '%(name)s' overlaps the rent-free period "
                    "'%(other)s' (%(start)s → %(end)s) on the same property.",
                    name=rec.name, other=clash.name,
                    start=clash.start_date, end=clash.end_date))

    # ------------------------------------------------------------------
    # Billing contribution
    # ------------------------------------------------------------------
    def _applies_to(self, period_start, period_end, property_line=None):
        self.ensure_one()
        if not self.active:
            return False
        if self.property_line_id and property_line and self.property_line_id != property_line:
            return False
        return self.start_date <= period_end and self.end_date >= period_start

    def _relief_for_period(self, period_start, period_end, gross_rent,
                           property_line=None):
        """Discount to deduct from ``gross_rent`` for this period.

        Prorated when the incentive covers only part of the period, using the
        company's proration convention -- a "first 45 days free" incentive on
        monthly billing relieves all of month 1 and half of month 2.

        Returns a **positive** number; the caller subtracts it.
        """
        self.ensure_one()
        if not self._applies_to(period_start, period_end, property_line):
            return 0.0
        from . import proration as prorate_lib

        method = self.contract_id.company_id.re_proration_method or 'actual'
        covered = prorate_lib.proration_factor(
            'none' if method == 'none' else method,
            period_start, period_end, self.start_date, self.end_date)

        if self.incentive_type in FULL_RELIEF_TYPES:
            return (gross_rent or 0.0) * covered
        if self.incentive_type == 'percent_discount':
            return (gross_rent or 0.0) * (self.percentage or 0.0) / 100.0 * covered
        return (self.amount or 0.0) * covered

    def action_approve(self):
        """Discounts are a commercial decision -- gate them on Rental Manager."""
        self.ensure_one()
        self.contract_id._require_group('atmta_real_estate.group_rental_manager')
        if not self.reason:
            raise UserError(_(
                "Record the commercial reason before approving incentive '%s'.",
                self.name))
        self.write({
            'approved_by_id': self.env.user.id,
            'approval_date': fields.Datetime.now(),
        })
        self.contract_id.message_post(body=Markup(_(
            "Incentive <b>%(name)s</b> approved (%(value)s). Reason: %(reason)s")) % {
                'name': self.name, 'value': self.total_value, 'reason': self.reason})
        return True


class ContractIncentiveMixin(models.Model):
    _inherit = 'realestate.contract'

    incentive_ids = fields.One2many(
        'realestate.contract.incentive', 'contract_id',
        string='Incentives', copy=True,
    )
    incentive_count = fields.Integer(compute='_compute_incentive_summary')
    total_incentive_value = fields.Monetary(
        string='Total Incentives', compute='_compute_incentive_summary', store=True,
    )

    @api.depends('incentive_ids.total_value', 'incentive_ids.active')
    def _compute_incentive_summary(self):
        for rec in self:
            active = rec.incentive_ids.filtered('active')
            rec.incentive_count = len(active)
            rec.total_incentive_value = sum(active.mapped('total_value'))

    def action_add_rent_free_months(self, months=1, start=None):
        """Convenience for the commonest ask: "first N months free"."""
        self.ensure_one()
        if not self.start_date:
            raise UserError(_("Set the lease start date first."))
        start = start or self.start_date
        end = start + relativedelta(months=int(months)) - relativedelta(days=1)
        return self.env['realestate.contract.incentive'].create({
            'contract_id': self.id,
            'name': _("First %s month(s) rent-free") % months,
            'incentive_type': 'rent_free',
            'start_date': start,
            'end_date': end,
        })

    def action_view_incentives(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Lease Incentives'),
            'res_model': 'realestate.contract.incentive',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }
