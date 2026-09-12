from odoo import _, api, fields, models


class ProjectConstruction(models.Model):
    _inherit = 'realestate.project'

    # Wave 25 — `construction_member_ids` moved down to
    # `atmta_construction_core`, so the project-team rules could move with
    # their models. This module still reads it; it no longer declares it.

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
        """The legacy cost-line total, kept and renamed in meaning.

        This is **not** the project's actual cost any more — M2 defines that as
        the posted ledger (`ctrl_actual_cost`). The field survives because
        views, reports and years of data reference it, but it now sums only
        the lines that are still allowed to behave like costs: declared
        accruals. Ledger-backed lines are excluded, because the analytic
        ledger already carries the same money and adding both is the
        double-count Phase 0 proved was possible.
        """
        for rec in self:
            accruals = rec.cost_line_ids.filtered('counts_as_actual')
            rec.actual_cost = sum(accruals.mapped('amount'))
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

    def construction_position(self):
        """The project's control position, from the one authoritative service.

        M9AS: if two surfaces show "Current Commitment" they must be literally
        the same calculation. This method exists so the project form, the cost
        sheet and the Control Tower cannot drift into three formulas that
        happen to agree today.
        """
        self.ensure_one()
        return self.env[
            'realestate.construction.cost.sheet'].totals_for(self)

    def action_open_control_tower(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'construction_control_tower',
            'name': _('Control Tower — %s') % self.display_name,
            'params': {'project_id': self.id},
            'context': {'active_id': self.id,
                        'default_project_id': self.id},
        }

    def action_open_cost_sheet(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'construction_control_tower',
            'name': _('Cost Sheet — %s') % self.display_name,
            'params': {'project_id': self.id, 'focus': 'cost_sheet'},
            'context': {'active_id': self.id},
        }
