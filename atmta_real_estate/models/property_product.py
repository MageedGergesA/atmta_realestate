from dateutil.relativedelta import relativedelta

from odoo import models, fields, api


class ProductProduct(models.Model):
    _inherit = 'product.product'
    _parent_store = True

    # Property identity
    property_image = fields.Binary(string='Image')
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
        ('reserved', 'Reserved'),
        ('rented', 'Rented'),
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
    attachment_ids = fields.Many2many('ir.attachment', 'product_attachment_rel', 'product_id',
                                      'attachment_id', 'Attachments',
                                      help="You may attach files to this template, to be added to all "
                                           "emails created from this template")
    contract_history_ids = fields.One2many('realestate.contract.line', 'property_id', string='Contract Histtory')
    parent_id = fields.Many2one(
        'product.product',
        string="Parent Property",
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
    def set_property_inactive(self):
        for rec in self:
            if rec.state not in ['reserved','rented', 'maintenance']:
                rec.state = 'inactive'
    def set_property_available(self):
        for rec in self:
            if rec.state in ['inactive']:
                rec.state = 'available'


    @api.depends('parent_path')
    def _compute_master_product_id(self):
        for rec in self:
            if rec.parent_path:
                rec.master_product_id = int(rec.parent_path.split('/')[0])
                print(f'jjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjj{rec.master_product_id}')

    # @api.onchange('parent_id','child_ids')
    # def check_parent_childs(self):
    #     for rec in self:
    #         rec.get_child_dept()

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

    rent_count = fields.Integer(string="Times Rented", compute="_compute_rent_stats")
    total_rent_months = fields.Float(string="Total Rental Duration (Months)", compute="_compute_rent_stats")
    actual_rent_months = fields.Integer(string="Actual Rent Months", compute="_compute_rent_stats", store=True)
    revenue_collected = fields.Monetary(string="Revenue Collected", compute="_compute_rent_stats")
    revenue_expected = fields.Monetary(string="Total Expected Revenue", compute="_compute_rent_stats")
    total_amount_due = fields.Monetary(string="Total Amount Due", compute="_compute_rent_stats")

    currency_id = fields.Many2one('res.currency', string="Currency", required=True,
                                  default=lambda self: self.env.company.currency_id)

    @api.depends('contract_history_ids', 'contract_history_ids.contract_id.contract_payment_ids.move_state')
    def _compute_rent_stats(self):
        for property in self:
            rent_count = 0
            total_months = 0
            actual_months = 0
            revenue_collected = 0.0
            revenue_expected = 0.0

            for line in property.contract_history_ids.filtered(lambda l: l.property_id == property):
                rent_count += 1

                # Duration in months (from start to end)
                if line.start_date and line.end_date:
                    month_count = (line.end_date.year - line.start_date.year) * 12 + (
                                line.end_date.month - line.start_date.month)
                    if line.end_date.day >= line.start_date.day:
                        month_count += 1
                    total_months += month_count

                # Related payments (only for this line)
                payments = line.contract_id.contract_payment_ids.filtered(lambda p: p.contract_line_id == line)

                # Paid months (count of unique months with 'paid' payments)
                paid_dates = payments.filtered(lambda p: p.move_state == 'posted').mapped('date_due')
                unique_months = {(d.year, d.month) for d in paid_dates if d}
                actual_months += len(unique_months)

                # Revenue
                revenue_collected += sum(p.amount for p in payments if p.move_state == 'posted')
                revenue_expected += sum(p.amount for p in payments)

            property.rent_count = rent_count
            property.total_rent_months = total_months
            property.actual_rent_months = actual_months
            property.revenue_collected = revenue_collected
            property.revenue_expected = revenue_expected
            property.total_amount_due = revenue_expected - revenue_collected
