from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class LaborLog(models.Model):
    """Daily labor entry for construction.

    One row = one crew's day on one workorder (milestone or task). Auto-rolls
    up to a cost_line so the construction P&L, project actual_cost, and any
    analytic account stay in sync without a separate reconciliation.
    """
    _name = 'realestate.construction.labor.log'
    _description = 'Daily Labor Log'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'),
    )
    date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)
    project_id = fields.Many2one(
        'realestate.project', string='Project', required=True, tracking=True,
        ondelete='cascade', index=True,
    )
    phase_id = fields.Many2one(
        'realestate.phase', string='Phase', ondelete='set null',
        domain="[('project_id', '=', project_id)]",
    )
    milestone_id = fields.Many2one(
        'realestate.construction.milestone', string='Workorder / Milestone',
        ondelete='set null', tracking=True,
        domain="[('project_id', '=', project_id)]",
    )
    task_id = fields.Many2one(
        'realestate.construction.task', string='Task',
        ondelete='set null',
        domain="[('milestone_id', '=', milestone_id)]",
    )
    contractor_id = fields.Many2one(
        'realestate.contractor', string='Labor Provider', tracking=True,
    )
    trade = fields.Selection([
        ('mason', 'Mason'),
        ('carpenter', 'Carpenter'),
        ('steel_fixer', 'Steel Fixer'),
        ('electrician', 'Electrician'),
        ('plumber', 'Plumber'),
        ('mep', 'MEP'),
        ('painter', 'Painter'),
        ('finisher', 'Finisher'),
        ('helper', 'Helper'),
        ('operator', 'Operator'),
        ('foreman', 'Foreman'),
        ('other', 'Other'),
    ], default='helper', required=True)

    workers = fields.Integer(string='Crew Size', default=1, required=True)
    hours = fields.Float(string='Hours per Worker', default=8.0, required=True)
    hourly_rate = fields.Monetary(string='Hourly Rate', required=True, default=0.0)
    total_hours = fields.Float(
        string='Total Hours', compute='_compute_totals', store=True,
    )
    total_cost = fields.Monetary(
        string='Total Cost', compute='_compute_totals', store=True,
    )
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    cost_line_id = fields.Many2one(
        'realestate.construction.cost.line', string='Cost Line',
        readonly=True, copy=False,
        help='Auto-generated rollup of this labor entry.',
    )
    # M6 — the dimensions the daily report and cost control need. Added to
    # the existing model rather than creating a second manpower table: two
    # headcount truths disagree within a week.
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null',
        index=True,
    )
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', string='Cost Code',
        ondelete='set null', index=True,
    )
    location = fields.Char()
    shift = fields.Selection([
        ('day', 'Day'), ('night', 'Night'),
    ], default='day')
    daily_report_id = fields.Many2one(
        'realestate.construction.daily.report', string='Daily Report',
        ondelete='set null', index=True,
        help="The day's report this labour belongs to. The log stays the "
             "authoritative record; the report summarises it.",
    )

    notes = fields.Char()

    @api.depends('workers', 'hours', 'hourly_rate')
    def _compute_totals(self):
        for rec in self:
            rec.total_hours = (rec.workers or 0) * (rec.hours or 0.0)
            rec.total_cost = rec.total_hours * (rec.hourly_rate or 0.0)

    @api.constrains('workers', 'hours', 'hourly_rate')
    def _check_positive(self):
        for rec in self:
            if rec.workers < 0 or rec.hours < 0 or rec.hourly_rate < 0:
                raise ValidationError(_("Crew size, hours and rate must be non-negative."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.construction.labor.log') or 'LAB-NEW'
        records = super().create(vals_list)
        for rec in records:
            rec._sync_cost_line()
        return records

    def write(self, vals):
        res = super().write(vals)
        cost_fields = {'workers', 'hours', 'hourly_rate', 'milestone_id', 'phase_id', 'project_id', 'contractor_id', 'date'}
        if cost_fields & set(vals):
            for rec in self:
                rec._sync_cost_line()
        return res

    def unlink(self):
        cost_lines = self.mapped('cost_line_id')
        res = super().unlink()
        cost_lines.unlink()
        return res

    def _sync_cost_line(self):
        """Push (or update) the corresponding cost_line so project totals stay in sync."""
        self.ensure_one()
        CostLine = self.env['realestate.construction.cost.line']
        label = _(
            'Labor %(trade)s — %(w)d × %(h)s h',
            trade=dict(self._fields['trade'].selection).get(self.trade, self.trade),
            w=self.workers, h=self.hours,
        )
        vals = {
            'name': label,
            'project_id': self.project_id.id,
            'phase_id': self.phase_id.id if self.phase_id else False,
            'milestone_id': self.milestone_id.id if self.milestone_id else False,
            'contractor_id': self.contractor_id.id if self.contractor_id else False,
            'date': self.date,
            'amount': self.total_cost,
            'category': 'labor',
            'currency_id': self.currency_id.id,
        }
        if self.cost_line_id:
            self.cost_line_id.write(vals)
        else:
            self.cost_line_id = CostLine.create(vals)
