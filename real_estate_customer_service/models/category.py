from odoo import fields, models


class TicketCategory(models.Model):
    _name = 'realestate.customer.ticket.category'
    _description = 'Customer Ticket Category'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    default_sla_hours = fields.Integer(
        string='Default SLA (hours)', default=24,
        help='Default resolution SLA when a ticket is created in this category.',
    )
    default_assignee_id = fields.Many2one(
        'res.users', string='Default Assignee',
        help='Optional. Auto-assigned to any new ticket in this category.',
    )
    active = fields.Boolean(default=True)
