# -*- coding: utf-8 -*-
"""M4I and M4AE — the two screens that read the governance layer.

```
    AVL REPORT      who may we invite, for this trade, on this date
    VENDOR AUDIT    what in this data does not add up
```

Both are transient and both compute from the authoritative services rather
than from a stored copy. The Approved Vendor List in particular is **not** a
persisted membership table: it is a question with four parameters (company,
trade, project, date), and the honest way to answer a question with four
parameters is to ask it, not to keep a table that is right for one set of them
and quietly wrong for the rest. Every row names the qualification that
authorises it, so nothing on this screen is an opinion.
"""

from odoo import _, api, fields, models

from ..models.vendor_eligibility import STATUS_LABELS


class AVLReport(models.TransientModel):
    _name = 'realestate.procurement.avl.report'
    _description = 'Approved Vendor List'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    category_id = fields.Many2one(
        'realestate.procurement.vendor.category', string='Trade',
        help="Leave empty to ask about the vendor generally rather than "
             "about one trade.")
    project_id = fields.Many2one('realestate.project', string='Project')
    as_of_date = fields.Date(
        string='As Of', required=True,
        default=lambda self: fields.Date.context_today(self),
        help="Eligibility is answered for this date and no other. Invitation, "
             "award and confirmation happen on different days and the answer "
             "legitimately differs between them.")
    purpose = fields.Selection([
        ('sourcing', 'Invitation to quote'),
        ('award', 'Receiving the order'),
    ], default='sourcing', required=True)
    include_ineligible = fields.Boolean(
        string='Show Excluded Vendors', default=True,
        help="On by default. A list that silently drops the suspended vendor "
             "cannot answer why they were not invited, which is the question "
             "this screen exists for.")
    line_ids = fields.One2many(
        'realestate.procurement.avl.report.line', 'report_id', readonly=True)
    eligible_count = fields.Integer(compute='_compute_counts')
    excluded_count = fields.Integer(compute='_compute_counts')

    @api.depends('line_ids.eligible')
    def _compute_counts(self):
        for rec in self:
            rec.eligible_count = len(rec.line_ids.filtered('eligible'))
            rec.excluded_count = len(rec.line_ids) - rec.eligible_count

    def action_compute(self):
        self.ensure_one()
        Eligibility = self.env['realestate.procurement.vendor.eligibility']
        self.line_ids.unlink()
        results = Eligibility.get_eligible_vendors(
            company=self.company_id, category=self.category_id or None,
            project=self.project_id or None, date=self.as_of_date,
            purpose=self.purpose,
            include_ineligible=self.include_ineligible)
        self.env['realestate.procurement.avl.report.line'].create([{
            'report_id': self.id,
            'partner_id': outcome['partner_id'],
            'qualification_id': outcome['qualification_id'],
            'category_id': outcome['category_id'],
            'eligible': outcome['eligible'],
            'qualified': outcome['qualified'],
            'status': outcome['status'],
            'valid_from': outcome['valid_from'],
            'valid_to': outcome['valid_to'],
            'conditions': '\n'.join(
                '%s%s' % (c['name'],
                          ' (%s)' % c['amount'] if c['amount'] else '')
                for c in outcome['conditions']),
            'reason': '\n'.join(outcome['blocking_reasons']
                                or outcome['warnings']),
        } for outcome in results])
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }


class AVLReportLine(models.TransientModel):
    _name = 'realestate.procurement.avl.report.line'
    _description = 'Approved Vendor List Line'
    _order = 'eligible desc, partner_id'

    report_id = fields.Many2one(
        'realestate.procurement.avl.report', required=True,
        ondelete='cascade')
    partner_id = fields.Many2one('res.partner', string='Vendor', readonly=True)
    category_id = fields.Many2one(
        'realestate.procurement.vendor.category', string='Trade', readonly=True)
    qualification_id = fields.Many2one(
        'realestate.procurement.vendor.qualification', string='Qualification',
        readonly=True,
        help="What authorises the row. A blank one means nothing does.")
    eligible = fields.Boolean(readonly=True)
    qualified = fields.Boolean(readonly=True)
    status = fields.Char(readonly=True)
    status_label = fields.Char(compute='_compute_status_label', string='Status')
    valid_from = fields.Date(readonly=True)
    valid_to = fields.Date(readonly=True, string='Valid Until')
    conditions = fields.Text(readonly=True)
    reason = fields.Text(readonly=True, string='Why')

    @api.depends('status')
    def _compute_status_label(self):
        for rec in self:
            rec.status_label = STATUS_LABELS.get(rec.status, rec.status or '')


class QualificationReject(models.TransientModel):
    """A reason is not optional, so it gets a screen of its own.

    The alternative — a Reject button that acts immediately and invites the
    approver to explain in the chatter afterwards — produces rejections with
    no reason roughly as often as people are busy.
    """
    _name = 'realestate.procurement.qualification.reject'
    _description = 'Reject Vendor Qualification'

    qualification_id = fields.Many2one(
        'realestate.procurement.vendor.qualification', required=True,
        default=lambda self: self.env.context.get('active_id'))
    reason = fields.Text(required=True, string='Why')

    def action_reject(self):
        self.ensure_one()
        self.qualification_id.action_reject(reason=self.reason)
        return {'type': 'ir.actions.act_window_close'}


