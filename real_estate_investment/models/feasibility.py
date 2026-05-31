from odoo import _, api, fields, models


def _calc_npv(rate, flows):
    """NPV given a discount rate and a list of net cash flows indexed by period."""
    if rate is None:
        rate = 0.0
    try:
        return sum(cf / ((1.0 + rate) ** t) for t, cf in enumerate(flows))
    except (ZeroDivisionError, OverflowError):
        return 0.0


def _calc_irr(flows, max_iter=200, tol=1e-7):
    """IRR via bisection. Returns the rate (as a fraction, e.g. 0.12 = 12%)."""
    if not flows or all(abs(f) < 1e-9 for f in flows):
        return 0.0
    # Need at least one sign change
    has_pos = any(f > 0 for f in flows)
    has_neg = any(f < 0 for f in flows)
    if not (has_pos and has_neg):
        return 0.0

    low, high = -0.999, 10.0
    f_low = _calc_npv(low, flows)
    f_high = _calc_npv(high, flows)
    # Expand bracket if needed
    expand_iter = 0
    while f_low * f_high > 0 and expand_iter < 50:
        high *= 2
        f_high = _calc_npv(high, flows)
        expand_iter += 1
    if f_low * f_high > 0:
        return 0.0

    for _ in range(max_iter):
        mid = (low + high) / 2
        f_mid = _calc_npv(mid, flows)
        if abs(f_mid) < tol:
            return mid
        if f_low * f_mid < 0:
            high = mid
            f_high = f_mid
        else:
            low = mid
            f_low = f_mid
    return (low + high) / 2


def _calc_payback(flows):
    """Payback period in periods (fractional). Returns None if never recovered."""
    cumulative = 0.0
    for t, cf in enumerate(flows):
        prev = cumulative
        cumulative += cf
        if cumulative >= 0 and t > 0:
            # Linear interpolation within the period
            if cf:
                return (t - 1) + (-prev / cf)
            return float(t)
    return None


class Feasibility(models.Model):
    _name = 'realestate.investment.feasibility'
    _description = 'Investment Feasibility Study'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(required=True, copy=False, default=lambda self: _('New'))
    project_id = fields.Many2one('realestate.project', string='Project', tracking=True,
                                  help='Link to a project, or leave empty for a hypothetical study.')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('archived', 'Archived'),
    ], default='draft', required=True, tracking=True)

    description = fields.Html()
    analysis_date = fields.Date(default=fields.Date.context_today, required=True)

    discount_rate = fields.Float(string='Discount Rate (%)', default=10.0, tracking=True)
    analysis_period_years = fields.Integer(string='Period (years)', default=5, required=True)
    base_year = fields.Integer(default=lambda self: fields.Date.today().year, required=True)

    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    cash_flow_ids = fields.One2many('realestate.investment.cash.flow', 'feasibility_id',
                                     string='Cash Flows')
    scenario_ids = fields.One2many('realestate.investment.scenario', 'feasibility_id',
                                    string='Scenarios')

    # Computed financial metrics
    total_inflow = fields.Monetary(compute='_compute_metrics', store=True)
    total_outflow = fields.Monetary(compute='_compute_metrics', store=True)
    net_undiscounted = fields.Monetary(compute='_compute_metrics', store=True)
    npv = fields.Monetary(string='NPV', compute='_compute_metrics', store=True)
    irr = fields.Float(string='IRR (%)', compute='_compute_metrics', store=True,
                       help='Internal Rate of Return (decimal e.g. 0.12 = 12%)')
    payback_period = fields.Float(string='Payback (years)', compute='_compute_metrics', store=True)
    is_positive = fields.Boolean(string='Positive NPV', compute='_compute_metrics', store=True)

    notes = fields.Html()

    @api.depends('cash_flow_ids.inflow', 'cash_flow_ids.outflow', 'cash_flow_ids.period_index',
                 'discount_rate', 'analysis_period_years')
    def _compute_metrics(self):
        for rec in self:
            flows_by_index = {}
            for cf in rec.cash_flow_ids:
                flows_by_index[cf.period_index] = flows_by_index.get(cf.period_index, 0.0) + (cf.inflow - cf.outflow)
            if not flows_by_index:
                rec.total_inflow = 0.0
                rec.total_outflow = 0.0
                rec.net_undiscounted = 0.0
                rec.npv = 0.0
                rec.irr = 0.0
                rec.payback_period = 0.0
                rec.is_positive = False
                continue
            max_index = max(flows_by_index.keys())
            flows = [flows_by_index.get(i, 0.0) for i in range(max_index + 1)]
            rec.total_inflow = sum(cf.inflow for cf in rec.cash_flow_ids)
            rec.total_outflow = sum(cf.outflow for cf in rec.cash_flow_ids)
            rec.net_undiscounted = rec.total_inflow - rec.total_outflow
            rec.npv = _calc_npv(rec.discount_rate / 100.0, flows)
            rec.irr = _calc_irr(flows) * 100.0
            payback = _calc_payback(flows)
            rec.payback_period = payback if payback is not None else 0.0
            rec.is_positive = rec.npv > 0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.investment.feasibility') or _('New')
        return super().create(vals_list)

    def action_generate_default_flows(self):
        """Pre-populate empty cash-flow rows for each year in the analysis period."""
        for rec in self:
            existing = {cf.period_index for cf in rec.cash_flow_ids}
            new = []
            for i in range(rec.analysis_period_years + 1):
                if i in existing:
                    continue
                new.append((0, 0, {
                    'period_index': i,
                    'inflow': 0.0,
                    'outflow': 0.0,
                }))
            if new:
                rec.cash_flow_ids = new

    def action_pull_project_estimates(self):
        """Seed cash flows from the linked project: budget as Year 0 outflow,
        expected revenue split evenly across remaining years."""
        for rec in self:
            if not rec.project_id:
                continue
            rec.cash_flow_ids.unlink()
            budget = rec.project_id.expected_budget
            revenue = rec.project_id.expected_revenue
            years = max(rec.analysis_period_years, 1)
            per_year_revenue = revenue / years if years else 0
            lines = [(0, 0, {
                'period_index': 0,
                'inflow': 0.0,
                'outflow': budget,
                'description': 'Initial investment (project budget)',
            })]
            for i in range(1, years + 1):
                lines.append((0, 0, {
                    'period_index': i,
                    'inflow': per_year_revenue,
                    'outflow': 0.0,
                    'description': f'Projected sales — Year {i}',
                }))
            rec.cash_flow_ids = lines

    def action_approve(self):
        self.write({'state': 'approved'})

    def action_reject(self):
        self.write({'state': 'rejected'})

    def action_archive_study(self):
        self.write({'state': 'archived'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})
