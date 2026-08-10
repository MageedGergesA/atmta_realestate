# -*- coding: utf-8 -*-
"""Data-control exceptions — the things a controller has to fix.

These are not project failures. They are places where the control data does
not yet support the control question: a purchase order with no cost code, a
cost code with no forecast, a package with no completion baseline. Every one
of them makes some number on the cost sheet less trustworthy than it looks,
which is exactly why they are listed rather than smoothed over.

Each exception is a class, a count, a sentence and an action. There is no
generic "8 warnings" badge — a warning nobody can open is decoration.
"""
from odoo import _, api, fields, models

#: Deterministic classes. A consumer can group by these without guessing.
CLASSES = ('financial', 'forecast', 'commercial', 'quality', 'document',
           'configuration')


class ConstructionExceptions(models.AbstractModel):
    _name = 'realestate.construction.exceptions'
    _description = 'Construction Data-Control Exceptions'

    @api.model
    def for_project(self, project):
        checks = [
            self._uncoded_purchase_lines,
            self._unassigned_actual,
            self._missing_forecast_lines,
            self._stale_forecast,
            self._no_baseline_budget,
            self._approved_changes_not_implemented,
            self._packages_without_orders,
            self._missing_completion_baseline,
            self._missing_control_accounts,
            self._boq_without_cost_codes,
            self._overdue_quality,
            self._documents_without_classification,
        ]
        exceptions = []
        for check in checks:
            result = check(project)
            if result and result.get('count'):
                exceptions.append(result)
        return {
            'exceptions': exceptions,
            'count': sum(e['count'] for e in exceptions),
            'by_class': {
                cls: sum(e['count'] for e in exceptions
                         if e['class'] == cls)
                for cls in CLASSES},
        }

    # ------------------------------------------------------------------
    def _entry(self, key, cls, count, message, model, domain):
        return {
            'key': key,
            'class': cls,
            'count': count,
            'message': message,
            'action': {
                'type': 'ir.actions.act_window',
                'name': message,
                'res_model': model,
                'domain': domain,
                'view_mode': 'list,form',
                'views': [[False, 'list'], [False, 'form']],
                'target': 'current',
                'context': {'create': False},
            },
        }

    # -- financial ------------------------------------------------------
    def _uncoded_purchase_lines(self, project):
        domain = [('order_id.re_project_id', '=', project.id),
                  ('order_id.state', 'in', ('purchase', 'done')),
                  ('re_cost_code_id', '=', False)]
        count = self.env['purchase.order.line'].search_count(domain)
        return self._entry(
            'uncoded_po_lines', 'financial', count,
            _("%s committed purchase line(s) carry no cost code, so their "
              "commitment sits under Unassigned.") % count,
            'purchase.order.line', domain)

    def _unassigned_actual(self, project):
        rows = self.env['realestate.construction.cost.sheet'].rows_for(project)
        unassigned = [row for row in rows if row['is_unassigned']
                      and row['actual_cost']]
        if not unassigned:
            return None
        amount = sum(row['actual_cost'] for row in unassigned)
        return self._entry(
            'unassigned_actual', 'financial', 1,
            _("%s of posted cost carries no cost code.")
            % project.currency_id.format(amount),
            'account.analytic.line',
            self.env['realestate.construction.cost.sheet']
            ._actual_drilldown_domain(project, 0))

    # -- forecast -------------------------------------------------------
    def _missing_forecast_lines(self, project):
        forecast = self.env[
            'realestate.construction.forecast'].latest_approved(project)
        if not forecast:
            return None
        domain = [('forecast_id', '=', forecast.id), ('has_etc', '=', False)]
        count = self.env[
            'realestate.construction.forecast.line'].search_count(domain)
        return self._entry(
            'missing_forecast', 'forecast', count,
            _("%s cost code(s) have no estimate to complete. Their EAC is "
              "unknown, not zero.") % count,
            'realestate.construction.forecast.line', domain)

    def _stale_forecast(self, project):
        Tower = self.env['realestate.construction.control.tower']
        forecast = self.env[
            'realestate.construction.forecast'].latest_approved(project)
        if not forecast or not forecast.as_of_date:
            return None
        age = (fields.Date.context_today(self) - forecast.as_of_date).days
        threshold = Tower.thresholds()['forecast_stale_days']
        if age <= threshold:
            return None
        return self._entry(
            'stale_forecast', 'forecast', 1,
            _("The approved forecast is %(age)s days old (threshold "
              "%(threshold)s). It is not wrong — it is simply not current.",
              age=age, threshold=threshold),
            'realestate.construction.forecast',
            [('project_id', '=', project.id), ('state', '=', 'approved')])

    def _no_baseline_budget(self, project):
        budget = self.env[
            'realestate.construction.budget'].current_for(project)
        if budget:
            return None
        return self._entry(
            'no_baseline_budget', 'financial', 1,
            _("No baselined budget. Every budget figure on this project is "
              "absent rather than zero."),
            'realestate.construction.budget',
            [('project_id', '=', project.id)])

    # -- commercial -----------------------------------------------------
    def _approved_changes_not_implemented(self, project):
        domain = [('project_id', '=', project.id), ('state', '=', 'approved')]
        count = self.env[
            'realestate.construction.change.order'].search_count(domain)
        return self._entry(
            'approved_not_implemented', 'commercial', count,
            _("%s change order(s) are approved but not implemented, so the "
              "baseline does not yet reflect them.") % count,
            'realestate.construction.change.order', domain)

    def _packages_without_orders(self, project):
        packages = self.env[
            'realestate.construction.contract.package'].search([
                ('project_id', '=', project.id),
                ('state', 'in', ('awarded', 'active'))])
        without = packages.filtered(lambda p: not p.purchase_order_ids)
        if not without:
            return None
        return self._entry(
            'packages_without_orders', 'commercial', len(without),
            _("%s awarded package(s) have no purchase order. Their "
              "commitment comes from the package itself.") % len(without),
            'realestate.construction.contract.package',
            [('id', 'in', without.ids)])

    def _missing_completion_baseline(self, project):
        packages = self.env[
            'realestate.construction.contract.package'].search([
                ('project_id', '=', project.id),
                ('state', 'in', ('awarded', 'active')),
                ('original_completion_date', '=', False)])
        if not packages:
            return None
        return self._entry(
            'missing_completion_baseline', 'commercial', len(packages),
            _("%s awarded package(s) have no original completion date, so no "
              "extension of time can be implemented against them.")
            % len(packages),
            'realestate.construction.contract.package',
            [('id', 'in', packages.ids)])

    # -- configuration --------------------------------------------------
    def _missing_control_accounts(self, project):
        company = project.company_id or self.env.company
        missing = [field for field in (
            'construction_retention_account_id',
            'construction_advance_account_id') if not company[field]]
        if not missing:
            return None
        return self._entry(
            'missing_control_accounts', 'configuration', len(missing),
            _("%s construction control account(s) are not configured. "
              "Certificates will refuse to post retention or advance "
              "recovery until they are.") % len(missing),
            'res.company', [('id', '=', company.id)])

    def _boq_without_cost_codes(self, project):
        domain = [('project_id', '=', project.id),
                  ('boq_id.state', 'in', ('approved', 'locked')),
                  ('cost_code_id', '=', False)]
        count = self.env['realestate.boq.line'].search_count(domain)
        return self._entry(
            'boq_without_cost_code', 'commercial', count,
            _("%s approved BOQ line(s) carry no cost code, so certified work "
              "on them cannot reach the cost report.") % count,
            'realestate.boq.line', domain)

    # -- quality / document ---------------------------------------------
    def _overdue_quality(self, project):
        NCR = self.env['realestate.construction.ncr']
        domain = [('project_id', '=', project.id), ('is_overdue', '=', True)]
        count = NCR.search_count(domain)
        return self._entry(
            'overdue_ncrs', 'quality', count,
            _("%s non-conformance report(s) are past their target date.")
            % count, 'realestate.construction.ncr', domain)

    def _documents_without_classification(self, project):
        domain = [('project_id', '=', project.id),
                  ('discipline', '=', False)]
        count = self.env[
            'realestate.construction.document'].search_count(domain)
        return self._entry(
            'documents_unclassified', 'document', count,
            _("%s document(s) have no discipline, so they are hard to find "
              "and easy to miss in a transmittal.") % count,
            'realestate.construction.document', domain)
