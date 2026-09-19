# -*- coding: utf-8 -*-
"""M2 / M20 — sales teams, and the project scope they carry.

No `realestate.sales.team` is created. Odoo's `crm.team` already provides
teams, and `crm.team.member` already provides multi-team membership — the two
things M2 asks for — so building a third team model would repeat the mistake
Phase 0 found with leads.

What CRM does *not* have is the notion that a team is authorised to sell
certain **projects**. That is added here, and it is what closes the gap Module
2 recorded as intentionally deferred:

> *"Phase 46 (project/team scoping) — agent-level scoping live; project/team
> scoping needs a project↔user assignment model and `crm` isn't a dependency."*

`crm` is a dependency now, so the assignment model is simply the team.
"""

from odoo import _, api, fields, models


class CrmTeam(models.Model):
    _inherit = 'crm.team'

    allowed_realestate_project_ids = fields.Many2many(
        'realestate.project', 'crm_team_realestate_project_rel',
        'team_id', 'project_id', string='Authorised Projects',
        help="Projects this team may sell. Leave empty for a team that is not "
             "restricted by project — an unrestricted team is the default, so "
             "adding this field changes nothing until somebody uses it.")
    realestate_project_scoped = fields.Boolean(
        string='Restrict to Authorised Projects',
        compute='_compute_realestate_project_scoped', store=True,
        help="True once the team names at least one project. The record rules "
             "read this rather than testing an empty relation, so an "
             "unrestricted team costs nothing at query time.")
    is_realestate_team = fields.Boolean(
        string='Real Estate Team',
        help="Marks this team as selling property. Real-estate views and "
             "reports filter on it, so a generic sales team in the same "
             "database is unaffected.")

    @api.depends('allowed_realestate_project_ids')
    def _compute_realestate_project_scoped(self):
        for team in self:
            team.realestate_project_scoped = bool(
                team.allowed_realestate_project_ids)

    # ------------------------------------------------------------------
    # The authority question, asked in one place
    # ------------------------------------------------------------------
    @api.model
    def _re_allowed_project_ids_for_user(self, user=None):
        """Projects the user may work, across every team they belong to.

        Returns `None` — deliberately not an empty list — when the user is
        unrestricted, so a caller can tell "may see everything" apart from "may
        see nothing". Conflating those two is how project scoping usually ends
        up either useless or locking everyone out.

        **On Odoo's multi-team behaviour**, which M2 said to test before
        relying on: multiple memberships are OFF by default in Odoo 18, gated
        on the `sales_team.membership_multi` config parameter. In the default
        mono-membership mode, adding a user to a second team **archives** their
        first membership (`crm.team.member._constrains_membership`), so a user
        belongs to exactly one team and the union below is that team's
        projects. Both modes are correct here and both are tested; this method
        does not require multi-membership to be enabled, and does not enable it.
        """
        user = user or self.env.user
        teams = self.sudo().search([
            # `in` takes a LIST in Odoo 18; a bare id raises
            # "Invalid domain term". This is the same shape Odoo's own
            # `crm.team._search` uses.
            '|', ('user_id', '=', user.id), ('member_ids', 'in', [user.id]),
        ])
        if not teams:
            return None
        scoped = teams.filtered('realestate_project_scoped')
        if not scoped:
            return None
        # A user on two teams may work the union of their projects: being added
        # to a second team grants access, it does not intersect it away.
        return scoped.allowed_realestate_project_ids.ids

    def _re_check_project_allowed(self, project):
        """Raise unless this team may sell that project."""
        self.ensure_one()
        if not self.realestate_project_scoped or not project:
            return True
        if project.id in self.allowed_realestate_project_ids.ids:
            return True
        from odoo.exceptions import UserError
        raise UserError(_(
            "Team %(team)s is not authorised to sell project %(project)s.",
            team=self.display_name, project=project.display_name))
