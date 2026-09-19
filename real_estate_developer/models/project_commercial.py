# -*- coding: utf-8 -*-
"""Phase 1 — the project as a commercial container.

Adds the commercial dimension to ``realestate.project`` without touching the
existing ``state`` field, which downstream modules read (see
``commercial_states.py`` for why).

Also adds ``company_id``, which the model has never had. Six models in this
module carry no company at all, which is why the module has no record rules —
there was nothing to filter on.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .commercial_states import COMMERCIAL_STATE, COMMERCIAL_STATE_SELLING


class ProjectCommercial(models.Model):
    _inherit = 'realestate.project'

    # `company_id` is NOT declared here any more. Company ownership is part of
    # the project's identity, so it belongs to `atmta_project_core` along with
    # the global isolation rule that filters on it -- otherwise Project Core
    # could not be installed multi-company-correct without this application.

    # ------------------------------------------------------------------
    # Commercial lifecycle — deliberately NOT `state`
    # ------------------------------------------------------------------
    commercial_state = fields.Selection(
        COMMERCIAL_STATE, string='Sales Status', default='planning',
        required=True, tracking=True, index=True, copy=False,
        help="Where this project stands commercially. Independent of "
             "construction progress: an off-plan project is normally selling "
             "and under construction at the same time.",
    )

    # ---- Commercial calendar ----
    sales_opening_date = fields.Date(
        string='Sales Opening', tracking=True,
        help="First day the project may be sold. Informational; the release "
             "batch is what actually makes a unit sellable.")
    commercial_launch_date = fields.Date(string='Commercial Launch', tracking=True)
    booking_start_date = fields.Date(string='Booking Opens', tracking=True)
    booking_end_date = fields.Date(string='Booking Closes', tracking=True)
    expected_handover_date = fields.Date(
        string='Expected Handover', tracking=True,
        help="Expected handover to buyers. Distinct from expected completion, "
             "which is a construction date.")

    # ---- Commercial defaults, inherited downward ----
    default_hold_duration_hours = fields.Float(
        string='Default Hold (hours)', default=72.0, tracking=True,
        help="How long a unit hold lasts before it expires. A phase may "
             "override this; a reservation may override the phase when the "
             "user is authorised to.")
    default_booking_fee = fields.Monetary(
        string='Default Booking Fee', tracking=True,
        help="Suggested booking amount for new reservations in this project.")
    # NOTE: no sales-team field here. `crm` is not a dependency of this
    # module, and declaring a Many2one to a model that may not exist breaks
    # registry loading. Team-based scoping is Phase 46 work and will either add
    # the dependency deliberately or scope by salesperson instead.

    @api.constrains('booking_start_date', 'booking_end_date')
    def _check_booking_window(self):
        for rec in self:
            if (rec.booking_start_date and rec.booking_end_date
                    and rec.booking_end_date < rec.booking_start_date):
                raise ValidationError(_(
                    "Project '%s': booking closes before it opens."
                ) % rec.display_name)

    @api.constrains('default_hold_duration_hours')
    def _check_hold_duration(self):
        for rec in self:
            if rec.default_hold_duration_hours <= 0:
                raise ValidationError(_(
                    "Project '%s': the default hold duration must be positive."
                ) % rec.display_name)

    _sql_constraints = [
        # The module has had no uniqueness at all. Scoped to the company so two
        # companies may legitimately reuse a code.
        ('project_code_company_uniq', 'UNIQUE(company_id, code)',
         'A project with this code already exists in this company.'),
    ]

    # ------------------------------------------------------------------
    # Commercial workflow
    # ------------------------------------------------------------------
    def _set_commercial_state(self, target):
        """Single writer for the commercial lifecycle, so every transition is
        validated in one place rather than by whichever button was pressed."""
        for rec in self:
            if rec.commercial_state == target:
                continue
            if target == 'selling' and not rec.phase_ids and not rec.property_ids:
                raise UserError(_(
                    "Project '%s' has no phases and no units, so there is "
                    "nothing to sell."
                ) % rec.display_name)
            rec.commercial_state = target

    def action_commercial_pre_launch(self):
        self._set_commercial_state('pre_launch')

    def action_commercial_start_selling(self):
        self._set_commercial_state('selling')

    def action_commercial_sold_out(self):
        self._set_commercial_state('sold_out')

    def action_commercial_close(self):
        self._set_commercial_state('closed')

    def action_commercial_reset(self):
        self._set_commercial_state('planning')

    @property
    def _is_commercially_open(self):
        self.ensure_one()
        return self.commercial_state in COMMERCIAL_STATE_SELLING

    # ------------------------------------------------------------------
    # Inventory rollups — grouped in the database, never per record
    # ------------------------------------------------------------------
    released_unit_count = fields.Integer(compute='_compute_commercial_counters')
    for_sale_unit_count = fields.Integer(
        compute='_compute_commercial_counters', string='Available for Sale')
    held_unit_count = fields.Integer(compute='_compute_commercial_counters')
    reserved_commercial_unit_count = fields.Integer(
        compute='_compute_commercial_counters', string='Reserved')
    contracted_unit_count = fields.Integer(compute='_compute_commercial_counters')
    blocked_unit_count = fields.Integer(compute='_compute_commercial_counters')

    @api.depends('property_ids.commercial_status',
                 'property_ids.is_released_for_sale',
                 'property_ids.is_available_for_sale')
    def _compute_commercial_counters(self):
        """One grouped query for every project in ``self``.

        The pre-existing ``_compute_counters`` walks ``property_ids`` in Python
        and filters it five times. At 20,000 units that reads the whole
        inventory into memory to produce five integers. This one does not.
        """
        zero = dict.fromkeys((
            'released_unit_count', 'for_sale_unit_count', 'held_unit_count',
            'reserved_commercial_unit_count', 'contracted_unit_count',
            'blocked_unit_count'), 0)
        for rec in self:
            rec.update(zero)
        if not self.ids:
            return

        Property = self.env['realestate.property']
        by_status = Property._read_group(
            [('project_id', 'in', self.ids)],
            groupby=['project_id', 'commercial_status'],
            aggregates=['__count'],
        )
        released = Property._read_group(
            [('project_id', 'in', self.ids), ('is_released_for_sale', '=', True)],
            groupby=['project_id'], aggregates=['__count'],
        )
        for_sale = Property._read_group(
            [('project_id', 'in', self.ids), ('is_available_for_sale', '=', True)],
            groupby=['project_id'], aggregates=['__count'],
        )

        status_field = {
            'held': 'held_unit_count',
            'reserved': 'reserved_commercial_unit_count',
            'contracted': 'contracted_unit_count',
            'blocked': 'blocked_unit_count',
        }
        for project, status, count in by_status:
            field = status_field.get(status)
            if field and project in self:
                project[field] += count
        for project, count in released:
            if project in self:
                project.released_unit_count = count
        for project, count in for_sale:
            if project in self:
                project.for_sale_unit_count = count

    # ------------------------------------------------------------------
    # Commercial value rollups
    # ------------------------------------------------------------------
    contracted_value = fields.Monetary(
        compute='_compute_commercial_value', string='Contracted Value',
        help="Sum of the sale price of every live sale contract in this "
             "project. Cancelled contracts are excluded.")
    contracted_deal_count = fields.Integer(compute='_compute_commercial_value')

    @api.depends('property_ids.sale_contract_ids.sale_price',
                 'property_ids.sale_contract_ids.state')
    def _compute_commercial_value(self):
        for rec in self:
            rec.contracted_value = 0.0
            rec.contracted_deal_count = 0
        if not self.ids:
            return
        groups = self.env['realestate.sale.contract']._read_group(
            [('project_id', 'in', self.ids), ('state', '!=', 'cancelled')],
            groupby=['project_id'], aggregates=['sale_price:sum', '__count'],
        )
        for project, total, count in groups:
            if project in self:
                project.contracted_value = total or 0.0
                project.contracted_deal_count = count

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def action_view_release_batches(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Release Batches'),
            'res_model': 'realestate.unit.release.batch',
            'views': [(False, 'list'), (False, 'form')],
            'view_mode': 'list,form',
            'domain': [('project_id', '=', self.id)],
            'context': {'default_project_id': self.id},
            'target': 'current',
        }
