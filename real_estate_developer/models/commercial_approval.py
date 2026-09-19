# -*- coding: utf-8 -*-
"""Phases 10 & 34 — the commercial approval engine.

Phase 34 asks whether one coherent approval architecture can serve discounts,
custom payment plans, reservation extensions, cancellations, refunds, early
settlements, unit swaps and contract transfers — without turning into a generic
BPM platform.

It can, because all of those share the same shape: *somebody wants to do X, the
size of X decides who must say yes.* So there are exactly two models — a rule
table and a request — and the action type is a plain selection rather than a
pluggable engine. Adding a new approvable action means adding one value to
``APPROVAL_ACTION`` and calling ``_require_approval()`` from that workflow.

What this deliberately is NOT: no configurable multi-step routing DSL, no
delegation matrix, no escalation timers. Those are BPM features, and Phase 34
rules them out.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

#: Commercial actions that can require authorisation. Each is a real business
#: event, not an abstract "operation".
APPROVAL_ACTION = [
    ('discount', 'Commercial Discount'),
    ('custom_payment_plan', 'Custom Payment Plan'),
    ('price_change', 'Price Change'),
    ('bulk_price_change', 'Bulk Price Change'),
    ('price_book_activation', 'Price Book Activation'),
    ('reservation_extension', 'Reservation Extension'),
    ('reservation_cancellation', 'Reservation Cancellation'),
    ('contract_cancellation', 'Contract Cancellation'),
    ('refund', 'Refund'),
    ('early_settlement', 'Early Settlement'),
    ('unit_swap', 'Unit Swap'),
    ('contract_transfer', 'Contract Transfer'),
    ('payment_restructure', 'Payment Restructuring'),
]

APPROVAL_STATE = [
    ('draft', 'Draft'),
    ('pending', 'Pending Approval'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('cancelled', 'Cancelled'),
]

#: The two states that are a decision rather than a step. Reaching either is
#: what `_find_approval` trusts, so reaching either has to go through
#: `action_approve` / `action_reject`, where `_user_may_decide` is checked.
DECIDED_STATES = ('approved', 'rejected')

#: Context key and value marking a write that a decision method has already
#: authorised. The value is an object, compared by identity: a context
#: arriving over RPC is JSON and cannot carry it, so a caller cannot claim to
#: be a decision method.
DECISION_CTX = 're_approval_decision'
DECISION_TOKEN = object()


class CommercialApprovalRule(models.Model):
    """Who may authorise how much of what.

    A rule matches on company, action type, optionally project, and a
    threshold band. The *narrowest matching rule wins*, evaluated by sequence,
    so a project-specific rule can override a company-wide one without having
    to restate everything.
    """
    _name = 'realestate.commercial.approval.rule'
    _description = 'Commercial Approval Rule'
    _order = 'sequence, id'
    _check_company_auto = True

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    action_type = fields.Selection(
        APPROVAL_ACTION, string='Action', required=True, index=True)
    project_id = fields.Many2one(
        'realestate.project', string='Project', check_company=True,
        ondelete='cascade',
        help="Leave empty to apply to every project in the company.")

    # ---- Threshold band ----
    # Both a percentage and an amount band are supported because developers
    # genuinely use both: "up to 2%" for an agent, "up to EGP 500,000" for a
    # director on a large unit.
    threshold_type = fields.Selection([
        ('percent', 'Percentage'),
        ('amount', 'Amount'),
        ('any', 'Any (no threshold)'),
    ], default='percent', required=True)
    min_value = fields.Float(
        string='From', help="Inclusive lower bound of the band.")
    max_value = fields.Float(
        string='To',
        help="Inclusive upper bound. Zero means unbounded — use it for the "
             "top authority in the chain.")

    approver_group_id = fields.Many2one(
        'res.groups', string='Approver Group',
        help="Any member of this group may approve. Leave empty and set a "
             "specific approver instead.")
    approver_user_id = fields.Many2one('res.users', string='Specific Approver')
    allow_self_approval = fields.Boolean(
        string='Allow Self-Approval', default=False,
        help="Off by default. A salesperson approving their own discount is "
             "not an approval.")

    @api.constrains('min_value', 'max_value', 'threshold_type')
    def _check_band(self):
        for rule in self:
            if rule.threshold_type == 'any':
                continue
            if rule.max_value and rule.max_value < rule.min_value:
                raise ValidationError(_(
                    "Rule '%s': the band ends below where it starts."
                ) % rule.name)
            if rule.min_value < 0 or rule.max_value < 0:
                raise ValidationError(_(
                    "Rule '%s': thresholds cannot be negative.") % rule.name)

    # `name` is in the trigger list on purpose. Odoo only validates the
    # constrained fields that appear in `vals`, so a create() that simply omits
    # both approver fields -- the exact mistake this guards against -- would
    # never have fired it. `name` is required, so it is always present.
    @api.constrains('approver_group_id', 'approver_user_id', 'name')
    def _check_approver(self):
        for rule in self:
            if not rule.approver_group_id and not rule.approver_user_id:
                raise ValidationError(_(
                    "Rule '%s' names no approver, so nothing could ever be "
                    "approved under it.") % rule.name)

    def _matches(self, value, project=None):
        """True when ``value`` falls inside this rule's band."""
        self.ensure_one()
        if self.project_id and project and self.project_id != project:
            return False
        if self.threshold_type == 'any':
            return True
        if value < self.min_value:
            return False
        if self.max_value and value > self.max_value:
            return False
        return True

    @api.model
    def _find_rule(self, action_type, value, company, project=None):
        """The narrowest rule that governs ``value``.

        Project-specific rules are preferred over company-wide ones; within
        each, ``sequence`` decides. Returns ``False`` when nothing governs the
        action, which callers must read as "no approval required" — a missing
        rule is not a licence to escalate, it is an unconfigured company.
        """
        domain = [
            ('action_type', '=', action_type),
            ('company_id', '=', company.id),
        ]
        candidates = self.search(domain)
        scoped = candidates.filtered(
            lambda r: r.project_id and project and r.project_id == project)
        generic = candidates.filtered(lambda r: not r.project_id)
        for bucket in (scoped, generic):
            for rule in bucket.sorted(lambda r: (r.sequence, r.id)):
                if rule._matches(value, project=project):
                    return rule
        return self.browse()


