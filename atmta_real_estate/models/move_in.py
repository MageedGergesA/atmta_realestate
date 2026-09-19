"""Move-in inspection (Phase 20).

The move-in is the moment the lease stops being paperwork and becomes a
physical handover. Everything recorded here exists to answer a question that
will be asked at move-out, often a year later and often in dispute:

* what condition was the unit in?
* what meter readings did the tenant inherit?
* how many keys, cards and remotes did they get?
* what furniture and fittings were in the unit?
* what defects were already there and are *not* the tenant's fault?

Completing a move-in stamps the occupancy on the lease allocations, which is
what makes the property read as occupied.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

MOVE_IN_STATES = [
    ('schedule', 'Scheduled'),
    ('inspection', 'Inspection'),
    ('completed', 'Completed'),
    ('cancelled', 'Cancelled'),
]

CONDITIONS = [
    ('excellent', 'Excellent'),
    ('good', 'Good'),
    ('fair', 'Fair'),
    ('poor', 'Poor'),
]


class MoveIn(models.Model):
    _name = 'realestate.move.in'
    _description = 'Tenant Move-In'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'scheduled_date desc, id desc'

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'),
    )
    contract_id = fields.Many2one(
        'realestate.contract', string='Lease', required=True,
        ondelete='cascade', index=True, tracking=True,
    )
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True, readonly=True)
    partner_id = fields.Many2one(
        related='contract_id.partner_id', string='Tenant', store=True, readonly=True)
    property_line_id = fields.Many2one(
        'realestate.contract.property.line', string='Property Allocation',
        domain="[('contract_id', '=', contract_id)]", ondelete='cascade',
        help="Leave empty to move in to every property on the lease.",
    )
    property_id = fields.Many2one(
        'realestate.property', string='Property',
        compute='_compute_property', store=True, readonly=False, index=True,
    )

    state = fields.Selection(
        MOVE_IN_STATES, default='schedule', required=True, tracking=True,
        index=True, copy=False,
    )
    scheduled_date = fields.Datetime(string='Scheduled', tracking=True)
    actual_date = fields.Datetime(string='Completed On', readonly=True, tracking=True)
    user_id = fields.Many2one(
        'res.users', string='Responsible', tracking=True,
        default=lambda self: self.env.user,
    )

    # ---------------- Handover items ----------------
    keys_delivered = fields.Integer(string='Keys Delivered')
    access_cards_delivered = fields.Integer(string='Access Cards')
    parking_remotes_delivered = fields.Integer(string='Parking Remotes')

    # ---------------- Meter readings ----------------
    reading_ids = fields.One2many(
        'realestate.property.meter.reading', 'move_in_id',
        string='Meter Readings',
    )
    reading_count = fields.Integer(compute='_compute_reading_count')

    # ---------------- Condition ----------------
    overall_condition = fields.Selection(
        CONDITIONS, string='Overall Condition', default='good', tracking=True)
    inventory_notes = fields.Html(
        string='Inventory / Furniture Checklist',
        help="What was in the unit at handover. The reference point for the "
             "move-out check.",
    )
    existing_defects = fields.Html(
        string='Pre-Existing Defects',
        help="Damage present BEFORE the tenant moved in. Recording it here is "
             "what stops it being charged to them at move-out.",
    )
    photo_ids = fields.Many2many(
        'ir.attachment', 'realestate_move_in_photo_rel',
        'move_in_id', 'attachment_id', string='Photos',
    )

    # ---------------- Acknowledgement ----------------
    tenant_acknowledged = fields.Boolean(
        string='Tenant Acknowledged', tracking=True,
        help="The tenant has signed off on the condition and inventory above.",
    )
    acknowledged_date = fields.Date(readonly=True)
    notes = fields.Text()

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('property_line_id', 'contract_id')
    def _compute_property(self):
        for rec in self:
            if rec.property_line_id:
                rec.property_id = rec.property_line_id.property_id
            elif not rec.property_id:
                rec.property_id = rec.contract_id.property_id

    @api.depends('reading_ids')
    def _compute_reading_count(self):
        for rec in self:
            rec.reading_count = len(rec.reading_ids)

    # ==================================================================
    # Workflow
    # ==================================================================
    def action_start_inspection(self):
        for rec in self:
            if rec.state != 'schedule':
                raise UserError(_(
                    "Move-in %s is not scheduled.", rec.name))
            rec.state = 'inspection'
        return True

    def action_capture_meter_readings(self):
        """Create a reading row for every meter on the property.

        Pre-fills from the meter's current value so the inspector edits rather
        than types from scratch.
        """
        self.ensure_one()
        Reading = self.env['realestate.property.meter.reading']
        target_date = fields.Date.context_today(self)
        created = Reading
        for meter in self._target_properties().mapped('meter_ids').filtered('active'):
            if self.reading_ids.filtered(lambda r, m=meter: r.meter_id == m):
                continue
            same_day = Reading.search([('meter_id', '=', meter.id),
                                       ('reading_date', '=', target_date)], limit=1)
            if same_day:
                # One reading a day per meter. A reading already taken today is
                # this handover's reading; skipping it left the handover
                # without one.
                if not same_day.move_in_id:
                    same_day.move_in_id = self.id
                    created |= same_day
                continue
            created |= Reading.create({
                'meter_id': meter.id,
                'reading_date': target_date,
                'current_reading': meter.current_reading,
                'move_in_id': self.id,
                'source_ref': '%s,%s' % (self._name, self.id),
                'notes': _("Captured at move-in %s") % self.name,
            })
        return created

    def action_complete(self):
        """Finish the handover and start the tenant's occupancy."""
        for rec in self:
            if rec.state not in ('schedule', 'inspection'):
                raise UserError(_(
                    "Move-in %s cannot be completed from its current state.",
                    rec.name))
            if not rec.tenant_acknowledged:
                raise UserError(_(
                    "The tenant must acknowledge the condition report before "
                    "move-in %s can be completed.", rec.name))
            if rec.contract_id.lifecycle_state not in ('active', 'notice'):
                raise UserError(_(
                    "Lease '%s' must be active before a tenant can move in.",
                    rec.contract_id.display_name))
            when = fields.Datetime.now()
            rec.write({
                'state': 'completed',
                'actual_date': when,
                'acknowledged_date': fields.Date.context_today(rec),
            })
            rec._register_occupancy(when.date())
            rec.contract_id.message_post(body=_(
                "Move-in completed for %(prop)s on %(date)s. Keys: %(keys)s, "
                "cards: %(cards)s, remotes: %(remotes)s. Condition: %(cond)s.",
                prop=rec.property_id.display_name or _('all properties'),
                date=when.date(), keys=rec.keys_delivered,
                cards=rec.access_cards_delivered,
                remotes=rec.parking_remotes_delivered,
                cond=dict(CONDITIONS).get(rec.overall_condition, '')))
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == 'completed':
                raise UserError(_(
                    "Move-in %s is complete and cannot be cancelled.", rec.name))
            rec.state = 'cancelled'
        return True

    # ==================================================================
    # Helpers
    # ==================================================================
    def _target_allocations(self):
        self.ensure_one()
        if self.property_line_id:
            return self.property_line_id
        return self.contract_id.property_line_ids

    def _target_properties(self):
        self.ensure_one()
        return self._target_allocations().mapped('property_id')

    def _register_occupancy(self, on_date):
        """Stamp the move-in date so the property reads as occupied."""
        self.ensure_one()
        self._target_allocations().action_register_move_in(on_date)

    @api.constrains('contract_id', 'property_line_id')
    def _check_allocation_belongs(self):
        for rec in self:
            if rec.property_line_id and rec.property_line_id.contract_id != rec.contract_id:
                raise ValidationError(_(
                    "The selected allocation does not belong to lease '%s'.",
                    rec.contract_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.move.in') or _('New')
        return super().create(vals_list)


class MeterReadingMoveIn(models.Model):
    _inherit = 'realestate.property.meter.reading'

    move_in_id = fields.Many2one(
        'realestate.move.in', string='Move-In', ondelete='set null', index=True)
    move_out_id = fields.Many2one(
        'realestate.move.out', string='Move-Out', ondelete='set null', index=True)


class ContractMoveInMixin(models.Model):
    _inherit = 'realestate.contract'

    move_in_ids = fields.One2many(
        'realestate.move.in', 'contract_id', string='Move-Ins')
    move_in_count = fields.Integer(compute='_compute_move_in_count')

    @api.depends('move_in_ids')
    def _compute_move_in_count(self):
        for rec in self:
            rec.move_in_count = len(rec.move_in_ids)

    def action_schedule_move_in(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Move-In'),
            'res_model': 'realestate.move.in',
            'view_mode': 'form',
            'context': {
                'default_contract_id': self.id,
                'default_property_id': self.property_id.id,
                'default_scheduled_date': self.start_date,
            },
        }

    def action_view_move_ins(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Move-Ins'),
            'res_model': 'realestate.move.in',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }
