from odoo import api, fields, models


class ProjectConstruction(models.Model):
    _inherit = 'realestate.project'

    milestone_ids = fields.One2many('realestate.construction.milestone', 'project_id', string='Milestones')
    cost_line_ids = fields.One2many('realestate.construction.cost.line', 'project_id', string='Cost Lines')

    construction_progress = fields.Float(
        string='Construction Progress (%)',
        compute='_compute_construction_progress', store=True,
    )
    actual_cost = fields.Monetary(
        string='Actual Cost',
        compute='_compute_costs', store=True,
    )
    cost_variance = fields.Monetary(
        string='Budget Variance',
        compute='_compute_costs', store=True,
        help='Actual cost minus expected budget. Negative = under budget.',
    )

    milestone_count = fields.Integer(compute='_compute_milestone_count')

    @api.depends('milestone_ids.completion_percentage', 'milestone_ids.weight', 'milestone_ids.state')
    def _compute_construction_progress(self):
        for rec in self:
            milestones = rec.milestone_ids.filtered(lambda m: m.state != 'cancelled')
            total_weight = sum(milestones.mapped('weight'))
            if total_weight <= 0:
                rec.construction_progress = 0.0
                continue
            weighted = sum(m.completion_percentage * m.weight for m in milestones)
            rec.construction_progress = weighted / total_weight

    @api.depends('cost_line_ids.amount', 'expected_budget')
    def _compute_costs(self):
        for rec in self:
            rec.actual_cost = sum(rec.cost_line_ids.mapped('amount'))
            rec.cost_variance = rec.actual_cost - rec.expected_budget

    def _compute_milestone_count(self):
        for rec in self:
            rec.milestone_count = len(rec.milestone_ids)

    def action_view_milestones(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Milestones',
            'res_model': 'realestate.construction.milestone',
            'view_mode': 'list,form,kanban',
            'domain': [('project_id', '=', self.id)],
            'context': {'default_project_id': self.id},
        }
