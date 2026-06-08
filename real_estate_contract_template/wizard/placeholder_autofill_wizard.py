import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.placeholder_helpers import (
    build_field_catalog, scan_docx_tokens, match_token, rewrite_docx,
)


class PlaceholderAutoFillWizard(models.TransientModel):
    _name = 'realestate.placeholder.autofill.wizard'
    _description = 'Auto-Fill Placeholders'

    template_id = fields.Many2one('realestate.contract.template', required=True)
    target_model_id = fields.Many2one(
        related='template_id.target_model_id', readonly=True,
    )
    target_model_name = fields.Char(
        related='template_id.target_model_name', readonly=True,
    )

    state = fields.Selection(
        [('scan', 'Scan'), ('review', 'Review'), ('applied', 'Applied')],
        required=True, default='scan',
    )

    scan_summary = fields.Char(readonly=True)
    line_ids = fields.One2many(
        'realestate.placeholder.autofill.line', 'wizard_id',
    )

    new_docx = fields.Binary(readonly=True, attachment=False)
    new_docx_filename = fields.Char(readonly=True)
    applied_count = fields.Integer(readonly=True)
    unmapped_count = fields.Integer(readonly=True)

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        if self._context.get('default_template_id'):
            vals['template_id'] = self._context['default_template_id']
        return vals

    def action_scan(self):
        self.ensure_one()
        if not self.template_id or not self.template_id.docx_template:
            raise UserError(_("Upload a .docx on the template first."))
        if not self.target_model_id:
            raise UserError(_("Set a target model on the template first."))
        raw = base64.b64decode(self.template_id.docx_template)
        tokens = scan_docx_tokens(raw)
        if not tokens:
            raise UserError(_(
                "No bracketed tokens [Like This] were found in the document. "
                "Mark the words you want to template — e.g. write [Customer Name] "
                "where the customer name should appear."
            ))
        catalog = build_field_catalog(self.env, self.target_model_name, depth=1)
        if not catalog:
            raise UserError(_("Target model %s has no exposable fields.", self.target_model_name))

        # Index catalog so the line dropdown can pick from it
        Catalog = self.env['realestate.placeholder.catalog.line']
        # Discard old lines
        self.line_ids = [(5, 0, 0)]

        lines = []
        for tok in tokens:
            row, conf = match_token(tok, catalog)
            lines.append((0, 0, {
                'token': tok,
                'suggested_label': row['label'] if row else '',
                'suggested_jinja': row['jinja'] if row else '',
                'mapped_jinja': row['jinja'] if (row and conf >= 50) else '',
                'confidence': conf if row else 0,
                'accepted': bool(row and conf >= 50),
            }))
        self.write({
            'state': 'review',
            'line_ids': lines,
            'scan_summary': _("Found %s token(s).", len(tokens)),
        })
        return self._reopen()

    def action_accept_high_confidence(self):
        self.ensure_one()
        for line in self.line_ids:
            if line.confidence >= 75 and line.suggested_jinja:
                line.write({
                    'accepted': True,
                    'mapped_jinja': line.suggested_jinja,
                })
        return self._reopen()

    def action_apply(self):
        self.ensure_one()
        mapping = {}
        for line in self.line_ids:
            if line.accepted and line.mapped_jinja:
                mapping[line.token] = line.mapped_jinja
        if not mapping:
            raise UserError(_("No mappings accepted — nothing to rewrite."))

        raw = base64.b64decode(self.template_id.docx_template)
        new_bytes = rewrite_docx(raw, mapping)

        unmapped = sum(1 for l in self.line_ids if not l.accepted or not l.mapped_jinja)
        encoded = base64.b64encode(new_bytes)

        # Write back into the template so the next render uses the new file
        old_name = self.template_id.docx_template_filename or 'template.docx'
        if not old_name.lower().endswith('.docx'):
            old_name = old_name + '.docx'
        new_name = old_name.replace('.docx', '_placeholders.docx')

        self.template_id.write({
            'docx_template': encoded,
            'docx_template_filename': new_name,
        })

        self.write({
            'state': 'applied',
            'new_docx': encoded,
            'new_docx_filename': new_name,
            'applied_count': len(mapping),
            'unmapped_count': unmapped,
        })
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class PlaceholderAutoFillLine(models.TransientModel):
    _name = 'realestate.placeholder.autofill.line'
    _description = 'Auto-Fill Mapping Line'
    _order = 'confidence desc, token'

    wizard_id = fields.Many2one(
        'realestate.placeholder.autofill.wizard', required=True, ondelete='cascade',
    )
    token = fields.Char(required=True, readonly=True)
    suggested_label = fields.Char(readonly=True)
    suggested_jinja = fields.Char(readonly=True)
    confidence = fields.Integer(readonly=True)
    mapped_jinja = fields.Char(
        string='Placeholder',
        help="The Jinja expression that will replace [Token] in the document.",
    )
    accepted = fields.Boolean(default=False)
