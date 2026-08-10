# -*- coding: utf-8 -*-
"""M5A — the Request for Information.

An RFI asks a question and records the official answer. What it must never do
is move money: a site engineer writing "potential cost impact 2M" is describing
a worry, not authorising anything.

```
    RFI  →  official response  →  potential impact  →  M4 CHANGE EVENT
                                                          → change order
                                                          → approval
                                                          → implementation
```

Only that last step touches a baseline, and it lives in M4 where the authority
rules are.

### Ball in court

The question a project manager actually asks is not "what status is it?" but
"who has it?". `ball_in_court` answers that directly rather than making people
infer responsibility from a status label.

### One official response

There are many comments on an RFI and exactly one contractual answer. The
answer is a field with an author and a date, not the last chatter message —
and revising it preserves what was previously issued.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

RFI_STATES = [
    ('draft', 'Draft'),
    ('open', 'Open'),
    ('under_review', 'Under Review'),
    ('answered', 'Answered'),
    ('closed', 'Closed'),
    ('reopened', 'Reopened'),
    ('void', 'Void'),
]

BALL_IN_COURT = [
    ('originator', 'Originator'),
    ('contractor', 'Contractor'),
    ('consultant', 'Consultant / Designer'),
    ('project_manager', 'Project Manager'),
    ('client', 'Client'),
    ('none', 'Nobody — Closed'),
]

IMPACT_CLASSIFICATION = [
    ('unknown', 'Unknown'),
    ('none', 'None'),
    ('potential', 'Potential'),
    ('confirmed', 'Confirmed via Change Management'),
]


class ConstructionRFI(models.Model):
    _name = 'realestate.construction.rfi'
    _description = 'Request for Information'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, required_response_date, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='RFI Number', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    subject = fields.Char(required=True, tracking=True)
    question = fields.Html(required=True)

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
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', ondelete='set null',
        check_company=True)
    location = fields.Char()
    discipline = fields.Selection(
        [('architectural', 'Architectural'), ('structural', 'Structural'),
         ('civil', 'Civil'), ('mechanical', 'Mechanical'),
         ('electrical', 'Electrical'), ('plumbing', 'Plumbing'),
         ('hvac', 'HVAC'), ('landscape', 'Landscape'),
         ('interior', 'Interior'), ('other', 'Other')],
        default='other', index=True)
    specification_reference = fields.Char()

    raised_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, tracking=True)
    originating_partner_id = fields.Many2one(
        'res.partner', string='Originating Organisation',
        help="Who asked. Not every originator is an Odoo user — a contractor "
             "raising an RFI is a partner.")
    contractor_id = fields.Many2one('realestate.contractor')
    assigned_to_id = fields.Many2one(
        'res.users', string='Assigned To', tracking=True)
    reviewer_partner_id = fields.Many2one(
        'res.partner', string='Consultant / Reviewer')

    created_date = fields.Date(
        default=fields.Date.context_today, required=True, tracking=True)
    submitted_date = fields.Date(tracking=True)
    required_response_date = fields.Date(
        tracking=True, index=True,
        help="The contractual date. Activities may remind; this is the date "
             "that counts.")
    actual_response_date = fields.Date(readonly=True, copy=False,
                                       tracking=True)
    closed_date = fields.Date(readonly=True, copy=False)

    priority = fields.Selection(
        [('0', 'Low'), ('1', 'Normal'), ('2', 'High'), ('3', 'Urgent')],
        default='1')
    state = fields.Selection(
        RFI_STATES, default='draft', required=True, tracking=True,
        copy=False, index=True)
    ball_in_court = fields.Selection(
        BALL_IN_COURT, compute='_compute_ball_in_court', store=True,
        help="Who needs to act now. The question people actually ask.")

    # ----- the official answer, and only one of it -----
    official_response = fields.Html(tracking=True)
    response_author_id = fields.Many2one('res.users', tracking=True)
    response_approved_by_id = fields.Many2one('res.users')
    response_history_ids = fields.One2many(
        'realestate.construction.rfi.response', 'rfi_id',
        string='Response History', readonly=True)

    # ----- impact, classified rather than typed -----
    cost_impact = fields.Selection(
        IMPACT_CLASSIFICATION, default='unknown', required=True, tracking=True)
    schedule_impact = fields.Selection(
        IMPACT_CLASSIFICATION, default='unknown', required=True, tracking=True)
    estimated_cost_impact = fields.Monetary(
        help="An indication only. Nothing in the control equations reads it; "
             "creating a Change Event is what starts the commercial process.")
    estimated_schedule_days = fields.Integer()
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)

    change_event_id = fields.Many2one(
        'realestate.construction.change.event', readonly=True, copy=False,
        help="Created deliberately, never automatically.")

    document_revision_ids = fields.Many2many(
        'realestate.construction.document.revision',
        'construction_rfi_document_revision_rel', 'rfi_id', 'revision_id',
        string='Referenced Revisions',
        help="The exact revisions the question is about. A question asked "
             "about Rev B stays about Rev B when Rev C arrives.")
    attachment_ids = fields.Many2many(
        'ir.attachment', 'construction_rfi_attachment_rel', 'rfi_id',
        'attachment_id', string='Attachments')

    days_remaining = fields.Integer(compute='_compute_due')
    overdue_days = fields.Integer(compute='_compute_due', store=True)
    is_overdue = fields.Boolean(compute='_compute_due', store=True, index=True)
    response_time_days = fields.Integer(
        compute='_compute_response_time', store=True,
        help="Submission to official response, using the real dates. Never "
             "measured against today once an answer exists.")
    closure_reason = fields.Char()

    @api.depends('state', 'assigned_to_id')
    def _compute_ball_in_court(self):
        for rec in self:
            if rec.state in ('closed', 'void'):
                rec.ball_in_court = 'none'
            elif rec.state == 'draft':
                rec.ball_in_court = 'originator'
            elif rec.state in ('open', 'reopened'):
                rec.ball_in_court = (
                    'consultant' if rec.reviewer_partner_id
                    else 'project_manager')
            elif rec.state == 'under_review':
                rec.ball_in_court = 'consultant'
            else:
                rec.ball_in_court = 'originator'

    @api.depends('required_response_date', 'actual_response_date', 'state')
    def _compute_due(self):
        today = fields.Date.context_today(self)
        for rec in self:
            due = rec.required_response_date
            if not due or rec.state in ('closed', 'void'):
                rec.days_remaining = 0
                rec.overdue_days = 0
                rec.is_overdue = False
                continue
            reference = rec.actual_response_date or today
            rec.days_remaining = (due - today).days
            rec.overdue_days = max((reference - due).days, 0)
            rec.is_overdue = bool(rec.overdue_days) and not \
                rec.actual_response_date

    @api.depends('submitted_date', 'actual_response_date')
    def _compute_response_time(self):
        for rec in self:
            if rec.submitted_date and rec.actual_response_date:
                rec.response_time_days = (
                    rec.actual_response_date - rec.submitted_date).days
            else:
                rec.response_time_days = 0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.rfi') or 'RFI/NEW'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    def action_open(self):
        for rec in self:
            if rec.state not in ('draft',):
                raise UserError(_("Only a draft RFI can be opened."))
            rec.write({'state': 'open',
                       'submitted_date': rec.submitted_date or
                       fields.Date.context_today(rec)})
        return True

    def action_review(self):
        for rec in self:
            if rec.state not in ('open', 'reopened'):
                raise UserError(_("Only an open RFI can go under review."))
            rec.state = 'under_review'
        return True

    def action_answer(self):
        """Record the official response, and keep the one it replaces."""
        for rec in self:
            if rec.state not in ('open', 'under_review', 'reopened'):
                raise UserError(_(
                    "%s is not awaiting an answer.") % rec.name)
            if not rec.official_response:
                raise UserError(_(
                    "An RFI is answered by an official response, not by a "
                    "state change."))
            rec._archive_response()
            rec.write({
                'state': 'answered',
                'actual_response_date': (rec.actual_response_date or
                                         fields.Date.context_today(rec)),
                'response_author_id': (rec.response_author_id.id
                                       or self.env.user.id),
            })
        return True

    def action_close(self):
        for rec in self:
            if rec.state == 'closed':
                continue
            if rec.state != 'answered' and not rec.closure_reason:
                raise UserError(_(
                    "%s has no official response. Closing it anyway needs a "
                    "stated reason — an unanswered question that quietly "
                    "disappears is how disputes start.") % rec.name)
            rec.write({'state': 'closed',
                       'closed_date': fields.Date.context_today(rec)})
        return True

    def action_reopen(self):
        for rec in self:
            if rec.state not in ('answered', 'closed'):
                raise UserError(_("Only an answered or closed RFI reopens."))
            rec._archive_response()
            rec.write({'state': 'reopened', 'closed_date': False})
        return True

    def action_void(self):
        for rec in self:
            if rec.state in ('answered', 'closed'):
                raise UserError(_(
                    "An answered RFI is part of the record and cannot be "
                    "voided."))
            rec.state = 'void'
        return True

    def _archive_response(self):
        """Never erase an officially issued response."""
        self.ensure_one()
        if not self.official_response:
            return
        existing = self.response_history_ids.filtered(
            lambda r: r.response == self.official_response)
        if existing:
            return
        self.env['realestate.construction.rfi.response'].create({
            'rfi_id': self.id,
            'response': self.official_response,
            'author_id': (self.response_author_id.id or self.env.user.id),
            'response_date': (self.actual_response_date or
                              fields.Date.context_today(self)),
        })

    # ------------------------------------------------------------------
    def action_create_change_event(self):
        """Hand the commercial question to M4, which owns it from here.

        Deliberately an action somebody takes. An RFI that created change
        events by itself would turn every "might this cost more?" into a
        commercial record nobody decided to raise.
        """
        self.ensure_one()
        if self.change_event_id:
            raise UserError(_(
                "%(rfi)s already has change event %(event)s.",
                rfi=self.name, event=self.change_event_id.name))
        event = self.env['realestate.construction.change.event'].create({
            'title': _("RFI %(number)s — %(subject)s",
                       number=self.name, subject=self.subject),
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'package_id': self.package_id.id or False,
            'contractor_id': self.contractor_id.id or False,
            'wbs_id': self.wbs_id.id or False,
            'cost_code_id': self.cost_code_id.id or False,
            'source': 'rfi',
            'source_reference': self.name,
            'source_model': self._name,
            'source_id': self.id,
            'estimated_cost_impact': self.estimated_cost_impact,
            'estimated_schedule_days': self.estimated_schedule_days,
            'description': self.official_response or self.question,
        })
        self.change_event_id = event
        if self.cost_impact == 'potential':
            self.cost_impact = 'confirmed'
        return event

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "RFI %(name)s is in %(company)s but its project is in "
                    "%(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))


class ConstructionRFIResponse(models.Model):
    """A response that was once official. Kept when a newer one replaces it."""
    _name = 'realestate.construction.rfi.response'
    _description = 'RFI Response History'
    _order = 'rfi_id, response_date desc, id desc'

    rfi_id = fields.Many2one(
        'realestate.construction.rfi', required=True, ondelete='cascade',
        index=True)
    response = fields.Html(required=True, readonly=True)
    author_id = fields.Many2one('res.users', required=True, readonly=True)
    response_date = fields.Date(required=True, readonly=True)
    revision_reason = fields.Char()

    def write(self, vals):
        if set(vals) - {'revision_reason'}:
            raise UserError(_(
                "An issued response is part of the record. Record a new one "
                "instead."))
        return super().write(vals)

    def unlink(self):
        raise UserError(_("Issued responses cannot be deleted."))