class VendorAudit(models.TransientModel):
    """M4AE — findings, not repairs.

    Every check here could be written as an automatic fix and every one of
    them is deliberately not. A qualification approved with no approver on
    it, or a current assessment whose blocking requirement failed, is
    somebody having done something the system should not have allowed — and
    quietly correcting it destroys the only evidence that it happened.
    """
    _name = 'realestate.procurement.vendor.audit'
    _description = 'Vendor Governance Data Audit'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    line_ids = fields.One2many(
        'realestate.procurement.vendor.audit.line', 'audit_id', readonly=True)
    finding_count = fields.Integer(compute='_compute_finding_count')

    @api.depends('line_ids')
    def _compute_finding_count(self):
        for rec in self:
            rec.finding_count = len(rec.line_ids)

    def action_scan(self):
        self.ensure_one()
        self.line_ids.unlink()
        findings = self._findings()
        self.env['realestate.procurement.vendor.audit.line'].create([
            dict(finding, audit_id=self.id) for finding in findings])
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _findings(self):
        self.ensure_one()
        Qualification = self.env[
            'realestate.procurement.vendor.qualification']
        Restriction = self.env['realestate.procurement.vendor.restriction']
        today = fields.Date.context_today(self)
        company = self.company_id
        findings = []

        def add(kind, record, detail):
            findings.append({
                'kind': kind,
                'model_name': record._name,
                'record_id': record.id,
                'record_ref': record.display_name,
                'detail': detail,
            })

        current = Qualification.search([
            ('company_id', '=', company.id), ('is_current', '=', True)])

        for qualification in current:
            if qualification.expiry_date and qualification.expiry_date < today \
                    and qualification.state == 'approved':
                add('expired_still_current', qualification, _(
                    "Expired on %s but still flagged current. The nightly job "
                    "has not run; eligibility is already answering correctly.")
                    % qualification.expiry_date)
            if not qualification.approver_id and qualification.state == 'approved':
                add('approved_without_approver', qualification, _(
                    "Approved with nobody recorded as having approved it. "
                    "Almost always a migrated record — leave it, but do not "
                    "treat it as evidence of a decision."))
            failed = qualification.sudo().response_ids.filtered(
                lambda r: r.blocking and not r.passed)
            if failed and qualification.result in (
                    'qualified', 'qualified_with_conditions'):
                add('blocking_failure_but_qualified', qualification, _(
                    "Blocking requirement(s) failed — %s — yet the result is "
                    "%s.") % (', '.join(failed.mapped('requirement_name')),
                              qualification.result))
            expired_docs = qualification.sudo().response_ids.filtered(
                lambda r: r.obligation == 'mandatory' and r.expiry_sensitive
                and r.document_expiry_date
                and r.document_expiry_date < today)
            if expired_docs:
                add('mandatory_document_expired', qualification, _(
                    "Mandatory document(s) expired: %s.")
                    % ', '.join(expired_docs.mapped('requirement_name')))
            if qualification.project_id and \
                    qualification.project_id.company_id and \
                    qualification.project_id.company_id != company:
                add('project_company_mismatch', qualification, _(
                    "Endorses a project belonging to %s.")
                    % qualification.project_id.company_id.display_name)
            if not qualification.partner_id.active:
                add('archived_vendor', qualification, _(
                    "The vendor is archived but the qualification is current."))

        # The unique index makes this impossible going forward. It is checked
        # anyway because a database that predates the index can still hold it.
        seen = {}
        for qualification in current:
            key = (qualification.partner_id.id, qualification.category_id.id,
                   qualification.project_id.id or 0)
            if key in seen:
                add('conflicting_current', qualification, _(
                    "A second current assessment for the same vendor, trade "
                    "and project as %s.") % seen[key].name)
            seen[key] = qualification

        for restriction in Restriction.search([
                ('company_id', '=', company.id),
                ('state', 'in', ('active', 'draft'))]):
            if restriction.effective_to and \
                    restriction.effective_to < restriction.effective_from:
                add('restriction_dates_invalid', restriction, _(
                    "Ends %(to)s, having started %(from)s.",
                    to=restriction.effective_to,
                    **{'from': restriction.effective_from}))
            if restriction.state == 'active' and not restriction.approved_by_id:
                add('restriction_without_approver', restriction, _(
                    "In force with nobody recorded as having imposed it."))

        return findings


class VendorAuditLine(models.TransientModel):
    _name = 'realestate.procurement.vendor.audit.line'
    _description = 'Vendor Governance Audit Finding'
    _order = 'kind, id'

    audit_id = fields.Many2one(
        'realestate.procurement.vendor.audit', required=True,
        ondelete='cascade')
    kind = fields.Selection([
        ('expired_still_current', 'Expired But Still Current'),
        ('approved_without_approver', 'Approved Without An Approver'),
        ('blocking_failure_but_qualified', 'Qualified Despite A Blocking Failure'),
        ('mandatory_document_expired', 'Mandatory Document Expired'),
        ('project_company_mismatch', 'Project Belongs To Another Company'),
        ('archived_vendor', 'Vendor Archived'),
        ('conflicting_current', 'Conflicting Current Assessments'),
        ('restriction_dates_invalid', 'Restriction Dates Invalid'),
        ('restriction_without_approver', 'Restriction Without An Approver'),
    ], required=True, readonly=True, string='Finding')
    model_name = fields.Char(readonly=True)
    record_id = fields.Integer(readonly=True)
    record_ref = fields.Char(readonly=True, string='Record')
    detail = fields.Text(readonly=True)

    def action_open_record(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.model_name,
            'res_id': self.record_id,
            'view_mode': 'form',
        }
