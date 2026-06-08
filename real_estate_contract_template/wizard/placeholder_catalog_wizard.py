from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.placeholder_helpers import build_field_catalog


class PlaceholderCatalogWizard(models.TransientModel):
    _name = 'realestate.placeholder.catalog.wizard'
    _description = 'Available Placeholders for a Template'

    template_id = fields.Many2one('realestate.contract.template')
    target_model_id = fields.Many2one('ir.model', required=True)
    target_model_name = fields.Char(related='target_model_id.model', readonly=True)
    line_ids = fields.One2many(
        'realestate.placeholder.catalog.line', 'wizard_id', readonly=True,
    )

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        if self._context.get('default_template_id'):
            tpl = self.env['realestate.contract.template'].browse(
                self._context['default_template_id'])
            if tpl.target_model_id:
                vals['target_model_id'] = tpl.target_model_id.id
        return vals

    @api.onchange('target_model_id')
    def _onchange_target_model(self):
        self.line_ids = [(5, 0, 0)]
        if not self.target_model_id:
            return
        catalog = build_field_catalog(self.env, self.target_model_id.model, depth=1)
        self.line_ids = [(0, 0, {
            'sequence': i,
            'group': row['group'],
            'label': row['label'],
            'jinja': row['jinja'],
            'field_type': row['type'],
            'help_text': row['help'] or '',
        }) for i, row in enumerate(catalog)]


class PlaceholderCatalogLine(models.TransientModel):
    _name = 'realestate.placeholder.catalog.line'
    _description = 'Placeholder Catalog Line'
    _order = 'group, label'

    wizard_id = fields.Many2one('realestate.placeholder.catalog.wizard', required=True, ondelete='cascade')
    sequence = fields.Integer()
    group = fields.Char(readonly=True)
    label = fields.Char(readonly=True)
    jinja = fields.Char(readonly=True, string='Placeholder')
    field_type = fields.Char(readonly=True)
    help_text = fields.Text(readonly=True, string='Help')
