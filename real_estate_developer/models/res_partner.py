from odoo import _, api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    developer_project_ids = fields.One2many(
        'realestate.project', 'developer_id',
        string='Developer Projects',
    )
    developer_project_count = fields.Integer(
        compute='_compute_developer_project_count', store=True,
    )
    is_realestate_developer = fields.Boolean(
        compute='_compute_developer_project_count', store=True, index=True,
        help="True when this partner is set as the developer on at least one "
             "project. Filter the Developers list by this flag.",
    )

    @api.depends('developer_project_ids')
    def _compute_developer_project_count(self):
        for p in self:
            count = len(p.developer_project_ids)
            p.developer_project_count = count
            p.is_realestate_developer = bool(count)

    def action_open_developer_projects(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Projects — %s') % self.display_name,
            'res_model': 'realestate.project',
            'view_mode': 'list,form',
            'domain': [('developer_id', '=', self.id)],
            'context': {'default_developer_id': self.id},
        }
