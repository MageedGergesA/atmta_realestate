from odoo import fields, models


class RealEstateProject(models.Model):
    _inherit = 'realestate.project'

    main_property_id = fields.Many2one(
        'realestate.property',
        string='2D Plan Entry Property',
        domain="[('project_id', '=', id)]",
        help="When set, the 2D Drill-Down Viewer jumps straight into this "
             "property (e.g. the compound) and shows only its hierarchy. "
             "Useful when the project has no master_plan_2d of its own but "
             "the compound underneath has its plan + regions fully set up.",
    )

    def action_open_plan_viewer(self):
        self.ensure_one()
        # If the user wired the project to a specific entry property, open
        # the viewer rooted at that property — no project-level picker.
        if self.main_property_id:
            return self.main_property_id.action_open_plan_viewer()
        return {
            'type': 'ir.actions.client',
            'tag': 'real_estate_plan.property_plan_viewer',
            'name': self.display_name + ' — Drill-Down Viewer',
            'target': 'fullscreen',
            'context': {'default_project_id': self.id},
            'params': {'project_id': self.id},
        }
