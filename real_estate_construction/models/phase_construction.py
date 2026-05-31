from odoo import api, fields, models


class PhaseConstruction(models.Model):
    _inherit = 'realestate.phase'

    milestone_ids = fields.One2many('realestate.construction.milestone', 'phase_id', string='Milestones')
    construction_progress = fields.Float(
        string='Construction Progress (%)',
        compute='_compute_construction_progress', store=True,
    )

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
