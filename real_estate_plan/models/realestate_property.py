from odoo import api, fields, models


class RealEstateProperty(models.Model):
    _inherit = 'realestate.property'

    plan_image = fields.Image(
        string='2D Plan Image',
        help="Site plan / building elevation / floor plan / villa lot map. "
             "Polygon regions drawn under the 2D Plan tab overlay this image.",
    )
    plan_image_filename = fields.Char()
    has_plan_image = fields.Boolean(
        compute='_compute_has_plan_image', store=True, readonly=True,
    )

    plan_region_ids = fields.One2many(
        'realestate.plan.region', 'parent_property_id', string='Plan Regions',
    )
    plan_region_count = fields.Integer(
        compute='_compute_plan_region_count', store=False,
    )

    @api.depends('plan_image')
    def _compute_has_plan_image(self):
        for rec in self:
            rec.has_plan_image = bool(rec.plan_image)

    @api.depends('plan_region_ids')
    def _compute_plan_region_count(self):
        for rec in self:
            rec.plan_region_count = len(rec.plan_region_ids)

    def action_open_plan_viewer(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'real_estate_plan.property_plan_viewer',
            'name': self.display_name + ' — 2D Plan',
            'target': 'fullscreen',
            'context': {'default_property_id': self.id},
            'params': {'property_id': self.id},
        }

    def action_clear_plan_image(self):
        self.ensure_one()
        self.write({'plan_image': False, 'plan_image_filename': False})
        return True
