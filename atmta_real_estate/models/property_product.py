from odoo import models, fields, api


class ProductProduct(models.Model):
    _inherit = 'product.product'
    _parent_store = True

    # Property identity
    is_property = fields.Boolean(string="Is Property", tracking=True)
    property_ref = fields.Char(string="Property Reference", tracking=True)
    property_number = fields.Char(string="Property Number", tracking=True)
    property_type_id = fields.Many2one('property.type', string="Property Type", tracking=True)
    area_sqm = fields.Float(string="Area (sqm)", tracking=True)
    floor_number = fields.Integer(string="Floor Number", tracking=True)
    bedroom_count = fields.Integer(string="Bedrooms", tracking=True)
    bathroom_count = fields.Integer(string="Bathrooms", tracking=True)
    living_room_count = fields.Integer(string="Living Rooms", tracking=True)
    has_kitchen = fields.Boolean(string="Kitchen", tracking=True)
    has_balcony = fields.Boolean(string="Balcony / Terrace", tracking=True)
    furnished_status = fields.Selection([
        ('unfurnished', 'Unfurnished'),
        ('semi', 'Semi-furnished'),
        ('furnished', 'Fully-furnished'),
    ], string="Furnished Status", tracking=True)
    property_notes = fields.Text(string="Description / Notes", tracking=True)

    # Location
    country_id = fields.Many2one('res.country', string="Country", tracking=True)
    city = fields.Char(string="City", tracking=True)
    district = fields.Char(string="District", tracking=True)
    sub_area = fields.Char(string="Sub-area", tracking=True)
    street_name = fields.Char(string="Street Name", tracking=True)
    building_no = fields.Char(string="Building Number", tracking=True)
    landmark = fields.Char(string="Landmark", tracking=True)
    gmap_url = fields.Char(string="Google Maps URL", tracking=True)

    # Status & availability
    state = fields.Selection([
        ('available', 'Available'),
        ('rented', 'Rented'),
        ('reserved', 'Reserved'),
        ('maintenance', 'Under Maintenance'),
        ('inactive', 'Inactive'),
    ], string="Status", default='available', tracking=True)
    next_available_date = fields.Date(string="Next Available Date", tracking=True)

    # Ownership
    owner_id = fields.Many2one('res.partner', string="Property Owner", tracking=True)
    is_internal = fields.Boolean(string="Company-Owned Asset", tracking=True)
    manager_id = fields.Many2one('res.users', string="Property Manager", tracking=True)
    property_Attachment_media_ids = fields.One2many(
        string="attachments And Media",
        comodel_name='property.image',
        inverse_name='product_variant_id',
        copy=True,
    )
    parent_id = fields.Many2one(
        'product.product',
        string="Parent Property",
        domain="[('is_property', '=', True)]",
        tracking=True
    )

    child_ids = fields.One2many(
        'product.product',
        'parent_id',
        string="Sub Properties"
    )
    is_parent_child = fields.Boolean(string='Is parent ',
                                     help='Check this if the record represents'
                                     ' a parent.'
                                     )
    parent_path = fields.Char(index=True)
    master_product_id = fields.Many2one(
        'product.product', 'Master Product', compute='_compute_master_product_id', store=True)

    child_org_ids = fields.One2many(
        'product.product',
        'master_product_id',
        string="Sub Properties"
    )

    @api.depends('parent_path')
    def _compute_master_product_id(self):
        for rec in self:
            if rec.parent_path:
                rec.master_product_id = int(rec.parent_path.split('/')[0])
                print(f'jjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjj{rec.master_product_id}')

    @api.model
    def get_child_dept(self, product_id, model):
        """Fetching the data to widget"""
        model_id = self.env['ir.model'].search([('model', '=', model)], limit=1)
        product = self.env[model_id.model].browse(product_id)

        parent = product.parent_id
        children = product.child_ids

        result = {
            'parent': {
                'id': parent.id,
                'name': parent.name,
            } if parent else None,

            'self': {
                'id': product.id,
                'name': product.name,
            },

            'child': [
                {
                    'id': child.id,
                    'name': child.name,
                } for child in children
            ]
        }

        return result

    @api.model_create_multi
    def create(self, vals_list):
        """To show the widget at the time of creation"""
        res = super(ProductProduct, self).create(vals_list)
        for record in res:
            if record.parent_id or record.child_ids:
                record.is_parent_child = True
            else:
                record.is_parent_child = False
            print(f'-----------------------------------------------------------------------------{res}')
        return res

    def write(self, values):
        """To update the widget at the time of update"""
        res = super(ProductProduct, self).write(values)
        if 'parent_id' in values:
            self.is_parent_child = True
        print(f';;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;; {res}')
        return res
