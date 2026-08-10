from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


MILESTONE_CATEGORIES = [
    ('excavation', 'Excavation'),
    ('foundation', 'Foundation'),
    ('structure', 'Structure'),
    ('masonry', 'Masonry'),
    ('mep', 'MEP'),
    ('facade', 'Facade'),
    ('finishing', 'Finishing'),
    ('landscape', 'Landscape'),
    ('handover_prep', 'Handover Preparation'),
    ('other', 'Other'),
]


class Milestone(models.Model):
    _name = 'realestate.construction.milestone'
    _description = 'Construction Milestone'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, sequence, id'

    name = fields.Char(string='Milestone', required=True, tracking=True)
    sequence = fields.Integer(default=10)
    project_id = fields.Many2one('realestate.project', string='Project', required=True, ondelete='cascade', tracking=True)
    phase_id = fields.Many2one(
        'realestate.phase', string='Phase',
        domain="[('project_id', '=', project_id)]",
        ondelete='set null', tracking=True,
    )
    category = fields.Selection(MILESTONE_CATEGORIES, default='other', required=True)
    weight = fields.Float(string='Weight (%)', default=10.0, tracking=True,
                          help='Share of project / phase progress this milestone represents.')

    expected_start_date = fields.Date(tracking=True)
    expected_end_date = fields.Date(tracking=True)
    actual_start_date = fields.Date(tracking=True)
    actual_end_date = fields.Date(tracking=True)

    completion_percentage = fields.Float(string='Completion (%)', default=0.0, tracking=True)
    state = fields.Selection([
        ('not_started', 'Not Started'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('delayed', 'Delayed'),
        ('cancelled', 'Cancelled'),
    ],
        # KNOWN BEHAVIOUR, deliberately unchanged at freeze. `default=` on an
        # editable computed field means every create() supplies a value, so
        # `_compute_state` does not run at creation: a milestone created
        # already past its expected end date reads 'Not Started' until a
        # dependency is next written. Removing the default makes the column
        # NULL on records created before any dependency exists, which is a
        # behavioural change too large to take during a freeze. Recorded in
        # IMPLEMENTATION_REPORT.md under Known deferred defects.
        default='not_started', tracking=True, required=True,
        compute='_compute_state', store=True, readonly=False)

    contractor_id = fields.Many2one('realestate.contractor', string='Contractor', tracking=True)
    budget_amount = fields.Monetary(string='Budgeted Cost', tracking=True)
    actual_cost = fields.Monetary(string='Actual Cost', compute='_compute_actual_cost', store=True)
    cost_variance = fields.Monetary(string='Variance', compute='_compute_actual_cost', store=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    cost_line_ids = fields.One2many('realestate.construction.cost.line', 'milestone_id', string='Cost Lines')

    # ----- Tasks -----
    task_ids = fields.One2many('realestate.construction.task', 'milestone_id', string='Tasks')
    task_count = fields.Integer(compute='_compute_task_stats')
    task_done_count = fields.Integer(compute='_compute_task_stats')
    completion_from_tasks = fields.Float(
        string='Completion from Tasks',
        compute='_compute_completion_from_tasks', store=True,
        help='Weighted task completion. Used as the milestone completion when tasks exist.',
    )

    # ----- Dependencies (predecessors) -----
    dependency_ids = fields.Many2many(
        'realestate.construction.milestone',
        'construction_milestone_dep_rel',
        'milestone_id', 'dependency_id',
        string='Depends On',
        domain="[('project_id', '=', project_id), ('id', '!=', id)]",
        help='Milestones that must finish (or be in progress) before this one can start.',
    )
    dependent_ids = fields.Many2many(
        'realestate.construction.milestone',
        'construction_milestone_dep_rel',
        'dependency_id', 'milestone_id',
        string='Blocks',
        readonly=True,
    )
    earliest_possible_start = fields.Date(
        string='Earliest Possible Start', compute='_compute_earliest_possible_start', store=True,
        help='Latest end date among predecessors. Cannot start before this date.',
    )
    is_blocked_by_delay = fields.Boolean(
        string='Blocked by Upstream Delay',
        compute='_compute_is_blocked_by_delay', store=True,
    )
    delay_days = fields.Integer(
        string='Delay (days)', compute='_compute_delay_days', store=True,
        help='Days the expected start has slipped because of predecessor delays.',
    )

    notes = fields.Html()

    @api.depends('completion_percentage', 'expected_end_date', 'actual_end_date')
    def _compute_state(self):
        # `context_today`, not `today`. Every deployment this runs in is ahead
        # of UTC, so for the last hours of each local day the two disagree —
        # and a milestone due yesterday was not being marked delayed until UTC
        # caught up. The stored value therefore reflects the timezone of
        # whoever triggers the recompute, which for a site milestone is the
        # right answer and is why the comparison lives here rather than in a
        # nightly job.
        for rec in self:
            today = fields.Date.context_today(rec)
            # Only re-compute if not manually overridden via cancelled
            if rec.state == 'cancelled':
                continue
            if rec.completion_percentage >= 100.0:
                rec.state = 'completed'
                if not rec.actual_end_date:
                    rec.actual_end_date = today
            elif rec.completion_percentage > 0.0:
                if rec.expected_end_date and today > rec.expected_end_date:
                    rec.state = 'delayed'
                else:
                    rec.state = 'in_progress'
            else:
                if rec.expected_end_date and today > rec.expected_end_date:
                    rec.state = 'delayed'
                else:
                    rec.state = 'not_started'

    @api.depends('cost_line_ids.amount')
    def _compute_actual_cost(self):
        for rec in self:
            rec.actual_cost = sum(rec.cost_line_ids.mapped('amount'))
            rec.cost_variance = rec.actual_cost - rec.budget_amount

    @api.depends('task_ids.weight', 'task_ids.completion_pct')
    def _compute_completion_from_tasks(self):
        for rec in self:
            tasks = rec.task_ids
            total_weight = sum(tasks.mapped('weight')) or 0.0
            if total_weight > 0:
                weighted = sum(t.weight * t.completion_pct for t in tasks)
                rec.completion_from_tasks = weighted / total_weight
            else:
                rec.completion_from_tasks = 0.0

    @api.depends('task_ids')
    def _compute_task_stats(self):
        for rec in self:
            rec.task_count = len(rec.task_ids)
            rec.task_done_count = len(rec.task_ids.filtered(lambda t: t.state in ('done', 'skipped')))

    @api.onchange('completion_from_tasks')
    def _onchange_completion_from_tasks(self):
        """When tasks exist, the milestone's completion mirrors the rolled-up task progress."""
        if self.task_ids:
            self.completion_percentage = self.completion_from_tasks

    @api.depends('dependency_ids.actual_end_date', 'dependency_ids.expected_end_date')
    def _compute_earliest_possible_start(self):
        for rec in self:
            if not rec.dependency_ids:
                rec.earliest_possible_start = False
                continue
            dates = [d.actual_end_date or d.expected_end_date for d in rec.dependency_ids]
            dates = [d for d in dates if d]
            rec.earliest_possible_start = max(dates) if dates else False

    @api.depends('earliest_possible_start', 'expected_start_date')
    def _compute_is_blocked_by_delay(self):
        for rec in self:
            rec.is_blocked_by_delay = bool(
                rec.earliest_possible_start
                and rec.expected_start_date
                and rec.earliest_possible_start > rec.expected_start_date
            )

    @api.depends('earliest_possible_start', 'expected_start_date')
    def _compute_delay_days(self):
        for rec in self:
            if rec.earliest_possible_start and rec.expected_start_date and \
                    rec.earliest_possible_start > rec.expected_start_date:
                rec.delay_days = (rec.earliest_possible_start - rec.expected_start_date).days
            else:
                rec.delay_days = 0

    @api.constrains('dependency_ids')
    def _check_no_circular_deps(self):
        for rec in self:
            visited = set()
            stack = list(rec.dependency_ids.ids)
            while stack:
                current_id = stack.pop()
                if current_id == rec.id:
                    raise ValidationError(_("Circular dependency detected on milestone '%s'.") % rec.name)
                if current_id in visited:
                    continue
                visited.add(current_id)
                stack.extend(self.browse(current_id).dependency_ids.ids)

    @api.constrains('completion_percentage')
    def _check_percentage(self):
        for rec in self:
            if not (0.0 <= rec.completion_percentage <= 100.0):
                raise ValidationError(_("Completion must be between 0 and 100."))

    @api.constrains('weight')
    def _check_weight(self):
        for rec in self:
            if rec.weight < 0 or rec.weight > 100:
                raise ValidationError(_("Weight must be between 0 and 100."))

    def action_start(self):
        for rec in self:
            # Block if any predecessor isn't at least in progress
            unfinished = rec.dependency_ids.filtered(lambda d: d.state in ('not_started', 'cancelled'))
            if unfinished:
                raise UserError(_(
                    "Cannot start '%s' — these predecessors are not yet started: %s"
                ) % (rec.name, ', '.join(unfinished.mapped('name'))))
            rec.write({
                'actual_start_date': fields.Date.context_today(rec),
                'completion_percentage': max(rec.completion_percentage, 1.0),
            })

    def action_complete(self):
        for rec in self:
            rec.write({
                'completion_percentage': 100.0,
                'actual_end_date': fields.Date.context_today(rec),
            })

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancelled'
