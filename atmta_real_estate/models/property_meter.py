"""Normalised utility meters and readings (Phase 23).

Before this, a property carried six flat columns::

    electricity_meter_number / electricity_current_reading
    gas_meter_number         / gas_current_reading
    water_meter_number       / water_current_reading

which cannot express a unit with two electricity meters, cannot record who took
a reading or when, and silently destroys the previous value on every update --
so consumption was unknowable and a disputed bill was unanswerable.

Meters and readings are now first-class records. The legacy columns are
**kept and auto-synchronised** (the newest reading writes back to
``*_current_reading``) so existing views, reports and downstream code keep
working untouched.

Scope note (Phase 35): this is a meter *register*, not a utility billing
engine. It records consumption and exposes it to the charge-rule engine; it
does not model tariffs, slabs or provider invoicing.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

METER_TYPES = [
    ('electricity', 'Electricity'),
    ('water', 'Water'),
    ('gas', 'Gas'),
    ('other', 'Other'),
]

#: meter type -> the legacy property columns it keeps in sync.
LEGACY_COLUMNS = {
    'electricity': ('electricity_meter_number', 'electricity_current_reading'),
    'water': ('water_meter_number', 'water_current_reading'),
    'gas': ('gas_meter_number', 'gas_current_reading'),
}


class PropertyMeter(models.Model):
    _name = 'realestate.property.meter'
    _description = 'Property Utility Meter'
    _inherit = ['mail.thread']
    _order = 'property_id, meter_type, id'

    name = fields.Char(
        string='Meter Number', required=True, index='trigram', tracking=True,
    )
    property_id = fields.Many2one(
        'realestate.property', string='Property', required=True,
        ondelete='cascade', index=True, tracking=True,
    )
    company_id = fields.Many2one(
        related='property_id.company_id', store=True, index=True, readonly=True,
    )
    meter_type = fields.Selection(
        METER_TYPES, string='Type', required=True, default='electricity',
        index=True, tracking=True,
    )
    provider_id = fields.Many2one(
        'res.partner', string='Utility Provider', tracking=True,
        help="The utility company. Used when the landlord settles the bill.",
    )
    uom_id = fields.Many2one(
        'uom.uom', string='Unit of Measure',
        help="kWh, m³, ... Used to label consumption and to price "
             "meter-based charge rules.",
    )
    installation_date = fields.Date(tracking=True)
    opening_reading = fields.Float(
        string='Opening Reading', digits=(16, 3), tracking=True,
        help="Reading at installation / handover. The first consumption is "
             "measured against this.",
    )
    active = fields.Boolean(default=True)
    notes = fields.Text()

    reading_ids = fields.One2many(
        'realestate.property.meter.reading', 'meter_id', string='Readings',
    )
    reading_count = fields.Integer(compute='_compute_reading_stats')
    current_reading = fields.Float(
        string='Current Reading', digits=(16, 3),
        compute='_compute_reading_stats', store=True,
    )
    last_reading_date = fields.Date(
        compute='_compute_reading_stats', store=True,
    )

    _sql_constraints = [
        ('meter_number_company_uniq',
         'unique(company_id, name, meter_type)',
         'A meter with this number and type already exists in this company.'),
    ]

    @api.depends('reading_ids.current_reading', 'reading_ids.reading_date',
                 'opening_reading')
    def _compute_reading_stats(self):
        for rec in self:
            # New readings on the meter form are unsaved and all default to
            # today, so ties are broken by the saved id (0 when new), never by
            # comparing unsaved ids.
            readings = rec.reading_ids.sorted(
                lambda r: (r.reading_date or fields.Date.today(), r._origin.id or 0))
            rec.reading_count = len(readings)
            rec.current_reading = readings[-1].current_reading if readings else rec.opening_reading
            rec.last_reading_date = readings[-1].reading_date if readings else False

    @api.depends('name', 'meter_type')
    def _compute_display_name(self):
        labels = dict(METER_TYPES)
        for rec in self:
            rec.display_name = '%s — %s' % (
                labels.get(rec.meter_type, rec.meter_type), rec.name or '?')

    def _sync_legacy_columns(self):
        """Write the newest reading back to the legacy property columns.

        Keeps the pre-upgrade property form, the property report and any
        downstream reader correct without them knowing meters exist.
        """
        for meter in self:
            columns = LEGACY_COLUMNS.get(meter.meter_type)
            if not columns or not meter.property_id:
                continue
            number_field, reading_field = columns
            meter.property_id.write({
                number_field: meter.name,
                reading_field: meter.current_reading,
            })

    @api.model_create_multi
    def create(self, vals_list):
        meters = super().create(vals_list)
        meters._sync_legacy_columns()
        return meters

    def write(self, vals):
        res = super().write(vals)
        if {'name', 'meter_type', 'opening_reading'} & set(vals):
            self._sync_legacy_columns()
        return res

    def action_view_readings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Readings — %s') % self.display_name,
            'res_model': 'realestate.property.meter.reading',
            'view_mode': 'list,form',
            'domain': [('meter_id', '=', self.id)],
            'context': {'default_meter_id': self.id},
        }


class PropertyMeterReading(models.Model):
    _name = 'realestate.property.meter.reading'
    _description = 'Property Meter Reading'
    _order = 'reading_date desc, id desc'

    meter_id = fields.Many2one(
        'realestate.property.meter', string='Meter', required=True,
        ondelete='cascade', index=True,
    )
    property_id = fields.Many2one(
        related='meter_id.property_id', store=True, index=True, readonly=True,
    )
    company_id = fields.Many2one(
        related='meter_id.company_id', store=True, index=True, readonly=True,
    )
    meter_type = fields.Selection(related='meter_id.meter_type', store=True, readonly=True)
    uom_id = fields.Many2one(related='meter_id.uom_id', readonly=True)

    reading_date = fields.Date(
        string='Reading Date', required=True, index=True,
        default=fields.Date.context_today,
    )
    previous_reading = fields.Float(
        string='Previous Reading', digits=(16, 3),
        compute='_compute_previous_reading', store=True, readonly=False,
        help="Auto-filled from the preceding reading (or the meter's opening "
             "value). Editable for corrections.",
    )
    current_reading = fields.Float(
        string='Current Reading', digits=(16, 3), required=True,
    )
    consumption = fields.Float(
        string='Consumption', digits=(16, 3),
        compute='_compute_consumption', store=True,
    )
    reading_kind = fields.Selection(
        [('actual', 'Actual'), ('estimated', 'Estimated')],
        string='Kind', default='actual', required=True,
    )
    photo = fields.Image(string='Meter Photo', max_width=1280, max_height=1280)
    user_id = fields.Many2one(
        'res.users', string='Read By', default=lambda self: self.env.user,
    )
    #: Set when this reading came from a move-in / move-out inspection, so the
    #: settlement can be traced back to the document that produced it.
    source_ref = fields.Reference(
        selection=[('realestate.move.in', 'Move-In'),
                   ('realestate.move.out', 'Move-Out')],
        string='Source Document', readonly=True, copy=False,
    )
    notes = fields.Text()

    _sql_constraints = [
        ('one_reading_per_meter_per_day',
         'unique(meter_id, reading_date)',
         'This meter already has a reading on that date.'),
    ]

    @api.depends('meter_id', 'reading_date')
    def _compute_previous_reading(self):
        for rec in self:
            if not rec.meter_id:
                rec.previous_reading = 0.0
                continue
            # On a form the reading, and on a new meter the meter too, are
            # unsaved records: search with their saved ids only. A meter that is
            # not saved yet has no readings in the database to follow.
            meter = rec.meter_id._origin
            if not meter:
                rec.previous_reading = rec.meter_id.opening_reading
                continue
            domain = [
                ('meter_id', '=', meter.id),
                ('reading_date', '<', rec.reading_date or fields.Date.context_today(rec)),
            ]
            if rec._origin.id:
                domain.append(('id', '!=', rec._origin.id))
            prior = self.search(domain, order='reading_date desc, id desc', limit=1)
            rec.previous_reading = (
                prior.current_reading if prior else rec.meter_id.opening_reading)

    @api.depends('previous_reading', 'current_reading')
    def _compute_consumption(self):
        for rec in self:
            rec.consumption = (rec.current_reading or 0.0) - (rec.previous_reading or 0.0)

    @api.constrains('previous_reading', 'current_reading', 'reading_kind')
    def _check_reading_not_backwards(self):
        """A meter that goes backwards is a data-entry error or a replaced
        meter. Force the user to say which."""
        for rec in self:
            if rec.current_reading < rec.previous_reading and rec.reading_kind == 'actual':
                raise ValidationError(_(
                    "Reading %(cur)s on meter '%(meter)s' is lower than the "
                    "previous reading %(prev)s. If the meter was replaced or "
                    "rolled over, record it as an Estimated reading and note "
                    "the reason.",
                    cur=rec.current_reading, prev=rec.previous_reading,
                    meter=rec.meter_id.display_name,
                ))

    @api.model_create_multi
    def create(self, vals_list):
        readings = super().create(vals_list)
        readings.mapped('meter_id')._sync_legacy_columns()
        return readings

    def write(self, vals):
        res = super().write(vals)
        if {'current_reading', 'reading_date'} & set(vals):
            self.mapped('meter_id')._sync_legacy_columns()
        return res


class PropertyMeterMixin(models.Model):
    _inherit = 'realestate.property'

    meter_ids = fields.One2many(
        'realestate.property.meter', 'property_id', string='Utility Meters',
    )
    meter_count = fields.Integer(compute='_compute_meter_count')

    @api.depends('meter_ids')
    def _compute_meter_count(self):
        for rec in self:
            rec.meter_count = len(rec.meter_ids)

    def action_view_meters(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Utility Meters'),
            'res_model': 'realestate.property.meter',
            'view_mode': 'list,form',
            'domain': [('property_id', '=', self.id)],
            'context': {'default_property_id': self.id},
        }

    def _normalise_legacy_meters(self):
        """Create meter records from the flat legacy columns.

        Called by the migration and available for manual re-runs. Idempotent:
        a meter is only created when one of that type does not already exist.
        """
        Meter = self.env['realestate.property.meter']
        created = Meter
        for prop in self:
            for meter_type, (number_field, reading_field) in LEGACY_COLUMNS.items():
                number = prop[number_field]
                if not number:
                    continue
                if prop.meter_ids.filtered(lambda m, t=meter_type: m.meter_type == t):
                    continue
                created |= Meter.create({
                    'name': number,
                    'property_id': prop.id,
                    'meter_type': meter_type,
                    'opening_reading': prop[reading_field] or 0.0,
                    'notes': _("Migrated from the legacy %s columns.") % meter_type,
                })
        return created
