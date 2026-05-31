from odoo import fields, models


CATEGORIES = [
    ('utilities', 'Utilities'),
    ('inspection', 'Inspection'),
    ('cleaning', 'Cleaning'),
    ('documents', 'Documents'),
    ('keys', 'Keys'),
    ('safety', 'Safety'),
    ('other', 'Other'),
]


class ChecklistTemplate(models.Model):
    _name = 'realestate.handover.checklist.template'
    _description = 'Handover Checklist Template'
    _order = 'name'

    name = fields.Char(required=True)
    description = fields.Text()
    active = fields.Boolean(default=True)
    item_ids = fields.One2many('realestate.handover.checklist.template.item', 'template_id', string='Items')


class ChecklistTemplateItem(models.Model):
    _name = 'realestate.handover.checklist.template.item'
    _description = 'Handover Checklist Template Item'
    _order = 'template_id, sequence, id'

    template_id = fields.Many2one('realestate.handover.checklist.template', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Item', required=True)
    category = fields.Selection(CATEGORIES, default='other', required=True)
    description = fields.Text()
