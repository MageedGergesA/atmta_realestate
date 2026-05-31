from odoo import api, fields, models


class CashFlow(models.Model):
    _name = 'realestate.investment.cash.flow'
    _description = 'Investment Cash Flow Entry'
    _order = 'feasibility_id, period_index'

    feasibility_id = fields.Many2one('realestate.investment.feasibility', required=True, ondelete='cascade')
    period_index = fields.Integer(string='Period (year offset)', required=True,
                                   help='0 = base year, 1 = base year + 1, etc.')
    year = fields.Integer(compute='_compute_year', store=True)
    period_label = fields.Char(compute='_compute_year', store=True)
    description = fields.Char(string='Note')

    inflow = fields.Monetary(string='Inflow (revenue)')
    outflow = fields.Monetary(string='Outflow (cost)')
    net_flow = fields.Monetary(string='Net', compute='_compute_net', store=True)
    discounted_flow = fields.Monetary(string='Discounted', compute='_compute_net', store=True)

    currency_id = fields.Many2one(related='feasibility_id.currency_id', store=True, readonly=True)

    @api.depends('feasibility_id.base_year', 'period_index')
    def _compute_year(self):
        for rec in self:
            base = rec.feasibility_id.base_year or 0
            rec.year = base + rec.period_index
            rec.period_label = f'Year {rec.period_index} ({base + rec.period_index})' if base else f'Year {rec.period_index}'

    @api.depends('inflow', 'outflow', 'period_index', 'feasibility_id.discount_rate')
    def _compute_net(self):
        for rec in self:
            rec.net_flow = (rec.inflow or 0.0) - (rec.outflow or 0.0)
            rate = (rec.feasibility_id.discount_rate or 0.0) / 100.0
            try:
                rec.discounted_flow = rec.net_flow / ((1 + rate) ** rec.period_index)
            except (ZeroDivisionError, OverflowError):
                rec.discounted_flow = 0.0
