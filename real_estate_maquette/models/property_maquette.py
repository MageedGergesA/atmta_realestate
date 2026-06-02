from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PropertyMaquette(models.Model):
    _inherit = 'realestate.property'

    maquette_mesh_name = fields.Char(
        string='3D Mesh Name',
        help='Exact mesh name inside the project\'s GLB. Click "Auto-match" on the project to fill this in '
             'automatically from the property code.',
    )
    floor_plan_image = fields.Binary(
        string='Floor Plan (Image)',
        attachment=True,
        help="PNG/JPG quick-preview of the unit's floor plan — shown in the "
             "maquette side panel and the Building Elevation dialog.",
    )
    has_floor_plan_effective = fields.Boolean(
        compute='_compute_has_floor_plan_effective',
        help="Alias used by the Building Elevation dialog.",
    )

    @api.depends('floor_plan_image')
    def _compute_has_floor_plan_effective(self):
        for rec in self:
            rec.has_floor_plan_effective = bool(rec.floor_plan_image)
    floor_plan_image_filename = fields.Char()
    floor_plan_pdf = fields.Binary(
        string='Floor Plan (PDF)',
        attachment=True,
        help='Optional downloadable PDF of the floor plan.',
    )
    floor_plan_pdf_filename = fields.Char()
    interior_glb = fields.Binary(
        string='3D Interior (.glb)',
        attachment=True,
        help='Optional glTF binary of the unit interior — viewed in the "3D Interior" tab.',
    )
    interior_glb_filename = fields.Char()
    image_count = fields.Integer(compute='_compute_image_count')
    maquette_color_override = fields.Char(
        string='Highlight Color',
        help='Optional hex color (e.g., #FF8800) to override the default state-based color in 3D.',
    )

    # ----- Elevation sheet / multi-floor drill-down -----
    elevation_sheet = fields.Image(
        string='Elevation Sheet',
        help="Architectural elevation sheet (front + side elevations, floor plans, "
             "site plan, specs). Shown in the Building Elevation dialog when the "
             "user clicks this building on the 2D master plan.",
    )
    elevation_sheet_filename = fields.Char()
    floor_ids = fields.One2many(
        'realestate.building.floor', 'building_id', string='Floors',
    )
    has_elevation = fields.Boolean(compute='_compute_has_elevation', store=True)
    spec_tag_ids = fields.Many2many(
        'realestate.spec.tag', 're_property_spec_tag_rel',
        'property_id', 'tag_id',
        string='Specifications / Finishes',
    )

    @api.depends('elevation_sheet')
    def _compute_has_elevation(self):
        for rec in self:
            rec.has_elevation = bool(rec.elevation_sheet)

    def _compute_image_count(self):
        for rec in self:
            rec.image_count = len(rec.property_Attachment_media_ids)

    def action_view_gallery(self):
        """Open the unit's images (reusing 'Attachments and Media') in a big
        image kanban — the bigger window with many images."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('%s — Images') % self.display_name,
            'res_model': 'property.image',
            'view_mode': 'kanban,form',
            'domain': [('property_id', '=', self.id)],
            'context': {'default_property_id': self.id},
        }

    @api.constrains('maquette_mesh_name')
    def _check_mesh_name_unique_per_project(self):
        for rec in self:
            if not rec.maquette_mesh_name:
                continue
            project = rec.project_id if 'project_id' in rec._fields else False
            if not project:
                # Try to derive project via hierarchy
                parent = rec.parent_id
                while parent and not (hasattr(parent, 'project_id') and parent.project_id):
                    parent = parent.parent_id
                project = parent.project_id if parent else False
            if not project:
                continue
            siblings = self.search([
                ('id', '!=', rec.id),
                ('maquette_mesh_name', '=', rec.maquette_mesh_name),
                ('project_id', '=', project.id),
            ])
            if siblings:
                raise ValidationError(_(
                    "Mesh name '%s' is already used by another unit in project %s."
                ) % (rec.maquette_mesh_name, project.display_name))

    def action_view_in_maquette(self):
        """Open the parent project's form on the 3D Maquette tab,
        passing this property's id so the viewer pre-selects and highlights it."""
        self.ensure_one()
        project = self.project_id if 'project_id' in self._fields and self.project_id else False
        if not project:
            # Walk up the hierarchy to find a project link
            node = self.parent_id
            while node:
                if 'project_id' in node._fields and node.project_id:
                    project = node.project_id
                    break
                node = node.parent_id
        if not project:
            raise UserError(_(
                "This unit is not linked to a project. Set a parent that belongs to a project."
            ))
        if not project.maquette_glb:
            raise UserError(_(
                "Project '%s' has no 3D maquette uploaded yet."
            ) % project.display_name)
        return {
            'type': 'ir.actions.act_window',
            'name': project.display_name,
            'res_model': 'realestate.project',
            'res_id': project.id,
            'view_mode': 'form',
            'context': {
                'maquette_focus_property_id': self.id,
                'maquette_focus_mesh_name': self.maquette_mesh_name or '',
            },
        }
