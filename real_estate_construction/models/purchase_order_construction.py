# -*- coding: utf-8 -*-
"""M2 — project-control coding on purchase documents.

Procurement already stamps `re_project_id` and a project analytic distribution
(`real_estate_procurement`). What it cannot do is say *which part of the works*
and *what kind of money*, because those concepts did not exist until M1. These
fields add that, and nothing else: Rule 2 keeps requisitions, RFQs, receipts and
approvals in Procurement, and this module only reads what they produce.

The analytic distribution is extended rather than replaced — the project side
is Procurement's and stays exactly as it was; the cost-code side is added
beside it in its own plan.
"""

from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    re_package_id = fields.Many2one(
        'realestate.construction.contract.package',
        string='Construction Package', index=True, ondelete='set null',
        help="The commercial package this order executes. An order that "
             "belongs to a package is *the* commitment for it — the package's "
             "own value is then a comparison, not an addition.",
    )

    @api.onchange('re_package_id')
    def _onchange_re_package_id(self):
        if self.re_package_id:
            self.re_project_id = self.re_package_id.project_id
            if not self.partner_id and self.re_package_id.partner_id:
                self.partner_id = self.re_package_id.partner_id


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    re_wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', index=True,
        ondelete='restrict',
        help="Where in the works this line belongs.",
    )
    re_cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', string='Cost Code', index=True,
        ondelete='restrict',
        help="What kind of money. One order routinely spans several codes; "
             "commitment is aggregated from these, never from the header.",
    )

    @api.onchange('re_cost_code_id', 're_wbs_id')
    def _onchange_re_coding(self):
        """Keep the analytic distribution in step with the coding.

        Written as an onchange rather than a compute so that a distribution
        somebody set by hand is never silently overwritten by a later edit
        elsewhere on the line.
        """
        for line in self:
            project = line.order_id.re_project_id
            if not project:
                continue
            distribution = self.env[
                'realestate.construction.analytic'].distribution_for(
                    project, line.re_cost_code_id)
            if distribution:
                line.analytic_distribution = distribution

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._apply_construction_analytic()
        return lines

    def _apply_construction_analytic(self):
        """Stamp the cost-code analytic account on lines that have a code.

        Only when the line does not already carry that plan's account, so a
        deliberate manual distribution survives.
        """
        Analytic = self.env['realestate.construction.analytic']
        plan_column = None
        for line in self:
            project = line.order_id.re_project_id
            if not (project and line.re_cost_code_id):
                continue
            account = line.re_cost_code_id._get_or_create_analytic_account()
            if plan_column is None:
                plan_column = str(account.id)
            distribution = dict(line.analytic_distribution or {})
            if str(account.id) in distribution:
                continue
            distribution.update(
                Analytic.distribution_for(project, line.re_cost_code_id) or {})
            line.analytic_distribution = distribution
