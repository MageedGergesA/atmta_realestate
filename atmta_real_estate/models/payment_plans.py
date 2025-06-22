from odoo import fields, models, _, api


class RealEstatePaymentPlan(models.Model):
    _name = 'realestate.payment.plan'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Payment Plan Rule'

    # contract_line_id = fields.Many2one('realestate.contract.line', string="Contract Line", ondelete='cascade')
    start_after = fields.Integer(string="Start After", required=True, default=0)
    start_after_unit = fields.Selection([
        ('day', 'Day(s)'),
        ('week', 'Week(s)'),
        ('month', 'Month(s)'),
        ('year', 'Year(s)'),
    ], string="Start After Unit", default='month', required=True)
    unit = fields.Selection([
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
        ('year', 'Years'),
    ], string="Unit", required=True, default='month', tracking=True)
    interval = fields.Integer(string="Every", required=True, default=1, tracking=True)

    name = fields.Char(string="Label", compute="_compute_name", store=True, tracking=True)

    @api.depends('interval', 'unit', 'start_after', 'start_after_unit')
    def _compute_name(self):
        for rec in self:
            interval_unit = dict(self._fields['unit'].selection).get(rec.unit, rec.unit)
            start_unit = dict(self._fields['start_after_unit'].selection).get(rec.start_after_unit,
                                                                              rec.start_after_unit)
            stop_str = ""  # Optional, if you're later adding a stop_after field
            rec.name = f"Pay every {rec.interval} {interval_unit.lower()}{'s' if rec.interval > 1 else ''}, " \
                       f"starting after {rec.start_after} {start_unit.lower()}{'s' if rec.start_after != 1 else ''}{stop_str}"
