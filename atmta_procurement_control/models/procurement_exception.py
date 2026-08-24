# -*- coding: utf-8 -*-
"""M3F — the evidence that somebody decided to go past a control.

Every control in M3 can be passed. That is not a weakness: projects run out of
budget on a Thursday and the concrete still has to arrive on Friday, and a
system that only knows how to say no gets worked around within a week.

What must never happen is passing a control *invisibly*. A line in the chatter
saying "approved by phone" is not evidence — it cannot be listed, counted,
filtered or audited, and it does not name what was exceeded or by how much. So
every override is a record with the amount, the shortfall, the policy in force
at the time, who asked and who agreed.

Three kinds, because they are genuinely different events:

```
    OVER_BUDGET       approved demand exceeds available capacity
    DIRECT_PURCHASE   a governed project PO with no requisition behind it
    AMOUNT_DELTA      a purchase order above the approved requisition basis
```

`insufficient_data` is a fourth, and it is the one that is not really an
override: it records that the control could not be established at all.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

EXCEPTION_TYPE = [
    ('over_budget', 'Over Budget'),
    ('direct_purchase', 'Direct Purchase'),
    ('amount_delta', 'Amount Above Approved Basis'),
    ('insufficient_data', 'Control Position Unknown'),
]


class ProcurementControlException(models.Model):
    _name = 'realestate.procurement.control.exception'
    _description = 'Procurement Control Exception'
    _order = 'id desc'

    name = fields.Char(
        required=True, copy=False, readonly=True,
        default=lambda self: _('New'))
    exception_type = fields.Selection(
        EXCEPTION_TYPE, required=True, index=True, string='Type')
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True)
    project_id = fields.Many2one(
        'realestate.project', index=True, ondelete='restrict')
    request_id = fields.Many2one(
        'realestate.material.request', index=True, ondelete='cascade',
        string='Requisition')
    purchase_order_id = fields.Many2one(
        'purchase.order', index=True, ondelete='cascade')
    # `cost_code_id` and `change_order_id` are added by
    # `real_estate_construction`: it owns both models and depends on this one.

    requested_amount = fields.Monetary(
        help="What was asked for, in company currency, tax-exclusive.")
    available_amount = fields.Monetary(
        help="What the control position said was available at that moment.")
    overage_amount = fields.Monetary(
        compute='_compute_overage', store=True,
        help="Requested minus available. Zero when the exception is not "
             "about an amount.")
    policy = fields.Char(
        readonly=True,
        help="The policy in force when this was raised. Kept as text because "
             "a later configuration change must not rewrite what the rule "
             "was on the day.")
    control_note = fields.Text(
        readonly=True,
        help="The control position as it read at the time — the sentence the "
             "approver was actually looking at.")

    reason = fields.Text(
        required=True,
        help="Why the control is being passed. Required, and deliberately not "
             "defaulted: an exception whose reason reads 'exception' is worse "
             "than none, because it looks like evidence.")
    state = fields.Selection([
        ('noted', 'Noted'),
        ('requested', 'Awaiting Authority'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], default='requested', required=True, index=True,
        help="Noted means nobody had to agree — the policy was Warn and this "
             "is a record of what happened. It is deliberately not called "
             "approved, because nobody approved it.")

    requested_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, readonly=True)
    request_date = fields.Datetime(
        default=fields.Datetime.now, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True)
    approval_date = fields.Datetime(readonly=True)
    decision_note = fields.Text()

    @api.depends('requested_amount', 'available_amount', 'exception_type')
    def _compute_overage(self):
        for rec in self:
            if rec.exception_type in ('over_budget', 'amount_delta'):
                rec.overage_amount = max(
                    0.0, (rec.requested_amount or 0.0)
                    - (rec.available_amount or 0.0))
            else:
                rec.overage_amount = 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.procurement.control.exception') or _('New')
        return super().create(vals_list)

    @api.constrains('company_id', 'project_id', 'request_id',
                    'purchase_order_id')
    def _check_company(self):
        """M3W — an exception cannot authorise across a company boundary."""
        for rec in self:
            for record, label in ((rec.project_id, _('project')),
                                  (rec.request_id, _('requisition')),
                                  (rec.purchase_order_id, _('purchase order'))):
                other = record.company_id if record else False
                if other and other != rec.company_id:
                    raise ValidationError(_(
                        "The %(label)s belongs to %(other)s and this exception "
                        "to %(company)s. Authority does not cross companies.",
                        label=label, other=other.display_name,
                        company=rec.company_id.display_name))

    # ------------------------------------------------------------------
    def action_approve(self):
        """Authorise the overrun. Server-side, and not by the same person."""
        for rec in self:
            if rec.state != 'requested':
                raise UserError(_(
                    "%s is not awaiting a decision.") % rec.name)
            if not self.env.user.has_group(
                    'atmta_roles.group_procurement_manager'):
                raise UserError(_(
                    "Passing a budget control is a manager's decision."))
            rec._check_not_self_approval()
            rec.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approval_date': fields.Datetime.now(),
            })
        return True

    def action_reject(self):
        for rec in self:
            if rec.state != 'requested':
                raise UserError(_(
                    "%s is not awaiting a decision.") % rec.name)
            if not rec.decision_note:
                raise UserError(_(
                    "Say why it is refused. A rejection with no reason cannot "
                    "be answered."))
            rec.write({
                'state': 'rejected',
                'approved_by_id': self.env.user.id,
                'approval_date': fields.Datetime.now(),
            })
        return True

    def _check_not_self_approval(self):
        """M3I applied to exceptions as well as to approval steps."""
        self.ensure_one()
        if self.requested_by_id != self.env.user:
            return
        company = self.company_id
        if company.procurement_allow_self_approval and (
                self.requested_amount or 0.0
        ) <= company.procurement_self_approval_limit:
            return
        raise UserError(_(
            "You raised %s. Asking for an exception and granting it are two "
            "roles, and one person cannot hold both here.") % self.name)

    def unlink(self):
        """M3Z — a decided exception is the audit trail, not a draft."""
        decided = self.filtered(lambda e: e.state in ('approved', 'rejected',
                                                      'noted'))
        if decided:
            raise UserError(_(
                "%s records a control decision. It can be superseded, not "
                "deleted.") % ', '.join(decided.mapped('name')))
        return super().unlink()
