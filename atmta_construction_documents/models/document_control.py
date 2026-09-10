# -*- coding: utf-8 -*-
"""M5C — controlled documents, their revisions, and the file behind each one.

### Three concepts, deliberately not one

```
    DOCUMENT        stable identity          A-ARC-DRG-1001
      └ REVISION    formal issue             Rev A, Rev B, Rev C
          └ FILE    technical replacement    the attachment, v1 v2 …
```

**Document** is what people mean when they say "drawing 1001": it outlives
every revision of itself. **Revision** is the formal issue that was
communicated — the thing an RFI is asked about and a transmittal records.
**File version** is a technical replacement inside a revision that has not been
issued yet: somebody fixed a typo in the title block before sending it.

Collapsing these is the classic failure. If replacing a file bumped the formal
revision, every typo would become "Rev D" and the register would lose the
meaning of a revision. If the register held only the latest file, an RFI about
Rev B would silently start referring to Rev C, and the question would stop
matching its answer.

### Issued means frozen

Once a revision is issued — submitted for review, transmitted, referenced by an
answered RFI — its file is evidence. It cannot be replaced; a correction is a
new revision. Everything historical keeps pointing at exactly what existed at
the time.

### Not a DMS

Odoo attachments are the file store. `documents` (Enterprise) is **not
installed in this deployment** and is not a dependency: the register, the
metadata and the workflow work on plain `ir.attachment`, and would gain
optional folder/version handling if Documents were ever present.

### Ordering is not alphabetical

Revision codes are `A, B, C` in one company and `00, 01, 02` in another, and
`max()` on a string gets both wrong eventually — `10` sorts before `9`. Every
revision therefore carries an explicit integer `sequence`, and "current" means
highest sequence, never highest string.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

DOCUMENT_TYPES = [
    ('drawing', 'Drawing'),
    ('shop_drawing', 'Shop Drawing'),
    ('specification', 'Specification'),
    ('method_statement', 'Method Statement'),
    ('material_data', 'Material / Product Data'),
    ('calculation', 'Calculation'),
    ('report', 'Report'),
    ('certificate', 'Certificate'),
    ('as_built', 'As-Built'),
    ('om_manual', 'O&M Manual'),
    ('correspondence', 'Correspondence'),
    ('other', 'Other'),
]

DISCIPLINES = [
    ('architectural', 'Architectural'),
    ('structural', 'Structural'),
    ('civil', 'Civil'),
    ('mechanical', 'Mechanical'),
    ('electrical', 'Electrical'),
    ('plumbing', 'Plumbing'),
    ('hvac', 'HVAC'),
    ('landscape', 'Landscape'),
    ('interior', 'Interior'),
    ('infrastructure', 'Infrastructure'),
    ('multi', 'Multi-Discipline'),
    ('other', 'Other'),
]

#: What a revision *is*. Not the same question as why it was issued.
REVISION_STATES = [
    ('draft', 'Draft'),
    ('for_review', 'For Review'),
    ('approved', 'Approved'),
    ('approved_with_comments', 'Approved with Comments'),
    ('rejected', 'Rejected'),
    ('superseded', 'Superseded'),
    ('as_built', 'As-Built'),
    ('void', 'Void'),
]

#: Why it was issued. A revision can be Approved *and* For Construction; those
#: are two facts, and one field cannot hold both.
PURPOSES_OF_ISSUE = [
    ('information', 'For Information'),
    ('review', 'For Review'),
    ('approval', 'For Approval'),
    ('tender', 'For Tender'),
    ('construction', 'For Construction'),
    ('fabrication', 'For Fabrication'),
    ('record', 'For Record'),
    ('as_built', 'As-Built'),
]

#: States in which a revision has left the building and is evidence.
ISSUED_STATES = ('for_review', 'approved', 'approved_with_comments',
                 'rejected', 'superseded', 'as_built')


class ConstructionDocument(models.Model):
    """The stable identity of a controlled document."""
    _name = 'realestate.construction.document'
    _description = 'Controlled Construction Document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, document_number'
    _check_company_auto = True

    document_number = fields.Char(
        required=True, index='trigram', tracking=True,
        help="Identifies the document across all its revisions — "
             "A-ARC-DRG-1001.")
    name = fields.Char(string='Title', required=True, tracking=True)
    display_name = fields.Char(compute='_compute_display_name', store=False)

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

    document_type = fields.Selection(
        DOCUMENT_TYPES, required=True, default='drawing', index=True)
    discipline = fields.Selection(DISCIPLINES, default='other', index=True)
    originator = fields.Char(help="The organisation that produced it.")
    author_id = fields.Many2one('res.users', string='Author')
    owner_id = fields.Many2one(
        'res.users', string='Document Owner',
        default=lambda self: self.env.user)
    contractor_id = fields.Many2one('realestate.contractor')

    confidentiality = fields.Selection([
        ('project', 'Project Team'),
        ('internal', 'Internal Only'),
        ('restricted', 'Restricted'),
    ], default='project', required=True,
        help="Read access follows this, the project and the company — not "
             "whoever happens to be able to open a related record.")

    revision_ids = fields.One2many(
        'realestate.construction.document.revision', 'document_id',
        string='Revisions')
    revision_count = fields.Integer(compute='_compute_revisions', store=True)

    current_revision_id = fields.Many2one(
        'realestate.construction.document.revision',
        compute='_compute_revisions', store=True,
        help="Highest sequence, whatever the revision code looks like.")
    current_approved_revision_id = fields.Many2one(
        'realestate.construction.document.revision',
        compute='_compute_revisions', store=True,
        help="Latest revision that was approved. Not necessarily the latest "
             "revision, and not necessarily the one issued for construction.")
    current_issued_revision_id = fields.Many2one(
        'realestate.construction.document.revision',
        compute='_compute_revisions', store=True,
        help="Latest revision issued for construction or fabrication — what "
             "the site is actually building from.")
    current_revision_code = fields.Char(
        related='current_revision_id.revision_code', store=True, readonly=True)
    current_state = fields.Selection(
        related='current_revision_id.state', store=True, readonly=True,
        string='Current Status')

    notes = fields.Text()
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('number_uniq_per_project',
         'unique(project_id, document_number)',
         'A document number must be unique within its project.'),
    ]

    @api.depends('document_number', 'name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = ' — '.join(
                part for part in (rec.document_number, rec.name) if part)

    @api.depends('revision_ids.sequence', 'revision_ids.state',
                 'revision_ids.approved_on', 'revision_ids.purpose_of_issue')
    def _compute_revisions(self):
        """Three different "current"s, and they are genuinely different.

        The latest revision may be a draft. The latest *approved* one may be
        two revisions back. The one the site is building from may be neither.
        Answering all three separately is the difference between a register and
        a list of files.
        """
        for rec in self:
            revisions = rec.revision_ids.filtered(
                lambda r: r.state != 'void').sorted('sequence')
            rec.revision_count = len(revisions)
            rec.current_revision_id = revisions[-1] if revisions else False

            # Approval is judged on `approved_on`, not on current state.
            # Superseding a revision does not un-approve it: when Rev B is
            # issued *for review* over an approved-for-construction Rev A, the
            # site is still building from Rev A, and a register that forgot
            # that would send people to a drawing nobody approved.
            approved = revisions.filtered(
                lambda r: r.approved_on and r.state not in ('rejected', 'void'))
            rec.current_approved_revision_id = approved[-1] if approved else False

            issued = approved.filtered(
                lambda r: r.purpose_of_issue in ('construction', 'fabrication',
                                                 'as_built'))
            rec.current_issued_revision_id = issued[-1] if issued else False

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Document %(number)s is in %(company)s but its project is "
                    "in %(other)s.", number=rec.document_number,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

    def action_view_revisions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Revisions'),
            'res_model': 'realestate.construction.document.revision',
            'view_mode': 'list,form',
            'domain': [('document_id', '=', self.id)],
            'context': {'default_document_id': self.id},
        }


class ConstructionDocumentRevision(models.Model):
    """One formal issue of a document, and the file that was issued."""
    _name = 'realestate.construction.document.revision'
    _description = 'Controlled Document Revision'
    _inherit = ['mail.thread']
    _order = 'document_id, sequence, id'
    _check_company_auto = True

    document_id = fields.Many2one(
        'realestate.construction.document', required=True, ondelete='cascade',
        index=True, check_company=True)
    project_id = fields.Many2one(
        related='document_id.project_id', store=True, index=True,
        readonly=True)
    company_id = fields.Many2one(
        related='document_id.company_id', store=True, index=True,
        readonly=True)
    document_number = fields.Char(
        related='document_id.document_number', store=True, readonly=True,
        index='trigram')

    revision_code = fields.Char(
        required=True, tracking=True,
        help="As the project writes it — A, B, C or 00, 01, 02. Free text on "
             "purpose: revision schemes differ between companies.")
    sequence = fields.Integer(
        required=True, default=0, index=True,
        help="Explicit order. 'Latest' means highest sequence, never highest "
             "string — '10' sorts before '9' and would quietly pick the "
             "wrong revision.")
    file_version = fields.Integer(
        default=1, readonly=True,
        help="Technical replacements of the file *within* this formal "
             "revision, before it was issued. Correcting a title-block typo "
             "is not a new revision to the project.")

    name = fields.Char(string='Description')
    revision_date = fields.Date(
        default=fields.Date.context_today, tracking=True)
    state = fields.Selection(
        REVISION_STATES, default='draft', required=True, tracking=True,
        index=True)
    purpose_of_issue = fields.Selection(
        PURPOSES_OF_ISSUE, default='review', required=True, tracking=True,
        help="Why it was issued — a different question from its status. A "
             "revision may be Approved *and* For Construction.")

    attachment_id = fields.Many2one(
        'ir.attachment', string='File', ondelete='restrict', copy=False)
    attachment_ids = fields.Many2many(
        'ir.attachment', 'construction_doc_revision_attachment_rel',
        'revision_id', 'attachment_id', string='Supporting Files')
    file_name = fields.Char(related='attachment_id.name', readonly=True)
    file_size = fields.Integer(
        related='attachment_id.file_size', readonly=True)
    checksum = fields.Char(
        related='attachment_id.checksum', readonly=True, store=True,
        help="Proves the bytes behind an issued revision never changed.")

    uploaded_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, readonly=True)
    issued_on = fields.Datetime(readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    superseded_on = fields.Datetime(readonly=True, copy=False)
    superseded_by_id = fields.Many2one(
        'realestate.construction.document.revision', readonly=True,
        copy=False)
    supersedes_id = fields.Many2one(
        'realestate.construction.document.revision', readonly=True,
        copy=False)

    is_current = fields.Boolean(compute='_compute_flags', store=True)
    is_current_approved = fields.Boolean(compute='_compute_flags', store=True)
    is_issued = fields.Boolean(compute='_compute_flags', store=True)

    submittal_revision_ids = fields.One2many(
        'realestate.construction.submittal.revision', 'document_revision_id',
        string='Submitted Under')
    transmittal_line_ids = fields.One2many(
        'realestate.construction.transmittal.line', 'document_revision_id',
        string='Transmitted In')
    notes = fields.Text()

    _sql_constraints = [
        ('code_uniq_per_document',
         'unique(document_id, revision_code)',
         'A document cannot have two revisions with the same code.'),
    ]

    @api.depends('document_id.current_revision_id',
                 'document_id.current_approved_revision_id', 'state',
                 'approved_on')
    def _compute_flags(self):
        for rec in self:
            rec.is_current = rec.document_id.current_revision_id == rec
            rec.is_current_approved = (
                rec.document_id.current_approved_revision_id == rec)
            rec.is_issued = rec.state in ISSUED_STATES

    @api.model_create_multi
    def create(self, vals_list):
        """Sequence itself when nobody said where this revision belongs."""
        for vals in vals_list:
            if vals.get('sequence') or not vals.get('document_id'):
                continue
            last = self.search(
                [('document_id', '=', vals['document_id'])],
                order='sequence desc', limit=1)
            vals['sequence'] = (last.sequence + 10) if last else 10
        revisions = super().create(vals_list)
        revisions._supersede_previous()
        return revisions

    def _supersede_previous(self):
        """A new revision retires the one before it, and says so.

        The old revision is never deleted and never altered beyond being
        marked superseded — every RFI, submittal and transmittal that pointed
        at it still resolves to exactly the file that was issued.
        """
        for rec in self:
            previous = self.search([
                ('document_id', '=', rec.document_id.id),
                ('id', '!=', rec.id),
                ('sequence', '<', rec.sequence),
                ('state', 'not in', ('superseded', 'void')),
            ], order='sequence desc', limit=1)
            if not previous:
                continue
            rec.supersedes_id = previous
            previous.with_context(re_document_superseding=True).write({
                'state': 'superseded',
                'superseded_on': fields.Datetime.now(),
                'superseded_by_id': rec.id,
            })

    # ------------------------------------------------------------------
    def write(self, vals):
        """An issued revision is evidence. Evidence does not get edited.

        The file and the identifying metadata are frozen once the revision has
        left the building. Supersession and review outcomes are still allowed —
        those are things that *happen to* a revision, not rewrites of it.
        """
        frozen_fields = {'attachment_id', 'revision_code', 'sequence',
                         'document_id', 'revision_date'}
        touched = frozen_fields & set(vals)
        if touched and not self.env.context.get('re_document_superseding'):
            issued = self.filtered('is_issued')
            if issued:
                raise UserError(_(
                    "%(refs)s have been issued. Their file and revision "
                    "identity are the evidence of what was sent — record a "
                    "new revision instead of editing this one.",
                    refs=', '.join(
                        '%s Rev %s' % (r.document_number, r.revision_code)
                        for r in issued)))
        if 'attachment_id' in vals and not self.env.context.get(
                're_document_superseding'):
            for rec in self:
                if rec.attachment_id and rec.state == 'draft':
                    # A technical replacement inside an unissued revision.
                    vals.setdefault('file_version', rec.file_version + 1)
        return super().write(vals)

    def unlink(self):
        if any(rec.is_issued for rec in self):
            raise UserError(_(
                "An issued revision cannot be deleted. Void it if it was "
                "raised in error — the register is a history, not a folder."))
        return super().unlink()

    # ------------------------------------------------------------------
    def action_issue_for_review(self):
        for rec in self:
            rec._require_file()
            rec.write({'state': 'for_review',
                       'issued_on': fields.Datetime.now()})
        return True

    def action_approve(self, with_comments=False):
        for rec in self:
            rec._require_file()
            rec.write({
                'state': ('approved_with_comments' if with_comments
                          else 'approved'),
                'approved_on': fields.Datetime.now(),
                'approved_by_id': self.env.user.id,
                'issued_on': rec.issued_on or fields.Datetime.now(),
            })
        return True

    def action_reject(self):
        for rec in self:
            rec.write({'state': 'rejected',
                       'issued_on': rec.issued_on or fields.Datetime.now()})
        return True

    def action_void(self):
        for rec in self:
            if rec.transmittal_line_ids.filtered(
                    lambda l: l.transmittal_id.state in ('sent',
                                                         'acknowledged',
                                                         'closed')):
                raise UserError(_(
                    "This revision has been formally transmitted and cannot "
                    "be voided — issue a superseding revision instead."))
            rec.state = 'void'
        return True

    def _require_file(self):
        self.ensure_one()
        if not self.attachment_id:
            raise UserError(_(
                "%(doc)s Rev %(rev)s has no file. Issuing a revision that "
                "nobody can open is not issuing anything.",
                doc=self.document_number, rev=self.revision_code))

    @api.constrains('attachment_id')
    def _check_attachment_company(self):
        for rec in self:
            attachment = rec.attachment_id
            if attachment and attachment.company_id and \
                    attachment.company_id != rec.company_id:
                raise ValidationError(_(
                    "The file belongs to another company."))
