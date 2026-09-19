from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    is_realestate_agent = fields.Boolean(string='Is Real Estate Agent')
    agent_license_number = fields.Char(string='Agent License #')
    commission_share_default = fields.Float(
        string='Default Commission Share (%)',
        help='Default commission percentage applied to this agent on new transactions.',
    )

    # ------------------------------------------------------------------
    # M20 — project authority, in a shape a record rule can use
    # ------------------------------------------------------------------
    re_allowed_project_ids = fields.Many2many(
        'realestate.project', string='Authorised Projects',
        compute='_compute_re_allowed_projects',
        help="Every project this user may work, resolved from their sales "
             "teams.\n\n"
             "Resolves to *all* projects when the teams place no restriction. "
             "`crm.team._re_allowed_project_ids_for_user` returns None for "
             "that case, which is the right answer for Python and a useless "
             "one for an `ir.rule` domain — a rule cannot branch. Expanding it "
             "here means one domain serves both cases and neither "
             "'unrestricted' nor 'restricted to nothing' can be mistaken for "
             "the other.")
    re_project_scoped = fields.Boolean(
        string='Project Scoped', compute='_compute_re_allowed_projects',
        help="Whether any real restriction actually applies.")

    @api.depends('groups_id')
    def _compute_re_allowed_projects(self):
        Project = self.env['realestate.project'].sudo()
        Team = self.env['crm.team']
        for user in self:
            allowed = Team._re_allowed_project_ids_for_user(user)
            if allowed is None:
                user.re_allowed_project_ids = Project.search([])
                user.re_project_scoped = False
            else:
                user.re_allowed_project_ids = Project.browse(allowed)
                user.re_project_scoped = True
