# -*- coding: utf-8 -*-
"""Claims — administration, evidence and audit history.

**ATMTA does not determine entitlement.** It records what was claimed, what was
assessed, what was determined and what was settled, and it keeps every one of
those four figures. Whether a claim succeeds is a question for the contract,
the governing law, the facts and a human with the authority to decide. Nothing
in this file concludes otherwise, and several things in it deliberately refuse
to.

A claim moves no money. When entitlement is accepted, a change order is created
through M4 and the baseline moves there, once.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

CLAIM_SIDES = [
    ('contractor', 'Contractor Claim'),
    ('employer', 'Employer / Owner Claim'),
]

#: Commercial descriptions, not legal categories. The contract decides what
#: any of these entitles anybody to.
CLAIM_TYPES = [
    ('variation', 'Variation'),
    ('extension_of_time', 'Extension of Time'),
    ('prolongation', 'Prolongation'),
    ('disruption', 'Disruption'),
    ('acceleration', 'Acceleration'),
    ('delay_cost', 'Delay Cost'),
    ('unforeseen_condition', 'Unforeseen Condition'),
    ('suspension', 'Suspension'),
    ('differing_site_condition', 'Differing Site Condition'),
    ('payment', 'Payment'),
    ('measurement', 'Measurement'),
    ('loss_and_expense', 'Loss and Expense'),
    ('damage', 'Damage'),
    ('defect_cost', 'Defect Cost'),
    ('other', 'Other'),
]

CLAIM_STATES = [
    ('draft', 'Draft'),
    ('notice', 'Notice Given'),
    ('preparing', 'Preparing'),
    ('submitted', 'Submitted'),
    ('under_review', 'Under Review'),
    ('assessed', 'Assessed'),
    ('determination_pending', 'Determination Pending'),
    ('determined', 'Determined'),
    ('settlement', 'Settlement'),
    ('closed', 'Closed'),
    ('rejected', 'Rejected'),
    ('withdrawn', 'Withdrawn'),
    ('disputed', 'Disputed'),
    ('cancelled', 'Cancelled'),
]

#: States in which a claim is a live request for entitlement.
OPEN_CLAIM_STATES = ('notice', 'preparing', 'submitted', 'under_review',
                     'assessed', 'determination_pending', 'determined',
                     'settlement', 'disputed')

QUANTUM_CATEGORIES = [
    ('labor', 'Direct Labour'),
    ('materials', 'Materials'),
    ('equipment', 'Equipment / Plant'),
    ('subcontract', 'Subcontract'),
    ('site_overhead', 'Site Overhead / Prolongation'),
    ('head_office', 'Head Office Overhead'),
    ('financing', 'Financing'),
    ('escalation', 'Escalation'),
    ('disruption', 'Disruption'),
    ('other', 'Other'),
]

SCHEDULE_METHODS = [
    ('time_impact', 'Time Impact Analysis'),
    ('impacted_as_planned', 'Impacted As-Planned'),
    ('windows', 'Windows Analysis'),
    ('as_planned_vs_as_built', 'As-Planned vs As-Built'),
    ('retrospective', 'Retrospective Longest Path'),
    ('narrative', 'Schedule Narrative'),
    ('manual', 'Manual Assessment'),
    ('external', 'External Scheduler Determination'),
]


class ConstructionClaim(models.Model):
    _name = 'realestate.construction.claim'
    _description = 'Construction Claim'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, submission_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(required=True, tracking=True)
    description = fields.Text()

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True, index=True, tracking=True,
        domain="[('project_id', '=', project_id)]")
    contractor_id = fields.Many2one('realestate.contractor', index=True,
                                    tracking=True)

    side = fields.Selection(
        CLAIM_SIDES, required=True, default='contractor', tracking=True,
        index=True,
        help="Claims do not only come from contractors. An employer claim "
             "runs through the same administration in the other direction.")
    claim_type = fields.Selection(
        CLAIM_TYPES, required=True, default='other', tracking=True,
        index=True)
    claimant_partner_id = fields.Many2one('res.partner', string='Claimant')
    respondent_partner_id = fields.Many2one('res.partner', string='Respondent')
    clause_reference = fields.Char(string='Contract Clause')

    # -- Cause and evidence ------------------------------------------------
    delay_event_ids = fields.Many2many(
        'realestate.construction.delay.event', 'claim_delay_event_rel',
        'claim_id', 'event_id', string='Delay Events')
    change_event_ids = fields.Many2many(
        'realestate.construction.change.event', 'claim_change_event_rel',
        'claim_id', 'change_event_id', string='Related Change Events')
    rfi_ids = fields.Many2many(
        'realestate.construction.rfi', 'claim_rfi_rel', 'claim_id', 'rfi_id',
        string='Related RFIs')
    ncr_ids = fields.Many2many(
        'realestate.construction.ncr', 'claim_ncr_rel', 'claim_id', 'ncr_id',
        string='Related NCRs')
    notice_ids = fields.One2many(
        'realestate.construction.notice', 'claim_id', string='Notices')
    evidence_ids = fields.One2many(
        'realestate.construction.claim.evidence', 'claim_id',
        string='Contemporary Records')
    evidence_count = fields.Integer(compute='_compute_counts')
    has_late_notice = fields.Boolean(compute='_compute_notice', store=True)
    notice_status = fields.Char(compute='_compute_notice', store=True)
    first_notice_date = fields.Date(compute='_compute_notice', store=True)

    # -- Dates -------------------------------------------------------------
    event_date = fields.Date(tracking=True, index=True)
    submission_date = fields.Date(tracking=True, index=True)
    response_due_date = fields.Date(tracking=True)
    determination_date = fields.Date(readonly=True, copy=False, tracking=True)
    closed_date = fields.Date(readonly=True, copy=False)
    age_days = fields.Integer(compute='_compute_age')

    # -- The four money figures, kept apart -------------------------------
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)
    estimated_exposure = fields.Monetary(
        tracking=True,
        help="What this might cost, before anybody claimed anything.")
    claimed_cost = fields.Monetary(
        compute='_compute_from_submissions', store=True, readonly=False,
        tracking=True,
        help="The current submission's figure. Earlier revisions keep theirs.")
    assessed_cost = fields.Monetary(
        tracking=True, groups='real_estate_construction.group_construction_commercial',
        help="The internal assessment. Commercial roles only — an assessment "
             "visible to the other side is a negotiating position given away.")
    determined_cost = fields.Monetary(
        readonly=True, copy=False, tracking=True,
        compute='_compute_from_determinations', store=True)
    settled_cost = fields.Monetary(readonly=True, copy=False, tracking=True)

    cost_claimed_known = fields.Boolean(
        default=False,
        help="Whether a cost figure has been claimed at all. A claim with no "
             "money is not a claim for nothing — M3's rule, applied here.")

    # -- The four time figures, kept apart --------------------------------
    claimed_days = fields.Float(
        compute='_compute_from_submissions', store=True, readonly=False,
        tracking=True)
    assessed_days = fields.Float(
        tracking=True, groups='real_estate_construction.group_construction_commercial')
    determined_days = fields.Float(
        readonly=True, copy=False, tracking=True,
        compute='_compute_from_determinations', store=True)
    settled_days = fields.Float(readonly=True, copy=False, tracking=True)

    # -- Analysis ----------------------------------------------------------
    schedule_method = fields.Selection(
        SCHEDULE_METHODS,
        help="Which delay-analysis method was used. ATMTA stores the method "
             "and the analyst's conclusion; it does not run the analysis.")
    schedule_analyst = fields.Char()
    schedule_reference = fields.Char(string='Programme Revision Analysed')
    schedule_conclusion = fields.Text()
    analysis_document_revision_id = fields.Many2one(
        'realestate.construction.document.revision',
        string='Analysis Document')

    # -- Narrative ---------------------------------------------------------
    contractual_basis = fields.Html(
        groups='real_estate_construction.group_construction_commercial')
    cause_and_effect = fields.Html()
    mitigation_narrative = fields.Html()
    relief_sought = fields.Html()
    internal_position = fields.Html(
        groups='real_estate_construction.group_construction_commercial',
        help="Negotiating position and strategy. Server-side restricted — "
             "hiding it in a view would still expose it through a relation.")

    state = fields.Selection(
        CLAIM_STATES, default='draft', required=True, tracking=True,
        copy=False, index=True)
    reviewer_id = fields.Many2one('res.users', string='Responsible Reviewer',
                                  tracking=True)
    determination_authority_id = fields.Many2one(
        'res.users', string='Determination Authority', tracking=True)
    submitted_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    submission_ids = fields.One2many(
        'realestate.construction.claim.submission', 'claim_id',
        string='Submissions')
    current_submission_id = fields.Many2one(
        'realestate.construction.claim.submission',
        compute='_compute_from_submissions', store=True)
    revision = fields.Integer(compute='_compute_from_submissions', store=True)
    determination_ids = fields.One2many(
        'realestate.construction.claim.determination', 'claim_id',
        string='Determinations')
    cost_line_ids = fields.One2many(
        'realestate.construction.claim.cost.line', 'claim_id',
        string='Quantum')
    eot_ids = fields.One2many('realestate.construction.eot', 'claim_id')
    change_order_id = fields.Many2one(
        'realestate.construction.change.order', readonly=True, copy=False,
        help="The change order created from an accepted determination. M4 "
             "owns what it does to the baseline.")

    # -- Dispute -----------------------------------------------------------
    disputed_cost = fields.Monetary(readonly=True, copy=False)
    disputed_days = fields.Float(readonly=True, copy=False)
    dispute_reason = fields.Text()
    dispute_document_revision_id = fields.Many2one(
        'realestate.construction.document.revision', string='Dispute Notice')
    next_action = fields.Char()

    settlement_date = fields.Date(readonly=True, copy=False)
    settlement_authority_id = fields.Many2one('res.users', readonly=True,
                                              copy=False)
    settlement_document_revision_id = fields.Many2one(
        'realestate.construction.document.revision',
        string='Settlement Agreement')

    notes = fields.Html()

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('submission_ids.revision', 'submission_ids.claimed_cost',
                 'submission_ids.claimed_days', 'submission_ids.state')
    def _compute_from_submissions(self):
        for rec in self:
            issued = rec.submission_ids.filtered(
                lambda s: s.state == 'issued').sorted('revision')
            current = issued[-1:] if issued else rec.submission_ids[:1]
            rec.current_submission_id = current
            rec.revision = current.revision if current else 0
            if current:
                rec.claimed_cost = current.claimed_cost
                rec.claimed_days = current.claimed_days
            else:
                rec.claimed_cost = rec.claimed_cost or 0.0
                rec.claimed_days = rec.claimed_days or 0.0

    @api.depends('determination_ids.state',
                 'determination_ids.determined_cost',
                 'determination_ids.determined_days')
    def _compute_from_determinations(self):
        for rec in self:
            live = rec.determination_ids.filtered(
                lambda d: d.state == 'issued')
            rec.determined_cost = sum(live.mapped('determined_cost'))
            rec.determined_days = sum(live.mapped('determined_days'))

    @api.depends('notice_ids.status', 'notice_ids.is_late',
                 'notice_ids.notice_date')
    def _compute_notice(self):
        for rec in self:
            rec.has_late_notice = any(rec.notice_ids.mapped('is_late'))
            issued = rec.notice_ids.filtered('notice_date').sorted(
                'notice_date')
            rec.first_notice_date = issued[:1].notice_date or False
            if not rec.notice_ids:
                rec.notice_status = 'none'
            elif rec.has_late_notice:
                rec.notice_status = 'late'
            else:
                rec.notice_status = issued[:1].status if issued else \
                    rec.notice_ids[0].status

    @api.depends('evidence_ids')
    def _compute_counts(self):
        for rec in self:
            rec.evidence_count = len(rec.evidence_ids)

    def _compute_age(self):
        today = fields.Date.context_today(self)
        for rec in self:
            start = rec.submission_date or rec.event_date
            rec.age_days = (today - start).days if start else 0

    # ------------------------------------------------------------------
    @api.constrains('project_id', 'company_id')
    def _check_company_matches_project(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Claim %(name)s is in %(company)s but its project is in "
                    "%(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

    @api.constrains('claimed_cost', 'claimed_days', 'state')
    def _check_something_is_claimed(self):
        """A claim for no money and no time is not a claim.

        A claim for money and no days, or days and no money, is entirely
        ordinary — and a claimed cost of zero is different from no claimed
        cost at all, which is why `cost_claimed_known` exists.
        """
        for rec in self:
            if rec.state in ('draft', 'notice', 'preparing', 'cancelled',
                             'withdrawn'):
                continue
            if not rec.cost_claimed_known and not rec.claimed_days:
                raise ValidationError(_(
                    "%s claims neither time nor money. Record what is being "
                    "asked for — a claimed cost of zero is a statement, and "
                    "an empty one is not.") % rec.name)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.claim') or 'CLM/NEW'
            if vals.get('claimed_cost'):
                vals.setdefault('cost_claimed_known', True)
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('claimed_cost'):
            vals.setdefault('cost_claimed_known', True)
        frozen = {'claimed_cost', 'claimed_days', 'side', 'package_id'}
        if frozen & set(vals) and not self.env.context.get('re_claim_stage'):
            locked = self.filtered(
                lambda c: c.state in ('determined', 'settlement', 'closed'))
            if locked:
                raise UserError(_(
                    "%(refs)s have been determined. A determined claim is "
                    "part of the record — issue a further submission or a "
                    "superseding determination.",
                    refs=', '.join(locked.mapped('name'))))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Workflow. None of these steps decides entitlement.
    # ------------------------------------------------------------------
    def action_prepare(self):
        for rec in self:
            if rec.state not in ('draft', 'notice'):
                raise UserError(_("%s is past preparation.") % rec.name)
            rec.state = 'preparing'
        return True

    def action_submit(self):
        """Record that the claim was submitted. Nothing is granted."""
        for rec in self:
            if rec.state in ('closed', 'cancelled', 'withdrawn'):
                raise UserError(_("%s is no longer live.") % rec.name)
            rec.write({
                'state': 'submitted',
                'submission_date': rec.submission_date
                or fields.Date.context_today(rec),
                'submitted_by_id': self.env.user.id,
            })
            if rec.has_late_notice:
                # Said out loud, and only as a fact about a date.
                rec.message_post(body=_(
                    "Submitted with a notice issued after the deadline "
                    "configured on this contract. The consequence, if any, "
                    "is a matter for the contract and the determination."))
        return True

    def action_review(self):
        for rec in self:
            if rec.state not in ('submitted', 'under_review'):
                raise UserError(_("%s is not submitted.") % rec.name)
            rec.state = 'under_review'
        return True

    def action_assess(self):
        """Record the internal assessment. Still not a determination."""
        for rec in self:
            if rec.state not in ('submitted', 'under_review'):
                raise UserError(_(
                    "A claim is assessed after it is submitted."))
            rec._check_assessment_authority()
            rec.state = 'assessed'
        return True

    def _check_assessment_authority(self):
        self.ensure_one()
        if not self.env.user.has_group(
                'real_estate_construction.group_construction_commercial'):
            raise UserError(_(
                "Assessing a claim is a commercial role's work."))
        return True

    def action_request_determination(self):
        for rec in self:
            if rec.state != 'assessed':
                raise UserError(_(
                    "Assess the claim before asking for a determination."))
            rec.state = 'determination_pending'
        return True

    def action_reject(self, reason=None):
        """A person rejects a claim. Nothing here rejects one by itself."""
        for rec in self:
            if rec.state in ('closed', 'cancelled'):
                raise UserError(_("%s is closed.") % rec.name)
            rec.write({'state': 'rejected',
                       'dispute_reason': reason or rec.dispute_reason})
        return True

    def action_withdraw(self):
        for rec in self:
            rec.state = 'withdrawn'
        return True

    def action_dispute(self, cost=None, days=None, reason=None):
        for rec in self:
            if rec.state not in ('determined', 'settlement', 'rejected'):
                raise UserError(_(
                    "There is nothing determined to dispute yet."))
            rec.write({
                'state': 'disputed',
                'disputed_cost': cost if cost is not None else max(
                    0.0, rec.claimed_cost - rec.determined_cost),
                'disputed_days': days if days is not None else max(
                    0.0, rec.claimed_days - rec.determined_days),
                'dispute_reason': reason or rec.dispute_reason,
            })
        return True

    def action_settle(self, cost=None, days=None):
        for rec in self:
            if rec.state not in ('determined', 'disputed', 'settlement'):
                raise UserError(_(
                    "Settle a claim after it has been determined or "
                    "disputed."))
            if not self.env.user.has_group(
                    'real_estate_construction.group_construction_commercial'):
                raise UserError(_(
                    "Settling a claim is a commercial decision."))
            rec.write({
                'state': 'settlement',
                'settled_cost': cost if cost is not None
                else rec.determined_cost,
                'settled_days': days if days is not None
                else rec.determined_days,
                'settlement_date': fields.Date.context_today(rec),
                'settlement_authority_id': self.env.user.id,
            })
        return True

    def action_close(self):
        """Administrative closure, with the loose ends named."""
        for rec in self:
            if rec.state not in ('determined', 'settlement', 'rejected',
                                 'withdrawn', 'disputed'):
                raise UserError(_(
                    "%s has not reached an outcome to close on.") % rec.name)
            if rec.determined_cost and not rec.change_order_id:
                raise UserError(_(
                    "%(name)s determined %(amount)s of cost and no change "
                    "order carries it. Money determined and never authorised "
                    "is the gap every final account argues about.",
                    name=rec.name, amount=rec.determined_cost))
            unimplemented = rec.eot_ids.filtered(
                lambda e: e.state == 'determined')
            if unimplemented:
                raise UserError(_(
                    "%(refs)s are determined and not implemented. The "
                    "contract completion date does not yet reflect them.",
                    refs=', '.join(unimplemented.mapped('name'))))
            rec.write({'state': 'closed',
                       'closed_date': fields.Date.context_today(rec)})
        return True

    # ------------------------------------------------------------------
    chronology_html = fields.Html(
        compute='_compute_chronology', sanitize=False,
        help="What happened, in order, from the records themselves. Nothing "
             "here is authored — every line is a record that already exists.")

    def _chronology_rows(self):
        """`[(date, reference, description)]` built from linked records."""
        self.ensure_one()
        rows = []

        def add(date, reference, description):
            if date:
                rows.append((fields.Date.to_date(date), reference,
                             description or ''))

        if self.event_date:
            add(self.event_date, self.name, _('Event'))
        for event in self.delay_event_ids:
            add(event.start_date, event.name, _('Delay event starts'))
            if event.end_date:
                add(event.end_date, event.name, _('Delay event ends'))
            for record in event.daily_delay_ids:
                add(record.report_id.report_date, record.report_id.name,
                    _('Daily report records the delay'))
        for notice in self.notice_ids:
            add(notice.notice_date, notice.name, _('Notice issued'))
            if notice.acknowledged_on:
                add(notice.acknowledged_on, notice.name,
                    _('Notice acknowledged'))
        for rfi in self.rfi_ids:
            add(rfi.create_date, rfi.name, _('RFI raised'))
        for ncr in self.ncr_ids:
            add(ncr.discovery_date, ncr.name, _('NCR raised'))
        for event in self.change_event_ids:
            add(event.create_date, event.name, _('Change event opened'))
        for evidence in self.evidence_ids:
            add(evidence.record_date, evidence.reference or '',
                _('Evidence cited'))
        for submission in self.submission_ids:
            add(submission.submission_date, self.name,
                _('Claim submission revision %s') % submission.revision)
        for determination in self.determination_ids:
            add(determination.determination_date, determination.name,
                _('Determination'))
        for eot in self.eot_ids:
            add(eot.determination_date, eot.name, _('EOT determined'))
            add(eot.effective_date if eot.state == 'implemented' else False,
                eot.name, _('EOT implemented'))
        return sorted(rows, key=lambda row: row[0])

    @api.depends('delay_event_ids', 'notice_ids.notice_date',
                 'submission_ids.submission_date', 'evidence_ids',
                 'determination_ids.determination_date', 'eot_ids.state')
    def _compute_chronology(self):
        for rec in self:
            rows = rec._chronology_rows()
            if not rows:
                rec.chronology_html = '<p class="text-muted">%s</p>' % _(
                    'Nothing linked yet. The chronology is built from the '
                    'records, not typed.')
                continue
            items = ''.join(
                '<tr><td class="text-nowrap">%s</td><td>%s</td>'
                '<td>%s</td></tr>' % (row[0], row[1], row[2])
                for row in rows)
            rec.chronology_html = (
                '<table class="table table-sm o_claim_chronology">'
                '<thead><tr><th>%s</th><th>%s</th><th>%s</th></tr></thead>'
                '<tbody>%s</tbody></table>' % (
                    _('Date'), _('Record'), _('Event'), items))

    # ------------------------------------------------------------------
    def action_create_change_order(self):
        """Hand accepted money to M4. It arrives as a draft, never approved.

        A claim determination is an entitlement decision. Moving the budget is
        an authorisation decision, and they are made by different people at
        different moments — so this creates the document and stops.
        """
        self.ensure_one()
        if self.change_order_id:
            raise UserError(_(
                "%s already has a change order.") % self.name)
        if not self.determined_cost:
            raise UserError(_(
                "Nothing has been determined to authorise."))
        cost_code = self.cost_line_ids[:1].cost_code_id
        if not cost_code:
            raise UserError(_(
                "Give the quantum at least one cost code, so the change "
                "order lands somewhere the cost report can see."))
        order = self.env['realestate.construction.change.order'].create({
            'title': _("Claim %(ref)s — %(title)s",
                       ref=self.name, title=self.title),
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'package_id': self.package_id.id or False,
            'contractor_id': self.contractor_id.id or False,
            'order_type': ('owner_variation' if self.side == 'employer'
                           else 'contractor_variation'),
            'reason': _("<p>Determined on claim %(ref)s.</p>", ref=self.name),
            'line_ids': [(0, 0, {
                'cost_code_id': (line.cost_code_id or cost_code).id,
                'wbs_id': line.wbs_id.id or False,
                'impact_side': 'commitment',
                'estimated_amount': line.determined_amount or line.amount,
                'submitted_amount': line.claimed_amount or line.amount,
                'negotiated_amount': line.assessed_amount or line.amount,
            }) for line in self.cost_line_ids] or [(0, 0, {
                'cost_code_id': cost_code.id,
                'impact_side': 'commitment',
                'estimated_amount': self.determined_cost,
                'submitted_amount': self.claimed_cost,
                'negotiated_amount': self.assessed_cost,
            })],
        })
        self.change_order_id = order
        return order

    def action_create_eot(self):
        self.ensure_one()
        if not self.package_id:
            raise UserError(_(
                "An extension of time extends a contract. Link the package."))
        return self.env['realestate.construction.eot'].create({
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'package_id': self.package_id.id,
            'claim_id': self.id,
            'claimed_days': self.claimed_days,
            'delay_event_ids': [(6, 0, self.delay_event_ids.ids)],
        })


class ConstructionClaimSubmission(models.Model):
    """One revision of a claim submission. Earlier revisions are not edited."""
    _name = 'realestate.construction.claim.submission'
    _description = 'Claim Submission'
    _order = 'claim_id, revision desc, id desc'
    _check_company_auto = True

    claim_id = fields.Many2one(
        'realestate.construction.claim', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='claim_id.company_id', store=True, readonly=True, index=True)
    currency_id = fields.Many2one(
        related='claim_id.currency_id', readonly=True)
    revision = fields.Integer(required=True, default=0, readonly=True)
    submission_date = fields.Date(
        required=True, default=fields.Date.context_today)
    claimed_cost = fields.Monetary()
    claimed_days = fields.Float()
    narrative = fields.Html()
    document_revision_id = fields.Many2one(
        'realestate.construction.document.revision',
        string='Submission Document')
    submitted_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, readonly=True)
    is_final = fields.Boolean(
        string='Final Particulars',
        help="Marks the fully detailed submission, where the contract "
             "distinguishes one.")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('issued', 'Issued'),
        ('superseded', 'Superseded'),
    ], default='draft', required=True, index=True, copy=False)

    _sql_constraints = [
        ('revision_uniq_per_claim', 'unique(claim_id, revision)',
         'That revision number already exists on this claim.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'revision' not in vals and vals.get('claim_id'):
                existing = self.search([('claim_id', '=', vals['claim_id'])],
                                       order='revision desc', limit=1)
                vals['revision'] = (existing.revision + 1) if existing else 0
        return super().create(vals_list)

    def write(self, vals):
        """Rev 0 says what Rev 0 said. That is the whole point of revisions."""
        protected = {'claimed_cost', 'claimed_days', 'revision',
                     'submission_date', 'narrative', 'document_revision_id'}
        if protected & set(vals) and not self.env.context.get(
                're_submission_issuing'):
            issued = self.filtered(lambda s: s.state != 'draft')
            if issued:
                raise UserError(_(
                    "Revision %(revs)s has been issued. Raise the next "
                    "revision rather than rewriting the one already sent.",
                    revs=', '.join(str(r.revision) for r in issued)))
        return super().write(vals)

    def action_issue(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Revision %s is already issued.")
                                % rec.revision)
            rec.claim_id.submission_ids.filtered(
                lambda s: s.state == 'issued' and s.id != rec.id
            ).with_context(re_submission_issuing=True).write(
                {'state': 'superseded'})
            rec.with_context(re_submission_issuing=True).state = 'issued'
        return True


class ConstructionClaimCostLine(models.Model):
    """Quantum, line by line, with each stage kept apart."""
    _name = 'realestate.construction.claim.cost.line'
    _description = 'Claim Quantum Line'
    _order = 'claim_id, sequence, id'
    _check_company_auto = True

    claim_id = fields.Many2one(
        'realestate.construction.claim', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='claim_id.company_id', store=True, readonly=True, index=True)
    currency_id = fields.Many2one(
        related='claim_id.currency_id', readonly=True)
    sequence = fields.Integer(default=10)
    category = fields.Selection(QUANTUM_CATEGORIES, required=True,
                                default='other')
    description = fields.Char(required=True)
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null')
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', ondelete='set null', index=True)
    period_start = fields.Date()
    period_end = fields.Date()
    quantity = fields.Float(default=1.0)
    uom_id = fields.Many2one('uom.uom', string='UoM')
    rate = fields.Monetary()
    amount = fields.Monetary(
        compute='_compute_amount', store=True, readonly=False,
        help="Tax-exclusive, like every control figure in this module.")
    claimed_amount = fields.Monetary()
    assessed_amount = fields.Monetary(
        groups='real_estate_construction.group_construction_commercial')
    determined_amount = fields.Monetary(readonly=True, copy=False)

    basis = fields.Text(
        help="How the figure was built. A quantum line nobody can follow is "
             "a quantum line nobody will accept.")
    source_model = fields.Char(readonly=True)
    source_id = fields.Integer(readonly=True)
    source_reference = fields.Char()
    uses_actual_cost = fields.Boolean(
        help="Set when the figure was taken from posted ledger cost. Actual "
             "cost is evidence of spend, not proof of entitlement — somebody "
             "still has to decide it is recoverable.")

    @api.depends('quantity', 'rate')
    def _compute_amount(self):
        for line in self:
            if line.quantity and line.rate:
                line.amount = line.quantity * line.rate
            else:
                line.amount = line.amount or 0.0

    @api.constrains('amount', 'claimed_amount')
    def _check_amounts(self):
        for line in self:
            if line.amount < 0 or line.claimed_amount < 0:
                raise ValidationError(_(
                    "A negative quantum line is a credit, and belongs on the "
                    "other side of the claim."))


class ConstructionClaimEvidence(models.Model):
    """A pointer to a contemporary record. Never a copy of one."""
    _name = 'realestate.construction.claim.evidence'
    _description = 'Claim Evidence'
    _order = 'claim_id, record_date, id'
    _check_company_auto = True

    claim_id = fields.Many2one(
        'realestate.construction.claim', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='claim_id.company_id', store=True, readonly=True, index=True)
    category = fields.Selection([
        ('daily_report', 'Daily Site Report'),
        ('labor', 'Labour Record'),
        ('equipment', 'Equipment Record'),
        ('weather', 'Weather Record'),
        ('rfi', 'RFI'),
        ('submittal', 'Submittal'),
        ('document', 'Document Revision'),
        ('transmittal', 'Transmittal'),
        ('inspection', 'Inspection'),
        ('ncr', 'NCR'),
        ('change_event', 'Change Event'),
        ('certificate', 'Payment Certificate'),
        ('cost', 'Cost / Accounting Record'),
        ('delay_event', 'Delay Event'),
        ('notice', 'Notice'),
        ('other', 'Other'),
    ], required=True, default='other', index=True)
    record_ref = fields.Reference(selection=[
        ('realestate.construction.daily.report', 'Daily Site Report'),
        ('realestate.construction.labor.log', 'Labour Log'),
        ('realestate.construction.daily.equipment', 'Equipment Record'),
        ('realestate.construction.rfi', 'RFI'),
        ('realestate.construction.submittal', 'Submittal'),
        ('realestate.construction.document.revision', 'Document Revision'),
        ('realestate.construction.transmittal', 'Transmittal'),
        ('realestate.construction.inspection', 'Inspection'),
        ('realestate.construction.ncr', 'NCR'),
        ('realestate.construction.change.event', 'Change Event'),
        ('realestate.construction.payment.certificate', 'Payment Certificate'),
        ('realestate.construction.delay.event', 'Delay Event'),
        ('realestate.construction.notice', 'Notice'),
        ('account.move', 'Journal Entry'),
        ('account.analytic.line', 'Analytic Line'),
    ], string='Record')
    #: What the record was when it was cited. M5 proved why this matters:
    #: citing "the drawing" and letting it mean Rev D three months later is
    #: how an evidence bundle stops matching the argument built on it.
    reference = fields.Char(
        readonly=True,
        help="The record's reference as it stood when it was cited.")
    revision_label = fields.Char(readonly=True)
    record_date = fields.Date(index=True)
    description = fields.Char()
    relevance = fields.Text(help="Why this record matters to this claim.")
    cited_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, readonly=True)
    cited_on = fields.Datetime(
        default=fields.Datetime.now, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        """Stamp what was cited, at the moment of citing."""
        records = super().create(vals_list)
        for rec in records:
            target = rec.record_ref
            if not target:
                continue
            values = {'reference': target.display_name}
            if 'revision' in target._fields:
                values['revision_label'] = str(target.revision)
            if not rec.record_date:
                for field in ('report_date', 'date', 'create_date'):
                    if field in target._fields and target[field]:
                        values['record_date'] = fields.Date.to_date(
                            target[field])
                        break
            rec.with_context(re_evidence_stamp=True).write(values)
        return records

    def write(self, vals):
        if self.env.context.get('re_evidence_stamp'):
            return super().write(vals)
        if {'record_ref', 'reference', 'revision_label'} & set(vals):
            raise UserError(_(
                "What a claim cited, and which revision it cited, is part of "
                "the claim. Add a further evidence line instead."))
        return super().write(vals)


class ConstructionClaimDetermination(models.Model):
    """A determination by whoever the contract says decides."""
    _name = 'realestate.construction.claim.determination'
    _description = 'Claim Determination'
    _inherit = ['mail.thread']
    _order = 'claim_id, determination_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'))
    claim_id = fields.Many2one(
        'realestate.construction.claim', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='claim_id.company_id', store=True, readonly=True, index=True)
    currency_id = fields.Many2one(
        related='claim_id.currency_id', readonly=True)
    authority_id = fields.Many2one(
        'res.users', required=True, tracking=True,
        default=lambda self: self.env.user)
    authority_role = fields.Char(
        help="Engineer, Employer's Representative, Project Manager — as the "
             "contract names the role.")
    determination_date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True)

    determined_cost = fields.Monetary(tracking=True)
    determined_days = fields.Float(tracking=True)
    reasons = fields.Html(
        help="The reasoning. Written by the person determining, not "
             "generated.")
    conditions = fields.Text()
    document_revision_id = fields.Many2one(
        'realestate.construction.document.revision',
        string='Determination Document')
    supersedes_id = fields.Many2one(
        'realestate.construction.claim.determination', readonly=True,
        copy=False)
    superseded_by_id = fields.Many2one(
        'realestate.construction.claim.determination', readonly=True,
        copy=False)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('issued', 'Issued'),
        ('superseded', 'Superseded'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.claim.determination') or 'DET/NEW'
        return super().create(vals_list)

    def write(self, vals):
        protected = {'determined_cost', 'determined_days', 'reasons',
                     'authority_id', 'determination_date'}
        if protected & set(vals) and not self.env.context.get(
                're_determination'):
            issued = self.filtered(lambda d: d.state == 'issued')
            if issued:
                raise UserError(_(
                    "%(refs)s have been issued. A determination is corrected "
                    "by a superseding determination, which is how the record "
                    "shows that it changed.",
                    refs=', '.join(issued.mapped('name'))))
        return super().write(vals)

    def action_issue(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("%s is already issued.") % rec.name)
            claim = rec.claim_id
            if not rec.reasons:
                raise UserError(_(
                    "A determination without reasons is an instruction. "
                    "Record why."))
            if claim.submitted_by_id == self.env.user and not \
                    self._self_determination_allowed():
                raise UserError(_(
                    "%(user)s submitted this claim. Determining one's own "
                    "claim needs a separate authority, or the configuration "
                    "that explicitly permits it.",
                    user=self.env.user.display_name))
            rec.with_context(re_determination=True).write({'state': 'issued'})
            claim.with_context(re_claim_stage=True).write({
                'state': 'determined',
                'determination_date': rec.determination_date,
                'determination_authority_id': rec.authority_id.id,
            })
        return True

    def _self_determination_allowed(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'real_estate_construction.allow_self_determination',
            'False') in ('True', 'true', '1')

    def action_supersede(self, new_values=None):
        """Correct a determination by issuing another one."""
        self.ensure_one()
        if self.state != 'issued':
            raise UserError(_("Only an issued determination is superseded."))
        values = {
            'claim_id': self.claim_id.id,
            'authority_id': self.env.user.id,
            'authority_role': self.authority_role,
            'determined_cost': self.determined_cost,
            'determined_days': self.determined_days,
            'supersedes_id': self.id,
        }
        values.update(new_values or {})
        replacement = self.create(values)
        self.with_context(re_determination=True).write({
            'state': 'superseded', 'superseded_by_id': replacement.id})
        return replacement
