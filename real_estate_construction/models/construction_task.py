from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ConstructionTask(models.Model):
    _name = 'realestate.construction.task'
    _description = 'Construction Task (under a milestone)'
    _inherit = ['mail.thread']
    _order = 'milestone_id, sequence, id'

    name = fields.Char(string='Task', required=True, tracking=True)
    milestone_id = fields.Many2one(
        'realestate.construction.milestone', string='Milestone',
        required=True, ondelete='cascade', index=True,
    )
    project_id = fields.Many2one(related='milestone_id.project_id', store=True, readonly=True)
    sequence = fields.Integer(default=10)
    weight = fields.Float(
        string='Weight', default=1.0,
        help='Relative weight inside the milestone (used for completion roll-up).',
    )

    assignee_id = fields.Many2one('res.users', string='Assignee', tracking=True)
    contractor_id = fields.Many2one(
        'realestate.contractor', string='Contractor',
        default=lambda self: self.env.context.get('default_contractor_id'),
    )

    state = fields.Selection([
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('done', 'Done'),
        ('skipped', 'Skipped'),
    ], default='pending', required=True, tracking=True)

    expected_start_date = fields.Date(tracking=True)
    expected_end_date = fields.Date(tracking=True)
    actual_start_date = fields.Date(tracking=True)
    actual_end_date = fields.Date(tracking=True)

    completion_pct = fields.Float(
        string='Completion (%)', compute='_compute_completion_pct',
        store=True,
        help='100 if done/skipped, 50 if in progress, 0 otherwise.',
    )
    notes = fields.Html()

    @api.depends('state')
    def _compute_completion_pct(self):
        for rec in self:
            if rec.state in ('done', 'skipped'):
                rec.completion_pct = 100.0
            elif rec.state == 'in_progress':
                rec.completion_pct = 50.0
            else:
                rec.completion_pct = 0.0

    @api.constrains('weight')
    def _check_weight(self):
        for rec in self:
            if rec.weight < 0:
                raise ValidationError(_("Task weight cannot be negative."))

    def action_start(self):
        for rec in self:
            rec.write({
                'state': 'in_progress',
                'actual_start_date': (rec.actual_start_date
                                      or fields.Date.context_today(rec)),
            })

    def action_done(self):
        for rec in self:
            rec.write({
                'state': 'done',
                'actual_end_date': fields.Date.context_today(rec),
            })

    def action_skip(self):
        for rec in self:
            rec.state = 'skipped'

    def action_reopen(self):
        for rec in self:
            rec.write({'state': 'pending', 'actual_end_date': False})

    def _push_completion_to_milestone(self):
        for ms in self.mapped('milestone_id'):
            if ms.task_ids:
                ms.completion_percentage = ms.completion_from_tasks

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._push_completion_to_milestone()
        return records

    def write(self, vals):
        res = super().write(vals)
        if any(k in vals for k in ('state', 'weight')):
            self._push_completion_to_milestone()
        return res

    def unlink(self):
        milestones = self.mapped('milestone_id')
        res = super().unlink()
        for ms in milestones:
            if ms.task_ids:
                ms.completion_percentage = ms.completion_from_tasks
        return res
