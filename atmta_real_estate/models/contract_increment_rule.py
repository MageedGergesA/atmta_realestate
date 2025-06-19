from odoo import fields, models, api


class RealEstateContractIncrementRule(models.Model):
    _name = 'realestate.contract.increment.rule'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Contract Price Increment Rule'
    _order = 'start_month asc'

    start_month = fields.Integer(string="Start After (Months)", required=True, tracking=True)
    increase_type = fields.Selection([
        ('fixed', 'Fixed Amount'),
        ('percent', 'Percentage'),
    ], string="Type", required=True, default='fixed', tracking=True)
    increase_value = fields.Float(string="Value", required=True, tracking=True)
    priority = fields.Integer(string="Priority", default=10)

    name = fields.Char(string="Label", compute="_compute_name", store=True)
    discount = fields.Boolean(string="Is Discount", help="Used to mark this rule as a discount (shown in label only).")
    duration_months = fields.Integer(
        string="Duration (Months)",
        default=0,
        help="How many months this rule remains active. Leave 0 to apply once only."
    )

    @api.depends('increase_value', 'increase_type', 'start_month', 'discount', 'priority')
    def _compute_name(self):
        for rec in self:
            is_discount = rec.discount or rec.increase_value < 0
            direction_label = "Discount" if is_discount else "Increase"

            value = abs(rec.increase_value)
            if rec.increase_type == 'fixed':
                value_str = f"{value:.0f} EGP"
            else:
                value_str = f"{value:.0f}%"

            month_str = f"after month {rec.start_month}" if rec.start_month else "from start"
            duration_str = f" for {rec.duration_months} month(s)" if rec.duration_months else ""
            rec.name = f"{direction_label}: {value_str} after month {rec.start_month}{duration_str} (Priority {rec.priority})"