# -*- coding: utf-8 -*-
"""Notices — the letter, its deadline, and whether it was late.

Most construction contracts require notice of a claim event within a period.
The period differs by contract; the consequence of missing it differs by
contract and by governing law, and in several jurisdictions a time-bar that
would otherwise apply is unenforceable.

So this module does exactly two things: it computes the deadline **the
contract configured**, and it says whether the notice was issued after it.
It never concludes that entitlement was lost. That is a determination, and a
person makes determinations.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

NOTICE_TYPES = [
    ('claim_event', 'Notice of Claim Event'),
    ('eot', 'Notice of Delay / EOT'),
    ('detailed_particulars', 'Detailed Particulars'),
    ('intention', 'Notice of Intention'),
    ('dissatisfaction', 'Notice of Dissatisfaction'),
    ('employer_claim', 'Employer Claim Notice'),
    ('other', 'Other'),
]

NOTICE_METHODS = [
    ('transmittal', 'Controlled Transmittal'),
    ('letter', 'Letter'),
    ('email', 'Email'),
    ('portal', 'Project Portal'),
    ('hand', 'Hand Delivery'),
    ('other', 'Other'),
]


class ConstructionNotice(models.Model):
    _name = 'realestate.construction.notice'
    _description = 'Contractual Notice'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, deadline_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(tracking=True)
    notice_type = fields.Selection(
        NOTICE_TYPES, required=True, default='claim_event', tracking=True,
        index=True)

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True, index=True,
        domain="[('project_id', '=', project_id)]")
    delay_event_id = fields.Many2one(
        'realestate.construction.delay.event', ondelete='set null',
        index=True, check_company=True)
    claim_id = fields.Many2one(
        'realestate.construction.claim', ondelete='set null', index=True,
        check_company=True)

    clause_reference = fields.Char(
        string='Contract Clause',
        help="The clause this notice is given under, as written in this "
             "contract.")

    trigger_date = fields.Date(
        string='Triggering Event', tracking=True,
        help="When the event that requires notice occurred.")
    awareness_date = fields.Date(
        tracking=True,
        help="When the party became aware. Most contracts run the period "
             "from awareness rather than from the event.")
    deadline_date = fields.Date(
        compute='_compute_deadline', store=True, tracking=True,
        help="Computed from the period configured on THIS contract. Empty "
             "when the contract records no period — which is not the same as "
             "a deadline of today.")
    has_deadline = fields.Boolean(compute='_compute_deadline', store=True)
    notice_date = fields.Date(
        string='Notice Issued', tracking=True, index=True)

    days_taken = fields.Integer(compute='_compute_status', store=True)
    days_remaining = fields.Integer(compute='_compute_status')
    is_late = fields.Boolean(compute='_compute_status', store=True, index=True)
    status = fields.Selection([
        ('not_required', 'Not Required'),
        ('pending', 'Pending'),
        ('due_soon', 'Due Soon'),
        ('issued', 'Issued'),
        ('late', 'Issued Late'),
        ('overdue', 'Overdue — Not Issued'),
        ('acknowledged', 'Acknowledged'),
        ('disputed', 'Disputed'),
    ], compute='_compute_status', store=True, index=True)
    deadline_warning = fields.Char(compute='_compute_status')

    issued_by_id = fields.Many2one('res.users', tracking=True)
    issued_to_partner_id = fields.Many2one('res.partner', tracking=True)
    method = fields.Selection(NOTICE_METHODS, default='transmittal',
                              tracking=True)
    transmittal_id = fields.Many2one(
        'realestate.construction.transmittal', ondelete='set null',
        help="The controlled transmittal that carried it. M5 already proves "
             "what was sent and when; nothing is re-implemented here.")
    document_revision_id = fields.Many2one(
        'realestate.construction.document.revision', ondelete='set null',
        string='Notice Document')

    acknowledged = fields.Boolean(tracking=True)
    acknowledged_on = fields.Date(tracking=True)
    acknowledgement_reference = fields.Char()
    disputed = fields.Boolean(tracking=True)
    dispute_reason = fields.Text()

    comments = fields.Text()

    # ------------------------------------------------------------------
    def _period_days(self):
        """The notice period **this contract** configured, or None."""
        self.ensure_one()
        package = self.package_id
        if not package or not package.notice_required:
            return None
        if self.notice_type == 'detailed_particulars':
            return package.detailed_claim_period_days or None
        return package.notice_period_days or None

    @api.depends('package_id', 'package_id.notice_required',
                 'package_id.notice_period_days',
                 'package_id.detailed_claim_period_days',
                 'package_id.notice_day_basis',
                 'awareness_date', 'trigger_date', 'notice_type')
    def _compute_deadline(self):
        for rec in self:
            period = rec._period_days()
            start = rec.awareness_date or rec.trigger_date
            if not period or not start:
                # No configured period, or nothing to count from. An empty
                # deadline means "unknown", and every consumer must read
                # `has_deadline` rather than treating False as "due now".
                rec.deadline_date = False
                rec.has_deadline = False
                continue
            basis = rec.package_id.notice_day_basis or 'calendar'
            rec.deadline_date = (
                rec._add_business_days(start, period) if basis == 'business'
                else start + relativedelta(days=period))
            rec.has_deadline = True

    @staticmethod
    def _add_business_days(start, days):
        """Monday–Friday. Public holidays are a calendar this module does not
        own; where they matter, contracts use calendar days or a resource
        calendar is configured elsewhere."""
        current, added = start, 0
        while added < days:
            current += relativedelta(days=1)
            if current.weekday() < 5:
                added += 1
        return current

    @api.depends('deadline_date', 'has_deadline', 'notice_date',
                 'awareness_date', 'acknowledged', 'disputed',
                 'package_id.notice_required',
                 'package_id.notice_reminder_days')
    def _compute_status(self):
        today = fields.Date.context_today(self)
        for rec in self:
            start = rec.awareness_date or rec.trigger_date
            rec.days_taken = (
                (rec.notice_date - start).days
                if rec.notice_date and start else 0)
            rec.days_remaining = (
                (rec.deadline_date - today).days
                if rec.has_deadline and not rec.notice_date else 0)
            rec.is_late = bool(
                rec.has_deadline and rec.notice_date
                and rec.notice_date > rec.deadline_date)

            if rec.package_id and not rec.package_id.notice_required:
                rec.status = 'not_required'
            elif rec.disputed:
                rec.status = 'disputed'
            elif rec.notice_date and rec.is_late:
                rec.status = 'late'
            elif rec.notice_date and rec.acknowledged:
                rec.status = 'acknowledged'
            elif rec.notice_date:
                rec.status = 'issued'
            elif rec.has_deadline and rec.deadline_date < today:
                rec.status = 'overdue'
            elif rec.has_deadline and rec.days_remaining <= (
                    rec.package_id.notice_reminder_days or 0):
                rec.status = 'due_soon'
            else:
                rec.status = 'pending'

            # The warning states a fact about a date. It states nothing about
            # entitlement, because entitlement is not this module's to state.
            if rec.is_late:
                rec.deadline_warning = _(
                    "Notice issued %(days)s day(s) after the deadline "
                    "configured on this contract (%(deadline)s). The "
                    "contractual consequence, if any, is a matter for the "
                    "contract, the governing law and the determination.",
                    days=(rec.notice_date - rec.deadline_date).days,
                    deadline=rec.deadline_date)
            elif rec.status == 'overdue':
                rec.deadline_warning = _(
                    "No notice recorded and the configured deadline "
                    "(%(deadline)s) has passed.", deadline=rec.deadline_date)
            else:
                rec.deadline_warning = False

    @api.constrains('project_id', 'company_id')
    def _check_company_matches_project(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Notice %(name)s is in %(company)s but its project is in "
                    "%(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.notice') or 'NOT/NEW'
        return super().create(vals_list)

    def write(self, vals):
        """An issued notice is evidence of what was sent, and when."""
        protected = {'notice_date', 'trigger_date', 'awareness_date',
                     'issued_to_partner_id', 'transmittal_id',
                     'document_revision_id'}
        if protected & set(vals):
            issued = self.filtered(lambda n: n.notice_date and not
                                   self.env.context.get('re_notice_issuing'))
            if issued:
                raise UserError(_(
                    "%(refs)s have been issued. When a notice actually went "
                    "out is the fact a time-bar argument turns on — issue a "
                    "further notice rather than editing this one.",
                    refs=', '.join(issued.mapped('name'))))
        return super().write(vals)

    def action_issue(self, notice_date=None):
        for rec in self:
            if rec.notice_date:
                raise UserError(_("%s has already been issued.") % rec.name)
            rec.with_context(re_notice_issuing=True).write({
                'notice_date': notice_date or fields.Date.context_today(rec),
                'issued_by_id': self.env.user.id,
            })
            if rec.is_late:
                rec.message_post(body=rec.deadline_warning)
        return True

    def action_acknowledge(self):
        for rec in self:
            if not rec.notice_date:
                raise UserError(_(
                    "A notice that was never issued cannot be acknowledged."))
            rec.write({'acknowledged': True,
                       'acknowledged_on': fields.Date.context_today(rec)})
        return True

    def action_dispute(self, reason=None):
        for rec in self:
            rec.write({'disputed': True,
                       'dispute_reason': reason or rec.dispute_reason})
        return True