class CommercialApprovalRequest(models.Model):
    _name = 'realestate.commercial.approval.request'
    _description = 'Commercial Approval Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        required=True, copy=False, readonly=True,
        default=lambda self: _('New'), index='trigram')
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    action_type = fields.Selection(
        APPROVAL_ACTION, string='Action', required=True, index=True, tracking=True)
    state = fields.Selection(
        APPROVAL_STATE, default='draft', required=True, tracking=True, index=True)

    project_id = fields.Many2one(
        'realestate.project', string='Project', check_company=True, index=True)

    #: What the request is about. A reference rather than a hard link, because
    #: this one model serves reservations, contracts, price books and wizards,
    #: and a column per model would be worse than useless.
    res_model = fields.Char(string='Document Model', readonly=True, index=True)
    res_id = fields.Integer(string='Document ID', readonly=True, index=True)
    document_ref = fields.Char(
        string='Document', compute='_compute_document_ref', store=True)

    requested_value = fields.Float(
        string='Requested Value', tracking=True,
        help="The number the authority band is measured against: a percentage "
             "for a discount, an amount for a refund.")
    requested_amount = fields.Monetary(string='Amount', tracking=True)
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id)
    reason = fields.Text(string='Justification', tracking=True)

    rule_id = fields.Many2one(
        'realestate.commercial.approval.rule', string='Governing Rule',
        readonly=True)
    requested_by_id = fields.Many2one(
        'res.users', string='Requested By', required=True,
        default=lambda self: self.env.user, readonly=True)
    approver_user_id = fields.Many2one(
        'res.users', string='Approved / Rejected By', readonly=True, copy=False)
    decided_on = fields.Datetime(readonly=True, copy=False)
    decision_note = fields.Text(string='Approver Comment')

    approved_value = fields.Float(
        string='Approved Value',
        help="What was actually authorised, which may be less than requested.")

    @api.depends('res_model', 'res_id')
    def _compute_document_ref(self):
        for rec in self:
            if rec.res_model and rec.res_id and rec.res_model in self.env:
                doc = self.env[rec.res_model].browse(rec.res_id).exists()
                rec.document_ref = doc.display_name if doc else False
            else:
                rec.document_ref = False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.commercial.approval.request') or _('New')
        # A request cannot be born decided. Agents hold create rights on this
        # model, and `_find_approval` asks only for state and approved value,
        # so creating one already `approved` would authorise a discount that
        # no approver ever saw.
        if not self.env.su:
            for vals in vals_list:
                if vals.get('state') in DECIDED_STATES:
                    raise AccessError(_(
                        "An approval request cannot be created already "
                        "decided. Raise it and let an authorised approver "
                        "decide it."))
                for field in ('approver_user_id', 'decided_on'):
                    if vals.get(field):
                        raise AccessError(_(
                            "The approver and decision date are recorded by "
                            "the approval action, not supplied when the "
                            "request is raised."))
        return super().create(vals_list)

    def write(self, vals):
        """Guard the decision, however it arrives.

        `action_approve` and `action_reject` check `_user_may_decide` and then
        write; this makes that the only route. Without it an agent with write
        access set `state` to `approved` directly and `_find_approval` took it
        at face value, which is the whole commercial authority matrix bypassed
        by one field.

        `approved_value` stays writable while the request is pending, because
        the approver edits it on the form before deciding. It only means
        anything once the state is `approved`, and that is now gated.
        """
        decided = vals.get('state') in DECIDED_STATES
        touches_decision = bool(
            {'approver_user_id', 'decided_on'} & set(vals))
        if (decided or touches_decision) and not self.env.su \
                and self.env.context.get(DECISION_CTX) is not DECISION_TOKEN:
            raise AccessError(_(
                "An approval decision is recorded by the Approve or Reject "
                "action, which checks that you hold the authority for this "
                "band and are not the person who raised the request."))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Authorisation
    # ------------------------------------------------------------------
    def _user_may_decide(self, user):
        """Whether ``user`` is allowed to decide this request.

        Enforced here, server-side, rather than by hiding the button. A hidden
        button is not security.
        """
        self.ensure_one()
        rule = self.rule_id
        if not rule:
            return False
        if not rule.allow_self_approval and user == self.requested_by_id:
            return False
        if rule.approver_user_id:
            return user == rule.approver_user_id
        if rule.approver_group_id:
            return rule.approver_group_id in user.groups_id
        return False

    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft requests can be submitted."))
            if not rec.rule_id:
                # A request raised by hand from the menu carries no rule, and
                # `_user_may_decide` refuses every approver of a request with
                # no rule, so it could never be decided. The band decides who
                # governs it, exactly as for a request raised by a workflow.
                rule = self.env['realestate.commercial.approval.rule']._find_rule(
                    rec.action_type, rec.requested_value, rec.company_id,
                    project=rec.project_id or None)
                if not rule:
                    raise UserError(_(
                        "No approval rule governs %(action)s of %(value)s in "
                        "%(company)s, so there is nobody to approve request "
                        "%(name)s. Nothing in that band needs approval."
                    ) % {'action': dict(APPROVAL_ACTION).get(
                            rec.action_type, rec.action_type),
                         'value': rec.requested_value,
                         'company': rec.company_id.display_name,
                         'name': rec.name})
                rec.rule_id = rule
            rec.state = 'pending'

    def action_approve(self, note=None, approved_value=None):
        for rec in self:
            if rec.state != 'pending':
                raise UserError(_(
                    "Request %s is not awaiting a decision.") % rec.name)
            if not rec._user_may_decide(self.env.user):
                raise UserError(_(
                    "You are not authorised to approve %s. %s"
                ) % (rec.name, _(
                    "A request cannot be approved by the person who raised it."
                ) if self.env.user == rec.requested_by_id
                    else _("This level requires a different approver.")))
            rec.with_context(
                **{DECISION_CTX: DECISION_TOKEN}).write({
                'state': 'approved',
                'approver_user_id': self.env.user.id,
                'decided_on': fields.Datetime.now(),
                'decision_note': note or rec.decision_note,
                'approved_value': (approved_value if approved_value is not None
                                   else rec.requested_value),
            })
            rec.message_post(body=_("Approved by %s.") % self.env.user.display_name)

    def action_reject(self, note=None):
        for rec in self:
            if rec.state != 'pending':
                raise UserError(_(
                    "Request %s is not awaiting a decision.") % rec.name)
            if not rec._user_may_decide(self.env.user):
                raise UserError(_(
                    "You are not authorised to decide %s.") % rec.name)
            rec.with_context(
                **{DECISION_CTX: DECISION_TOKEN}).write({
                'state': 'rejected',
                'approver_user_id': self.env.user.id,
                'decided_on': fields.Datetime.now(),
                'decision_note': note or rec.decision_note,
            })

    def action_cancel(self):
        self.filtered(lambda r: r.state in ('draft', 'pending')).write(
            {'state': 'cancelled'})


