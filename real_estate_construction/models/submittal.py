# -*- coding: utf-8 -*-
"""M5B — submittals: the workflow that asks somebody to review a revision.

A submittal is **not** the document. The document is the artefact; the
submittal is the request for approval of one particular revision of it, and
the record of what the reviewers said.

```
    SUBMITTAL  SUB-00425
      └ REV 0   submitted → reviewed → revise & resubmit
      └ REV 1   submitted → reviewed → approved      ← current
```

Rev 0 does not disappear when Rev 1 arrives. It carries the reviewer's comments
that *caused* Rev 1, which is the most useful thing in the whole record.

### State is not response

`state` is where the workflow is; `response` is what the reviewer decided.
"Responded / revise and resubmit" is a normal, meaningful combination, and a
single field could not express it.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

SUBMITTAL_TYPES = [
    ('shop_drawing', 'Shop Drawing'),
    ('material', 'Material'),
    ('method_statement', 'Method Statement'),
    ('technical_data', 'Technical Data'),
    ('sample', 'Sample'),
    ('mockup', 'Mock-up'),
    ('test_report', 'Test Report'),
    ('certificate', 'Certificate'),
    ('product_data', 'Product Data'),
    ('as_built', 'As-Built'),
    ('om_manual', 'O&M Manual'),
    ('other', 'Other'),
]

SUBMITTAL_STATES = [
    ('draft', 'Draft'),
    ('required', 'Required'),
    ('submitted', 'Submitted'),
    ('under_review', 'Under Review'),
    ('responded', 'Responded'),
    ('closed', 'Closed'),
    ('void', 'Void'),
]

#: Semantic categories. Labels may be customised per project; the meaning that
#: workflow logic depends on must not move.
RESPONSE_CODES = [
    ('approved', 'Approved'),
    ('approved_as_noted', 'Approved as Noted'),
    ('revise_resubmit', 'Revise and Resubmit'),
    ('rejected', 'Rejected'),
]

#: Responses that let work proceed.
ACCEPTING_RESPONSES = ('approved', 'approved_as_noted')


class ConstructionSubmittal(models.Model):
    _name = 'realestate.construction.submittal'
    _description = 'Construction Submittal'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, required_submission_date, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Submittal Number', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(required=True, tracking=True)
    description = fields.Html()

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    submittal_package_id = fields.Many2one(
        'realestate.construction.submittal.package', ondelete='set null',
        string='Submittal Package')

    contractor_id = fields.Many2one(
        'realestate.contractor', string='Responsible Contractor',
        tracking=True)
    contractor_partner_id = fields.Many2one(
        'res.partner', string='Submitting Organisation',
        help="Not every submitter is an Odoo user.")
    submittal_type = fields.Selection(
        SUBMITTAL_TYPES, required=True, default='shop_drawing', index=True)
    discipline = fields.Selection(
        [('architectural', 'Architectural'), ('structural', 'Structural'),
         ('civil', 'Civil'), ('mechanical', 'Mechanical'),
         ('electrical', 'Electrical'), ('plumbing', 'Plumbing'),
         ('hvac', 'HVAC'), ('landscape', 'Landscape'),
         ('interior', 'Interior'), ('other', 'Other')],
        default='other', index=True)
    specification_section = fields.Char(index='trigram')

    manager_id = fields.Many2one(
        'res.users', string='Submittal Manager',
        default=lambda self: self.env.user, tracking=True)

    required_submission_date = fields.Date(tracking=True, index=True)
    submitted_date = fields.Date(readonly=True, copy=False, tracking=True)
    required_approval_date = fields.Date(tracking=True, index=True)
    final_response_date = fields.Date(readonly=True, copy=False)

    state = fields.Selection(
        SUBMITTAL_STATES, default='draft', required=True, tracking=True,
        copy=False, index=True)
    final_response = fields.Selection(
        RESPONSE_CODES, readonly=True, copy=False, tracking=True,
        help="The decision on the current revision. Distinct from the "
             "workflow state — 'Responded / Revise and Resubmit' is a normal "
             "and meaningful combination.")

    revision_ids = fields.One2many(
        'realestate.construction.submittal.revision', 'submittal_id',
        string='Revisions')
    revision_count = fields.Integer(compute='_compute_current', store=True)
    current_revision_id = fields.Many2one(
        'realestate.construction.submittal.revision',
        compute='_compute_current', store=True)
    current_revision_code = fields.Char(
        related='current_revision_id.revision_code', store=True,
        readonly=True)

    change_event_id = fields.Many2one(
        'realestate.construction.change.event', readonly=True, copy=False)
    distribution_partner_ids = fields.Many2many(
        'res.partner', 'construction_submittal_distribution_rel',
        'submittal_id', 'partner_id', string='Distribution')

    submission_overdue_days = fields.Integer(
        compute='_compute_overdue', store=True)
    review_overdue_days = fields.Integer(
        compute='_compute_overdue', store=True)
    is_overdue = fields.Boolean(compute='_compute_overdue', store=True,
                                index=True)

    @api.depends('revision_ids.sequence', 'revision_ids.response')
    def _compute_current(self):
        for rec in self:
            revisions = rec.revision_ids.sorted('sequence')
            rec.revision_count = len(revisions)
            rec.current_revision_id = revisions[-1] if revisions else False

    @api.depends('required_submission_date', 'submitted_date',
                 'required_approval_date', 'final_response_date', 'state')
    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.state in ('closed', 'void'):
                rec.submission_overdue_days = 0
                rec.review_overdue_days = 0
                rec.is_overdue = False
                continue
            submission_ref = rec.submitted_date or today
            review_ref = rec.final_response_date or today
            rec.submission_overdue_days = max(
                (submission_ref - rec.required_submission_date).days, 0
            ) if rec.required_submission_date else 0
            rec.review_overdue_days = max(
                (review_ref - rec.required_approval_date).days, 0
            ) if rec.required_approval_date else 0
            rec.is_overdue = bool(
                (rec.submission_overdue_days and not rec.submitted_date)
                or (rec.review_overdue_days and not rec.final_response_date))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.submittal') or 'SUB/NEW'
        submittals = super().create(vals_list)
        for submittal in submittals:
            if not submittal.revision_ids:
                submittal._create_revision(code='0', sequence=0)
        return submittals

    def _create_revision(self, code, sequence, document_revision=None):
        self.ensure_one()
        return self.env['realestate.construction.submittal.revision'].create({
            'submittal_id': self.id,
            'revision_code': code,
            'sequence': sequence,
            'document_revision_id': (document_revision.id
                                     if document_revision else False),
        })

    # ------------------------------------------------------------------
    def action_require(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft submittal can be required."))
            rec.state = 'required'
        return True

    def action_submit(self):
        for rec in self:
            if rec.state not in ('draft', 'required', 'responded'):
                raise UserError(_(
                    "%s is not in a state that can be submitted.") % rec.name)
            revision = rec.current_revision_id
            if not revision:
                raise UserError(_("There is no revision to submit."))
            if revision.response:
                raise UserError(_(
                    "Revision %s has already been responded to. Create a new "
                    "revision before submitting again.")
                    % revision.revision_code)
            revision.write({
                'submitted_date': fields.Date.context_today(rec),
                'state': 'submitted',
            })
            rec.write({
                'state': 'submitted',
                'submitted_date': (rec.submitted_date or
                                   fields.Date.context_today(rec)),
                'final_response': False,
            })
        return True

    def action_review(self):
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_("Only a submitted submittal is reviewed."))
            rec.state = 'under_review'
        return True

    def action_create_revision(self):
        """The next revision, when the reviewer asked for one.

        Never overwrites: the previous revision keeps its file, its reviewers
        and their comments, which are the reason this revision exists.
        """
        self.ensure_one()
        current = self.current_revision_id
        if current and not current.response:
            raise UserError(_(
                "Revision %s has not been responded to yet — a new revision "
                "would leave an open review nobody closed.")
                % current.revision_code)
        next_sequence = (current.sequence + 1) if current else 0
        revision = self._create_revision(
            code=str(next_sequence), sequence=next_sequence)
        self.write({'state': 'required', 'final_response': False})
        return revision

    def action_close(self):
        for rec in self:
            if rec.state != 'responded':
                raise UserError(_(
                    "A submittal closes once it has been responded to."))
            if rec.final_response == 'revise_resubmit':
                raise UserError(_(
                    "%s awaits a revised submission and cannot be closed.")
                    % rec.name)
            rec.state = 'closed'
        return True

    def action_void(self):
        for rec in self:
            if rec.revision_ids.filtered('response'):
                raise UserError(_(
                    "A submittal that has been responded to is part of the "
                    "record."))
            rec.state = 'void'
        return True

    def action_create_change_event(self):
        """A review outcome may have commercial consequences. Somebody decides."""
        self.ensure_one()
        if self.change_event_id:
            raise UserError(_("%s already has a change event.") % self.name)
        event = self.env['realestate.construction.change.event'].create({
            'title': _("Submittal %(number)s — %(title)s",
                       number=self.name, title=self.title),
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'package_id': self.package_id.id or False,
            'contractor_id': self.contractor_id.id or False,
            'wbs_id': self.wbs_id.id or False,
            'source': 'specification_change',
            'source_reference': self.name,
            'source_model': self._name,
            'source_id': self.id,
        })
        self.change_event_id = event
        return event

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Submittal %(name)s is in %(company)s but its project is "
                    "in %(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))


class ConstructionSubmittalRevision(models.Model):
    """One submission of one revision, and what the reviewers said about it."""
    _name = 'realestate.construction.submittal.revision'
    _description = 'Submittal Revision'
    _order = 'submittal_id, sequence, id'
    _check_company_auto = True

    submittal_id = fields.Many2one(
        'realestate.construction.submittal', required=True,
        ondelete='cascade', index=True, check_company=True)
    project_id = fields.Many2one(
        related='submittal_id.project_id', store=True, index=True,
        readonly=True)
    company_id = fields.Many2one(
        related='submittal_id.company_id', store=True, index=True,
        readonly=True)

    revision_code = fields.Char(required=True, readonly=True)
    sequence = fields.Integer(required=True, default=0, readonly=True,
                              index=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('responded', 'Responded'),
    ], default='draft', required=True, readonly=True)

    document_revision_id = fields.Many2one(
        'realestate.construction.document.revision',
        string='Controlled Revision', ondelete='restrict',
        help="The exact registered revision under review, when the submittal "
             "concerns a controlled document.")
    attachment_ids = fields.Many2many(
        'ir.attachment', 'construction_submittal_revision_attachment_rel',
        'revision_id', 'attachment_id', string='Submitted Files')

    submitted_date = fields.Date(readonly=True)
    response = fields.Selection(RESPONSE_CODES, readonly=True, copy=False)
    response_date = fields.Date(readonly=True, copy=False)
    responded_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    response_comments = fields.Text(readonly=True)

    review_step_ids = fields.One2many(
        'realestate.construction.submittal.review', 'revision_id',
        string='Reviews')
    reviews_responded = fields.Integer(compute='_compute_reviews')
    reviews_required = fields.Integer(compute='_compute_reviews')
    reviews_complete = fields.Boolean(compute='_compute_reviews')

    is_current = fields.Boolean(compute='_compute_is_current', store=True)

    @api.depends('submittal_id.current_revision_id')
    def _compute_is_current(self):
        for rec in self:
            rec.is_current = rec.submittal_id.current_revision_id == rec

    @api.depends('review_step_ids.state')
    def _compute_reviews(self):
        for rec in self:
            steps = rec.review_step_ids.filtered(
                lambda s: s.state != 'cancelled')
            rec.reviews_required = len(steps)
            responded = steps.filtered(lambda s: s.state == 'responded')
            rec.reviews_responded = len(responded)
            rec.reviews_complete = bool(steps) and len(responded) == len(steps)

    def action_respond(self, response, comments=None, force=False):
        """Record the final decision on this revision.

        Refuses while reviewers are still outstanding unless somebody
        deliberately overrides: marking a review complete because one of three
        reviewers replied is how comments get lost.
        """
        self.ensure_one()
        if self.response:
            raise UserError(_(
                "Revision %s already has a response. Create a new revision.")
                % self.revision_code)
        if response not in dict(RESPONSE_CODES):
            raise UserError(_("Unknown review response: %s") % response)
        if self.review_step_ids and not self.reviews_complete and not force:
            raise UserError(_(
                "%(responded)s of %(required)s reviewers have replied. Wait "
                "for the rest, or finalise deliberately.",
                responded=self.reviews_responded,
                required=self.reviews_required))

        self.write({
            'response': response,
            'response_date': fields.Date.context_today(self),
            'responded_by_id': self.env.user.id,
            'response_comments': comments,
            'state': 'responded',
        })
        self.submittal_id.write({
            'state': 'responded',
            'final_response': response,
            'final_response_date': fields.Date.context_today(self),
        })
        # An accepted revision of a controlled document updates the register.
        if response in ACCEPTING_RESPONSES and self.document_revision_id:
            self.document_revision_id.action_approve(
                with_comments=response == 'approved_as_noted')
        return True

    def write(self, vals):
        """A revision that has been responded to is history."""
        frozen = {'document_revision_id', 'revision_code', 'sequence',
                  'submitted_date', 'attachment_ids'}
        if frozen & set(vals):
            responded = self.filtered('response')
            if responded:
                raise UserError(_(
                    "Revision %s has been reviewed. Its submission is the "
                    "evidence of what was reviewed — create a new revision.")
                    % responded[0].revision_code)
        return super().write(vals)


class ConstructionSubmittalReview(models.Model):
    """One reviewer's step on one revision."""
    _name = 'realestate.construction.submittal.review'
    _description = 'Submittal Review Step'
    _order = 'revision_id, sequence, id'

    revision_id = fields.Many2one(
        'realestate.construction.submittal.revision', required=True,
        ondelete='cascade', index=True)
    submittal_id = fields.Many2one(
        related='revision_id.submittal_id', store=True, index=True,
        readonly=True)
    sequence = fields.Integer(default=10)
    reviewer_id = fields.Many2one('res.users', string='Reviewer')
    reviewer_partner_id = fields.Many2one(
        'res.partner', string='Reviewing Organisation')
    role = fields.Char(help="Consultant, structural engineer, client…")
    due_date = fields.Date()
    responded_date = fields.Date(readonly=True)
    response = fields.Selection(RESPONSE_CODES)
    comments = fields.Text()
    state = fields.Selection([
        ('pending', 'Pending'),
        ('responded', 'Responded'),
        ('skipped', 'Skipped'),
        ('cancelled', 'Cancelled'),
    ], default='pending', required=True)
    is_overdue = fields.Boolean(compute='_compute_overdue')

    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_overdue = bool(
                rec.due_date and rec.state == 'pending'
                and rec.due_date < today)

    def action_respond(self, response=None, comments=None):
        for rec in self:
            rec.write({
                'response': response or rec.response,
                'comments': comments or rec.comments,
                'responded_date': fields.Date.context_today(rec),
                'state': 'responded',
            })
        return True


class ConstructionSubmittalPackage(models.Model):
    """A grouping of submittals reviewed together. Optional by design."""
    _name = 'realestate.construction.submittal.package'
    _description = 'Submittal Package'
    _order = 'project_id, name'
    _check_company_auto = True

    name = fields.Char(required=True)
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
        index=True)
    contractor_id = fields.Many2one('realestate.contractor')
    submittal_ids = fields.One2many(
        'realestate.construction.submittal', 'submittal_package_id',
        string='Submittals')
    submittal_count = fields.Integer(compute='_compute_stats')
    responded_count = fields.Integer(compute='_compute_stats')
    required_submission_date = fields.Date()
    notes = fields.Text()

    @api.depends('submittal_ids.state')
    def _compute_stats(self):
        for rec in self:
            rec.submittal_count = len(rec.submittal_ids)
            rec.responded_count = len(rec.submittal_ids.filtered(
                lambda s: s.state in ('responded', 'closed')))
