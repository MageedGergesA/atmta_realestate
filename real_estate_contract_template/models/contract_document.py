import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError


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
    template_kind = fields.Selection(related='template_id.kind', store=True, readonly=True)
    template_jurisdiction = fields.Selection(
        related='template_id.jurisdiction', store=True, readonly=True,
    )
    template_language = fields.Selection(related='template_id.language', store=True, readonly=True)
    template_version = fields.Char(related='template_id.form_version', store=True, readonly=True)

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

    def _open_target(self):
        self.ensure_one()
        if not self.target_model_name or not self.target_record_id:
            raise UserError(_("This document has no linked source record."))
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
