from odoo import fields, models


class Phase(models.Model):
    _name = 'realestate.phase'
    _description = 'Project Phase'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, sequence, name'

    name = fields.Char(string='Phase Name', required=True, tracking=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(string='Code', tracking=True)
    project_id = fields.Many2one('realestate.project', string='Project', required=True, ondelete='cascade', tracking=True)
    # A phase has no company of its own: it belongs to whichever company owns
    # the project. Storing the related value keeps the isolation rule below a
    # plain indexed column comparison instead of a join, which is why it is
    # stored rather than computed on the fly. Carried over unchanged.
    company_id = fields.Many2one(
        'res.company', related='project_id.company_id', store=True, index=True,
        readonly=True,
        help="Always the project's company; a phase cannot belong elsewhere.",
    )
    state = fields.Selection([
        ('planning', 'Planning'),
        ('construction', 'Under Construction'),
        ('ready', 'Ready'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ], default='planning', tracking=True, required=True)

    start_date = fields.Date(string='Start Date', tracking=True)
    expected_completion_date = fields.Date(string='Expected Completion', tracking=True)
    expected_delivery_date = fields.Date(string='Expected Delivery', tracking=True)

    description = fields.Html()

    # ``property_ids`` and the unit counters are not declared here, for the same
    # reason they are absent from ``realestate.project``: they read
    # ``realestate.property``, which belongs above this module. See
    # ``real_estate_developer/models/phase_units.py``.

    def action_set_construction(self):
        self.write({'state': 'construction'})

    def action_set_ready(self):
        self.write({'state': 'ready'})

    def action_set_delivered(self):
        self.write({'state': 'delivered'})
