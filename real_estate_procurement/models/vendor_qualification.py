# -*- coding: utf-8 -*-
"""M4E–M4H — one qualification decision, and the evidence it rests on.

A qualification is a statement with four dimensions and two dates:

```
    VENDOR  +  COMPANY  +  TRADE  [+ PROJECT]      assessed on   D1
                                                   valid until   D2
```

It is not a flag on the partner. `res.partner` remains the vendor master
(Rule 1) and gains nothing here except a pointer to its governance profile —
because "approved" is never true in the abstract. A supplier may be qualified
for ready-mix in Cairo and not qualified for elevators anywhere, and a single
boolean cannot say that.

### State and result are two different things

```
    STATE   where the assessment is in its workflow
    RESULT  what the assessor concluded
```

`state = approved, result = qualified_with_conditions` is a perfectly ordinary
outcome: the review is finished and the answer is "yes, but". Storing one
field for both would force that to be recorded as an approval with a note, and
notes cannot be filtered, counted or enforced.

### Suspension is deliberately not a state here

The brief lists SUSPENDED among the possible states and this implementation
does not have it. Suspending is a management act about a vendor, taken later
and usually for reasons that have nothing to do with the assessment — and if
it rewrote the assessment's state, the record of what the assessor concluded
would be gone. `realestate.procurement.vendor.restriction` carries suspension
as its own dated, approved, reversible record, and the eligibility service
lets it outrank a valid qualification without touching it. Test C is exactly
this.

### Historic eligibility

`approved_date`, `effective_date` and `expiry_date` are all real and all
consulted (M4Y). An expired or superseded assessment is *still* the answer to
"was this vendor eligible in March" — which is why expiry sets a state and
never deletes anything, and why the service accepts expired records inside
their own validity window.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.sql import index_exists

from .qualification_template import OBLIGATION, REQUIREMENT_TYPE

STATE = [
    ('draft', 'Draft'),
    ('submitted', 'Submitted'),
    ('under_review', 'Under Review'),
    ('assessed', 'Assessed'),
    ('pending_approval', 'Pending Approval'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('expired', 'Expired'),
    ('superseded', 'Superseded'),
    ('cancelled', 'Cancelled'),
]

#: States in which the record is a decision that once meant something. The
#: eligibility service reads all three and lets the dates decide.
HISTORIC_STATES = ('approved', 'expired', 'superseded')

RESULT = [
    ('qualified', 'Qualified'),
    ('qualified_with_conditions', 'Qualified With Conditions'),
    ('not_qualified', 'Not Qualified'),
    ('pending_information', 'Pending Information'),
]

CONDITION_TYPE = [
    ('max_award_value', 'Maximum Award Value'),
    ('project_limited', 'Limited To Projects'),
    ('category_limited', 'Limited To Trades'),
    ('date_limited', 'Limited Validity'),
    ('pre_award_action', 'Action Required Before Award'),
    ('warning', 'Warning'),
]

VERIFICATION = [
    ('pending', 'Not Verified'),
    ('verified', 'Verified'),
    ('failed', 'Failed Verification'),
]

#: The advisory-lock namespace for "one current qualification per scope".
LOCK_NAMESPACE = 'realestate.procurement.vendor.qualification'


class VendorQualification(models.Model):
    _name = 'realestate.procurement.vendor.qualification'
    _description = 'Vendor Qualification'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'effective_date desc, id desc'

    name = fields.Char(
        required=True, copy=False, readonly=True, default=lambda self: _('New'))
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company,
        help="The company making this decision. A qualification granted in "
             "one company has never qualified anybody in another.")
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)
    partner_id = fields.Many2one(
        'res.partner', string='Vendor', required=True, index=True,
        ondelete='restrict', tracking=True,
        help="The supplier master record. M4 adds no second one.")
    profile_id = fields.Many2one(
        'realestate.procurement.vendor.profile', string='Governance Profile',
        readonly=True, ondelete='set null', index=True,
        help="Set when the assessment is created. Deliberately not a compute: "
             "a compute that creates records runs in places nothing should be "
             "created, and this one would.")
    category_id = fields.Many2one(
        'realestate.procurement.vendor.category', string='Trade',
        required=True, index=True, ondelete='restrict', tracking=True)
    project_id = fields.Many2one(
        'realestate.project', string='Project', index=True,
        ondelete='restrict', tracking=True,
        help="Empty for the general company/trade qualification. Set only "
             "for a project endorsement — the extra approval a particular "
             "development needs on top of the general one.")
    is_endorsement = fields.Boolean(
        compute='_compute_is_endorsement', store=True,
        help="A project-scoped qualification. It narrows; it never replaces "
             "the general one.")

    template_id = fields.Many2one(
        'realestate.procurement.qualification.template', string='Template',
        required=True, ondelete='restrict', tracking=True)
    template_version = fields.Integer(
        readonly=True, copy=False,
        help="Snapshotted at creation. Later template versions do not reach "
             "back and change what this assessment asked.")
    scoring_enabled = fields.Boolean(readonly=True, copy=False)
    min_score = fields.Float(
        string='Minimum Score (%)', readonly=True, copy=False)

    assessment_date = fields.Date(
        default=lambda self: fields.Date.context_today(self), tracking=True)
    effective_date = fields.Date(
        required=True, tracking=True,
        default=lambda self: fields.Date.context_today(self),
        help="The first day this qualification is valid from.")
    expiry_date = fields.Date(
        tracking=True,
        help="The last day it is valid. Empty means it does not expire — "
             "which is a decision, and shows up as one on the register.")
    approved_date = fields.Date(
        readonly=True, copy=False, tracking=True,
        help="When the approval was actually given. Eligibility before this "
             "date is impossible however far back the effective date is "
             "typed.")

    assessor_id = fields.Many2one(
        'res.users', string='Assessor', tracking=True,
        default=lambda self: self.env.user)
    reviewer_id = fields.Many2one('res.users', string='Reviewer', tracking=True)
    approver_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False,
        tracking=True)

    state = fields.Selection(
        STATE, default='draft', required=True, readonly=True, copy=False,
        index=True, tracking=True)
    result = fields.Selection(
        RESULT, readonly=True, copy=False, index=True, tracking=True,
        help="What the assessment concluded, which is not the same question "
             "as where it is in its workflow.")
    score = fields.Float(readonly=True, copy=False, string='Score (%)')
    score_explanation = fields.Text(
        readonly=True, copy=False,
        help="The arithmetic, in full. A score nobody can reproduce is not "
             "evidence of anything.")
    rejection_reason = fields.Text(readonly=True, copy=False)
    notes = fields.Html()

    is_current = fields.Boolean(
        readonly=True, copy=False, index=True, string='Current',
        help="Exactly one approved qualification per vendor / company / "
             "trade / project scope carries this, enforced by a partial "
             "unique index rather than by hoping.")
    previous_id = fields.Many2one(
        'realestate.procurement.vendor.qualification',
        string='Reassessment Of', readonly=True, copy=False, index=True)
    superseded_by_id = fields.Many2one(
        'realestate.procurement.vendor.qualification',
        string='Superseded By', readonly=True, copy=False)

    response_ids = fields.One2many(
        'realestate.procurement.qualification.response', 'qualification_id',
        string='Responses')
    condition_ids = fields.One2many(
        'realestate.procurement.qualification.condition', 'qualification_id',
        string='Conditions')
    condition_count = fields.Integer(compute='_compute_condition_count')

    is_group_wide = fields.Boolean(
        string='Recognised By Other Companies',
        help="Off by default and deliberately so. A group qualification is a "
             "real thing, but it has to be stated — leaving the company "
             "empty to get the same effect would expose the decision "
             "everywhere by accident.")
    shared_company_ids = fields.Many2many(
        'res.company', relation='procurement_qualification_company_rel',
        column1='qualification_id', column2='company_id',
        string='Recognised By',
        help="The companies that accept this decision as their own.")

    expiry_status = fields.Selection([
        ('none', 'No Expiry'),
        ('valid', 'Valid'),
        ('expiring', 'Expiring Soon'),
        ('expired', 'Expired'),
    ], compute='_compute_expiry_status', string='Validity')
    days_to_expiry = fields.Integer(compute='_compute_expiry_status')

    _sql_constraints = [
        ('effective_before_expiry',
         "CHECK (expiry_date IS NULL OR effective_date <= expiry_date)",
         'A qualification cannot expire before it becomes effective.'),
    ]

    # ------------------------------------------------------------------
    def init(self):
        """One current qualification per scope, enforced by Postgres.

        M4AF. Two approvers finalising competing reassessments of the same
        vendor and trade both pass an ORM check that reads rows neither has
        committed. Only a unique index rejects the second one. It is partial
        because uniqueness applies to the *current* record — a vendor with
        five years of superseded history has five rows and should.

        `COALESCE(project_id, 0)` because NULLs do not collide in a unique
        index, and two current general qualifications for the same trade are
        exactly the collision this exists to prevent.
        """
        super().init()
        if not index_exists(self.env.cr, 'proc_qualification_one_current'):
            self.env.cr.execute("""
                CREATE UNIQUE INDEX proc_qualification_one_current
                    ON realestate_procurement_vendor_qualification
                       (company_id, partner_id, category_id,
                        COALESCE(project_id, 0))
                 WHERE is_current
            """)
        # M4AG — the eligibility service's hot path is
        # (partner, company, category, state, dates).
        if not index_exists(self.env.cr, 'proc_qualification_lookup'):
            self.env.cr.execute("""
                CREATE INDEX proc_qualification_lookup
                    ON realestate_procurement_vendor_qualification
                       (partner_id, company_id, category_id, state,
                        effective_date, expiry_date)
            """)

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('project_id')
    def _compute_is_endorsement(self):
        for rec in self:
            rec.is_endorsement = bool(rec.project_id)

    @api.depends('condition_ids')
    def _compute_condition_count(self):
        for rec in self:
            rec.condition_count = len(rec.condition_ids)

    @api.depends('expiry_date', 'state')
    def _compute_expiry_status(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.expiry_date:
                rec.expiry_status = 'none'
                rec.days_to_expiry = 0
                continue
            warn_days = rec.company_id.procurement_qualification_warn_days or 0
            remaining = (rec.expiry_date - today).days
            rec.days_to_expiry = remaining
            if remaining < 0:
                rec.expiry_status = 'expired'
            elif warn_days and remaining <= warn_days:
                rec.expiry_status = 'expiring'
            else:
                rec.expiry_status = 'valid'

    # ------------------------------------------------------------------
    # Creation — the template is snapshotted, not referenced
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.procurement.vendor.qualification') or _('New')
        records = super().create(vals_list)
        Profile = self.env['realestate.procurement.vendor.profile']
        for rec in records:
            rec.profile_id = Profile._get_or_create(
                rec.partner_id, rec.company_id)
            rec._instantiate_template()
        return records

    def _instantiate_template(self):
        """Copy the questionnaire onto the assessment.

        M4D and M4F: after this, reading the assessment never has to consult
        the template again. That is the whole point — a template edited or
        replaced in 2028 must not change what a 2026 approval was based on.
        """
        self.ensure_one()
        template = self.template_id
        self.write({
            'template_version': template.version,
            'scoring_enabled': template.scoring_enabled,
            'min_score': template.min_score,
        })
        if not self.expiry_date and template.validity_months:
            self.expiry_date = fields.Date.add(
                self.effective_date, months=template.validity_months)
        Response = self.env['realestate.procurement.qualification.response']
        for requirement in template.requirement_ids:
            Response.create(dict(requirement._snapshot(),
                                 qualification_id=self.id))

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('company_id', 'project_id', 'template_id', 'partner_id')
    def _check_company(self):
        for rec in self:
            if rec.project_id and rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "%(project)s belongs to %(other)s, so %(company)s cannot "
                    "endorse a vendor on it.",
                    project=rec.project_id.display_name,
                    other=rec.project_id.company_id.display_name,
                    company=rec.company_id.display_name))
            if rec.template_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "%(template)s belongs to %(other)s.",
                    template=rec.template_id.display_name,
                    other=rec.template_id.company_id.display_name))

    @api.constrains('is_group_wide', 'shared_company_ids')
    def _check_group_wide_is_explicit(self):
        for rec in self:
            if rec.is_group_wide and not rec.shared_company_ids:
                raise ValidationError(_(
                    "Name the companies that recognise %s. A group "
                    "qualification that does not say who it applies to "
                    "applies to nobody in particular, which is worse than "
                    "not having one.") % rec.display_name)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def _require(self, states, action):
        for rec in self:
            if rec.state not in states:
                raise UserError(_(
                    "%(name)s is %(state)s, so it cannot be %(action)s.",
                    name=rec.display_name,
                    state=dict(STATE)[rec.state], action=action))

    def action_submit(self):
        self._require(('draft',), _('submitted'))
        self.write({'state': 'submitted'})

    def action_start_review(self):
        self._require(('submitted',), _('reviewed'))
        self.write({'state': 'under_review',
                    'reviewer_id': self.env.user.id})

    def action_assess(self):
        """Score the responses and record what they add up to.

        Nothing here is a guess: `_evaluate()` returns the result, the score
        and the arithmetic that produced it, and all three are written down.
        """
        self._require(('under_review', 'assessed'), _('assessed'))
        for rec in self:
            outcome = rec._evaluate()
            rec.write({
                'state': 'assessed',
                'result': outcome['result'],
                'score': outcome['score'],
                'score_explanation': outcome['explanation'],
                'assessment_date': fields.Date.context_today(rec),
            })
            rec._sync_auto_conditions(outcome['soft_failures'])

    def action_request_approval(self):
        self._require(('assessed',), _('sent for approval'))
        self.write({'state': 'pending_approval'})

    def action_approve(self):
        """The only way `is_current` is ever set.

        Two things happen under one advisory lock: the previous current
        qualification for this scope is superseded, and this one takes its
        place. Doing them in either order without the lock leaves a window in
        which a vendor has two current qualifications or none, and the partial
        unique index would turn the first case into a crash rather than a
        wrong answer — better, but still not an answer.
        """
        self._require(('pending_approval',), _('approved'))
        for rec in self:
            rec._check_not_self_approval()
            rec._lock_scope()
            # A `not_qualified` result may be approved and become current.
            # Approving means "this conclusion stands", and a recorded,
            # current, findable "no" is far more useful than an abandoned
            # draft — it makes nobody eligible either way.
            previous = rec._current_peer()
            if previous:
                previous.write({
                    'state': 'superseded',
                    'is_current': False,
                    'superseded_by_id': rec.id,
                })
                # Flushed before the new record claims the slot. The ORM
                # buffers writes and would otherwise send both UPDATEs
                # together, in an order that has the partial unique index
                # reject the approval instead of letting it through — a
                # crash rather than a wrong answer, but still not an answer.
                previous.flush_recordset(['is_current'])
                previous.message_post(body=_(
                    "Superseded by %s.") % rec.display_name)
            rec.write({
                'state': 'approved',
                'is_current': True,
                'approver_id': self.env.user.id,
                'approved_date': fields.Date.context_today(rec),
            })
            rec.profile_id._on_qualification_approved(rec)
            rec.message_post(body=_(
                "Approved: %(result)s. Effective %(effective)s, valid until "
                "%(expiry)s.",
                result=dict(RESULT).get(rec.result, ''),
                effective=rec.effective_date,
                expiry=rec.expiry_date or _('further notice')))

    def action_reject(self, reason=None):
        """A rejection is a decision with a reason, not a reset to draft."""
        reason = reason or self.env.context.get('rejection_reason')
        if not reason:
            raise UserError(_(
                "Say why. A rejection with no reason cannot be answered, "
                "appealed or learned from."))
        self._require(('submitted', 'under_review', 'assessed',
                       'pending_approval'), _('rejected'))
        for rec in self:
            rec._check_not_self_approval()
            rec.write({'state': 'rejected',
                       'result': 'not_qualified',
                       'approver_id': self.env.user.id,
                       'rejection_reason': reason})
            rec.message_post(body=_("Rejected: %s") % reason)

    def action_cancel(self):
        self._require(('draft', 'submitted', 'under_review', 'assessed',
                       'pending_approval'), _('cancelled'))
        self.write({'state': 'cancelled'})

    def action_reset_to_draft(self):
        self._require(('rejected', 'cancelled'), _('reopened'))
        self.write({'state': 'draft', 'result': False,
                    'rejection_reason': False})

    def action_reassess(self):
        """M4O — a new assessment, not an extended old one.

        The evidence references come forward so nobody retypes them; the
        verification does not, for anything that expires. An insurance
        certificate that was verified two years ago is not verified now, and
        copying the tick would be the single most dangerous convenience in
        this module.
        """
        self.ensure_one()
        if self.state not in HISTORIC_STATES:
            raise UserError(_(
                "Reassess a qualification that was approved. %s has not "
                "been.") % self.display_name)
        template = self.template_id
        if template.next_version_id and \
                template.next_version_id.state == 'active':
            template = template.next_version_id
        new = self.create({
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'category_id': self.category_id.id,
            'project_id': self.project_id.id,
            'template_id': template.id,
            'effective_date': fields.Date.context_today(self),
            'assessor_id': self.env.user.id,
            'previous_id': self.id,
        })
        new._carry_forward_from(self)
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': new.id,
            'view_mode': 'form',
        }

    def _carry_forward_from(self, previous):
        """Copy evidence references; re-open verification where it expires."""
        self.ensure_one()
        by_code = {}
        for response in previous.response_ids:
            key = response.requirement_code or response.requirement_name
            by_code.setdefault(key, response)
        for response in self.response_ids:
            key = response.requirement_code or response.requirement_name
            old = by_code.get(key)
            if not old:
                continue
            values = {
                'value_bool': old.value_bool,
                'value_char': old.value_char,
                'value_number': old.value_number,
                'value_selection': old.value_selection,
                'value_text': old.value_text,
                'issuer': old.issuer,
                'document_number': old.document_number,
                'assessor_comment': old.assessor_comment,
            }
            if not response.expiry_sensitive:
                values.update({
                    'value_date': old.value_date,
                    'document_expiry_date': old.document_expiry_date,
                    'document_issue_date': old.document_issue_date,
                    'verification': old.verification,
                    'verified_by_id': old.verified_by_id.id,
                    'verified_on': old.verified_on,
                    'score': old.score,
                })
            response.write(values)

    # ------------------------------------------------------------------
    # Maker / checker — M4U
    # ------------------------------------------------------------------
    def _check_not_self_approval(self):
        """Server-side, from `env.user`, never from anything the client sent.

        Qualification approval is kept separate from M3's spend approval on
        purpose: they answer different questions and a company may reasonably
        allow one and not the other. What they share is the principle, not
        the switch.
        """
        self.ensure_one()
        if self.company_id.procurement_qualification_allow_self_approval:
            return
        if self.env.user == self.assessor_id:
            raise UserError(_(
                "%(user)s assessed %(name)s, so somebody else has to approve "
                "it. Turn that off on the company if this organisation "
                "genuinely wants one person to do both.",
                user=self.env.user.display_name, name=self.display_name))

    def _lock_scope(self):
        """Serialise approvals competing for the same current slot."""
        self.ensure_one()
        token = '%s:%s:%s:%s:%s' % (
            LOCK_NAMESPACE, self.company_id.id, self.partner_id.id,
            self.category_id.id, self.project_id.id or 0)
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (token,))

    def _current_peer(self):
        """The qualification this one is about to replace, if any."""
        self.ensure_one()
        return self.search([
            ('id', '!=', self.id),
            ('company_id', '=', self.company_id.id),
            ('partner_id', '=', self.partner_id.id),
            ('category_id', '=', self.category_id.id),
            ('project_id', '=', self.project_id.id or False),
            ('is_current', '=', True),
        ], limit=1)

    # ------------------------------------------------------------------
    # Scoring — M4G
    # ------------------------------------------------------------------
    def _evaluate(self):
        """Result, score and the arithmetic behind both.

        The rules, in the order they are applied:

        1. a blocking requirement that failed ends it — `not_qualified`,
           whatever the total says;
        2. a mandatory requirement with no answer ends it differently —
           `pending_information`, because nobody has decided anything yet;
        3. a mandatory requirement that failed without being blocking
           downgrades to `qualified_with_conditions` and leaves a structured
           condition naming it (M4H: not a note);
        4. a scored template below its minimum is `not_qualified`.
        """
        self.ensure_one()
        lines = []
        blocking_failures, unanswered, soft_failures = [], [], []
        weighted, weighted_max = 0.0, 0.0

        for response in self.response_ids.sorted(
                lambda r: (r.sequence, r.id)):
            if response.obligation == 'informational':
                continue
            if response.obligation == 'mandatory' and not response.is_answered:
                unanswered.append(response.requirement_name)
                continue
            if response.obligation == 'scored':
                contribution = response.score * response.weight
                ceiling = response.max_score * response.weight
                weighted += contribution
                weighted_max += ceiling
                lines.append('%s: %.2f / %.2f × weight %.2f = %.2f' % (
                    response.requirement_name, response.score,
                    response.max_score, response.weight, contribution))
            if response.obligation == 'mandatory' and not response.passed:
                if response.blocking:
                    blocking_failures.append(response.requirement_name)
                else:
                    soft_failures.append(response.requirement_name)

        score = (weighted / weighted_max * 100.0) if weighted_max else 0.0
        if self.scoring_enabled and weighted_max:
            lines.append(_('Total: %(got).2f / %(max).2f = %(pct).2f%%',
                           got=weighted, max=weighted_max, pct=score))
            lines.append(_('Minimum required: %.2f%%') % self.min_score)
        elif not self.scoring_enabled:
            lines.append(_(
                'This template is pass/fail. No percentage is computed, and '
                'the result is decided by the mandatory requirements alone.'))

        if blocking_failures:
            result = 'not_qualified'
            lines.append(_('Blocking requirement(s) failed: %s. This decides '
                           'the result regardless of any score.')
                         % ', '.join(blocking_failures))
        elif unanswered:
            result = 'pending_information'
            lines.append(_('Mandatory requirement(s) unanswered: %s.')
                         % ', '.join(unanswered))
        elif self.scoring_enabled and weighted_max and score < self.min_score:
            result = 'not_qualified'
            lines.append(_('Below the minimum score.'))
        elif soft_failures:
            result = 'qualified_with_conditions'
            lines.append(_('Mandatory requirement(s) failed without being '
                           'blocking: %s. Recorded as conditions.')
                         % ', '.join(soft_failures))
        elif self.condition_ids.filtered(lambda c: not c.auto_generated):
            result = 'qualified_with_conditions'
        else:
            result = 'qualified'

        return {'result': result,
                'score': score if self.scoring_enabled else 0.0,
                'explanation': '\n'.join(lines),
                'soft_failures': soft_failures}

    def _sync_auto_conditions(self, soft_failures):
        """Keep one auto condition per unmet non-blocking requirement."""
        self.ensure_one()
        Condition = self.env['realestate.procurement.qualification.condition']
        existing = self.condition_ids.filtered('auto_generated')
        existing.unlink()
        for name in soft_failures:
            Condition.create({
                'qualification_id': self.id,
                'condition_type': 'warning',
                'auto_generated': True,
                'name': _('Unmet requirement: %s') % name,
                'note': _("Mandatory but not blocking, so the assessment "
                          "stands with this recorded against it."),
            })

    # ------------------------------------------------------------------
    # Expiry — M4N
    # ------------------------------------------------------------------
    @api.model
    def expire_due_qualifications(self, company=None):
        """Nightly. Moves the state; touches nothing else.

        Expiry is not a deletion and not a downgrade of what the assessor
        concluded. The record keeps its result, its evidence and its dates,
        and the eligibility service keeps returning it for questions about
        dates it was valid on.
        """
        today = fields.Date.context_today(self)
        domain = [('state', '=', 'approved'),
                  ('expiry_date', '!=', False),
                  ('expiry_date', '<', today)]
        if company:
            domain.append(('company_id', '=', company.id))
        due = self.search(domain)
        for rec in due:
            rec.write({'state': 'expired', 'is_current': False})
            rec.message_post(body=_("Expired on %s.") % rec.expiry_date)
        return len(due)

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------
    #: Everything that would change what a decided assessment asserted.
    _DECIDED_FIELDS = {
        'partner_id', 'company_id', 'category_id', 'project_id',
        'template_id', 'template_version', 'effective_date', 'result',
        'score', 'min_score', 'scoring_enabled', 'approved_date',
        'approver_id',
    }

    def write(self, vals):
        engine = self.env.context.get('re_qualification_engine')
        touched = self._DECIDED_FIELDS & set(vals)
        if touched and not engine:
            for rec in self:
                if rec.state in HISTORIC_STATES:
                    raise UserError(_(
                        "%(name)s was decided on %(date)s. %(fields)s cannot "
                        "be changed now — reassess it instead, and both "
                        "records stay readable.",
                        name=rec.display_name,
                        date=rec.approved_date or rec.assessment_date,
                        fields=', '.join(sorted(touched))))
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state in HISTORIC_STATES:
                raise UserError(_(
                    "%s is a governance decision with a date on it. Cancel or "
                    "supersede it — deleting it would make the sourcing it "
                    "authorised unexplainable.") % rec.display_name)
        return super().unlink()

    def _write_engine(self, vals):
        """Internal writes that are allowed to touch decided fields."""
        return self.with_context(re_qualification_engine=True).write(vals)


class QualificationResponse(models.Model):
    _name = 'realestate.procurement.qualification.response'
    _description = 'Vendor Qualification Response'
    _order = 'sequence, id'

    qualification_id = fields.Many2one(
        'realestate.procurement.vendor.qualification', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='qualification_id.company_id', store=True, index=True)
    partner_id = fields.Many2one(
        related='qualification_id.partner_id', store=True, index=True)
    sequence = fields.Integer(default=10)

    # -- Snapshot of the requirement (M4F) -----------------------------
    requirement_id = fields.Many2one(
        'realestate.procurement.qualification.requirement',
        string='Requirement (source)', ondelete='set null', readonly=True,
        help="Traceability only. Everything needed to read this response is "
             "copied onto it, so a deleted or re-versioned template changes "
             "nothing here.")
    requirement_name = fields.Char(required=True, readonly=True,
                                   string='Requirement')
    requirement_code = fields.Char(readonly=True)
    requirement_type = fields.Selection(
        REQUIREMENT_TYPE, required=True, readonly=True, string='Answer Type')
    obligation = fields.Selection(OBLIGATION, required=True, readonly=True)
    blocking = fields.Boolean(readonly=True)
    expiry_sensitive = fields.Boolean(readonly=True, string='Expires')
    confidential = fields.Boolean(readonly=True, index=True)
    weight = fields.Float(readonly=True, default=1.0)
    max_score = fields.Float(readonly=True, default=10.0)
    threshold = fields.Float(readonly=True, string='Minimum Value')
    selection_options = fields.Char(readonly=True)
    area_id = fields.Many2one(
        'realestate.procurement.qualification.area', readonly=True,
        ondelete='set null')
    area_name = fields.Char(readonly=True, string='Area')

    # -- The answer ----------------------------------------------------
    value_bool = fields.Boolean(string='Yes')
    value_char = fields.Char(string='Value')
    value_number = fields.Float(string='Number')
    value_date = fields.Date(string='Date')
    value_selection = fields.Char(string='Selected')
    value_text = fields.Text(string='Evidence')

    attachment_ids = fields.Many2many(
        'ir.attachment', relation='procurement_qual_response_attachment_rel',
        column1='response_id', column2='attachment_id', string='Documents',
        help="Held against this response, not against the partner. A user "
             "who may read the vendor does not thereby gain the vendor's "
             "financial statements.")
    document_number = fields.Char()
    issuer = fields.Char()
    document_issue_date = fields.Date(string='Issued')
    document_expiry_date = fields.Date(string='Document Expiry')

    verification = fields.Selection(
        VERIFICATION, default='pending', required=True)
    verified_by_id = fields.Many2one('res.users', readonly=True)
    verified_on = fields.Date(readonly=True)
    assessor_comment = fields.Text()
    score = fields.Float()

    exception_reason = fields.Text(
        string='Waiver Reason',
        help="Why this requirement was passed without being met. Named and "
             "approved, or it is not a waiver.")
    exception_approved_by_id = fields.Many2one(
        'res.users', string='Waiver Approved By', readonly=True, copy=False)

    is_answered = fields.Boolean(compute='_compute_answer', store=True)
    passed = fields.Boolean(compute='_compute_answer', store=True)

    @api.depends('value_bool', 'value_char', 'value_number', 'value_date',
                 'value_selection', 'value_text', 'attachment_ids',
                 'document_expiry_date', 'verification', 'score',
                 'requirement_type', 'threshold', 'max_score',
                 'exception_approved_by_id')
    def _compute_answer(self):
        for rec in self:
            rec.is_answered = rec._answered()
            rec.passed = rec._passed()

    def _answered(self):
        self.ensure_one()
        kind = self.requirement_type
        if kind == 'boolean':
            return True          # a boolean is always answered; False is "no"
        if kind == 'document':
            return bool(self.attachment_ids or self.document_number)
        if kind == 'date':
            return bool(self.value_date)
        if kind == 'number':
            return bool(self.value_number)
        if kind == 'selection':
            return bool(self.value_selection)
        if kind == 'score':
            return bool(self.score)
        return bool(self.value_text or self.value_char)

    def _passed(self):
        """Did the answer satisfy the requirement?

        A waiver approved by a named person passes it. Anything else is
        judged on the evidence.
        """
        self.ensure_one()
        if self.exception_approved_by_id:
            return True
        if self.verification == 'failed':
            return False
        if not self._answered():
            return False
        kind = self.requirement_type
        if kind == 'boolean':
            return bool(self.value_bool)
        if kind == 'document':
            if not self.attachment_ids and not self.document_number:
                return False
            if self.expiry_sensitive and self.document_expiry_date and \
                    self.document_expiry_date < fields.Date.context_today(self):
                return False
            return self.verification != 'pending' or not self.blocking
        if kind == 'date':
            return not self.expiry_sensitive or (
                self.value_date >= fields.Date.context_today(self))
        if kind == 'number':
            return self.value_number >= self.threshold
        if kind == 'score':
            return self.score >= (self.threshold or 0.0)
        return True

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._bind_attachments()
        return records

    def _bind_attachments(self):
        """Tie every document to this response, so access follows the record.

        M4Z's attachment test exists because of what happens without this.
        A many2many of `ir.attachment` created through the web widget leaves
        the attachment unattached — `res_model` empty — and an unattached
        attachment is readable by anybody who can read attachments at all. A
        vendor's audited accounts would then be one `/web/content/` away from
        every requester in the company.

        Stamping `res_model` and `res_id` makes Odoo's own attachment check
        ask whether the reader may read *this response*, which is exactly the
        question the record rules already answer. The **read** path therefore
        carries no elevation at all. The one `sudo()` below is on the write
        that does the binding — the uploader does not necessarily own the
        attachment record — and every use of it tightens access rather than
        widening it.
        """
        for rec in self:
            loose = rec.attachment_ids.sudo().filtered(
                lambda a: not a.res_model or a.res_model == 'ir.attachment')
            if loose:
                loose.write({'res_model': rec._name, 'res_id': rec.id})

    def action_verify(self):
        self.write({'verification': 'verified',
                    'verified_by_id': self.env.user.id,
                    'verified_on': fields.Date.context_today(self)})

    def action_fail_verification(self):
        self.write({'verification': 'failed',
                    'verified_by_id': self.env.user.id,
                    'verified_on': fields.Date.context_today(self)})

    def action_waive(self):
        """Pass a requirement that was not met, on somebody's authority."""
        for rec in self:
            if not rec.exception_reason:
                raise UserError(_(
                    "Say why %s is being waived.") % rec.requirement_name)
            if rec.env.user == rec.qualification_id.assessor_id and not \
                    rec.company_id.procurement_qualification_allow_self_approval:
                raise UserError(_(
                    "The assessor cannot waive their own requirement."))
            rec.exception_approved_by_id = rec.env.user.id

    def write(self, vals):
        """Answers are frozen once the assessment has been decided."""
        if self.env.context.get('re_qualification_engine'):
            res = super().write(vals)
            if 'attachment_ids' in vals:
                self._bind_attachments()
            return res
        for rec in self:
            if rec.qualification_id.state in HISTORIC_STATES:
                raise UserError(_(
                    "%(name)s belongs to a decided assessment. Reassess "
                    "%(qual)s to record new evidence.",
                    name=rec.requirement_name,
                    qual=rec.qualification_id.display_name))
        res = super().write(vals)
        if 'attachment_ids' in vals:
            self._bind_attachments()
        return res

    def unlink(self):
        for rec in self:
            if rec.qualification_id.state in HISTORIC_STATES:
                raise UserError(_(
                    "A decided assessment keeps its evidence."))
        return super().unlink()


