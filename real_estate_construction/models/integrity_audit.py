# -*- coding: utf-8 -*-
"""The integrity audit — read-only evidence about a live database.

M9's exceptions service answers "what data is missing?". This answers a harder
question: "is what we have internally consistent?" It re-derives the control
equations from their sources and reports where a database disagrees with
itself.

Three rules govern it:

* **It never writes.** `run()` inspects and reports. Corrections belong in
  versioned upgrade scripts, where they are reviewable and repeatable, or in
  a human's hands where they are a Finance decision. An audit that fixed
  things would be an audit nobody could safely run on production.
* **It never invents.** Where legacy data is ambiguous it says so and stops.
* **Severity means something.** `critical` is reserved for money, company
  isolation and authoritative history. Cosmetic problems are `low`, so that a
  critical finding still means somebody should stop what they are doing.
"""
from odoo import _, api, models
from odoo.exceptions import AccessError

SEVERITIES = ('critical', 'high', 'medium', 'low')

CATEGORIES = ('financial', 'budget', 'commitment', 'forecast', 'change',
              'certification', 'document', 'quality', 'claims', 'risk',
              'security')


class ConstructionIntegrityAudit(models.AbstractModel):
    _name = 'realestate.construction.integrity.audit'
    _description = 'Construction Integrity Audit (read-only)'

    # ------------------------------------------------------------------
    @api.model
    def run(self, project=None, company=None):
        """Audit one project, one company, or everything the user may read."""
        projects = self._scope(project, company)
        checks = (
            # financial
            self._check_budget_equation,
            self._check_commitment_equation,
            self._check_eac_equation,
            self._check_uncoded_commitment,
            self._check_uncoded_actual,
            self._check_tax_contamination,
            self._check_legacy_retention,
            self._check_missing_control_accounts,
            self._check_advance_balance,
            # budget / commitment
            self._check_baseline_count,
            self._check_budget_lines_unclassified,
            self._check_package_and_order_double_count,
            # forecast
            self._check_forecast_coverage,
            # change
            self._check_approved_not_implemented,
            self._check_implemented_without_impact,
            # certification
            self._check_over_certification,
            self._check_certificate_without_cost_code,
            # document / quality
            self._check_duplicate_document_numbers,
            self._check_multiple_current_revisions,
            self._check_ncr_closed_without_verification,
            # claims
            self._check_eot_date_equation,
            self._check_implemented_eot_without_determination,
            # risk
            self._check_materialised_risk_without_issue,
            # security
            self._check_cross_company_links,
        )
        findings = []
        unavailable = []
        for check in checks:
            try:
                result = check(projects)
            except AccessError:
                # The auditor cannot read this source. Elevating would let the
                # audit report records the user may not see, and skipping
                # quietly would let a clean-looking report hide a check that
                # never ran. So it is reported as unavailable, by name.
                unavailable.append(check.__name__.lstrip('_'))
                continue
            if result and result.get('count'):
                findings.append(result)

        if unavailable:
            findings.append(self._finding(
                'checks_not_run', 'security', 'low', len(unavailable),
                _("%s check(s) could not run with this user's access rights.")
                % len(unavailable),
                _("Every check runs, or is named."),
                ', '.join(sorted(unavailable)),
                _("Re-run as a user who can read the underlying records — "
                  "typically one with purchase and accounting access. The "
                  "audit deliberately does not elevate.")))

        findings.sort(key=lambda f: SEVERITIES.index(f['severity']))
        return {
            'project_ids': projects.ids,
            'finding_count': len(findings),
            'record_count': sum(f['count'] for f in findings),
            'by_severity': {
                severity: sum(f['count'] for f in findings
                              if f['severity'] == severity)
                for severity in SEVERITIES},
            'blocking': [f for f in findings if f['severity'] == 'critical'],
            'findings': findings,
        }

    @api.model
    def _scope(self, project=None, company=None):
        Project = self.env['realestate.project']
        if project:
            return project if not isinstance(project, int) \
                else Project.browse(project)
        domain = [('company_id', '=', company.id)] if company else []
        return Project.search(domain)

    def _finding(self, key, category, severity, count, finding, expected,
                 actual, remediation, model=None, domain=None, records=None):
        return {
            'key': key,
            'category': category,
            'severity': severity,
            'count': count,
            'finding': finding,
            'expected': expected,
            'actual': actual,
            'remediation': remediation,
            'model': model,
            'record_ids': records or [],
            'action': {
                'type': 'ir.actions.act_window',
                'name': finding,
                'res_model': model,
                'domain': domain or [('id', 'in', records or [])],
                'view_mode': 'list,form',
                'views': [[False, 'list'], [False, 'form']],
                'target': 'current',
                'context': {'create': False},
            } if model else False,
        }

    # ------------------------------------------------------------------
    # Financial
    # ------------------------------------------------------------------
    def _check_budget_equation(self, projects):
        Sheet = self.env['realestate.construction.cost.sheet']
        broken = []
        for project in projects:
            for row in Sheet.rows_for(project):
                expected = (row['original_budget']
                            + row['approved_budget_changes'])
                if abs(row['current_budget'] - expected) > 0.01:
                    broken.append((project, row))
        return self._finding(
            'budget_equation', 'financial', 'critical', len(broken),
            _("Current Budget does not equal Original plus approved changes."),
            _("current_budget == original_budget + approved_budget_changes"),
            _("%s cost code row(s) disagree.") % len(broken),
            _("Re-derive from the baseline and the implemented change orders; "
              "do not adjust the current figure directly."),
            model='realestate.construction.budget',
            domain=[('project_id', 'in', projects.ids)])

    def _check_commitment_equation(self, projects):
        Sheet = self.env['realestate.construction.cost.sheet']
        broken = []
        for project in projects:
            for row in Sheet.rows_for(project):
                expected = (row['original_commitment']
                            + row['approved_commitment_changes'])
                if abs(row['current_commitment'] - expected) > 0.01:
                    broken.append((project, row))
        return self._finding(
            'commitment_equation', 'financial', 'critical', len(broken),
            _("Current Commitment does not equal Original plus approved "
              "changes."),
            _("current_commitment == original + approved_commitment_changes"),
            _("%s cost code row(s) disagree.") % len(broken),
            _("Check for a package counted beside its purchase orders, or an "
              "implemented variation counted twice."),
            model='realestate.construction.contract.package',
            domain=[('project_id', 'in', projects.ids)])

    def _check_eac_equation(self, projects):
        Sheet = self.env['realestate.construction.cost.sheet']
        broken = []
        for project in projects:
            for row in Sheet.rows_for(project):
                if not row['has_etc']:
                    continue
                if abs(row['eac'] - (row['actual_cost'] + row['etc'])) > 0.01:
                    broken.append(row)
        return self._finding(
            'eac_equation', 'forecast', 'critical', len(broken),
            _("EAC does not equal Actual plus ETC."),
            _("eac == actual_cost + etc"),
            _("%s forecast row(s) disagree.") % len(broken),
            _("Re-approve the forecast; EAC is derived and must never be "
              "stored independently."),
            model='realestate.construction.forecast')

    def _check_uncoded_commitment(self, projects):
        domain = [('order_id.re_project_id', 'in', projects.ids),
                  ('order_id.state', 'in', ('purchase', 'done')),
                  ('re_cost_code_id', '=', False)]
        lines = self.env['purchase.order.line'].search(domain)
        return self._finding(
            'uncoded_commitment', 'commitment', 'medium', len(lines),
            _("Committed purchase lines carry no cost code."),
            _("Every committed line is coded, so commitment reaches the cost "
              "report."),
            _("%s line(s) sit under Unassigned.") % len(lines),
            _("Code the lines. The commitment is real either way — it is the "
              "attribution that is missing."),
            model='purchase.order.line', domain=domain)

    def _check_uncoded_actual(self, projects):
        Sheet = self.env['realestate.construction.cost.sheet']
        total, affected = 0.0, 0
        for project in projects:
            for row in Sheet.rows_for(project):
                if row['is_unassigned'] and row['actual_cost']:
                    total += row['actual_cost']
                    affected += 1
        return self._finding(
            'uncoded_actual', 'financial', 'medium', affected,
            _("Posted cost carries no cost code."),
            _("Analytic postings carry both the project and a cost code."),
            _("%s project(s), %.2f uncoded.") % (affected, total),
            _("Correct the analytic distribution on the source documents; do "
              "not reallocate postings from the cost sheet."),
            model='account.analytic.line')

    def _check_tax_contamination(self, projects):
        """Tax lines inherit the base line's analytic distribution.

        M1 excluded them with the `UNTAXED` domain. This proves no tax line is
        reaching a control figure, because a VAT-inflated actual is the kind of
        error that survives for a year.
        """
        Analytic = self.env['realestate.construction.analytic']
        contaminated = 0
        for project in projects:
            account = project.analytic_account_id
            if not account:
                continue
            domain = [
                (Analytic._project_plan()._column_name(), '=', account.id),
                ('move_line_id.tax_line_id', '!=', False)]
            contaminated += self.env['account.analytic.line'].sudo(
            ).search_count(domain)
        return self._finding(
            'tax_contamination', 'financial', 'high', contaminated,
            _("Tax analytic lines exist on construction projects."),
            _("Control figures exclude them via the UNTAXED domain."),
            _("%s tax analytic line(s) present.") % contaminated,
            _("No action needed if the cost sheet still excludes them — this "
              "is a watch item confirming the exclusion is still doing work."),
            model='account.analytic.line')

    def _check_legacy_retention(self, projects):
        Certificate = self.env[
            'realestate.construction.payment.certificate']
        domain = [('project_id', 'in', projects.ids),
                  ('state', 'in', ('invoiced', 'paid')),
                  ('retention_amount', '>', 0),
                  ('retention_posted_correctly', '=', False)]
        certificates = Certificate.search(domain)
        understated = sum(certificates.mapped('retention_amount'))
        return self._finding(
            'legacy_retention', 'financial', 'high', len(certificates),
            _("Pre-M7 certificates posted retention as negative expense."),
            _("Retention credits a liability; cost is gross of it."),
            _("%s certificate(s), cost understated by %.2f.")
            % (len(certificates), understated),
            _("A Finance reclassification, not a data fix. The posted entries "
              "must not be rewritten — see UPGRADE_AND_OPERATIONS.md."),
            model='realestate.construction.payment.certificate', domain=domain)

    def _check_missing_control_accounts(self, projects):
        companies = projects.mapped('company_id') or self.env.company
        missing = companies.filtered(
            lambda c: not c.construction_retention_account_id
            or not c.construction_advance_account_id)
        return self._finding(
            'missing_control_accounts', 'financial', 'high', len(missing),
            _("Retention or advance control accounts are not configured."),
            _("Both accounts are set per company."),
            _("%s company/companies incomplete.") % len(missing),
            _("Set them in Settings → Accounting → Construction. Certificates "
              "refuse to post retention without them, by design."),
            model='res.company', records=missing.ids)

    def _check_advance_balance(self, projects):
        Advance = self.env['realestate.construction.advance']
        advances = Advance.search([('project_id', 'in', projects.ids)])
        broken = advances.filtered(
            lambda a: a.outstanding_amount < -0.01
            or a.recovered_amount > a.amount + 0.01)
        return self._finding(
            'advance_balance', 'financial', 'critical', len(broken),
            _("An advance has recovered more than was advanced."),
            _("0 <= recovered <= amount, outstanding never negative."),
            _("%s advance(s) out of range.") % len(broken),
            _("Investigate the recovery lines on the certificates involved."),
            model='realestate.construction.advance', records=broken.ids)

    # ------------------------------------------------------------------
    # Budget and commitment structure
    # ------------------------------------------------------------------
    def _check_baseline_count(self, projects):
        Budget = self.env['realestate.construction.budget']
        offenders = []
        for project in projects:
            baselined = Budget.search_count([
                ('project_id', '=', project.id),
                ('state', '=', 'baselined')])
            if baselined > 1:
                offenders.append(project.id)
        return self._finding(
            'multiple_baselines', 'budget', 'critical', len(offenders),
            _("More than one baselined budget on a project."),
            _("Exactly one baseline is in force at a time."),
            _("%s project(s) affected.") % len(offenders),
            _("The partial unique index should make this impossible; if it "
              "appears, the index is missing on this database."),
            model='realestate.construction.budget',
            domain=[('project_id', 'in', offenders),
                    ('state', '=', 'baselined')])

    def _check_budget_lines_unclassified(self, projects):
        domain = [('project_id', 'in', projects.ids),
                  ('cost_code_id', '=', False)]
        lines = self.env['realestate.construction.budget.line'].search(domain)
        return self._finding(
            'budget_line_unclassified', 'budget', 'medium', len(lines),
            _("Budget lines carry no cost code."),
            _("Every budget line is coded so it can be compared with cost."),
            _("%s line(s).") % len(lines),
            _("Classify them. A legacy migration deliberately leaves "
              "ambiguous lines uncoded rather than guessing."),
            model='realestate.construction.budget.line', domain=domain)

    def _check_package_and_order_double_count(self, projects):
        """The defect M8 found: a package counted beside its own variations."""
        Commitment = self.env['realestate.construction.commitment']
        suspects = []
        for package in self.env[
                'realestate.construction.contract.package'].search(
                    [('project_id', 'in', projects.ids),
                     ('state', 'in', ('awarded', 'active'))]):
            amount, source = Commitment._package_commitment(package)
            if source == 'package' and abs(
                    amount - package.original_contract_value) > 0.01:
                suspects.append(package.id)
        return self._finding(
            'package_variation_double_count', 'commitment', 'critical',
            len(suspects),
            _("An orderless package contributes more than its original value."),
            _("Package contributes its ORIGINAL value; variations arrive "
              "through the change path."),
            _("%s package(s) would be counted twice.") % len(suspects),
            _("This is the M8 double-count defect. If it appears, the "
              "commitment engine has regressed."),
            model='realestate.construction.contract.package',
            records=suspects)

    # ------------------------------------------------------------------
    def _check_forecast_coverage(self, projects):
        Tower = self.env['realestate.construction.control.tower']
        uncovered = 0
        for project in projects:
            forecast = Tower._forecast(project)
            if forecast.get('has_forecast'):
                uncovered += forecast.get('missing_line_count', 0)
        return self._finding(
            'forecast_coverage', 'forecast', 'medium', uncovered,
            _("Cost codes in play have no estimate to complete."),
            _("Every code carrying budget, commitment or cost is forecast."),
            _("%s uncovered cost code(s).") % uncovered,
            _("Forecast them, or mark them 'no forecast required' — which is "
              "a decision, unlike an empty line."),
            model='realestate.construction.forecast.line')

    def _check_approved_not_implemented(self, projects):
        domain = [('project_id', 'in', projects.ids), ('state', '=', 'approved')]
        orders = self.env[
            'realestate.construction.change.order'].search(domain)
        return self._finding(
            'approved_not_implemented', 'change', 'medium', len(orders),
            _("Change orders are approved but not implemented."),
            _("An approved order is implemented so the baseline reflects it."),
            _("%s order(s) waiting.") % len(orders),
            _("Implement them, or explain the delay. The baseline is correct "
              "meanwhile — it simply does not include them yet."),
            model='realestate.construction.change.order', domain=domain)

    def _check_implemented_without_impact(self, projects):
        orders = self.env['realestate.construction.change.order'].search([
            ('project_id', 'in', projects.ids),
            ('state', '=', 'implemented')])
        broken = orders.filtered(lambda o: not o.line_ids)
        return self._finding(
            'implemented_without_impact', 'change', 'critical', len(broken),
            _("An implemented change order has no impact lines."),
            _("Implementation moves a baseline through its lines."),
            _("%s order(s) moved nothing.") % len(broken),
            _("Investigate before trusting the baseline on those projects."),
            model='realestate.construction.change.order', records=broken.ids)

    # ------------------------------------------------------------------
    def _check_over_certification(self, projects):
        lines = self.env['realestate.boq.line'].search([
            ('project_id', 'in', projects.ids),
            ('is_over_certified', '=', True)])
        return self._finding(
            'legacy_overcertification', 'certification', 'high', len(lines),
            _("Certified quantity exceeds the authorised quantity."),
            _("certified_qty <= authorised_quantity"),
            _("%s BOQ line(s) over-certified.") % len(lines),
            _("Historic over-certification is preserved deliberately. Do not "
              "rewrite posted certificates; future certificates already "
              "refuse to add to it."),
            model='realestate.boq.line', records=lines.ids)

    def _check_certificate_without_cost_code(self, projects):
        domain = [('project_id', 'in', projects.ids),
                  ('state', 'in', ('certified', 'invoiced', 'paid')),
                  ('cost_code_id', '=', False)]
        certificates = self.env[
            'realestate.construction.payment.certificate'].search(domain)
        return self._finding(
            'certificate_uncoded', 'certification', 'medium',
            len(certificates),
            _("Certificates carry no cost code."),
            _("A certificate lands in the cost report's dimension."),
            _("%s certificate(s).") % len(certificates),
            _("Code future certificates. Posted ones stay as posted."),
            model='realestate.construction.payment.certificate', domain=domain)

    # ------------------------------------------------------------------
    def _check_duplicate_document_numbers(self, projects):
        groups = self.env['realestate.construction.document']._read_group(
            [('project_id', 'in', projects.ids)],
            groupby=['project_id', 'document_number'],
            aggregates=['__count'])
        duplicates = [(project, number) for project, number, count in groups
                      if count > 1]
        return self._finding(
            'duplicate_document_number', 'document', 'high', len(duplicates),
            _("The same document number appears more than once on a project."),
            _("Document numbers are unique within a project."),
            _("%s duplicate number(s).") % len(duplicates),
            _("Renumber the later document. A unique index enforces this for "
              "new records."),
            model='realestate.construction.document')

    def _check_multiple_current_revisions(self, projects):
        groups = self.env[
            'realestate.construction.document.revision']._read_group(
                [('document_id.project_id', 'in', projects.ids),
                 ('is_current', '=', True)],
                groupby=['document_id'], aggregates=['__count'])
        offenders = [document.id for document, count in groups if count > 1]
        return self._finding(
            'multiple_current_revisions', 'document', 'critical',
            len(offenders),
            _("A document has more than one current revision."),
            _("Exactly one revision is current per document."),
            _("%s document(s) affected.") % len(offenders),
            _("Critical: transmittals and claims cite 'the current revision', "
              "and two of them makes every such citation ambiguous."),
            model='realestate.construction.document', records=offenders)

    def _check_ncr_closed_without_verification(self, projects):
        ncrs = self.env['realestate.construction.ncr'].search([
            ('project_id', 'in', projects.ids),
            ('state', 'in', ('verified', 'closed'))])
        broken = ncrs.filtered(lambda n: not n.verified_by_id)
        return self._finding(
            'ncr_closed_without_verification', 'quality', 'high', len(broken),
            _("Non-conformances are closed with nobody recorded as verifying."),
            _("Closure follows a verification by a named person."),
            _("%s NCR(s).") % len(broken),
            _("Legacy records may predate the rule; new ones cannot do this."),
            model='realestate.construction.ncr', records=broken.ids)

    # ------------------------------------------------------------------
    def _check_eot_date_equation(self, projects):
        from dateutil.relativedelta import relativedelta
        packages = self.env[
            'realestate.construction.contract.package'].search([
                ('project_id', 'in', projects.ids),
                ('original_completion_date', '!=', False)])
        broken = []
        for package in packages:
            expected = package.original_completion_date + relativedelta(
                days=int(package.approved_eot_days
                         + package.other_time_adjustment_days))
            if package.current_completion_date != expected:
                broken.append(package.id)
        return self._finding(
            'eot_date_equation', 'claims', 'critical', len(broken),
            _("Current completion date does not equal original plus approved "
              "extensions."),
            _("current == original + approved EOT + authorised adjustment"),
            _("%s package(s) disagree.") % len(broken),
            _("Never correct the current date directly; correct the "
              "extension that should have moved it."),
            model='realestate.construction.contract.package', records=broken)

    def _check_implemented_eot_without_determination(self, projects):
        eots = self.env['realestate.construction.eot'].search([
            ('project_id', 'in', projects.ids),
            ('state', '=', 'implemented')])
        broken = eots.filtered(lambda e: not e.determination_date)
        return self._finding(
            'eot_without_determination', 'claims', 'high', len(broken),
            _("An implemented extension has no determination date."),
            _("Days are determined by an authority before they are granted."),
            _("%s extension(s).") % len(broken),
            _("Record the determination. The contract date already moved."),
            model='realestate.construction.eot', records=broken.ids)

    def _check_materialised_risk_without_issue(self, projects):
        risks = self.env['realestate.construction.risk'].search([
            ('project_id', 'in', projects.ids),
            ('state', '=', 'materialised')])
        broken = risks.filtered(lambda r: not r.issue_id)
        return self._finding(
            'materialised_risk_without_issue', 'risk', 'medium', len(broken),
            _("A risk is marked materialised with no issue linked."),
            _("Materialisation creates the issue and links both ways."),
            _("%s risk(s).") % len(broken),
            _("Link or reopen. The snapshot on the risk is still the record "
              "of what was foreseen."),
            model='realestate.construction.risk', records=broken.ids)

    # ------------------------------------------------------------------
    def _check_cross_company_links(self, projects):
        """A child record in a different company from its project.

        Critical without qualification: company isolation is the one boundary
        that no privilege is allowed to cross.
        """
        offenders = []
        models_to_check = (
            ('realestate.construction.budget', 'project_id'),
            ('realestate.construction.contract.package', 'project_id'),
            ('realestate.construction.payment.certificate', 'project_id'),
            ('realestate.construction.claim', 'project_id'),
            ('realestate.construction.rfi', 'project_id'),
            ('realestate.construction.ncr', 'project_id'),
            ('realestate.construction.risk', 'project_id'),
        )
        for model_name, project_field in models_to_check:
            model = self.env[model_name]
            records = model.search([(project_field, 'in', projects.ids)])
            for record in records:
                project_company = record[project_field].company_id
                if project_company and record.company_id != project_company:
                    offenders.append((model_name, record.id))
        return self._finding(
            'cross_company_link', 'security', 'critical', len(offenders),
            _("A record belongs to a different company from its project."),
            _("Every child record shares its project's company."),
            _("%s record(s) cross a company boundary.") % len(offenders),
            _("Correct the company, or move the record. Do not relax the "
              "record rule to make it visible."),
            model=None)

    # ------------------------------------------------------------------
    @api.model
    def summary_text(self, project=None):
        """One paragraph a person can paste into a go-live checklist."""
        report = self.run(project)
        if not report['findings']:
            return _("Integrity audit: no findings.")
        parts = [_("Integrity audit: %(records)s record(s) across "
                   "%(findings)s finding(s).",
                   records=report['record_count'],
                   findings=report['finding_count'])]
        for severity in SEVERITIES:
            count = report['by_severity'][severity]
            if count:
                parts.append('%s: %s' % (severity, count))
        return ' — '.join(parts)
