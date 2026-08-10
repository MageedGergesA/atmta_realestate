# -*- coding: utf-8 -*-
"""M4 — the records an implemented change writes.

These are what make `+ APPROVED CHANGES` a real number rather than a field
somebody typed. Nothing here is editable: the way to correct an implemented
change is another change, which is the difference between an audit trail and a
spreadsheet.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

BUDGET_CHANGE_TYPES = [
    ('increase', 'Increase'),
    ('decrease', 'Decrease'),
    ('transfer_in', 'Transfer In'),
    ('transfer_out', 'Transfer Out'),
    ('contingency_drawdown', 'Contingency Drawdown'),
]


class ConstructionBudgetChangeLine(models.Model):
    """One approved movement of authorised budget."""
    _name = 'realestate.construction.budget.change.line'
    _description = 'Budget Change Line'
    _order = 'budget_id, effective_date, id'

    change_order_id = fields.Many2one(
        'realestate.construction.change.order', required=True,
        ondelete='restrict', index=True, readonly=True)
    budget_id = fields.Many2one(
        'realestate.construction.budget', required=True, ondelete='restrict',
        index=True, readonly=True)
    project_id = fields.Many2one(
        related='budget_id.project_id', store=True, index=True, readonly=True)
    company_id = fields.Many2one(
        related='budget_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='budget_id.currency_id', readonly=True)
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', required=True, index=True,
        ondelete='restrict', readonly=True)
    budget_line_id = fields.Many2one(
        'realestate.construction.budget.line', index=True,
        ondelete='restrict', readonly=True,
        help="The baselined line this change moves. Empty when the change "
             "adds a cost code the baseline never had — which is legitimate "
             "and is reported as an addition rather than forced onto an "
             "unrelated line.")
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', ondelete='restrict', readonly=True)
    amount = fields.Monetary(required=True, readonly=True)
    change_type = fields.Selection(
        BUDGET_CHANGE_TYPES, required=True, readonly=True)
    effective_date = fields.Date(readonly=True, index=True)

    def write(self, vals):
        raise UserError(_(
            "A budget change is a record of a decision. Correct it with "
            "another change, not by editing this one."))

    def unlink(self):
        raise UserError(_(
            "Budget changes cannot be deleted — they are why the current "
            "budget is what it is."))


class ConstructionCommitmentChange(models.Model):
    """One approved movement of committed value."""
    _name = 'realestate.construction.commitment.change'
    _description = 'Commitment Change'
    _order = 'project_id, effective_date, id'

    change_order_id = fields.Many2one(
        'realestate.construction.change.order', required=True,
        ondelete='restrict', index=True, readonly=True)
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        readonly=True)
    company_id = fields.Many2one(
        related='project_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='change_order_id.currency_id', readonly=True)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='restrict',
        index=True, readonly=True)
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', required=True, index=True,
        ondelete='restrict', readonly=True)
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', ondelete='restrict', readonly=True)
    amount = fields.Monetary(required=True, readonly=True)
    effective_date = fields.Date(readonly=True, index=True)

    reflected_in_purchase_order = fields.Boolean(
        readonly=True,
        help="True once the linked purchase order has been amended to include "
             "this change. The commitment engine then reads the order and "
             "ignores this record, so the same money is counted once — the "
             "M2 source-precedence rule, extended to variations.")
    purchase_order_id = fields.Many2one(
        'purchase.order', ondelete='set null', readonly=True)

    def write(self, vals):
        # The PO link is the one thing that may be set after the fact, because
        # amending the order is a separate act from approving the change.
        allowed = {'reflected_in_purchase_order', 'purchase_order_id'}
        if set(vals) - allowed:
            raise UserError(_(
                "A commitment change records what was approved. Correct it "
                "with another change."))
        return super().write(vals)

    def unlink(self):
        raise UserError(_(
            "Commitment changes cannot be deleted — they are why the current "
            "commitment is what it is."))


class ConstructionRevenueChange(models.Model):
    """Owner-side value. Deliberately not commitment: cost and revenue
    variations are different money and routinely different amounts."""
    _name = 'realestate.construction.revenue.change'
    _description = 'Owner Revenue Change'
    _order = 'project_id, effective_date, id'

    change_order_id = fields.Many2one(
        'realestate.construction.change.order', required=True,
        ondelete='restrict', index=True, readonly=True)
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        readonly=True)
    company_id = fields.Many2one(
        related='project_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='change_order_id.currency_id', readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    amount = fields.Monetary(required=True, readonly=True)
    effective_date = fields.Date(readonly=True, index=True)

    def write(self, vals):
        raise UserError(_("Revenue changes are records, not drafts."))

    def unlink(self):
        raise UserError(_("Revenue changes cannot be deleted."))


class ConstructionChangeAuthority(models.AbstractModel):
    """Who may approve what, by amount.

    Thresholds live in configuration parameters rather than in code, because
    every company's delegation of authority differs and a hard-coded number is
    a number somebody will have to fork the module to change.

    Judged on **gross** impact (§39): a change that adds 5M to one code and
    removes 5M from another nets to zero and is emphatically a decision worth
    approving.
    """
    _name = 'realestate.construction.change.authority'
    _description = 'Change Approval Authority'

    #: parameter → (group, label). Checked in order, smallest first.
    LEVELS = [
        ('real_estate_construction.change_approval_limit_manager',
         'real_estate_construction.group_construction_manager',
         'Construction Manager', 1_000_000.0),
    ]

    @api.model
    def rule_for(self, order):
        """The authority level this change needs."""
        Param = self.env['ir.config_parameter'].sudo()
        for param, group, label, default in self.LEVELS:
            threshold = float(Param.get_param(param, default))
            if abs(order.gross_impact) <= threshold:
                return {'group': group, 'group_name': label,
                        'threshold': threshold}
        return {'group': 'real_estate_construction.group_construction_manager',
                'group_name': _('Construction Manager (above all thresholds)'),
                'threshold': 0.0}

    @api.model
    def check(self, order):
        """Server-side, always. A hidden button is not an authorisation."""
        rule = self.rule_for(order)
        if not self.env.user.has_group(rule['group']):
            raise UserError(_(
                "%(amount)s of gross change needs %(role)s approval.",
                amount=order.currency_id.format(order.gross_impact),
                role=rule['group_name']))

        allow_self = self.env['ir.config_parameter'].sudo().get_param(
            'real_estate_construction.allow_self_approval', 'False')
        if allow_self in ('True', 'true', '1'):
            return True
        if order.responsible_id == self.env.user and \
                order.create_uid == self.env.user:
            threshold = float(self.env['ir.config_parameter'].sudo().get_param(
                'real_estate_construction.self_approval_limit', 0.0))
            if abs(order.gross_impact) > threshold:
                raise UserError(_(
                    "You raised this change and cannot also approve it. Ask "
                    "somebody with the authority, or set a self-approval "
                    "limit if this company genuinely works that way."))
        return True
