from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ProjectMaquette(models.Model):
    _inherit = 'realestate.project'

    maquette_glb = fields.Binary(
        string='Building Model (.glb)',
        attachment=True,
        help='Upload a glTF binary (.glb) file containing the project\'s 3D model. '
             'Each unit must be a named mesh inside the file.',
    )
    maquette_glb_filename = fields.Char(string='GLB Filename')
    maquette_env_hdr = fields.Binary(
        string='Environment HDR',
        attachment=True,
        help='Optional .hdr / .exr environment map for realistic reflections and sky lighting.',
    )
    maquette_env_hdr_filename = fields.Char(string='HDR Filename')

    # 2D master-plan image — shown on its own tab and overlaid on the map.
    master_plan_2d = fields.Image(string='2D Master Plan')
    master_plan_2d_filename = fields.Char(string='2D Plan Filename')
    # Stored presence flags so lists/menus don't have to load the binaries.
    has_master_plan_2d = fields.Boolean(compute='_compute_has_master_plan_2d', store=True)
    has_maquette = fields.Boolean(compute='_compute_has_maquette', store=True)

    @api.depends('master_plan_2d')
    def _compute_has_master_plan_2d(self):
        for rec in self:
            rec.has_master_plan_2d = bool(rec.master_plan_2d)

    region_ids = fields.One2many(
        'realestate.building.region', 'project_id', string='2D Plan Regions',
    )
    region_count = fields.Integer(compute='_compute_region_count')

    @api.depends('region_ids')
    def _compute_region_count(self):
        for rec in self:
            rec.region_count = len(rec.region_ids)

    def action_open_master_plan_2d(self):
        """Open the interactive 2D Master Plan viewer for this project."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'real_estate_maquette.master_plan_2d',
            'params': {'project_id': self.id},
        }

    @api.depends('maquette_glb')
    def _compute_has_maquette(self):
        for rec in self:
            rec.has_maquette = bool(rec.maquette_glb)

    maquette_default_camera = fields.Char(
        string='Default Camera',
        help='JSON-encoded camera position saved from the viewer. '
             'Users land at this angle when they open the maquette.',
    )
    maquette_mesh_naming_hint = fields.Char(
        string='Mesh Naming Convention',
        default='unit_<property_code>',
        help='Documents how the 3D artist should name unit meshes. '
             'Defaults to "unit_<property_code>" — meshes named after the property\'s code match automatically.',
    )

    maquette_unit_count = fields.Integer(
        string='Units Linked to 3D',
        compute='_compute_maquette_unit_counts',
    )
    maquette_total_units = fields.Integer(
        string='Total Units',
        compute='_compute_maquette_unit_counts',
    )
    maquette_status = fields.Selection([
        ('not_uploaded', 'No 3D model'),
        ('uploaded_no_mapping', 'Uploaded, no units mapped'),
        ('partial', 'Partially mapped'),
        ('mapped', 'Fully mapped'),
    ], compute='_compute_maquette_status', string='3D Status')

    @api.depends('property_ids', 'property_ids.maquette_mesh_name', 'property_ids.hierarchy_level')
    def _compute_maquette_unit_counts(self):
        for rec in self:
            units = rec.property_ids.filtered(lambda p: p.hierarchy_level == 'unit')
            rec.maquette_total_units = len(units)
            rec.maquette_unit_count = len(units.filtered(lambda p: p.maquette_mesh_name))

    @api.depends('maquette_glb', 'maquette_unit_count', 'maquette_total_units')
    def _compute_maquette_status(self):
        for rec in self:
            if not rec.maquette_glb:
                rec.maquette_status = 'not_uploaded'
            elif rec.maquette_unit_count == 0:
                rec.maquette_status = 'uploaded_no_mapping'
            elif rec.maquette_unit_count < rec.maquette_total_units:
                rec.maquette_status = 'partial'
            else:
                rec.maquette_status = 'mapped'

    # ----- Actions -----
    def action_auto_match_meshes(self):
        """Server-side helper: ask the viewer to scan mesh names from the GLB
        and propose mappings. This action just opens a confirmation dialog —
        the actual scan happens in JS (we can't decode GLB cheaply in Python)."""
        self.ensure_one()
        if not self.maquette_glb:
            raise UserError(_("Upload a GLB file first."))
        return {
            'type': 'ir.actions.client',
            'tag': 'real_estate_maquette.auto_match',
            'params': {'project_id': self.id},
        }

    def action_clear_maquette(self):
        """Remove the GLB and reset all unit mesh mappings."""
        self.ensure_one()
        self.write({
            'maquette_glb': False,
            'maquette_glb_filename': False,
            'maquette_default_camera': False,
        })
        self.property_ids.filtered(lambda p: p.maquette_mesh_name).write({
            'maquette_mesh_name': False,
        })
        return True

    def get_maquette_units_data(self):
        """JSON-serializable list of all units in this project with the data
        the 3D viewer needs to render and react to clicks."""
        self.ensure_one()
        units = self.property_ids.filtered(lambda p: p.hierarchy_level == 'unit')
        data = []
        for u in units:
            data.append({
                'id': u.id,
                'property_code': u.property_code or '',
                'name': u.name,
                'mesh_name': u.maquette_mesh_name or '',
                'state': u.state or '',
                'base_price': u.base_price if 'base_price' in u._fields else 0.0,
                'currency': u.currency_id.symbol if u.currency_id else '',
                'area_sqm': u.area_sqm or 0.0,
                'property_type': u.property_type_id.name if u.property_type_id else '',
                'has_floor_plan': bool(u.has_floor_plan_effective),
                'color_override': u.maquette_color_override or '',
            })
        return data