class QualificationCondition(models.Model):
    """M4H — "yes, but" said in fields rather than in prose.

    A condition nobody can query is a condition nobody will honour. Award
    (M7) will read `max_award_value` and refuse; today the eligibility
    service returns it so a buyer can see it before inviting anybody.
    """
    _name = 'realestate.procurement.qualification.condition'
    _description = 'Qualification Condition'
    _order = 'qualification_id, id'

    qualification_id = fields.Many2one(
        'realestate.procurement.vendor.qualification', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='qualification_id.company_id', store=True)
    currency_id = fields.Many2one(
        related='qualification_id.currency_id', readonly=True)
    condition_type = fields.Selection(
        CONDITION_TYPE, required=True, default='warning')
    name = fields.Char(required=True, default=lambda self: _('Condition'))
    amount = fields.Monetary(
        string='Maximum Award Value', currency_field='currency_id',
        help="For a value-capped qualification. Company currency.")
    project_ids = fields.Many2many(
        'realestate.project', relation='procurement_qual_condition_project_rel',
        column1='condition_id', column2='project_id', string='Projects')
    category_ids = fields.Many2many(
        'realestate.procurement.vendor.category',
        relation='procurement_qual_condition_trade_rel',
        column1='condition_id', column2='trade_id', string='Trades')
    date_from = fields.Date()
    date_to = fields.Date()
    action_required = fields.Text(
        help="What has to happen before an award — a manufacturer letter, a "
             "sample approval, a site visit.")
    note = fields.Text()
    auto_generated = fields.Boolean(
        readonly=True,
        help="Created by the assessment itself from an unmet non-blocking "
             "requirement, and rebuilt whenever it is reassessed.")

    @api.constrains('condition_type', 'amount')
    def _check_amount(self):
        for rec in self:
            if rec.condition_type == 'max_award_value' and rec.amount <= 0:
                raise ValidationError(_(
                    "A maximum award value of zero would mean the vendor is "
                    "qualified for nothing. Say Not Qualified instead."))

    def _as_dict(self):
        self.ensure_one()
        return {
            'id': self.id,
            'type': self.condition_type,
            'name': self.name,
            'amount': self.amount,
            'currency_id': self.currency_id.id,
            'project_ids': self.project_ids.ids,
            'category_ids': self.category_ids.ids,
            'date_from': self.date_from,
            'date_to': self.date_to,
            'action_required': self.action_required,
            'note': self.note,
        }
