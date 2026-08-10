# -*- coding: utf-8 -*-
"""Project-control coding on procurement demand.

Construction owns WBS and cost codes, and Construction depends on Procurement
rather than the other way round — so the foreign keys have to live here, in
exactly the same way `purchase_order_construction.py` already adds
`re_wbs_id` and `re_cost_code_id` to `purchase.order.line`.

Procurement owns the requisition, the plan and the sourcing workflow. This
file adds the two dimensions those documents need in order to be legible to
the cost report, and one override that carries them onto the purchase order
line. Nothing here changes a Construction control figure: a requisition is
demand, and demand is not commitment.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class ProcurementPlanLine(models.Model):
    _inherit = 'realestate.procurement.plan.line'

    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='restrict',
        index=True, domain="[('project_id', '=', project_id)]",
        help="Where in the works the planned item belongs.")
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', string='Cost Code',
        ondelete='restrict', index=True,
        help="What kind of money it will be when it is spent.")

    @api.constrains('wbs_id', 'plan_id')
    def _check_wbs_project(self):
        for line in self:
            if line.wbs_id and line.wbs_id.project_id != line.plan_id.project_id:
                raise ValidationError(_(
                    "%(wbs)s belongs to another project.",
                    wbs=line.wbs_id.display_name))

    def _prepare_requisition_line_values(self):
        values = super()._prepare_requisition_line_values()
        values.update({
            'wbs_id': self.wbs_id.id or False,
            'cost_code_id': self.cost_code_id.id or False,
        })
        return values


class MaterialRequest(models.Model):
    _inherit = 'realestate.material.request'

    change_order_id = fields.Many2one(
        'realestate.construction.change.order', string='Change Order',
        ondelete='set null', index=True, check_company=True,
        help="Set when the need arises from an approved change. Recorded "
             "here; what it means for budget authority is M3's question.")
    boq_line_id = fields.Many2one(
        'realestate.boq.line', string='BOQ Line', ondelete='set null',
        help="The bill-of-quantities line the demand came from. A reference, "
             "not a copy — the contractual quantity stays on the BOQ.")

    #: Header coding is a default for new lines and nothing more. The line
    #: value is what reaches the purchase order, because one requisition
    #: routinely spans several codes.
    default_wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='Default WBS',
        ondelete='set null', domain="[('project_id', '=', project_id)]")
    default_cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', string='Default Cost Code',
        ondelete='set null')

    @api.model_create_multi
    def create(self, vals_list):
        requests = super().create(vals_list)
        requests._set_construction_source()
        return requests

    def _set_construction_source(self):
        """Name the source when the record already says what it is.

        Only where it is unambiguous, and never over an answer somebody gave:
        a request that names a change order came from that change order, and
        one that names a BOQ line came from the BOQ. Everything else keeps
        whatever classification it was created with, because guessing between
        'site request' and 'construction works' from a task reference would be
        inventing provenance.
        """
        construction_sources = ('realestate.construction.task',
                                'realestate.construction.milestone')
        for rec in self:
            if rec.source_type not in ('manual', False):
                continue
            if rec.change_order_id:
                rec.source_type = 'change_order'
            elif rec.boq_line_id:
                rec.source_type = 'boq'
            elif rec.source_model in construction_sources:
                rec.source_type = 'construction'

    @api.constrains('project_id')
    def _check_project_team_membership(self):
        """Procurement follows the project access Construction already has.

        A project with a named construction team is that team's; a project
        without one is open. Procurement keeps no second list of allowed
        projects on the user — there is one answer to "may this person work on
        this project", and it lives on the project.
        """
        for rec in self:
            rec.project_id._check_construction_membership(_(
                "requisition %s") % (rec.name or _('New')))

    @api.depends('line_ids', 'line_ids.cost_code_id', 'line_ids.wbs_id')
    def _compute_coding_coverage(self):
        """Same computation, now watching the fields it is about.

        Procurement declares this compute without being able to name the two
        fields — they are added here. Re-declaring the dependencies is the
        only way the coverage figures refresh when somebody codes a line.
        """
        return super()._compute_coding_coverage()


class MaterialRequestLine(models.Model):
    _inherit = 'realestate.material.request.line'

    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='restrict',
        index=True, domain="[('project_id', '=', project_id)]",
        help="Where in the works this line belongs.")
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', string='Cost Code',
        ondelete='restrict', index=True,
        help="What kind of money. Carried onto the purchase order line so the "
             "eventual commitment reaches the cost report's own dimension "
             "instead of Unassigned.")

    @api.onchange('request_id')
    def _onchange_request_coding_default(self):
        for line in self:
            request = line.request_id
            if not line.wbs_id and request.default_wbs_id:
                line.wbs_id = request.default_wbs_id
            if not line.cost_code_id and request.default_cost_code_id:
                line.cost_code_id = request.default_cost_code_id

    @api.constrains('wbs_id', 'cost_code_id', 'request_id')
    def _check_coding_belongs_to_the_project(self):
        """A requisition cannot borrow another project's breakdown.

        Enforced here rather than only in a view domain: a domain helps
        whoever is typing, and this has to hold for imports, RPC and every
        other way a record arrives.
        """
        for line in self:
            project = line.request_id.project_id
            if line.wbs_id and project and line.wbs_id.project_id != project:
                raise ValidationError(_(
                    "%(wbs)s belongs to %(other)s, not to %(project)s.",
                    wbs=line.wbs_id.display_name,
                    other=line.wbs_id.project_id.display_name,
                    project=project.display_name))
            company = line.request_id.company_id
            code = line.cost_code_id
            if code and company and code.company_id \
                    and code.company_id != company:
                raise ValidationError(_(
                    "%(code)s belongs to %(other)s, not to %(company)s.",
                    code=code.display_name,
                    other=code.company_id.display_name,
                    company=company.display_name))


class ProjectMembershipCheck(models.Model):
    """The one question, asked in one place."""
    _inherit = 'realestate.project'

    def _check_construction_membership(self, subject):
        """Raise unless the current user may work on this project.

        A construction manager sees every project by design — that is already
        how Construction's own record rules read, and a second, stricter
        answer here would mean two different definitions of project access.
        """
        for project in self:
            members = project.sudo().construction_member_ids
            if not members:
                continue
            user = self.env.user
            if user in members or user.has_group(
                    'real_estate_construction.group_construction_manager'):
                continue
            raise AccessError(_(
                "%(project)s has a named construction team and you are not on "
                "it, so you cannot raise %(subject)s against it.",
                project=project.display_name, subject=subject))
        return True


class ProcurementReservation(models.Model):
    """M3 — the control dimensions of a reservation.

    Same architecture as the requisition line above and for the same reason:
    reservation is per cost code because that is the dimension Construction
    controls money in, and the foreign key has to live on this side of the
    dependency. Procurement's availability service groups by `cost_code_id`
    when it finds it and by nothing when it does not, so a Procurement-only
    install still works — it simply has one bucket instead of many.
    """
    _inherit = 'realestate.procurement.reservation'

    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='restrict',
        index=True, help="Where in the works the reserved demand belongs.")
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', string='Cost Code',
        ondelete='restrict', index=True,
        help="The budget line this reservation consumes capacity from. "
             "Blank means the demand was never coded, and the control "
             "position for it reads as unknown rather than as zero.")

    @api.constrains('cost_code_id', 'company_id')
    def _check_cost_code_company(self):
        for reservation in self:
            code = reservation.cost_code_id
            if code and code.company_id \
                    and code.company_id != reservation.company_id:
                raise ValidationError(_(
                    "%(code)s belongs to %(other)s, not to %(company)s.",
                    code=code.display_name,
                    other=code.company_id.display_name,
                    company=reservation.company_id.display_name))


class ProcurementControlException(models.Model):
    """M3F — the coding and the change order an override relates to."""
    _inherit = 'realestate.procurement.control.exception'

    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', string='Cost Code',
        ondelete='restrict', index=True,
        help="The budget position that was exceeded.")
    change_order_id = fields.Many2one(
        'realestate.construction.change.order', string='Change Order',
        ondelete='set null', index=True,
        help="Where the money is expected to come from, when a change order "
             "is the answer. Evidence and context only: the authoritative "
             "budget is still Construction's, and it moves when the change "
             "order is implemented — not when it is cited here.")


class MaterialRequestSourcing(models.Model):
    """The one override that closes the Phase 0 Unassigned defect."""
    _inherit = 'realestate.material.request'

    def _prepare_rfq_line(self, line):
        """Carry the project-control coding onto the enquiry.

        Procurement builds the commercial values; this adds the two dimensions
        that decide where the money lands. The analytic distribution itself is
        produced by `purchase.order.line.create()` in
        `purchase_order_construction.py` — one formula, in one place.
        """
        values = super()._prepare_rfq_line(line)
        values.update({
            're_wbs_id': line.wbs_id.id or False,
            're_cost_code_id': line.cost_code_id.id or False,
        })
        return values
