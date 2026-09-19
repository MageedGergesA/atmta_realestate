"""Unit turn -- the gap between one tenant leaving and the next arriving
(Phase 22).

Scope discipline (Phase 35): this is **not** a facilities-management module.
It is the minimum record needed to answer "is this unit lettable yet?", plus
clean hooks for a future maintenance or FM module to hang work off.

    LEASE ENDS → MOVE-OUT → UNIT TURN → CLEANING / REPAIR
               → FINAL INSPECTION → READY FOR LEASE

The turn is the **only** thing that returns a unit to the market. Move-out
does not, termination does not, and the lease ending does not. That single rule
is what stops a damaged unit being re-let before it is fixed.

Integration with ``real_estate_customer_service`` and
``real_estate_procurement`` is deliberately optional and late-bound: this
module never imports them, it only looks them up in the registry. Installing
them adds capability; not installing them changes nothing. No circular
dependency is created in either direction.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TURN_STATES = [
    ('draft', 'Draft'),
    ('cleaning', 'Cleaning'),
    ('repair', 'Repair'),
    ('inspection', 'Final Inspection'),
    ('ready', 'Ready for Lease'),
    ('cancelled', 'Cancelled'),
]


class UnitTurn(models.Model):
    _name = 'realestate.unit.turn'
    _description = 'Unit Turn'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'start_date desc, id desc'

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'),
    )
    property_id = fields.Many2one(
        'realestate.property', string='Property', required=True,
        ondelete='cascade', index=True, tracking=True,
    )
    company_id = fields.Many2one(
        related='property_id.company_id', store=True, index=True, readonly=True)
    contract_id = fields.Many2one(
        'realestate.contract', string='Outgoing Lease',
        ondelete='set null', index=True,
    )
    move_out_id = fields.Many2one(
        'realestate.move.out', string='Move-Out', ondelete='set null')
    termination_id = fields.Many2one(
        'realestate.contract.termination', string='Termination',
        ondelete='set null')

    state = fields.Selection(
        TURN_STATES, default='draft', required=True, tracking=True,
        index=True, copy=False,
    )
    start_date = fields.Date(
        string='Vacated On', required=True, index=True,
        default=fields.Date.context_today, tracking=True,
    )
    target_ready_date = fields.Date(string='Target Ready', tracking=True)
    actual_ready_date = fields.Date(string='Ready On', readonly=True, tracking=True)
    vacant_days = fields.Integer(
        string='Vacant Days', compute='_compute_vacant_days',
        help="Days since the unit was vacated, or until it was ready once the "
             "turn is complete. Computed for today and never stored: an open "
             "turn's count grows every day.",
    )
    turnaround_days = fields.Integer(
        string='Turnaround Days', compute='_compute_turnaround_days', store=True,
        help="Days from the unit being vacated to it being ready, for completed "
             "turns. Stored because it no longer changes once the turn is "
             "complete; feeds the average-vacant-days KPI.",
    )

    cleaning_required = fields.Boolean(tracking=True)
    cleaning_done = fields.Boolean(tracking=True)
    repair_required = fields.Boolean(tracking=True)
    repair_done = fields.Boolean(tracking=True)
    inspection_passed = fields.Boolean(tracking=True)

    user_id = fields.Many2one(
        'res.users', string='Responsible', default=lambda self: self.env.user,
        tracking=True,
    )
    estimated_cost = fields.Monetary(string='Estimated Cost')
    actual_cost = fields.Monetary(string='Actual Cost')
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id,
        required=True,
    )

    #: Late-bound links to optional downstream modules. Declared as plain
    #: integers/references so this module never has to depend on them.
    maintenance_request_ids = fields.One2many(
        'realestate.maintenance.request', 'unit_turn_id',
        string='Maintenance Requests',
    )
    maintenance_count = fields.Integer(compute='_compute_maintenance_count')
    notes = fields.Html()

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('start_date', 'actual_ready_date')
    def _compute_vacant_days(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.start_date:
                rec.vacant_days = 0
                continue
            end = rec.actual_ready_date or today
            rec.vacant_days = max((end - rec.start_date).days, 0)

    @api.depends('start_date', 'actual_ready_date')
    def _compute_turnaround_days(self):
        for rec in self:
            if rec.start_date and rec.actual_ready_date:
                rec.turnaround_days = max((rec.actual_ready_date - rec.start_date).days, 0)
            else:
                rec.turnaround_days = 0

    @api.depends('maintenance_request_ids')
    def _compute_maintenance_count(self):
        for rec in self:
            rec.maintenance_count = len(rec.maintenance_request_ids)

    # ==================================================================
    # Workflow
    # ==================================================================
    def action_start(self):
        """Begin the turn and block the unit from being let meanwhile."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Turn %s has already started.", rec.name))
            rec.state = 'cleaning' if rec.cleaning_required else (
                'repair' if rec.repair_required else 'inspection')
            rec.property_id._set_maintenance_status('maintenance', reason=_(
                "Unit turn %s in progress.") % rec.name)
        return True

    def action_cleaning_done(self):
        for rec in self:
            rec.cleaning_done = True
            if rec.state == 'cleaning':
                rec.state = 'repair' if (
                    rec.repair_required and not rec.repair_done) else 'inspection'
        return True

    def action_repair_done(self):
        for rec in self:
            rec.repair_done = True
            if rec.state == 'repair':
                rec.state = 'inspection'
        return True

    def action_pass_inspection(self):
        for rec in self:
            if rec.state != 'inspection':
                raise UserError(_(
                    "Turn %s is not at the final inspection stage.", rec.name))
            rec.inspection_passed = True
        return True

    def action_mark_ready(self):
        """The one action that returns a unit to the market."""
        self.mapped('property_id').check_access('write')
        for rec in self:
            blockers = rec._readiness_blockers()
            if blockers:
                raise UserError(_(
                    "Unit turn %(name)s is not ready:\n- %(blockers)s",
                    name=rec.name, blockers='\n- '.join(blockers)))
            rec.write({
                'state': 'ready',
                'actual_ready_date': fields.Date.context_today(rec),
            })
            rec.property_id._set_maintenance_status('normal', reason=_(
                "Unit turn %s complete — unit is ready to lease.") % rec.name)
            if rec.termination_id:
                rec.termination_id.property_available_date = rec.actual_ready_date
            rec.message_post(body=_(
                "Unit ready for lease after %s vacant day(s).", rec.vacant_days))
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == 'ready':
                raise UserError(_(
                    "Turn %s is complete and cannot be cancelled.", rec.name))
            rec.state = 'cancelled'
            rec.property_id._set_maintenance_status('normal', reason=_(
                "Unit turn %s cancelled.") % rec.name)
        return True

    def _readiness_blockers(self):
        """Everything standing between this unit and the market."""
        self.ensure_one()
        blockers = []
        if self.cleaning_required and not self.cleaning_done:
            blockers.append(_("Cleaning is not done."))
        if self.repair_required and not self.repair_done:
            blockers.append(_("Repairs are not done."))
        if not self.inspection_passed:
            blockers.append(_("The final inspection has not been passed."))
        open_requests = self.maintenance_request_ids.filtered(
            lambda r: r.state not in ('done', 'cancelled'))
        if open_requests:
            blockers.append(_("%s maintenance request(s) are still open.")
                            % len(open_requests))
        return blockers

    # ==================================================================
    # Optional downstream hooks (Phase 22 / 35)
    # ==================================================================
    def action_raise_maintenance(self):
        """Raise a maintenance request against this turn.

        Uses the maintenance model that ships in this module. If
        ``real_estate_procurement`` is installed, that request can spawn a
        material request through its own existing hook -- nothing here needs to
        know about it.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Maintenance Request'),
            'res_model': 'realestate.maintenance.request',
            'view_mode': 'form',
            'context': {
                'default_property_id': self.property_id.id,
                'default_unit_turn_id': self.id,
                'default_name': _("Unit turn — %s") % self.property_id.display_name,
            },
        }

    def _optional_model(self, model_name):
        """Return a model only if its module is installed.

        The late-binding hook that keeps this module free of downstream
        dependencies.
        """
        if model_name in self.env:
            return self.env[model_name]
        return None

    # ==================================================================
    # ORM
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.unit.turn') or _('New')
        return super().create(vals_list)

    @api.depends('property_id', 'state')
    def _compute_display_name(self):
        labels = dict(TURN_STATES)
        for rec in self:
            rec.display_name = '%s — %s' % (
                rec.name or '', labels.get(rec.state, rec.state))


class PropertyUnitTurnMixin(models.Model):
    _inherit = 'realestate.property'

    unit_turn_ids = fields.One2many(
        'realestate.unit.turn', 'property_id', string='Unit Turns')
    unit_turn_count = fields.Integer(compute='_compute_unit_turn_stats')
    active_turn_id = fields.Many2one(
        'realestate.unit.turn', string='Open Unit Turn',
        compute='_compute_unit_turn_stats',
    )
    average_vacant_days = fields.Float(
        string='Average Vacant Days', compute='_compute_unit_turn_stats',
        help="Mean turnaround across completed turns on this unit.",
    )

    @api.depends('unit_turn_ids.state', 'unit_turn_ids.turnaround_days')
    def _compute_unit_turn_stats(self):
        for rec in self:
            turns = rec.unit_turn_ids
            rec.unit_turn_count = len(turns)
            open_turns = turns.filtered(
                lambda t: t.state not in ('ready', 'cancelled'))
            rec.active_turn_id = open_turns[:1].id if open_turns else False
            done = turns.filtered(lambda t: t.state == 'ready')
            rec.average_vacant_days = (
                sum(done.mapped('turnaround_days')) / len(done)) if done else 0.0

    def action_view_unit_turns(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Unit Turns'),
            'res_model': 'realestate.unit.turn',
            'view_mode': 'list,form',
            'domain': [('property_id', '=', self.id)],
            'context': {'default_property_id': self.id},
        }


class MaintenanceRequestTurnMixin(models.Model):
    """Optional back-link so a turn can gather its own work."""
    _inherit = 'realestate.maintenance.request'

    unit_turn_id = fields.Many2one(
        'realestate.unit.turn', string='Unit Turn',
        ondelete='set null', index=True,
        help="Set when this request is part of preparing a vacated unit for "
             "re-letting.",
    )
