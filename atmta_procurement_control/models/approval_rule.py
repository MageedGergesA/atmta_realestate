# -*- coding: utf-8 -*-
"""M3G/M3H — who has to agree, and the proof that they did.

Two models with deliberately different lifetimes:

```
    APPROVAL RULE   configuration. Changes whenever the company changes.
    APPROVAL STEP   evidence. Must never change again once it exists.
```

Phase 0's approval "matrix" matched on one dimension (amount), stamped a group
on a step, and had no concept of a decision — a step was a boolean that went
from false to true, so a rejection could only be expressed by putting the
request back to draft and leaving no trace that anybody had refused anything.

The step now records a decision, the basis it was taken against, and the rule
it came from **as text**. That last part is the point of M3H: if the rule is
edited or archived next month, the historic step must still say what the
authority was on the day. A related field would have rewritten it silently.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

DECISION = [
    ('pending', 'Pending'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('skipped', 'Skipped'),
    ('cancelled', 'Cancelled'),
]


class ApprovalRule(models.Model):
    """One row = "demand that looks like this needs somebody like that".

    Every dimension here is a fact about the demand at submission. None of
    them is a fact about who is submitting it, which is what keeps the matrix
    from being negotiable by the requester.
    """
    _name = 'realestate.procurement.approval.rule'
    _description = 'Procurement Approval Rule'
    _order = 'sequence, min_amount'

    name = fields.Char(required=True)
    sequence = fields.Integer(
        default=10,
        help="Approval order. Two rules with the same sequence are asked in "
             "parallel; a later sequence cannot decide before an earlier one "
             "has.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company,
        help="Blank applies to every company. A rule from one company never "
             "governs another's requisition.")

    # -- What the rule matches -----------------------------------------
    min_amount = fields.Float(
        string='From Amount', required=True, default=0.0,
        help="Compared against the requisition's tax-exclusive control "
             "amount, in company currency. A USD request is converted before "
             "it is compared — comparing 100,000 USD to a 100,000 EGP "
             "threshold is not a comparison.")
    max_amount = fields.Float(
        string='Up To (blank = infinity)',
        help="Optional upper bound. Leave blank for no ceiling.")
    project_id = fields.Many2one(
        'realestate.project', string='Project',
        help="Blank matches every project.")
    procurement_type = fields.Selection([
        ('material', 'Material'),
        ('service', 'Service'),
        ('subcontract', 'Subcontract'),
        ('equipment', 'Equipment'),
        ('other', 'Other'),
    ], string='Procurement Type', help="Blank matches every type.")
    priority = fields.Selection([
        ('0', 'Normal Only'),
        ('1', 'Urgent Only'),
    ], string='Priority',
        help="Blank matches both. Urgent demand may need *more* authority, "
             "never less — this dimension exists to add an emergency "
             "approver, and there is deliberately no setting that removes "
             "one.")
    budget_status = fields.Selection([
        ('ok', 'Within Budget'),
        ('over_budget', 'Over Budget'),
        ('insufficient_data', 'Position Unknown'),
    ], string='Budget Status',
        help="Blank matches any position. Set it to add an approver when the "
             "demand does not fit, or when the control position could not be "
             "established at all.")

    # -- Who decides ---------------------------------------------------
    group_id = fields.Many2one(
        'res.groups', string='Approver Group', required=True,
        help="Anybody in this group may take the decision.")
    approver_user_id = fields.Many2one(
        'res.users', string='Named Approver',
        help="Optional. When set, only this person may decide the step — the "
             "group still applies as the access right.")

    @api.constrains('min_amount', 'max_amount')
    def _check_bracket(self):
        for rule in self:
            if rule.max_amount and rule.max_amount < rule.min_amount:
                raise ValidationError(_(
                    "%s ends below where it starts.") % rule.name)

    def _matches(self, request, amount, control_status):
        """Does this rule govern that requisition?

        Every dimension is optional and an unset dimension matches everything,
        which is what makes a simple company able to run on one row.
        """
        self.ensure_one()
        if self.company_id and self.company_id != request.company_id:
            return False
        if amount < self.min_amount:
            return False
        if self.max_amount and amount > self.max_amount:
            return False
        if self.project_id and self.project_id != request.project_id:
            return False
        if self.procurement_type \
                and self.procurement_type != request.procurement_type:
            return False
        if self.priority and self.priority != request.priority:
            return False
        if self.budget_status and self.budget_status != control_status:
            return False
        return True


class ApprovalStep(models.Model):
    """One required decision on one requisition, and what became of it."""
    _name = 'realestate.procurement.approval.step'
    _description = 'Procurement Approval Step'
    _order = 'request_id, sequence, id'

    request_id = fields.Many2one(
        'realestate.material.request', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='request_id.company_id', store=True, readonly=True, index=True)
    currency_id = fields.Many2one(
        related='request_id.currency_id', readonly=True)
    project_id = fields.Many2one(
        related='request_id.project_id', store=True, readonly=True)
    sequence = fields.Integer(default=10)
    rule_id = fields.Many2one(
        'realestate.procurement.approval.rule', ondelete='restrict',
        help="The rule that required this step. Kept for traceability; the "
             "authority itself is snapshotted on this record, so archiving "
             "the rule cannot rewrite what was required.")

    # -- Snapshot of the authority (M3H) -------------------------------
    rule_name = fields.Char(readonly=True, string='Rule')
    group_id = fields.Many2one(
        'res.groups', readonly=True, string='Approver Group')
    approver_user_id = fields.Many2one(
        'res.users', readonly=True, string='Named Approver')
    amount_basis = fields.Monetary(
        readonly=True, string='Amount Approved',
        help="The tax-exclusive control amount this decision was taken "
             "against. If the requisition later says a different number, the "
             "decision no longer covers it.")
    basis_hash = fields.Char(
        readonly=True,
        help="A fingerprint of the commercial basis — lines, quantities, "
             "amounts, coding, project, currency. Changing any of them "
             "invalidates the approval rather than silently inheriting it.")

    # -- The decision --------------------------------------------------
    decision = fields.Selection(
        DECISION, default='pending', required=True, index=True)
    approved = fields.Boolean(
        compute='_compute_approved', store=True,
        help="Kept as a field because views, filters and existing "
             "integrations read it. `decision` is authoritative.")
    approver_id = fields.Many2one(
        'res.users', readonly=True, string='Decided By')
    approval_date = fields.Datetime(readonly=True, string='Decided On')
    requested_on = fields.Datetime(readonly=True, default=fields.Datetime.now)
    waiting_days = fields.Integer(
        compute='_compute_waiting', store=True,
        help="Days this step has been waiting. M3R: an approval nobody has "
             "looked at for three weeks is an operational fact, and a "
             "register that cannot show it is why people ring each other up.")
    comment = fields.Char()
    note = fields.Char()
    activity_id = fields.Many2one(
        'mail.activity', readonly=True, ondelete='set null',
        help="M3Q — the worklist entry for this step, when the step names a "
             "person to assign it to. One activity, created once and closed "
             "on decision: an activity generated from a compute or a cron "
             "produces a new one every recomputation, and people stop "
             "reading a list that lies about how much work is in it.")

    @api.depends('decision')
    def _compute_approved(self):
        for step in self:
            step.approved = step.decision == 'approved'

    @api.depends('decision', 'requested_on', 'approval_date')
    def _compute_waiting(self):
        now = fields.Datetime.now()
        for step in self:
            if step.decision != 'pending' or not step.requested_on:
                step.waiting_days = 0
            else:
                step.waiting_days = (now - step.requested_on).days

    # ------------------------------------------------------------------
    def _blocking_predecessors(self):
        """Earlier steps that have not been decided yet."""
        self.ensure_one()
        return self.request_id.approval_step_ids.filtered(
            lambda s: s.sequence < self.sequence and s.decision == 'pending')

    def _check_may_decide(self):
        """Everything that has to be true before anybody's decision counts."""
        self.ensure_one()
        request = self.request_id
        if self.decision != 'pending':
            raise UserError(_("%s has already been decided.") % self.rule_name)
        if request.state != 'submitted':
            raise UserError(_(
                "%s is not awaiting approval.") % request.name)
        if self._blocking_predecessors():
            raise UserError(_(
                "An earlier approval step is still pending. Approving out of "
                "order would mean the sequence was decoration."))
        user = self.env.user
        if self.approver_user_id and self.approver_user_id != user:
            raise UserError(_(
                "%s is reserved for %s.")
                % (self.rule_name, self.approver_user_id.display_name))
        if self.group_id and self.group_id not in user.groups_id:
            raise UserError(_(
                "You are not in %s, which is the authority this step "
                "requires.") % self.group_id.name)
        request._check_not_self_approval(self.amount_basis)
        request._check_basis_still_matches(self)

    def action_approve(self):
        for step in self:
            step._check_may_decide()
            step.write({
                'decision': 'approved',
                'approver_id': self.env.user.id,
                'approval_date': fields.Datetime.now(),
            })
            step._close_activity()
            step.request_id._maybe_promote_after_approval()
        return True

    def action_reject(self):
        """Refuse, with a reason, keeping every earlier approval intact.

        Phase 0 had no rejection at all: the only way to say no was to put the
        request back to draft, which looks identical to the requester changing
        their mind. Earlier approvals are deliberately **not** reset — they
        happened, and a resubmission opens a new cycle rather than pretending
        the old one did not exist.
        """
        for step in self:
            if step.decision != 'pending':
                raise UserError(_("%s has already been decided.")
                                % step.rule_name)
            if not step.comment:
                raise UserError(_(
                    "Say why. A rejection with no reason cannot be answered, "
                    "so it comes back unchanged next week."))
            if step.group_id and step.group_id not in self.env.user.groups_id:
                raise UserError(_(
                    "You are not in %s.") % step.group_id.name)
            step.write({
                'decision': 'rejected',
                'approver_id': self.env.user.id,
                'approval_date': fields.Datetime.now(),
            })
            step._close_activity()
            step.request_id._on_step_rejected(step)
        return True

    def _schedule_activity(self):
        """One activity, for a step that names somebody to do it.

        Group steps get none on purpose. Odoo activities belong to a person,
        so a step approvable by any of twelve buyers would mean twelve
        activities and eleven of them stale the moment one is decided. Those
        steps are worked from the approval register's *Awaiting Approval*
        filter instead, which is one list that is always right.
        """
        self.ensure_one()
        if self.activity_id or not self.approver_user_id:
            return
        activity = self.env['mail.activity'].sudo().create({
            'res_model_id': self.env['ir.model']._get(
                'realestate.material.request').id,
            'res_id': self.request_id.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'user_id': self.approver_user_id.id,
            'summary': _("Procurement approval: %s") % (self.rule_name or ''),
            'date_deadline': fields.Date.context_today(self),
        })
        self.activity_id = activity

    def _close_activity(self):
        """Complete the worklist entry. The step remains authoritative."""
        self.ensure_one()
        if self.activity_id:
            self.activity_id.sudo().action_feedback(feedback=_(
                "%(rule)s: %(decision)s", rule=self.rule_name or '',
                decision=dict(DECISION).get(self.decision, '')))

    def unlink(self):
        """M3Z — a decision is not a draft."""
        decided = self.filtered(lambda s: s.decision not in ('pending',
                                                             'cancelled'))
        if decided:
            raise UserError(_(
                "An approval decision cannot be deleted. Cancel the "
                "requisition, or revise it — both keep the history."))
        return super().unlink()
