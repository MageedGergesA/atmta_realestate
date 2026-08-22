from odoo import api, fields, models


class ProjectBoundaryPoint(models.Model):
    _name = 'realestate.project.boundary.point'
    _description = 'Project Plot Boundary Point'
    _order = 'project_id, sequence, id'

    project_id = fields.Many2one(
        'realestate.project', string='Project',
        required=True, ondelete='cascade', index=True,
    )
    sequence = fields.Integer(default=10)
    latitude = fields.Float(string='Latitude', digits=(10, 7), required=True)
    longitude = fields.Float(string='Longitude', digits=(10, 7), required=True)
    label = fields.Char(string='Label', help='Optional name for this corner (e.g. "NW corner").')

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped('project_id')._recompute_centroid_from_boundary()
        return records

    def write(self, vals):
        res = super().write(vals)
        if any(k in vals for k in ('latitude', 'longitude', 'project_id')):
            self.mapped('project_id')._recompute_centroid_from_boundary()
        return res

    def unlink(self):
        projects = self.mapped('project_id')
        res = super().unlink()
        projects._recompute_centroid_from_boundary()
        return res
