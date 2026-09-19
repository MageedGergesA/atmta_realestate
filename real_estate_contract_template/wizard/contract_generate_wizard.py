import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ContractGenerateWizard(models.TransientModel):
    _name = 'realestate.contract.generate.wizard'
    _description = 'Generate Contract Document'

    target_model_name = fields.Char(required=True)
    record_id = fields.Integer(required=True)
    record_display = fields.Char(compute='_compute_record_display', readonly=True)

    template_id = fields.Many2one(
        'realestate.contract.template', required=True,
        domain="[('target_model_name', '=', target_model_name), ('active', '=', True)]",
    )
    template_jurisdiction = fields.Selection(
        related='template_id.jurisdiction', readonly=True,
    )
    template_kind = fields.Selection(related='template_id.kind', readonly=True)
    template_language = fields.Selection(related='template_id.language', readonly=True)
    template_version = fields.Char(related='template_id.form_version', readonly=True)

    output_format = fields.Selection(
        [('docx', 'Word (.docx)'), ('pdf', 'PDF')],
        required=True, default='pdf',
    )

    document_id = fields.Many2one('realestate.contract.document', readonly=True)
    output_file = fields.Binary(readonly=True, attachment=False)
    output_filename = fields.Char(readonly=True)
    generated = fields.Boolean(readonly=True, default=False)

    @api.depends('target_model_name', 'record_id')
    def _compute_record_display(self):
        for wiz in self:
            wiz.record_display = ''
            if wiz.target_model_name and wiz.record_id:
                rec = self.env[wiz.target_model_name].browse(wiz.record_id).exists()
                if rec:
                    wiz.record_display = rec.display_name

    def _get_record(self):
        self.ensure_one()
        if not self.target_model_name or not self.record_id:
            raise UserError(_("No source record selected."))
        rec = self.env[self.target_model_name].browse(self.record_id).exists()
        if not rec:
            raise UserError(_("Source record %s(%s) not found.",
                              self.target_model_name, self.record_id))
        return rec

    def action_generate(self):
        self.ensure_one()
        record = self._get_record()
        payload, filename = self.template_id.render(record, output_format=self.output_format)
        encoded = base64.b64encode(payload)

        Doc = self.env['realestate.contract.document']
        target_model = self.env['ir.model']._get(self.target_model_name)
        version = Doc._next_version_for_target(self.target_model_name, self.record_id)
        document = Doc.create({
            'name': f"{self.template_id.name} — v{version}",
            'template_id': self.template_id.id,
            'target_model_id': target_model.id,
            'target_record_id': self.record_id,
            # Stamped once, here. The document must keep saying which form it
            # was issued from even after the template is refreshed to a new
            # regulator version.
            'template_kind': self.template_id.kind,
            'template_jurisdiction': self.template_id.jurisdiction,
            'template_language': self.template_id.language,
            'template_version': self.template_id.form_version,
            'output_format': self.output_format,
            'file_data': encoded,
            'file_name': filename,
            'version': version,
            'state': 'draft',
        })

        if hasattr(record, 'message_post'):
            record.message_post(
                body=_(
                    "Generated contract document: %(name)s (v%(ver)s, %(fmt)s)",
                    name=self.template_id.name, ver=version,
                    fmt=self.output_format.upper(),
                ),
            )

        self.write({
            'output_file': encoded,
            'output_filename': filename,
            'document_id': document.id,
            'generated': True,
        })

        # Stay in the dialog. Returning an act_url closed it, which threw the
        # user straight back to the source record with a downloaded file and no
        # sign that a document record had been created — and left the whole
        # `generated` half of the wizard view (the success message, the file
        # field, "Open Document") unreachable. The file is on `output_file`,
        # one click away, and the document is one click after that.
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_open_document(self):
        self.ensure_one()
        if not self.document_id:
            raise UserError(_("No document to open."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.contract.document',
            'res_id': self.document_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
