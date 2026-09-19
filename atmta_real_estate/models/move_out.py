"""Move-out inspection and settlement (Phase 21).

The move-out answers the questions the move-in recorded: what has changed, what
is fair wear and tear, what is damage, and what does that cost. It produces the
deduction list that the deposit settlement then acts on.

The rule that matters most here is the last one:

    **Completing a move-out does not make the property available.**

A unit with a hole in the wall and no keys back is not lettable. Move-out hands
the unit to a :class:`~odoo.addons.atmta_real_estate.models.unit_turn.UnitTurn`,
and the turn decides when it is ready. Marking it available at move-out is
exactly how a unit gets double-booked into a lease it cannot honour.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .move_in import CONDITIONS

MOVE_OUT_STATES = [
    ('schedule', 'Scheduled'),
    ('inspection', 'Inspection'),
    ('assessment', 'Assessment'),
    ('completed', 'Completed'),
    ('cancelled', 'Cancelled'),
]


class MoveOut(models.Model):
    _name = 'realestate.move.out'
    _description = 'Tenant Move-Out'
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
    currency_id = fields.Many2one(
        related='contract_id.currency_id', store=True, readonly=True)
    partner_id = fields.Many2one(
        related='contract_id.partner_id', string='Tenant', store=True, readonly=True)
    termination_id = fields.Many2one(
        'realestate.contract.termination', string='Termination',
        ondelete='set null', index=True,
    )
    property_line_id = fields.Many2one(
        'realestate.contract.property.line', string='Property Allocation',
        domain="[('contract_id', '=', contract_id)]", ondelete='cascade',
    )
    property_id = fields.Many2one(
        'realestate.property', string='Property',
        compute='_compute_property', store=True, readonly=False, index=True,
    )
    move_in_id = fields.Many2one(
        'realestate.move.in', string='Move-In Reference',
        compute='_compute_move_in', store=True, readonly=False,
        help="The move-in this inspection is compared against.",
    )

    state = fields.Selection(
        MOVE_OUT_STATES, default='schedule', required=True, tracking=True,
        index=True, copy=False,
    )
    scheduled_date = fields.Datetime(string='Scheduled', tracking=True)
    actual_date = fields.Datetime(string='Completed On', readonly=True, tracking=True)
    user_id = fields.Many2one(
        'res.users', string='Responsible', default=lambda self: self.env.user)

    # ---------------- Returns ----------------
    keys_returned = fields.Integer(string='Keys Returned')
    access_cards_returned = fields.Integer(string='Access Cards Returned')
    parking_remotes_returned = fields.Integer(string='Parking Remotes Returned')
    keys_missing = fields.Integer(
        string='Items Missing', compute='_compute_missing_items', store=True,
        help="Difference against what was handed over at move-in.",
    )

    # ---------------- Meter readings ----------------
    reading_ids = fields.One2many(
        'realestate.property.meter.reading', 'move_out_id',
        string='Final Meter Readings',
    )
    total_consumption = fields.Float(
        string='Consumption Since Move-In', compute='_compute_consumption')

    # ---------------- Condition ----------------
    overall_condition = fields.Selection(
        CONDITIONS, string='Condition at Exit', default='good', tracking=True)
    damages = fields.Html(string='Damages Found')
    cleaning_required = fields.Boolean(string='Cleaning Required', tracking=True)
    repair_required = fields.Boolean(string='Repair Required', tracking=True)
    photo_ids = fields.Many2many(
        'ir.attachment', 'realestate_move_out_photo_rel',
        'move_out_id', 'attachment_id', string='Photos',
    )

    # ---------------- Settlement ----------------
    deduction_ids = fields.One2many(
        'realestate.move.out.deduction', 'move_out_id', string='Deposit Deductions')
    deduction_total = fields.Monetary(
        string='Total Deductions', compute='_compute_totals', store=True)
    final_charges = fields.Monetary(
        string='Other Final Charges', tracking=True)
    settlement_total = fields.Monetary(
        string='Total Chargeable', compute='_compute_totals', store=True)

    tenant_acknowledged = fields.Boolean(
        string='Tenant Acknowledged', tracking=True)
    acknowledged_date = fields.Date(readonly=True)
    tenant_dispute = fields.Text(
        string='Tenant Dispute',
        help="Recorded verbatim when the tenant disagrees with the assessment.",
    )
    unit_turn_id = fields.Many2one(
        'realestate.unit.turn', string='Unit Turn', readonly=True, copy=False)
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

    @api.depends('contract_id', 'property_id')
    def _compute_move_in(self):
        for rec in self:
            if rec.move_in_id:
                continue
            candidates = rec.contract_id.move_in_ids.filtered(
                lambda m: m.state == 'completed'
                and (not rec.property_id or m.property_id == rec.property_id))
            rec.move_in_id = candidates[:1].id if candidates else False

    @api.depends('keys_returned', 'access_cards_returned',
                 'parking_remotes_returned', 'move_in_id')
    def _compute_missing_items(self):
        for rec in self:
            source = rec.move_in_id
            if not source:
                rec.keys_missing = 0
                continue
            rec.keys_missing = max(
                (source.keys_delivered - rec.keys_returned), 0
            ) + max(
                (source.access_cards_delivered - rec.access_cards_returned), 0
            ) + max(
                (source.parking_remotes_delivered - rec.parking_remotes_returned), 0)

    @api.depends('reading_ids.consumption')
    def _compute_consumption(self):
        for rec in self:
            rec.total_consumption = sum(rec.reading_ids.mapped('consumption'))

    @api.depends('deduction_ids.amount', 'final_charges')
    def _compute_totals(self):
        for rec in self:
            rec.deduction_total = sum(rec.deduction_ids.mapped('amount'))
            rec.settlement_total = rec.deduction_total + (rec.final_charges or 0.0)

    # ==================================================================
    # Workflow
    # ==================================================================
    def action_start_inspection(self):
        for rec in self:
            if rec.state != 'schedule':
                raise UserError(_("Move-out %s is not scheduled.", rec.name))
            rec.state = 'inspection'
        return True

    def action_capture_meter_readings(self):
        """Final readings for every meter on the property."""
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
                if not same_day.move_out_id:
                    same_day.move_out_id = self.id
                    created |= same_day
                continue
            created |= Reading.create({
                'meter_id': meter.id,
                'reading_date': target_date,
                'current_reading': meter.current_reading,
                'move_out_id': self.id,
                'source_ref': '%s,%s' % (self._name, self.id),
                'notes': _("Captured at move-out %s") % self.name,
            })
        return created

    def action_start_assessment(self):
        for rec in self:
            if rec.state != 'inspection':
                raise UserError(_(
                    "Inspect the unit before assessing move-out %s.", rec.name))
            rec.state = 'assessment'
        return True

    def action_complete(self):
        """Close the inspection, vacate the allocation, and open a unit turn."""
        for rec in self:
            if rec.state not in ('inspection', 'assessment'):
                raise UserError(_(
                    "Move-out %s cannot be completed from its current state.",
                    rec.name))
            if rec.deduction_ids and not rec.tenant_acknowledged \
                    and not rec.tenant_dispute:
                raise UserError(_(
                    "Deductions are proposed on move-out %s. Record either the "
                    "tenant's acknowledgement or their dispute before "
                    "completing.", rec.name))
            when = fields.Datetime.now()
            rec.write({
                'state': 'completed',
                'actual_date': when,
                'acknowledged_date': (fields.Date.context_today(rec)
                                      if rec.tenant_acknowledged else False),
            })
            rec._register_vacancy(when.date())
            turn = rec._open_unit_turn()
            rec.unit_turn_id = turn[:1].id if turn else False
            rec.contract_id.message_post(body=_(
                "Move-out completed for %(prop)s on %(date)s. Condition: "
                "%(cond)s. Deductions: %(ded)s. The unit is NOT yet available "
                "— see unit turn %(turn)s.",
                prop=rec.property_id.display_name or _('all properties'),
                date=when.date(),
                cond=dict(CONDITIONS).get(rec.overall_condition, ''),
                ded=rec.deduction_total,
                turn=turn[:1].display_name if turn else _('n/a')))
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == 'completed':
                raise UserError(_(
                    "Move-out %s is complete and cannot be cancelled.", rec.name))
            rec.state = 'cancelled'
        return True

    def action_settle_deposit(self):
        """Open the deposit settlement with this move-out's deductions.

        The deposit itself is settled by ``realestate.contract.deposit`` so the
        money moves through the liability account properly; this only carries
        the assessment across.
        """
        self.ensure_one()
        deposits = self.contract_id.deposit_record_ids.filtered(
            lambda d: d.state in ('held', 'received', 'partially_refunded'))
        if not deposits:
            raise UserError(_(
                "Lease '%s' has no deposit being held.",
                self.contract_id.display_name))
        deposit = deposits[0]
        reason = _("Move-out %(name)s deductions: %(detail)s") % {
            'name': self.name,
            'detail': ', '.join(
                '%s (%s)' % (d.name, d.amount) for d in self.deduction_ids
            ) or _('none'),
        }
        # A default_* key does nothing on an existing record, so opening the
        # deposit form carried neither the deductions nor the reason across.
        # The settlement dialog takes both, and the manager confirms the split.
        action = deposit.action_open_settlement()
        action['context'] = dict(
            action['context'],
            default_move_out_id=self.id,
            default_deduction_amount=min(self.deduction_total, deposit.held_amount),
            default_reason=reason,
        )
        return action

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

    def _register_vacancy(self, on_date):
        self.ensure_one()
        allocations = self._target_allocations().filtered('move_in_date')
        if allocations:
            allocations.action_register_move_out(on_date)

    def _open_unit_turn(self):
        """Hand the unit to the turn process.

        Deliberately does not touch availability -- see the module docstring.
        """
        self.ensure_one()
        Turn = self.env['realestate.unit.turn']
        turns = Turn
        for prop in self._target_properties():
            existing = Turn.search([
                ('property_id', '=', prop.id),
                ('state', 'not in', ('ready', 'cancelled')),
            ], limit=1)
            if existing:
                existing.move_out_id = self.id
                turns |= existing
                continue
            turns |= Turn.create({
                'property_id': prop.id,
                'contract_id': self.contract_id.id,
                'move_out_id': self.id,
                'termination_id': self.termination_id.id or False,
                'start_date': fields.Date.context_today(self),
                'cleaning_required': self.cleaning_required,
                'repair_required': self.repair_required,
            })
        return turns

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.move.out') or _('New')
        moves = super().create(vals_list)
        for move in moves:
            if move.termination_id and not move.termination_id.move_out_id:
                move.termination_id.move_out_id = move.id
        return moves


class MoveOutDeduction(models.Model):
    """One line of the move-out assessment."""
    _name = 'realestate.move.out.deduction'
    _description = 'Move-Out Deposit Deduction'
    _order = 'move_out_id, id'

    move_out_id = fields.Many2one(
        'realestate.move.out', string='Move-Out', required=True,
        ondelete='cascade', index=True,
    )
    currency_id = fields.Many2one(
        related='move_out_id.currency_id', store=True, readonly=True)
    name = fields.Char(string='Description', required=True)
    category = fields.Selection([
        ('cleaning', 'Cleaning'),
        ('repair', 'Repair'),
        ('damage', 'Damage'),
        ('missing_item', 'Missing Item'),
        ('unpaid_rent', 'Unpaid Rent'),
        ('utilities', 'Unpaid Utilities'),
        ('other', 'Other'),
    ], string='Category', default='repair', required=True)
    amount = fields.Monetary(string='Amount', required=True)
    is_wear_and_tear = fields.Boolean(
        string='Fair Wear & Tear',
        help="Flagged items are the landlord's cost, not the tenant's. Kept "
             "on the list so the decision is visible rather than silent.",
    )
    notes = fields.Text()

    @api.constrains('amount', 'is_wear_and_tear')
    def _check_amount(self):
        for rec in self:
            if rec.amount < 0:
                raise UserError(_("A deduction cannot be negative."))


class ContractMoveOutMixin(models.Model):
    _inherit = 'realestate.contract'

    move_out_ids = fields.One2many(
        'realestate.move.out', 'contract_id', string='Move-Outs')
    move_out_count = fields.Integer(compute='_compute_move_out_count')

    @api.depends('move_out_ids')
    def _compute_move_out_count(self):
        for rec in self:
            rec.move_out_count = len(rec.move_out_ids)

    def action_schedule_move_out(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Move-Out'),
            'res_model': 'realestate.move.out',
            'view_mode': 'form',
            'context': {
                'default_contract_id': self.id,
                'default_property_id': self.property_id.id,
                # A lease being terminated is left on the termination's
                # effective date, not on the end date it no longer reaches.
                'default_scheduled_date': (self.active_termination_id.effective_date
                                           or self.end_date),
                'default_termination_id': self.active_termination_id.id or False,
            },
        }

    def action_view_move_outs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Move-Outs'),
            'res_model': 'realestate.move.out',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }
