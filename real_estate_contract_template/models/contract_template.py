import base64
import io
import logging
import os
import shutil
import subprocess
import tempfile

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from docxtpl import DocxTemplate, InlineImage
    from docx.shared import Mm
except ImportError:
    DocxTemplate = None
    InlineImage = None
    Mm = None


JURISDICTION_SELECTION = [
    ('eg', 'Egypt'),
    ('sa', 'Saudi Arabia'),
    ('ae_dubai', 'UAE — Dubai'),
    ('ae_abudhabi', 'UAE — Abu Dhabi'),
    ('generic', 'Generic / Pan-MENA'),
]

KIND_SELECTION = [
    ('sale', 'Off-plan / Unit Sale'),
    ('rental', 'Rental / Lease'),
    ('brokerage', 'Brokerage / Listing'),
    ('subcontract', 'Construction Subcontract'),
    ('other', 'Other'),
]

LANGUAGE_SELECTION = [
    ('en', 'English'),
    ('ar', 'Arabic'),
    ('bilingual', 'Bilingual (Arabic + English)'),
]


class ContractTemplate(models.Model):
    _name = 'realestate.contract.template'
    _description = 'Real Estate Contract Template'
    _order = 'kind, jurisdiction, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(help="Short identifier, e.g. sale-egypt-bilingual-v1.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    kind = fields.Selection(KIND_SELECTION, required=True, default='sale')
    jurisdiction = fields.Selection(JURISDICTION_SELECTION, required=True, default='generic')
    language = fields.Selection(LANGUAGE_SELECTION, required=True, default='bilingual')

    target_model_id = fields.Many2one(
        'ir.model', required=True, ondelete='cascade',
        domain=[('transient', '=', False)],
        help="The Odoo model this template renders against (e.g. realestate.sale.contract).",
    )
    target_model_name = fields.Char(related='target_model_id.model', store=True, readonly=True)

    docx_template = fields.Binary(string='DOCX Template', required=True, attachment=True)
    docx_template_filename = fields.Char(string='DOCX Filename')

    form_version = fields.Char(
        default='v1',
        help="Stamped onto every generated contract so regulator-form refreshes "
             "don't break previously-issued documents.",
    )
    notes = fields.Html(help="Internal notes — instructions for the user, placeholder reference, etc.")

    last_used = fields.Datetime(readonly=True)
    use_count = fields.Integer(readonly=True, default=0)

    _sql_constraints = [
        ('code_company_unique', 'UNIQUE(code, company_id)',
         "Template code must be unique per company."),
    ]

    @api.constrains('docx_template_filename')
    def _check_docx_extension(self):
        for rec in self:
            if rec.docx_template_filename and not rec.docx_template_filename.lower().endswith('.docx'):
                raise UserError(_("Template file must be a .docx (got %s).", rec.docx_template_filename))

    def render(self, record, output_format='docx'):
        """Render this template against `record`. Returns (bytes, filename)."""
        self.ensure_one()
        if not record or not record.exists():
            raise UserError(_("No record to render against."))
        if record._name != self.target_model_name:
            raise UserError(_(
                "Template '%(tpl)s' targets %(expected)s but was called on %(actual)s.",
                tpl=self.name, expected=self.target_model_name, actual=record._name,
            ))

        docx_bytes = self._render_docx(record)
        base_name = self._build_filename(record)

        if output_format == 'docx':
            payload = docx_bytes
            filename = f"{base_name}.docx"
        elif output_format == 'pdf':
            payload = self._docx_to_pdf(docx_bytes)
            filename = f"{base_name}.pdf"
        else:
            raise UserError(_("Unknown output format: %s", output_format))

        self.sudo().write({
            'last_used': fields.Datetime.now(),
            'use_count': self.use_count + 1,
        })
        return payload, filename

    def _render_docx(self, record):
        """Fill the docx template with the record's data via Jinja2."""
        if DocxTemplate is None:
            raise UserError(_(
                "The Python package 'docxtpl' is not installed. "
                "Run: pip install docxtpl"
            ))
        if not self.docx_template:
            raise UserError(_("Template '%s' has no DOCX file attached.", self.name))

        raw = base64.b64decode(self.docx_template)
        doc = DocxTemplate(io.BytesIO(raw))
        try:
            doc.render(self._build_context(record, tpl=doc))
        except Exception as e:
            _logger.exception("docxtpl render failed for template %s on %s(%s)",
                              self.id, record._name, record.id)
            raise UserError(_("Template render failed: %s", e)) from e

        out = io.BytesIO()
        doc.save(out)
        return out.getvalue()

    def _build_context(self, record, tpl=None):
        """Return the Jinja2 context. record is exposed directly; common odoo
        objects + the template's own metadata are added for convenience.

        If `tpl` is the docxtpl DocxTemplate instance and the company has a
        logo, an InlineImage `company_logo` is added to the context so the
        template can place `{{ company_logo }}` in headers / cover pages."""
        ctx = {
            'record': record,
            'doc': record,
            'company': self.env.company,
            'user': self.env.user,
            'today': fields.Date.context_today(self),
            'now': fields.Datetime.context_timestamp(self, fields.Datetime.now()),
            'template_name': self.name,
            'template_version': self.form_version or '',
            'jurisdiction': self.jurisdiction,
            'language': self.language,
            'company_logo': '',
        }
        if tpl is not None and InlineImage is not None and self.env.company.logo:
            try:
                logo_tmp = tempfile.NamedTemporaryFile(
                    suffix='.png', prefix='odoo_company_logo_', delete=False)
                logo_tmp.write(base64.b64decode(self.env.company.logo))
                logo_tmp.close()
                ctx['company_logo'] = InlineImage(tpl, logo_tmp.name, width=Mm(35))
            except Exception:
                _logger.exception("Failed to attach company logo to template context")
        return ctx

    def _build_filename(self, record):
        name = (record.display_name or record._name).replace('/', '_').replace(' ', '_')
        lang = self.language or 'lang'
        return f"{self.kind}_{lang}_{name}_{self.form_version or 'v1'}"

    def _generate_for_record(self, record, output_format='docx'):
        """Alias kept for callers that want a clearer name than `render`."""
        return self.render(record, output_format=output_format)

    def _docx_to_pdf(self, docx_bytes):
        """Convert DOCX bytes → PDF bytes via LibreOffice headless."""
        soffice = shutil.which('libreoffice') or shutil.which('soffice')
        if not soffice:
            raise UserError(_(
                "LibreOffice is not installed on the server. "
                "Install it to enable PDF export (apt install libreoffice)."
            ))
        with tempfile.TemporaryDirectory(prefix='odoo_contract_pdf_') as tmpdir:
            docx_path = os.path.join(tmpdir, 'in.docx')
            with open(docx_path, 'wb') as f:
                f.write(docx_bytes)
            try:
                result = subprocess.run(
                    [soffice, '--headless', '--convert-to', 'pdf',
                     '--outdir', tmpdir, docx_path],
                    capture_output=True, timeout=120, check=False,
                    env={**os.environ, 'HOME': tmpdir},
                )
            except subprocess.TimeoutExpired:
                raise UserError(_("PDF conversion timed out after 120s."))
            if result.returncode != 0:
                _logger.error("libreoffice stderr: %s", result.stderr.decode('utf-8', 'replace'))
                raise UserError(_("LibreOffice PDF conversion failed (exit %s).", result.returncode))
            pdf_path = os.path.join(tmpdir, 'in.pdf')
            if not os.path.exists(pdf_path):
                raise UserError(_("LibreOffice ran but produced no PDF output."))
            with open(pdf_path, 'rb') as f:
                return f.read()

    def action_open_placeholder_catalog(self):
        self.ensure_one()
        if not self.target_model_id:
            raise UserError(_("Pick a target model first."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.placeholder.catalog.wizard',
            'view_mode': 'form',
            'target': 'new',
            'name': _('Available Placeholders'),
            'context': {'default_template_id': self.id},
        }

    def action_open_placeholder_autofill(self):
        self.ensure_one()
        if not self.docx_template:
            raise UserError(_("Upload a .docx on the template first."))
        if not self.target_model_id:
            raise UserError(_("Pick a target model first."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.placeholder.autofill.wizard',
            'view_mode': 'form',
            'target': 'new',
            'name': _('Auto-Fill Placeholders'),
            'context': {'default_template_id': self.id},
        }

    def action_open_target_records(self):
        """Manager-side test helper: jump to the list of records this template
        targets, so the user can pick one and click 'Generate Document' from
        the record's own header. That's the only flow that can render — the
        wizard needs a real source record."""
        self.ensure_one()
        if not self.target_model_id:
            raise UserError(_("This template has no target model."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.target_model_name,
            'view_mode': 'list,form',
            'name': _('Open a %s and click "Generate Document"', self.target_model_id.name),
            'target': 'current',
        }
