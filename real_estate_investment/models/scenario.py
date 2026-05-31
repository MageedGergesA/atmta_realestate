from odoo import api, fields, models
from .feasibility import _calc_npv, _calc_irr


class Scenario(models.Model):
    _name = 'realestate.investment.scenario'
    _description = 'Investment Scenario'
    _order = 'feasibility_id, sequence, name'

    feasibility_id = fields.Many2one('realestate.investment.feasibility', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, default='Scenario')
    scenario_type = fields.Selection([
        ('best', 'Best Case'),
        ('expected', 'Expected Case'),
        ('worst', 'Worst Case'),
        ('custom', 'Custom'),
    ], default='custom', required=True)

    # Sensitivity factors applied to base cash flows
    inflow_factor = fields.Float(string='Inflow Factor', default=1.0,
                                  help='Multiplier on all inflows (e.g. 0.85 = 15% slower sales).')
    outflow_factor = fields.Float(string='Outflow Factor', default=1.0,
                                   help='Multiplier on all outflows (e.g. 1.15 = 15% cost overrun).')
    discount_rate_override = fields.Float(string='Discount Rate Override (%)',
                                           help='Leave 0 to inherit the base feasibility rate.')

    # Computed
    npv = fields.Monetary(compute='_compute_metrics', store=True)
    irr = fields.Float(string='IRR (%)', compute='_compute_metrics', store=True)
    is_positive = fields.Boolean(compute='_compute_metrics', store=True)
    currency_id = fields.Many2one(related='feasibility_id.currency_id', store=True, readonly=True)

    @api.depends('feasibility_id.cash_flow_ids.inflow', 'feasibility_id.cash_flow_ids.outflow',
                 'feasibility_id.cash_flow_ids.period_index',
                 'inflow_factor', 'outflow_factor', 'discount_rate_override',
                 'feasibility_id.discount_rate')
    def _compute_metrics(self):
        for rec in self:
            flows_by_index = {}
            for cf in rec.feasibility_id.cash_flow_ids:
                adj = (cf.inflow * rec.inflow_factor) - (cf.outflow * rec.outflow_factor)
                flows_by_index[cf.period_index] = flows_by_index.get(cf.period_index, 0.0) + adj
            if not flows_by_index:
                rec.npv = 0.0
                rec.irr = 0.0
                rec.is_positive = False
                continue
            max_index = max(flows_by_index.keys())
            flows = [flows_by_index.get(i, 0.0) for i in range(max_index + 1)]
            rate = (rec.discount_rate_override / 100.0) if rec.discount_rate_override \
                else ((rec.feasibility_id.discount_rate or 0.0) / 100.0)
            rec.npv = _calc_npv(rate, flows)
            rec.irr = _calc_irr(flows) * 100.0
            rec.is_positive = rec.npv > 0
