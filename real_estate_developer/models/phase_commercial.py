# -*- coding: utf-8 -*-
"""Phase 2 — commercial control at phase level, with inheritance.

A phase may sell on different terms from its project, but it should not have to
restate the project's configuration. Every inheritable setting here is
"unset means inherit": ``False``/``0`` on the phase falls back to the project.

The resolution helpers below are the *only* place that fallback is expressed.
Nothing else in the module should read ``project.default_*`` directly, or the
two layers drift.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .commercial_states import COMMERCIAL_STATE, COMMERCIAL_STATE_SELLING


class PhaseCommercial(models.Model):
    _inherit = 'realestate.phase'

    # `company_id` moved to `atmta_project_core` with the project's own; see
    # the note in `project_commercial.py`.

    commercial_state = fields.Selection(
        COMMERCIAL_STATE, string='Sales Status', default='planning',
        required=True, tracking=True, index=True, copy=False,
        help="Commercial lifecycle of this phase, independent of construction.",
    )

    sales_launch_date = fields.Date(string='Sales Launch', tracking=True)

    # ---- Inheritable overrides: unset means "use the project's value" ----
    hold_duration_hours = fields.Float(
        string='Hold Override (hours)', tracking=True,
        help="Leave empty to inherit the project's default hold duration.")
    booking_fee = fields.Monetary(
        string='Booking Fee Override', tracking=True,
        help="Leave empty to inherit the project's default booking fee.")
    currency_id = fields.Many2one(
        'res.currency', related='project_id.currency_id', readonly=True)

    target_sales_value = fields.Monetary(
        string='Target Sales Value', tracking=True,
        help="Commercial target for this phase. Reporting only.")

    @api.constrains('hold_duration_hours')
    def _check_hold_duration(self):
        for rec in self:
            if rec.hold_duration_hours < 0:
                raise ValidationError(_(
                    "Phase '%s': the hold override cannot be negative. Leave "
                    "it empty to inherit the project's value."
                ) % rec.display_name)

    # ------------------------------------------------------------------
    # Inheritance resolution — the single source of the fallback chain
    # ------------------------------------------------------------------
    def _resolve_hold_duration_hours(self):
        """Effective hold duration: phase override, else project default."""
        self.ensure_one()
        if self.hold_duration_hours:
            return self.hold_duration_hours
        return self.project_id.default_hold_duration_hours or 72.0

    def _resolve_booking_fee(self):
        """Effective booking fee: phase override, else project default.

        Zero is a legitimate project-level answer ("no booking fee"), so this
        returns the project value as-is rather than substituting a default.
        """
        self.ensure_one()
        if self.booking_fee:
            return self.booking_fee
        return self.project_id.default_booking_fee or 0.0

    def _is_commercially_open(self):
        """A phase sells only if BOTH it and its project are open.

        A phase cannot out-rank its project: closing a project must close
        everything under it, and a per-phase override would silently defeat
        that.
        """
        self.ensure_one()
        return (
            self.commercial_state in COMMERCIAL_STATE_SELLING
            and self.project_id.commercial_state in COMMERCIAL_STATE_SELLING
        )

    # ------------------------------------------------------------------
    # Counters
    # ------------------------------------------------------------------
    released_unit_count = fields.Integer(compute='_compute_phase_commercial_counters')
    for_sale_unit_count = fields.Integer(
        compute='_compute_phase_commercial_counters', string='Available for Sale')
    contracted_unit_count = fields.Integer(compute='_compute_phase_commercial_counters')

    @api.depends('property_ids.commercial_status',
                 'property_ids.is_released_for_sale',
                 'property_ids.is_available_for_sale')
    def _compute_phase_commercial_counters(self):
        for rec in self:
            rec.released_unit_count = 0
            rec.for_sale_unit_count = 0
            rec.contracted_unit_count = 0
        if not self.ids:
            return
        Property = self.env['realestate.property']
        for phase, count in Property._read_group(
                [('phase_id', 'in', self.ids), ('is_released_for_sale', '=', True)],
                groupby=['phase_id'], aggregates=['__count']):
            if phase in self:
                phase.released_unit_count = count
        for phase, count in Property._read_group(
                [('phase_id', 'in', self.ids), ('is_available_for_sale', '=', True)],
                groupby=['phase_id'], aggregates=['__count']):
            if phase in self:
                phase.for_sale_unit_count = count
        for phase, count in Property._read_group(
                [('phase_id', 'in', self.ids),
                 ('commercial_status', 'in', ('contracted', 'sold'))],
                groupby=['phase_id'], aggregates=['__count']):
            if phase in self:
                phase.contracted_unit_count = count

    # ------------------------------------------------------------------
    # Commercial workflow
    # ------------------------------------------------------------------
    def action_commercial_pre_launch(self):
        self.write({'commercial_state': 'pre_launch'})

    def action_commercial_start_selling(self):
        self.write({'commercial_state': 'selling'})

    def action_commercial_sold_out(self):
        self.write({'commercial_state': 'sold_out'})

    def action_commercial_close(self):
        self.write({'commercial_state': 'closed'})
