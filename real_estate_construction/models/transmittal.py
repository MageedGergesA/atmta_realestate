# -*- coding: utf-8 -*-
"""M5D — transmittals: evidence that specific information was formally sent.

The entire value of a transmittal is that it says what was sent **at the time**.
So a line does not point at "the document's current revision" — it snapshots the
revision code, the document number and the title as they stood when the
transmittal went out.

```
    Rev B transmitted on 12 March
    Rev C issued on 4 April
    → the 12 March transmittal still says Rev B, forever
```

A line keeps a link to the revision record as well, so the file is one click
away; but the *text* of what was sent is copied, so even voiding or renumbering
a revision later cannot rewrite the evidence.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

TRANSMITTAL_STATES = [
    ('draft', 'Draft'),
    ('ready', 'Ready to Send'),
    ('sent', 'Sent'),
    ('acknowledged', 'Acknowledged'),
    ('closed', 'Closed'),
    ('cancelled', 'Cancelled'),
]

TRANSMITTAL_PURPOSES = [
    ('information', 'For Information'),
    ('review', 'For Review'),
    ('approval', 'For Approval'),
    ('construction', 'For Construction'),
    ('record', 'For Record'),
    ('tender', 'For Tender'),
]

#: Once sent, the content is evidence.
IMMUTABLE_STATES = ('sent', 'acknowledged', 'closed')


class ConstructionTransmittal(models.Model):
    _name = 'realestate.construction.transmittal'
    _description = 'Document Transmittal'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, sent_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Transmittal Number', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    subject = fields.Char(required=True, tracking=True)
    message = fields.Html(string='Remarks')

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")

    sender_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, required=True,
        tracking=True)
    sender_partner_id = fields.Many2one(
        'res.partner', string='Sending Organisation')
    recipient_partner_ids = fields.Many2many(
        'res.partner', 'construction_transmittal_recipient_rel',
        'transmittal_id', 'partner_id', string='Recipients', required=True)
    copy_partner_ids = fields.Many2many(
        'res.partner', 'construction_transmittal_cc_rel',
        'transmittal_id', 'partner_id', string='Copied To')

    purpose = fields.Selection(
        TRANSMITTAL_PURPOSES, default='information', required=True,
        tracking=True)
    sent_date = fields.Date(readonly=True, copy=False, tracking=True,
                            index=True)
    response_requested = fields.Boolean()
    response_due_date = fields.Date()
    acknowledgement_required = fields.Boolean(default=True)
    acknowledged_date = fields.Date(readonly=True, copy=False, tracking=True)
    acknowledged_by_id = fields.Many2one(
        'res.partner', readonly=True, copy=False,
        help="Proof of receipt. Emphatically not technical approval.")
    acknowledgement_comments = fields.Char()

    state = fields.Selection(
        TRANSMITTAL_STATES, default='draft', required=True, tracking=True,
        copy=False, index=True)
    line_ids = fields.One2many(
        'realestate.construction.transmittal.line', 'transmittal_id',
        string='Documents')
    document_count = fields.Integer(compute='_compute_counts', store=True)
    attachment_ids = fields.Many2many(
        'ir.attachment', 'construction_transmittal_attachment_rel',
        'transmittal_id', 'attachment_id', string='Other Attachments')

    acknowledgement_overdue_days = fields.Integer(
        compute='_compute_overdue', store=True)
    is_acknowledgement_overdue = fields.Boolean(
        compute='_compute_overdue', store=True, index=True)

    @api.depends('line_ids')
    def _compute_counts(self):
        for rec in self:
            rec.document_count = len(rec.line_ids)

    @api.depends('sent_date', 'response_due_date', 'acknowledged_date',
                 'acknowledgement_required', 'state')
    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            due = rec.response_due_date
            outstanding = (
                rec.acknowledgement_required and not rec.acknowledged_date
                and rec.state == 'sent' and due)
            rec.acknowledgement_overdue_days = (
                max((today - due).days, 0) if outstanding else 0)
            rec.is_acknowledgement_overdue = bool(
                rec.acknowledgement_overdue_days)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.transmittal') or 'TRN/NEW'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    def action_ready(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft transmittal can be readied."))
            if not rec.line_ids:
                raise UserError(_(
                    "A transmittal with no documents transmits nothing."))
            rec.state = 'ready'
        return True

    def action_send(self):
        """Send it, and freeze what was sent."""
        for rec in self:
            if rec.state not in ('draft', 'ready'):
                raise UserError(_("%s has already been sent.") % rec.name)
            if not rec.line_ids:
                raise UserError(_(
                    "A transmittal with no documents transmits nothing."))
            if not rec.recipient_partner_ids:
                raise UserError(_("A transmittal needs a recipient."))
            rec.line_ids.with_context(re_transmittal_sending=True)._snapshot()
            rec.write({'state': 'sent',
                       'sent_date': fields.Date.context_today(rec)})
            rec.message_post(body=_(
                "Transmitted %(count)s document(s) to %(to)s.",
                count=len(rec.line_ids),
                to=', '.join(rec.recipient_partner_ids.mapped('display_name'))))
        return True

    def action_acknowledge(self, partner=None, comments=None):
        for rec in self:
            if rec.state != 'sent':
                raise UserError(_(
                    "Only a sent transmittal can be acknowledged."))
            rec.write({
                'state': 'acknowledged',
                'acknowledged_date': fields.Date.context_today(rec),
                'acknowledged_by_id': (
                    partner.id if partner
                    else rec.recipient_partner_ids[:1].id),
                'acknowledgement_comments': comments,
            })
        return True

    def action_close(self):
        for rec in self:
            if rec.state not in ('sent', 'acknowledged'):
                raise UserError(_("Only a sent transmittal closes."))
            rec.state = 'closed'
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state in IMMUTABLE_STATES:
                raise UserError(_(
                    "%s has been sent. A transmittal is evidence that "
                    "information left the building — issue another one rather "
                    "than cancelling this.") % rec.name)
            rec.state = 'cancelled'
        return True

    def write(self, vals):
        protected = {'line_ids', 'recipient_partner_ids', 'purpose',
                     'project_id', 'company_id', 'sent_date'}
        if protected & set(vals):
            sent = self.filtered(lambda t: t.state in IMMUTABLE_STATES)
            if sent and not self.env.context.get('re_transmittal_sending'):
                raise UserError(_(
                    "%(refs)s have been sent and cannot be changed.",
                    refs=', '.join(sent.mapped('name'))))
        return super().write(vals)

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Transmittal %(name)s is in %(company)s but its project "
                    "is in %(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))


class ConstructionTransmittalLine(models.Model):
    """One document revision, as it was when it was sent."""
    _name = 'realestate.construction.transmittal.line'
    _description = 'Transmittal Line'
    _order = 'transmittal_id, sequence, id'

    transmittal_id = fields.Many2one(
        'realestate.construction.transmittal', required=True,
        ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        related='transmittal_id.company_id', store=True, readonly=True)

    document_revision_id = fields.Many2one(
        'realestate.construction.document.revision', required=True,
        ondelete='restrict', index=True,
        help="The revision itself, so the file is one click away.")
    document_id = fields.Many2one(
        related='document_revision_id.document_id', store=True, readonly=True)

    # The snapshot. Copied text, not related fields: a related field would
    # follow the document if it were ever renumbered, and the point of a
    # transmittal is that it does not move.
    document_number = fields.Char(readonly=True)
    document_title = fields.Char(readonly=True)
    revision_code = fields.Char(readonly=True)
    revision_status = fields.Char(readonly=True)
    purpose_of_issue = fields.Char(readonly=True)
    copies = fields.Integer(default=1)
    notes = fields.Char()

    def _snapshot(self):
        """Freeze what is being sent, at the moment of sending."""
        for line in self:
            revision = line.document_revision_id
            line.write({
                'document_number': revision.document_number,
                'document_title': revision.document_id.name,
                'revision_code': revision.revision_code,
                'revision_status': dict(
                    revision._fields['state'].selection).get(revision.state),
                'purpose_of_issue': dict(
                    revision._fields['purpose_of_issue'].selection
                ).get(revision.purpose_of_issue),
            })
        return True

    def write(self, vals):
        sent = self.filtered(
            lambda l: l.transmittal_id.state in IMMUTABLE_STATES)
        if sent and not self.env.context.get('re_transmittal_sending'):
            raise UserError(_(
                "This transmittal has been sent. What it recorded is the "
                "evidence — send another one for a later revision."))
        return super().write(vals)

    def unlink(self):
        if any(l.transmittal_id.state in IMMUTABLE_STATES for l in self):
            raise UserError(_(
                "A sent transmittal's contents cannot be removed."))
        return super().unlink()
