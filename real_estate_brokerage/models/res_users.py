from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    is_realestate_agent = fields.Boolean(string='Is Real Estate Agent')
    agent_license_number = fields.Char(string='Agent License #')
    commission_share_default = fields.Float(
        string='Default Commission Share (%)',
        help='Default commission percentage applied to this agent on new transactions.',
    )
