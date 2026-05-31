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


class ChecklistItem(models.Model):
    _name = 'realestate.handover.checklist.item'
    _description = 'Handover Checklist Item'
    _order = 'handover_id, sequence, id'

    handover_id = fields.Many2one('realestate.handover', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Item', required=True)
    category = fields.Selection(CATEGORIES, default='other')
    description = fields.Text()
    status = fields.Selection([
        ('pending', 'Pending'),
        ('done', 'Done'),
        ('skipped', 'Skipped'),
        ('failed', 'Failed'),
    ], default='pending', required=True)
    completed_at = fields.Datetime(readonly=True)
    completed_by_id = fields.Many2one('res.users', readonly=True)
    notes = fields.Text()

    def action_mark_done(self):
        self.write({
            'status': 'done',
            'completed_at': fields.Datetime.now(),
            'completed_by_id': self.env.user.id,
        })

    def action_mark_skipped(self):
        self.write({'status': 'skipped'})

    def action_mark_failed(self):
        self.write({'status': 'failed'})

    def action_reset(self):
        self.write({'status': 'pending', 'completed_at': False, 'completed_by_id': False})
