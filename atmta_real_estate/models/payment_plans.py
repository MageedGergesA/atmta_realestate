from odoo import fields, models, _, api


class RealEstatePaymentPlan(models.Model):
    _name = 'realestate.payment.plan'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Payment Plan Rule'

    # contract_line_id = fields.Many2one('realestate.contract.line', string="Contract Line", ondelete='cascade')
    start_month = fields.Integer(string="Start After (Months)", required=True, tracking=True)
    unit = fields.Selection([
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
        ('year', 'Years'),
    ], string="Unit", required=True, default='month', tracking=True)
    interval = fields.Integer(string="Every", required=True, default=1, tracking=True)

    name = fields.Char(string="Label", compute="_compute_name", store=True, tracking=True)

    @api.depends('interval', 'unit', 'start_month')
    def _compute_name(self):
        for rec in self:
            unit_display = dict(rec._fields['unit'].selection).get(rec.unit, rec.unit)
            month_str = f"starting from month {rec.start_month}" if rec.start_month else "from start"
            rec.name = f"Pay Every {rec.interval} {unit_display.lower()}{'s' if rec.interval > 1 else ''} {month_str}"