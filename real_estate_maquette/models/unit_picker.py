from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MaquetteUnitPicker(models.TransientModel):
    """Wizard opened from the 3D maquette: clicking anywhere on the master plan
    pops this up so the user picks one of the project's units, previews it, and
    reserves / opens it. Decouples unit selection from per-mesh mapping, so an
    un-segmented master-plan model still works."""
    _name = 'realestate.maquette.unit.picker'
    _description = 'Maquette Unit Picker'

    project_id = fields.Many2one(
        'realestate.project', string='Project', required=True, readonly=True)
    mesh_name = fields.Char(
        string='Map Location', readonly=True,
        help="The 3D mesh that was clicked on the master plan.")
    unit_id = fields.Many2one(
        'realestate.property', string='Unit',
        domain="[('project_id', '=', project_id), ('hierarchy_level', '=', 'unit')]",
        help="Pick the unit this part of the master plan corresponds to.")

    # --- read-only preview of the picked unit ---
    unit_state = fields.Selection(related='unit_id.state', string='Status', readonly=True)
    unit_price = fields.Monetary(related='unit_id.base_price', string='Price', readonly=True)
    currency_id = fields.Many2one(related='unit_id.currency_id', readonly=True)
    unit_area = fields.Float(related='unit_id.area_sqm', string='Area (sqm)', readonly=True)
    unit_type_id = fields.Many2one(related='unit_id.property_type_id', string='Type', readonly=True)
    floor_plan_image = fields.Binary(related='unit_id.floor_plan_image', string='Floor Plan', readonly=True)
    image_ids = fields.Many2many('property.image', compute='_compute_image_ids')

    @api.depends('unit_id')
    def _compute_image_ids(self):
        for rec in self:
            rec.image_ids = rec.unit_id.property_Attachment_media_ids

    def _check_unit(self):
        self.ensure_one()
        if not self.unit_id:
            raise UserError(_("Select a unit first."))

    def action_view_gallery(self):
        self._check_unit()
        return self.unit_id.action_view_gallery()

    def action_open_unit(self):
        self._check_unit()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.property',
            'res_id': self.unit_id.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }

    def action_save(self):
        """Pin the picked unit onto the clicked spot of the master plan: assign
        its 3D mesh name to the clicked mesh and close. No reservation — this
        just makes the unit show on the map at that location."""
        self._check_unit()
        if not self.mesh_name:
            raise UserError(_("No map location was captured for this click."))
        # A mesh can map to only one unit per project — free it from any other
        # unit first, then assign it here.
        others = self.env['realestate.property'].search([
            ('id', '!=', self.unit_id.id),
            ('project_id', '=', self.project_id.id),
            ('maquette_mesh_name', '=', self.mesh_name),
        ])
        if others:
            others.write({'maquette_mesh_name': False})
        self.unit_id.maquette_mesh_name = self.mesh_name
        return {'type': 'ir.actions.act_window_close'}