class CommercialApprovalMixin(models.AbstractModel):
    """Mix into any model that needs an authority gate.

    Gives a workflow one call — ``_require_approval(...)`` — that answers
    "may this proceed?" and raises a specific error when it may not.
    """
    _name = 'realestate.commercial.approval.mixin'
    _description = 'Commercial Approval Mixin'

    approval_request_available = fields.Boolean(
        string='Approval Can Be Requested',
        compute='_compute_approval_request_available',
        help="The current step needs an approval the user does not hold, and "
             "no request for it is approved or waiting yet.")

    @api.depends_context('uid')
    def _compute_approval_request_available(self):
        for rec in self:
            rec.approval_request_available = bool(
                rec.id and not rec._approval_request_blocker())

    def _approval_needed(self):
        """The approval the record's next step waits on, or ``None``.

        Returns ``(action_type, value, amount, reason)``, measured exactly as
        the gated step measures it. Models whose steps call
        ``_require_approval`` override this so the user can raise the request
        from the form; without it an above-band step was a dead end.
        """
        self.ensure_one()
        return None

    def _approval_request_blocker(self):
        """Why no request can be raised for this record now, or ``False``."""
        self.ensure_one()
        need = self._approval_needed()
        if not need:
            return _("%s needs no approval at this stage.") % self.display_name
        action_type, value, _amount, _reason = need
        label = dict(APPROVAL_ACTION).get(action_type, action_type)
        rule = self.env['realestate.commercial.approval.rule']._find_rule(
            action_type, value, self._approval_company(),
            project=self._approval_project())
        if not rule:
            return _("No approval rule governs %(action)s of %(value)s, so "
                     "%(doc)s can proceed without one.") % {
                'action': label, 'value': value, 'doc': self.display_name}
        if self._user_holds_authority(rule):
            return _("You hold the authority for %(action)s of %(value)s; "
                     "proceed directly.") % {'action': label, 'value': value}
        if self._find_approval(action_type, value):
            return _("%(action)s of %(value)s on %(doc)s is already "
                     "approved.") % {
                'action': label, 'value': value, 'doc': self.display_name}
        waiting = self.env['realestate.commercial.approval.request'].search([
            ('res_model', '=', self._name),
            ('res_id', '=', self.id),
            ('action_type', '=', action_type),
            ('state', 'in', ('draft', 'pending')),
            ('requested_value', '>=', value),
        ], limit=1)
        if waiting:
            return _("Approval request %s is already waiting for a "
                     "decision.") % waiting.name
        return False

    def action_request_approval(self):
        """Raise the approval request the current step needs."""
        for rec in self:
            blocker = rec._approval_request_blocker()
            if blocker:
                raise UserError(blocker)
            action_type, value, amount, reason = rec._approval_needed()
            request = rec._open_approval_request(
                action_type, value, amount=amount, reason=reason)
            if hasattr(rec, 'message_post'):
                rec.message_post(body=_(
                    "Approval requested (%s).") % request.name)
        return True

    def _approval_company(self):
        self.ensure_one()
        return self.company_id if 'company_id' in self._fields else self.env.company

    def _approval_project(self):
        self.ensure_one()
        return self.project_id if 'project_id' in self._fields else self.env['realestate.project']

    def _find_approval(self, action_type, value):
        """An existing approved request covering ``value`` for this record."""
        self.ensure_one()
        return self.env['realestate.commercial.approval.request'].search([
            ('res_model', '=', self._name),
            ('res_id', '=', self.id),
            ('action_type', '=', action_type),
            ('state', '=', 'approved'),
            ('approved_value', '>=', value),
        ], limit=1)

    def _require_approval(self, action_type, value, amount=0.0, reason=None):
        """Raise unless ``value`` is within authority or already approved.

        Returns the governing rule (or an empty recordset when the action is
        ungoverned) so callers can record what authorised the decision.
        """
        self.ensure_one()
        Rule = self.env['realestate.commercial.approval.rule']
        company = self._approval_company()
        project = self._approval_project()
        rule = Rule._find_rule(action_type, value, company, project=project)
        if not rule:
            # No rule governs this band. That means the company has not
            # configured authority for it, NOT that anything goes -- but
            # blocking every unconfigured action would make the module
            # unusable out of the box, so this is permitted and left visible in
            # the chatter of the calling document.
            return rule

        # Does the acting user already hold the authority themselves?
        if self._user_holds_authority(rule):
            return rule

        approval = self._find_approval(action_type, value)
        if approval:
            return rule

        raise UserError(_(
            "%(action)s of %(value)s requires approval.\n\n"
            "Authority for this level: %(who)s.\n"
            "Raise an approval request and have it approved before continuing."
        ) % {
            'action': dict(APPROVAL_ACTION).get(action_type, action_type),
            'value': ('%.2f%%' % value if rule.threshold_type == 'percent'
                      else '%.2f' % value),
            'who': (rule.approver_user_id.display_name
                    or rule.approver_group_id.display_name or _('unassigned')),
        })

    def _user_holds_authority(self, rule):
        """True when the acting user is themselves the named authority."""
        self.ensure_one()
        user = self.env.user
        if rule.approver_user_id:
            return user == rule.approver_user_id
        if rule.approver_group_id:
            return rule.approver_group_id in user.groups_id
        return False

    def _open_approval_request(self, action_type, value, amount=0.0, reason=None):
        """Create a pending request for this record and return it."""
        self.ensure_one()
        Rule = self.env['realestate.commercial.approval.rule']
        company = self._approval_company()
        project = self._approval_project()
        rule = Rule._find_rule(action_type, value, company, project=project)
        request = self.env['realestate.commercial.approval.request'].create({
            'action_type': action_type,
            'company_id': company.id,
            'project_id': project.id if project else False,
            'res_model': self._name,
            'res_id': self.id,
            'requested_value': value,
            'requested_amount': amount,
            'reason': reason,
            'rule_id': rule.id if rule else False,
            'state': 'pending',
        })
        return request
