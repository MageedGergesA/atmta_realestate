from odoo import _, api, fields, models


class BuildingPreview(models.TransientModel):
    """One-shot dialog opened when a user clicks a building region on the 2D
    master plan. Surfaces gallery, key info, and unit availability."""
    _name = 'realestate.building.preview'
    _description = 'Building Preview Dialog'

    property_id = fields.Many2one(
        'realestate.property', required=True, ondelete='cascade',
    )
    # Non-related so that the picker can pass the region's project as a fallback
    # when the property itself has no project_id yet (legacy regions).
    project_id = fields.Many2one(
        'realestate.project', compute='_compute_project_id',
        store=False, readonly=True,
    )

    @api.depends('property_id')
    def _compute_project_id(self):
        for rec in self:
            ctx_pid = rec.env.context.get('default_project_id')
            rec.project_id = (
                rec.property_id.project_id
                or rec.env['realestate.project'].browse(ctx_pid).exists()
            )
    name = fields.Char(related='property_id.name', readonly=True)
    property_code = fields.Char(related='property_id.property_code', readonly=True)
    city = fields.Char(related='property_id.city', readonly=True)
    district = fields.Char(related='property_id.district', readonly=True)
    number_of_floors = fields.Integer(related='property_id.number_of_floors', readonly=True)
    number_of_units = fields.Integer(related='property_id.number_of_units', readonly=True)
    floor_plan_image = fields.Binary(related='property_id.floor_plan_image', readonly=True)
    hero_image = fields.Image(related='property_id.image_1920', readonly=True)
    image_ids = fields.Many2many(
        'property.image', string='Gallery',
        compute='_compute_image_ids',
    )
    attachment_image_ids = fields.Many2many(
        'ir.attachment', string='Attached Images',
        compute='_compute_attachment_image_ids',
    )
    has_any_image = fields.Boolean(compute='_compute_has_any_image')
    unit_ids = fields.Many2many(
        'realestate.property', string='Units',
        compute='_compute_unit_ids',
    )
    units_total = fields.Integer(compute='_compute_unit_stats')
    units_available = fields.Integer(compute='_compute_unit_stats')
    units_reserved = fields.Integer(compute='_compute_unit_stats')
    units_sold = fields.Integer(compute='_compute_unit_stats')

    def _family_properties(self):
        """Recordset of properties whose media we surface in the preview:
        the linked property, all parent_id-descendants of it, AND every
        property in the same project. The last bit matters because users
        often have flat data (no parent_id chain) — without it, the wizard
        misses sibling images in the project.

        If the property has no project_id but the wizard does (set from the
        clicked region), use that instead — covers regions created before
        the project auto-sync was in place."""
        self.ensure_one()
        Property = self.env['realestate.property']
        if not self.property_id:
            return Property
        family = Property.search([('id', 'child_of', self.property_id.id)])
        project = self.property_id.project_id or self.project_id
        if project:
            family |= Property.search([('project_id', '=', project.id)])
        return family

    @api.depends('property_id')
    def _compute_image_ids(self):
        for rec in self:
            rec.image_ids = rec._family_properties().mapped('property_Attachment_media_ids')

    @api.depends('property_id')
    def _compute_attachment_image_ids(self):
        """Also surface any ir.attachment image (chatter, attachments tab,
        anywhere) on the property tree — so users see their uploads no matter
        which widget they used."""
        for rec in self:
            family = rec._family_properties()
            if not family:
                rec.attachment_image_ids = False
                continue
            # Attachments linked via the Attachments tab (m2m field)…
            via_m2m = family.mapped('attachment_ids')
            # …plus chatter attachments / image_1920 stored attachments.
            via_res = self.env['ir.attachment'].search([
                ('res_model', '=', 'realestate.property'),
                ('res_id', 'in', family.ids),
            ])
            images = (via_m2m | via_res).filtered(
                lambda a: (a.mimetype or '').startswith('image/')
            )
            rec.attachment_image_ids = images

    @api.depends('hero_image', 'floor_plan_image', 'image_ids', 'attachment_image_ids')
    def _compute_has_any_image(self):
        for rec in self:
            rec.has_any_image = bool(
                rec.hero_image or rec.floor_plan_image
                or rec.image_ids or rec.attachment_image_ids
            )

    @api.depends('property_id')
    def _compute_unit_ids(self):
        for rec in self:
            # All descendant units (any depth) under this building.
            units = self.env['realestate.property'].search([
                ('id', 'child_of', rec.property_id.id),
                ('hierarchy_level', '=', 'unit'),
            ])
            rec.unit_ids = units

    @api.depends('unit_ids', 'unit_ids.state')
    def _compute_unit_stats(self):
        for rec in self:
            rec.units_total = len(rec.unit_ids)
            rec.units_available = len(rec.unit_ids.filtered(lambda u: u.state == 'available'))
            rec.units_reserved = len(rec.unit_ids.filtered(lambda u: u.state == 'reserved'))
            rec.units_sold = len(rec.unit_ids.filtered(lambda u: u.state == 'sold'))

    def action_open_building(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.property',
            'view_mode': 'form',
            'res_id': self.property_id.id,
            'target': 'current',
        }

    def action_open_maquette(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.project',
            'view_mode': 'form',
            'res_id': self.project_id.id,
            'target': 'current',
        }
