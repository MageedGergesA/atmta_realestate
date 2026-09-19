import base64

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .contract_template import (
    JURISDICTION_SELECTION, KIND_SELECTION, LANGUAGE_SELECTION,
)

MANAGER_GROUP = 'atmta_real_estate.group_realestate_manager'

# Once a document is signed it is the contract, not a draft of it: the file and
# what it was issued against are frozen.
ISSUED_STATES = ('signed', 'active', 'expired')
FROZEN_FIELDS = (
    'file_data', 'file_name', 'output_format',
    'template_id', 'template_kind', 'template_jurisdiction',
    'template_language', 'template_version',
    'target_model_id', 'target_record_id',
)


STATE_SELECTION = [
    ('draft', 'Draft'),
    ('under_review', 'Under Review'),
    ('approved', 'Approved'),
    ('sent_for_signature', 'Sent for Signature'),
    ('signed', 'Signed'),
    ('active', 'Active'),
    ('expired', 'Expired'),
    ('cancelled', 'Cancelled'),
]


class ContractDocument(models.Model):
    _name = 'realestate.contract.document'
    _description = 'Generated Contract Document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    name = fields.Char(required=True, tracking=True, copy=False)

    template_id = fields.Many2one(
        'realestate.contract.template', required=True, ondelete='restrict',
    )
    # Stamps, not mirrors. These used to be `related=..., store=True`, which is
    # a live copy: editing the template to a new regulator form rewrote what
    # an already-issued document said it had been rendered from. They are
    # written once, when the document is generated, and never follow the
    # template again. (Same columns, same names — installed databases keep the
    # values the stored related fields left behind.)
    template_kind = fields.Selection(KIND_SELECTION, readonly=True, copy=False)
    template_jurisdiction = fields.Selection(JURISDICTION_SELECTION, readonly=True, copy=False)
    template_language = fields.Selection(LANGUAGE_SELECTION, readonly=True, copy=False)
    template_version = fields.Char(readonly=True, copy=False)

    target_model_id = fields.Many2one(
        'ir.model', required=True, ondelete='cascade',
        domain=[('transient', '=', False)],
    )
    target_model_name = fields.Char(related='target_model_id.model', store=True, readonly=True)
    target_record_id = fields.Integer(required=True, index=True)
    target_display = fields.Char(compute='_compute_target_display', store=True)

    output_format = fields.Selection(
        [('docx', 'Word (.docx)'), ('pdf', 'PDF')],
        required=True, default='pdf',
    )
    file_data = fields.Binary(required=True, attachment=True)
    file_name = fields.Char(required=True)
    file_size = fields.Integer(compute='_compute_file_size', store=True)

    version = fields.Integer(default=1, readonly=True, copy=False, tracking=True)

    state = fields.Selection(
        STATE_SELECTION, required=True, default='draft', tracking=True, copy=False,
    )

    reviewer_id = fields.Many2one('res.users', tracking=True)
    approver_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    signed_on = fields.Datetime(readonly=True, copy=False)
    activated_on = fields.Datetime(readonly=True, copy=False)
    expiry_date = fields.Date(tracking=True)

    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True,
    )

    @api.depends('target_model_name', 'target_record_id')
    def _compute_target_display(self):
        for rec in self:
            rec.target_display = ''
            if rec.target_model_name and rec.target_record_id:
                target = self.env[rec.target_model_name].browse(rec.target_record_id).exists()
                if target:
                    rec.target_display = target.display_name

    @api.depends('file_data')
    def _compute_file_size(self):
        for rec in self:
            rec.file_size = len(base64.b64decode(rec.file_data)) if rec.file_data else 0

    @api.constrains('file_data')
    def _check_file_data_required(self):
        # `required=True` on an attachment=True binary is only a UI hint — the
        # ORM accepts False on create and on write — and a contract document
        # with no file is not a document.
        for rec in self:
            if not rec.file_data:
                raise ValidationError(_(
                    "Document '%s' must keep its generated file.", rec.name or '',
                ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Stamp the template's identity at issue time for any caller that
            # did not (the generate wizard passes them explicitly). Nothing
            # refreshes these afterwards — see the field definitions.
            template = self.env['realestate.contract.template'].browse(vals.get('template_id'))
            if not template:
                continue
            for fname, tpl_field in (
                ('template_kind', 'kind'),
                ('template_jurisdiction', 'jurisdiction'),
                ('template_language', 'language'),
                ('template_version', 'form_version'),
            ):
                if not vals.get(fname):
                    vals[fname] = template[tpl_field]
        return super().create(vals_list)

    def write(self, vals):
        frozen = [f for f in FROZEN_FIELDS if f in vals]
        if frozen:
            for rec in self:
                if rec.state in ISSUED_STATES:
                    raise UserError(_(
                        "%(doc)s has been signed: %(fields)s can no longer be "
                        "changed. Generate a new version instead.",
                        doc=rec.display_name, fields=', '.join(frozen),
                    ))
        return super().write(vals)

    def _check_manager(self):
        """Agents prepare, managers approve.

        A Real Estate User writes the document through its preparation steps
        (submit for review, send for signature, mark signed, cancel) — that is
        their own workflow, and the ACL grants them write for it. Approving and
        activating are the two steps that make the document binding, so they
        stop here. Guarded in Python and not only on the button: both methods
        are callable over RPC."""
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(_(
                "Approving or activating a contract document is a Real Estate "
                "Manager's decision."
            ))

    def _open_target(self):
        self.ensure_one()
        if not self.target_model_name or not self.target_record_id:
            raise UserError(_("This document has no linked source record."))
        # The source record is referenced by a plain integer, so deleting it
        # leaves the document pointing at nothing; say so here instead of
        # sending the user to Odoo's "record does not exist" page.
        target = self.env[self.target_model_name].browse(self.target_record_id).exists()
        if not target:
            raise UserError(_(
                "The source record this document was generated from "
                "(%(model)s, id %(rid)s) no longer exists.",
                model=self.target_model_name, rid=self.target_record_id,
            ))
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.target_model_name,
            'res_id': self.target_record_id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_target(self):
        return self._open_target()

    def action_download(self):
        self.ensure_one()
        if not self.file_data:
            raise UserError(_("This document has no file attached."))
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/realestate.contract.document/{self.id}/file_data/{self.file_name}?download=true',
            'target': 'self',
        }

    def _set_state(self, new_state, extra=None):
        vals = {'state': new_state}
        if extra:
            vals.update(extra)
        self.write(vals)

    def action_submit_for_review(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only Draft documents can be submitted for review."))
            rec._set_state('under_review')

    def action_approve(self):
        self._check_manager()
        for rec in self:
            if rec.state != 'under_review':
                raise UserError(_("Only documents Under Review can be approved."))
            rec._set_state('approved', {
                'approver_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })

    def action_send_for_signature(self):
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_("Only Approved documents can be sent for signature."))
            rec._set_state('sent_for_signature')

    def action_mark_signed(self):
        for rec in self:
            if rec.state not in ('approved', 'sent_for_signature'):
                raise UserError(_("Document must be Approved or Sent for Signature first."))
            rec._set_state('signed', {'signed_on': fields.Datetime.now()})

    def action_activate(self):
        self._check_manager()
        for rec in self:
            if rec.state != 'signed':
                raise UserError(_("Only Signed documents can be activated."))
            rec._set_state('active', {'activated_on': fields.Datetime.now()})

    def action_mark_expired(self):
        for rec in self:
            if rec.state not in ('active', 'signed'):
                raise UserError(_("Only Active or Signed documents can be marked Expired."))
            rec._set_state('expired')

    def action_cancel(self):
        for rec in self:
            if rec.state in ('signed', 'active', 'expired'):
                raise UserError(_("Cannot cancel a document that has already been Signed."))
            rec._set_state('cancelled')

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('under_review', 'cancelled'):
                raise UserError(_("Only Under Review or Cancelled documents can be reset to Draft."))
            rec._set_state('draft')

    @api.model
    def _next_version_for_target(self, target_model_name, target_record_id):
        last = self.search([
            ('target_model_name', '=', target_model_name),
            ('target_record_id', '=', target_record_id),
        ], order='version desc', limit=1)
        return (last.version + 1) if last else 1
