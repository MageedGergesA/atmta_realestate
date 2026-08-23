from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


HIERARCHY_LEVELS = [
    ('compound', 'Compound'),
    ('building', 'Building'),
    ('floor', 'Floor'),
    ('unit', 'Unit'),
    ('room', 'Room'),
]
HIERARCHY_RANK = {key: idx for idx, (key, _label) in enumerate(HIERARCHY_LEVELS)}


class RealEstateProperty(models.Model):
    _name = 'realestate.property'
    _description = 'Real Estate Property'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _inherits = {'product.template': 'product_tmpl_id'}
    _parent_store = True
    _order = 'property_code'

    product_tmpl_id = fields.Many2one(
        'product.template',
        string='Product Template',
        required=True,
        ondelete='cascade',
        auto_join=True,
    )
    product_variant_id = fields.Many2one(
        'product.product',
        string='Product Variant',
        related='product_tmpl_id.product_variant_id',
        store=True,
        readonly=True,
    )

    # Property identity
    property_code = fields.Char(
        string='Code', required=True, copy=False, index='trigram',
        default=lambda self: _('New'), tracking=True,
    )
    property_ref = fields.Char(string="Property Reference", tracking=True)
    property_number = fields.Char(string="Property Number", tracking=True)
    property_type_id = fields.Many2one('property.type', string="Property Type", tracking=True)

    # ------------------------------------------------------------------
    # Company — core, not leasing
    # ------------------------------------------------------------------
    # A property belongs to a company before it is ever leased or sold, and the
    # global isolation rule this module ships filters on it. Declaring it in a
    # module above this one would mean Property Core could not be installed
    # multi-company-correct on its own.
    #
    # Materialised from the delegated product template so it can carry a
    # database index and scope the uniqueness constraints. Writing it writes the
    # product template.
    company_id = fields.Many2one(
        'res.company', string='Company',
        related='product_tmpl_id.company_id', store=True, readonly=False,
        index=True, precompute=True,
        help="Materialised from the delegated product template so it can carry "
             "a database index and scope the uniqueness constraints. Writing "
             "it writes the product template.",
    )
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
    state_id = fields.Many2one(
        'res.country.state', string='State',
        domain="[('country_id', '=', country_id)]",
    )
    city = fields.Char(string="City", tracking=True)
    district = fields.Char(string="District", tracking=True)
    sub_area = fields.Char(string="Sub-area", tracking=True)
    street_name = fields.Char(string="Street Name", tracking=True)
    building_no = fields.Char(string="Building Number", tracking=True)
    landmark = fields.Char(string="Landmark", tracking=True)
    latitude = fields.Float(string="Latitude", digits=(10, 7), tracking=True)
    longitude = fields.Float(string="Longitude", digits=(10, 7), tracking=True)

    # Status & availability
    state = fields.Selection([
        ('available', 'Available'),
        ('reserved', 'Reserved'),
        ('rented', 'Rented'),
        ('sold', 'Sold'),
        ('maintenance', 'Under Maintenance'),
        ('inactive', 'Inactive'),
    ], string="Status", default='available', tracking=True)
    resale_count = fields.Integer(
        string='Times Resold', default=0, readonly=True, copy=False,
        help='Incremented each time the property is marked for resale after being sold.',
    )

    def action_mark_for_resale(self):
        """Re-open a sold property for resale (the only way to undo 'sold' state).
        Increments resale_count for traceability."""
        for rec in self:
            if rec.state != 'sold':
                raise ValidationError(_(
                    "Only sold properties can be marked for resale. "
                    "Property '%s' is in state '%s'."
                ) % (rec.display_name, rec.state))
            rec.write({
                'state': 'available',
                'resale_count': rec.resale_count + 1,
            })

    def _check_available_for_new_sale(self):
        """Raise if any property in self is not in a state that allows starting a new sale flow."""
        for rec in self:
            if rec.state == 'sold':
                raise ValidationError(_(
                    "Property '%s' is already sold. "
                    "Use 'Mark for Resale' on the property to re-open it for sale."
                ) % rec.display_name)
            if rec.state in ('maintenance', 'inactive'):
                raise ValidationError(_(
                    "Property '%s' is in state '%s' and cannot be sold."
                ) % (rec.display_name, rec.state))
    occupancy_state = fields.Selection([
        ('available', 'Available'),
        ('partial', 'Partially Rented'),
        ('rented', 'Fully Rented'),
    ], string="Occupancy", compute='_compute_occupancy_state', store=True)
    next_available_date = fields.Date(string="Next Available Date", tracking=True)

    # Ownership
    owner_id = fields.Many2one('res.partner', string="Property Owner", tracking=True)
    is_internal = fields.Boolean(string="Company-Owned Asset", tracking=True)
    manager_id = fields.Many2one('res.users', string="Property Manager", tracking=True)

    # Media / attachments
    property_Attachment_media_ids = fields.One2many(
        'property.image', 'property_id',
        string="Attachments and Media",
        copy=True,
    )
    attachment_ids = fields.Many2many(
        'ir.attachment', 'property_attachment_rel', 'property_id', 'attachment_id',
        string='Attachments',
    )

    # Hierarchy
    hierarchy_level = fields.Selection(
        HIERARCHY_LEVELS, string="Hierarchy Level",
        required=True, default='unit', tracking=True,
        help="Position in the property tree. A child's level must be deeper than its parent's.",
    )
    parent_id = fields.Many2one('realestate.property', string="Parent Property", ondelete='restrict', tracking=True)
    child_ids = fields.One2many('realestate.property', 'parent_id', string="Sub Properties")
    child_count = fields.Integer(compute='_compute_child_count')
    parent_path = fields.Char(index=True, unaccent=False)

    # Building details
    property_usage_id = fields.Many2one('property.usage', string='Property Usage')
    number_of_floors = fields.Integer(string='Number Of Floors')
    number_of_units = fields.Integer(string='Number Of Units')
    number_of_elevators = fields.Integer(string='Number Of Elevators')
    number_of_parking_lots = fields.Integer(string='Number Of Parking Lots')
    number_of_ac = fields.Integer(string='Number Of ACs')
    is_furnished = fields.Boolean(string='Is Furnished')
    ac_type = fields.Selection([
        ('split', _('Split AC')),
        ('window', _('Window AC')),
        ('central', _('Central AC')),
        ('cassette', _('Cassette AC')),
        ('portable', _('Portable AC')),
        ('floor_standing', _('Floor Standing AC')),
        ('ducted', _('Ducted AC')),
        ('vrf', _('VRF AC')),
    ], string='AC Type')

    # Utility meters
    electricity_meter_number = fields.Char(string="Electricity Meter Number")
    electricity_current_reading = fields.Float(string="Electricity Current Reading", digits=(16, 2))
    gas_meter_number = fields.Char(string="Gas Meter Number")
    gas_current_reading = fields.Float(string="Gas Current Reading", digits=(16, 2))
    water_meter_number = fields.Char(string="Water Meter Number")
    water_current_reading = fields.Float(string="Water Current Reading", digits=(16, 2))

    currency_id = fields.Many2one(
        'res.currency', string="Currency", required=True,
        default=lambda self: self.env.company.currency_id,
    )

    _sql_constraints = [
        ('property_code_uniq', 'UNIQUE(property_code)', 'Property Code must be unique.'),
    ]

    @api.constrains('property_code')
    def _check_unique_code(self):
        for rec in self:
            if rec.property_code and rec.property_code != _('New'):
                if self.search_count([('property_code', '=', rec.property_code), ('id', '!=', rec.id)]):
                    raise ValidationError(_("Property Code '%s' already exists.") % rec.property_code)

    @api.constrains('latitude', 'longitude')
    def _check_coordinates(self):
        for rec in self:
            if rec.latitude and not (-90 <= rec.latitude <= 90):
                raise ValidationError(_("Latitude must be between -90 and 90."))
            if rec.longitude and not (-180 <= rec.longitude <= 180):
                raise ValidationError(_("Longitude must be between -180 and 180."))

    @api.constrains('parent_id')
    def _check_parent_recursion(self):
        if not self._check_recursion():
            raise ValidationError(_("You cannot create a recursive property hierarchy."))

    @api.constrains('parent_id', 'hierarchy_level')
    def _check_parent_level(self):
        labels = dict(HIERARCHY_LEVELS)
        for rec in self:
            parent = rec.parent_id
            if not parent or not parent.hierarchy_level or not rec.hierarchy_level:
                continue
            if HIERARCHY_RANK[rec.hierarchy_level] <= HIERARCHY_RANK[parent.hierarchy_level]:
                raise ValidationError(_(
                    "%(child)s (%(child_level)s) cannot be a child of %(parent)s (%(parent_level)s). "
                    "A child's hierarchy level must be deeper than its parent's.",
                    child=rec.display_name,
                    child_level=labels[rec.hierarchy_level],
                    parent=parent.display_name,
                    parent_level=labels[parent.hierarchy_level],
                ))

    @api.depends('child_ids')
    def _compute_child_count(self):
        for rec in self:
            rec.child_count = len(rec.child_ids)

    @api.depends('state', 'child_ids.occupancy_state')
    def _compute_occupancy_state(self):
        for rec in self:
            if not rec.child_ids:
                rec.occupancy_state = 'rented' if rec.state == 'rented' else 'available'
                continue
            states = set(rec.child_ids.mapped('occupancy_state'))
            if states == {'available'}:
                rec.occupancy_state = 'available'
            elif states == {'rented'}:
                rec.occupancy_state = 'rented'
            else:
                rec.occupancy_state = 'partial'

    def action_view_hierarchy(self):
        self.ensure_one()
        root = self
        while root.parent_id:
            root = root.parent_id
        return {
            'type': 'ir.actions.act_window',
            'name': _('Property Hierarchy'),
            'res_model': 'realestate.property',
            'view_mode': 'hierarchy,list,form',
            'domain': [('id', 'child_of', root.id)],
            'context': {'hierarchy_root_id': root.id},
        }

    def set_property_inactive(self):
        for rec in self:
            if rec.state not in ('reserved', 'rented', 'maintenance'):
                rec.state = 'inactive'

    def set_property_available(self):
        for rec in self:
            if rec.state == 'inactive':
                rec.state = 'available'

    @api.model_create_multi
    def create(self, vals_list):
        Sequence = self.env['ir.sequence']
        for vals in vals_list:
            if vals.get('property_code', _('New')) == _('New'):
                vals['property_code'] = self._next_free_property_code()
        return super().create(vals_list)

    @api.model
    def _next_free_property_code(self, max_attempts=1000):
        """Pull the next sequence value, skipping any that already exist in DB.
        Self-heals after demo data / fixture imports that wrote explicit codes
        ahead of the sequence."""
        Sequence = self.env['ir.sequence']
        for _attempt in range(max_attempts):
            candidate = Sequence.next_by_code('realestate.property.code')
            if not candidate:
                raise ValidationError(_(
                    "Sequence 'realestate.property.code' is missing. "
                    "Upgrade the 'atmta_property_core' module to install it, "
                    "or create it manually under Settings → Technical → Sequences."
                ))
            self.env.cr.execute(
                "SELECT 1 FROM realestate_property WHERE property_code = %s LIMIT 1",
                (candidate,),
            )
            if not self.env.cr.fetchone():
                return candidate
        raise ValidationError(_(
            "Could not allocate a free property code after %d attempts. "
            "Run env['realestate.property']._sync_property_code_sequence() "
            "to realign the sequence."
        ) % max_attempts)

    @api.model
    def _sync_property_code_sequence(self):
        """Align the property_code sequence to the highest existing PROP-NNNNN code.
        Prevents sequence drift after loading demo data / fixture imports that
        write explicit codes."""
        import re
        self.env.cr.execute("""
            SELECT property_code FROM realestate_property
            WHERE property_code ~ '^PROP-[0-9]+$'
        """)
        max_num = 0
        for (code,) in self.env.cr.fetchall():
            m = re.match(r'^PROP-(\d+)$', code)
            if m:
                max_num = max(max_num, int(m.group(1)))
        seq = self.env.ref(
            'atmta_property_core.seq_realestate_property_code',
            raise_if_not_found=False,
        )
        if seq and max_num >= seq.number_next:
            seq.number_next = max_num + 1


class PropertyUsage(models.Model):
    _name = 'property.usage'
    _description = 'Property Usage'

    name = fields.Char(string='Name', required=True)
