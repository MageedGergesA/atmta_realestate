# -*- coding: utf-8 -*-
"""Wave 16 — what an approved change does to the money.

Change events and change orders moved to `atmta_construction_change`, below
every capability that raises one. What could not move is the implementation:
budgets, budget change lines, commitment changes, revenue changes, the
authority matrix and forecast adjustments are all models of this module.

The floor states the workflow -- price, submit, assess, negotiate, approve,
implement -- and calls seams at the points where an approved change becomes a
record somewhere else. This file supplies those records. The sequence is
unchanged, the arithmetic is unchanged, and the idempotency guarantee is
unchanged: implementation is idempotent because its linked records exist, and
`_already_implemented` is how the floor asks that question of a module it
cannot see.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ChangeOrderImplementation(models.Model):
    _inherit = 'realestate.construction.change.order'

    budget_change_ids = fields.One2many(
        'realestate.construction.budget.change.line', 'change_order_id',
        string='Budget Changes', readonly=True)
    commitment_change_ids = fields.One2many(
        'realestate.construction.commitment.change', 'change_order_id',
        string='Commitment Changes', readonly=True)

    @api.depends('budget_change_ids', 'commitment_change_ids', 'state')
    def _compute_implemented(self):
        return super()._compute_implemented()

    def _already_implemented(self):
        self.ensure_one()
        return bool(self.budget_change_ids or self.commitment_change_ids)

    def _authority_rule(self):
        self.ensure_one()
        return self.env['realestate.construction.change.authority'].rule_for(self)

    def _apply_budget_impact(self):
        """Budget changes become records; the baseline is never rewritten."""
        self.ensure_one()
        Budget = self.env['realestate.construction.budget']
        budget = Budget.current_for(self.project_id)
        budget_lines = self.line_ids.filtered(
            lambda l: l.impact_side in ('budget', 'contingency')
            and l.approved_amount)
        if not budget_lines:
            return
        if not budget:
            raise UserError(_(
                "%(project)s has no baselined budget, so there is nothing for "
                "a budget change to change. Baseline a budget first.",
                project=self.project_id.display_name))

        ChangeLine = self.env['realestate.construction.budget.change.line']
        for line in budget_lines:
            target = budget.line_ids.filtered(
                lambda bl: bl.cost_code_id == line.cost_code_id)[:1]
            ChangeLine.create({
                'change_order_id': self.id,
                'budget_id': budget.id,
                'budget_line_id': target.id if target else False,
                'cost_code_id': line.cost_code_id.id,
                'wbs_id': line.wbs_id.id or False,
                'amount': line.approved_amount,
                'change_type': line._budget_change_type(),
                'effective_date': self.effective_date,
            })

    def _apply_commitment_impact(self):
        """Commitment changes sit beside the original, never inside it."""
        self.ensure_one()
        commitment_lines = self.line_ids.filtered(
            lambda l: l.impact_side == 'commitment' and l.approved_amount)
        if not commitment_lines:
            return
        Change = self.env['realestate.construction.commitment.change']
        for line in commitment_lines:
            Change.create({
                'change_order_id': self.id,
                'project_id': self.project_id.id,
                'package_id': self.package_id.id or False,
                'cost_code_id': line.cost_code_id.id,
                'wbs_id': line.wbs_id.id or False,
                'amount': line.approved_amount,
                'effective_date': self.effective_date,
            })

    def _apply_revenue_impact(self):
        """Owner-side value. Cost and revenue changes are not the same money."""
        self.ensure_one()
        revenue = self.approved_revenue_amount
        if not revenue:
            return
        self.env['realestate.construction.revenue.change'].create({
            'change_order_id': self.id,
            'project_id': self.project_id.id,
            'partner_id': self.partner_id.id or False,
            'amount': revenue,
            'effective_date': self.effective_date,
        })

    def _convert_forecast_anticipations(self):
        """§44 — an anticipation that has become real stops being anticipated.

        Only **draft** forecasts are touched. An approved forecast is history
        and is left exactly as it was, even though it now contains an
        anticipation of something that has since been approved: that is what
        was believed at the time, and rewriting it would destroy the record.
        """
        self.ensure_one()
        events = self.change_event_id | self.related_event_ids
        if not events:
            return
        adjustments = self.env[
            'realestate.construction.forecast.adjustment'].search([
                ('change_event_id', 'in', events.ids),
                ('converted_to_change_order', '=', False),
            ])
        for adjustment in adjustments:
            adjustment.sudo().write({
                'converted_to_change_order': True,
                'converted_by_order_id': self.id,
            })

    def _check_authority(self):
        self.ensure_one()
        self.env['realestate.construction.change.authority'].check(self)
        return True


class ChangeEventForecast(models.Model):
    _inherit = 'realestate.construction.change.event'

    forecast_adjustment_ids = fields.One2many(
        'realestate.construction.forecast.adjustment', 'change_event_id',
        string='Forecast Adjustments')
