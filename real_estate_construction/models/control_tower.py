# -*- coding: utf-8 -*-
"""The Construction Control Tower — one payload, one set of numbers.

Everything here is consumption. The tower asks the milestone that owns a
figure and reports what it says, with the label the milestone gave it. It
never adds a sixth opinion, and it never adds two figures that mean different
things.

Three rules run through the whole file:

* **Pending is not approved.** Potential change, submitted claims and
  determined-but-unimplemented entitlement are reported in their own fields,
  never folded into a baseline.
* **Unknown is not zero.** A missing ETC is `None` with `has_forecast = False`,
  a missing planned progress is `None` with a stated reason. Zero is business
  information and must not be invented.
* **Role decides the payload, not the template.** Panels a user may not read
  are absent from the JSON, not hidden in the DOM.
"""
from odoo import _, api, fields, models

#: Default health thresholds. Every one is overridable per company through
#: `ir.config_parameter`, because tolerance for overrun is a management
#: policy and not a property of construction.
DEFAULT_THRESHOLDS = {
    'forecast_variance_attention_pct': 2.0,
    'forecast_variance_risk_pct': 5.0,
    'forecast_stale_days': 35,
    'forecast_coverage_min_pct': 90.0,
    'rfi_overdue_attention': 3,
    'ncr_high_attention': 2,
    'high_risk_attention': 3,
    'change_approval_sla_days': 30,
}


class ConstructionControlTower(models.AbstractModel):
    _name = 'realestate.construction.control.tower'
    _description = 'Construction Control Tower'

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    @api.model
    def thresholds(self):
        config = self.env['ir.config_parameter'].sudo()
        values = {}
        for key, default in DEFAULT_THRESHOLDS.items():
            raw = config.get_param(
                'real_estate_construction.%s' % key, default)
            values[key] = type(default)(raw)
        return values

    @api.model
    def _may(self, group):
        return self.env.user.has_group(
            'real_estate_construction.%s' % group)

    @api.model
    def sections_for_user(self):
        """Which panels this user may see. The payload follows this, exactly.

        A panel the user may not read is *absent*, not blanked: a restricted
        field that reaches the browser has already leaked, whatever the
        template does with it.
        """
        sections = ['header', 'health', 'progress', 'quality', 'information',
                    'risk', 'exceptions']
        if self._may('group_construction_cost'):
            sections += ['cost', 'forecast', 'cost_sheet']
        if self._may('group_construction_commercial'):
            sections += ['change', 'commercial', 'claims', 'certificates']
        if self._may('group_construction_manager'):
            sections = list(dict.fromkeys(
                sections + ['cost', 'forecast', 'cost_sheet', 'change',
                            'commercial', 'claims', 'certificates',
                            'procurement']))
        return sections

    # ------------------------------------------------------------------
    # The payload
    # ------------------------------------------------------------------
    @api.model
    def payload(self, project, sections=None, wbs=None, package=None,
                contractor=None):
        """One aggregated payload. Not one RPC per KPI."""
        if isinstance(project, int):
            project = self.env['realestate.project'].browse(project)
        project.check_access('read')

        allowed = self.sections_for_user()
        wanted = [s for s in (sections or allowed) if s in allowed]

        data = {'project_id': project.id,
                'project_name': project.display_name,
                'currency_id': project.currency_id.id,
                'sections': wanted,
                'as_of': fields.Date.context_today(self)}

        builders = {
            'header': self._header,
            'cost': self._cost,
            'cost_sheet': self._cost_sheet,
            'forecast': self._forecast,
            'change': self._change,
            'progress': self._progress,
            'commercial': self._commercial,
            'certificates': self._certificates,
            'quality': self._quality,
            'information': self._information,
            'claims': self._claims,
            'risk': self._risk,
            'procurement': self._procurement,
            'exceptions': self._exceptions,
            'health': self._health,
        }
        for section in wanted:
            builder = builders.get(section)
            if not builder:
                continue
            # Each panel runs inside its own savepoint. One failing panel must
            # not take the tower down, and it must not leave a broken cursor
            # behind for the panels after it either. What it must never do is
            # fail quietly into a zero — hence `failed`, which the client
            # renders as an error rather than as a number.
            try:
                with self.env.cr.savepoint():
                    data[section] = builder(project)
            except Exception as error:          # noqa: BLE001
                data[section] = {'error': str(error), 'failed': True}
        return data

    # ------------------------------------------------------------------
    def _header(self, project):
        Forecast = self.env['realestate.construction.forecast']
        forecast = Forecast.latest_approved(project)
        packages = self.env[
            'realestate.construction.contract.package'].search(
                [('project_id', '=', project.id)])
        last_posting = self.env['account.move.line'].sudo().search(
            [('analytic_distribution', '!=', False)], order='date desc',
            limit=1) if project.analytic_account_id else False
        return {
            'project_id': project.id,
            'company_id': project.company_id.id if project.company_id
            else self.env.company.id,
            # Live data and snapshot data are dated separately, on purpose:
            # actual cost today beside a forecast from three weeks ago is a
            # normal situation, and a single "as of" would hide it.
            'data_as_of': fields.Date.context_today(self),
            'forecast_as_of': forecast.as_of_date if forecast else None,
            'forecast_reference': forecast.name if forecast else None,
            'last_posting_date': last_posting.date if last_posting else None,
            'original_completion_dates': [
                {'package': package.display_name,
                 'original': package.original_completion_date,
                 'current': package.current_completion_date,
                 'approved_eot_days': package.approved_eot_days,
                 'claimed_eot_days': package.claimed_eot_days}
                for package in packages if package.original_completion_date],
        }

    def _cost(self, project):
        totals = self.env[
            'realestate.construction.cost.sheet'].totals_for(project)
        return {
            'original_budget': totals['original_budget'],
            'approved_budget_changes': totals['approved_budget_changes'],
            'current_budget': totals['current_budget'],
            'original_commitment': totals['original_commitment'],
            'approved_commitment_changes':
                totals['approved_commitment_changes'],
            'current_commitment': totals['current_commitment'],
            'actual_cost': totals['actual_cost'],
            'certified_amount': totals['certified_amount'],
            'etc': totals['etc'],
            'eac': totals['eac'],
            'forecast_variance': totals['forecast_variance'],
            'has_forecast': totals['has_forecast'],
            'available_before_commitment':
                totals['available_before_commitment'],
            'budget_remaining_vs_actual': totals['budget_remaining_vs_actual'],
            'over_committed': totals['over_committed'],
            'unassigned_commitment': totals['unassigned_commitment'],
            'unassigned_actual': totals['unassigned_actual'],
            'budget_state': totals['budget_state'],
        }

    def _cost_sheet(self, project):
        rows = self.env[
            'realestate.construction.cost.sheet'].rows_for(project)
        return {'rows': rows, 'row_count': len(rows)}

    def _forecast(self, project):
        Forecast = self.env['realestate.construction.forecast']
        forecast = Forecast.latest_approved(project)
        thresholds = self.thresholds()
        if not forecast:
            return {
                'has_forecast': False,
                'reason': _("No approved forecast exists for this project. "
                            "That is not an EAC of zero — it is an EAC "
                            "nobody has produced."),
                'coverage_pct': 0.0,
                'missing_line_count': 0,
                'is_stale': False,
            }
        lines = forecast.line_ids
        with_etc = lines.filtered('has_etc')
        age = (fields.Date.context_today(self) - forecast.as_of_date).days \
            if forecast.as_of_date else 0

        # Coverage is measured against the cost codes actually in play, not
        # against the forecast's own lines. M3 refuses to approve a forecast
        # with an unanswered line, so counting only its lines would report
        # 100% coverage on a project that has since committed money to a code
        # the forecast never saw — the precise blind spot this KPI exists for.
        rows = self.env['realestate.construction.cost.sheet'].rows_for(project)
        in_play = [row for row in rows
                   if row['current_budget'] or row['current_commitment']
                   or row['actual_cost']]
        covered = [row for row in in_play if row['has_etc']]
        coverage_pct = round(
            len(covered) / len(in_play) * 100.0, 1) if in_play else 0.0
        uncovered = len(in_play) - len(covered)
        return {
            'has_forecast': True,
            'forecast_id': forecast.id,
            'reference': forecast.name,
            'as_of_date': forecast.as_of_date,
            'age_days': age,
            'is_stale': age > thresholds['forecast_stale_days'],
            'stale_threshold_days': thresholds['forecast_stale_days'],
            'eac': forecast.total_eac,
            'previous_eac': forecast.previous_total_eac
            if 'previous_total_eac' in forecast._fields else 0.0,
            'coverage_pct': coverage_pct,
            'line_count': len(lines),
            'forecast_line_coverage_pct': round(
                len(with_etc) / len(lines) * 100.0, 1) if lines else 0.0,
            'missing_line_count': uncovered,
            'cost_codes_in_play': len(in_play),
            'uncommitted_etc': sum(lines.mapped('uncommitted_etc')),
            'lines_over_budget': len(lines.filtered('eac_exceeds_budget')),
        }

    def _change(self, project):
        Event = self.env['realestate.construction.change.event']
        Order = self.env['realestate.construction.change.order']
        thresholds = self.thresholds()
        today = fields.Date.context_today(self)

        open_events = Event.search([
            ('project_id', '=', project.id),
            ('state', 'in', ['draft', 'identified', 'under_review', 'pricing',
                             'assessed', 'change_required'])])
        pending_orders = Order.search([
            ('project_id', '=', project.id),
            ('state', 'in', ('draft', 'submitted', 'pending_approval'))])
        approved_unimplemented = Order.search([
            ('project_id', '=', project.id), ('state', '=', 'approved')])
        implemented = Order.search([
            ('project_id', '=', project.id), ('state', '=', 'implemented')])

        ages = [(today - order.create_date.date()).days
                for order in pending_orders if order.create_date]
        return {
            'open_change_events': len(open_events),
            'potential_cost_exposure': sum(
                open_events.mapped('estimated_cost_impact')),
            'pending_orders': len(pending_orders),
            'pending_amount': sum(pending_orders.mapped('total_amount'))
            if 'total_amount' in Order._fields else 0.0,
            'approved_unimplemented': len(approved_unimplemented),
            'implemented_orders': len(implemented),
            'average_pending_age_days': round(
                sum(ages) / len(ages), 1) if ages else 0.0,
            'over_sla': len([a for a in ages
                             if a > thresholds['change_approval_sla_days']]),
            'sla_days': thresholds['change_approval_sla_days'],
        }

    def _progress(self, project):
        """Four different progress questions, answered separately.

        `planned` is deliberately absent unless an authoritative baseline
        exists. ATMTA has no scheduling engine, and interpolating a straight
        line between a start and an end date would be a fabricated number that
        every variance is then measured against.
        """
        BOQLine = self.env['realestate.boq.line']
        totals = self.env[
            'realestate.construction.cost.sheet'].totals_for(project)

        boq_lines = BOQLine.search([
            ('project_id', '=', project.id),
            ('boq_id.state', 'in', ('approved', 'locked'))])
        authorised = sum(line.authorised_quantity * line.unit_rate
                         for line in boq_lines)
        certified_value = sum(line.certified_amount for line in boq_lines)

        current_budget = totals['current_budget']
        return {
            'planned_pct': None,
            'planned_reason': _(
                "No authoritative schedule baseline is held in ATMTA. Planned "
                "progress comes from the programme, and inventing a straight "
                "line between two dates would be a number nobody agreed."),
            'physical_pct': project.construction_progress
            if 'construction_progress' in project._fields else None,
            'physical_basis': _("Milestone weight × completion."),
            'certified_pct': round(certified_value / authorised * 100.0, 1)
            if authorised else None,
            'certified_basis': _("Certified BOQ value ÷ authorised BOQ value."),
            'financial_pct': round(
                totals['actual_cost'] / current_budget * 100.0, 1)
            if current_budget else None,
            'financial_basis': _("Posted actual cost ÷ current budget."),
        }

    def _commercial(self, project):
        Package = self.env['realestate.construction.contract.package']
        Retention = self.env['realestate.construction.retention']
        Advance = self.env['realestate.construction.advance']
        Billing = self.env['realestate.owner.progress.billing']

        packages = Package.search([('project_id', '=', project.id)])
        billings = Billing.search([('project_id', '=', project.id)])
        return {
            'package_count': len(packages),
            'original_contract_value': sum(
                packages.mapped('original_contract_value')),
            'approved_variations': sum(
                packages.mapped('approved_variation_amount')),
            'current_contract_value': sum(
                packages.mapped('current_contract_value')),
            'retention_held': Retention.balance(project=project,
                                                side='contractor'),
            'advance_outstanding': Advance.outstanding_for(project),

            # The owner side is reported beside the contractor side and never
            # netted against it. Margin needs a full and authoritative revenue
            # basis, and subtracting cost from billing is not one.
            'owner': {
                'billed': sum(billings.filtered(
                    lambda b: b.state in ('invoiced', 'paid')).mapped(
                        'net_invoiced')),
                'owner_retention_held': Retention.balance(
                    project=project, side='owner'),
                'billing_count': len(billings),
            },
        }

    def _certificates(self, project):
        Certificate = self.env[
            'realestate.construction.payment.certificate']
        Retention = self.env['realestate.construction.retention']
        certificates = Certificate.search([('project_id', '=', project.id)])
        counted = certificates.filtered(
            lambda c: c.state in ('certified', 'invoiced', 'paid'))
        held = Retention.search([('project_id', '=', project.id),
                                 ('side', '=', 'contractor')])
        return {
            'certificate_count': len(certificates),
            'applied_amount': sum(certificates.mapped('applied_amount')),
            'certified_amount': sum(counted.mapped('certified_amount')),
            'disallowed_amount': sum(counted.mapped('disallowed_amount')),
            'awaiting_certification': len(certificates.filtered(
                lambda c: c.state in ('draft', 'submitted'))),
            'awaiting_invoice': len(certificates.filtered(
                lambda c: c.state == 'certified')),
            'paid_count': len(certificates.filtered(
                lambda c: c.state == 'paid')),
            'retention_accrued': sum(held.filtered(
                lambda m: m.movement_type == 'hold').mapped('amount')),
            'retention_released': sum(held.filtered(
                lambda m: m.movement_type == 'release').mapped('amount')),
            'retention_outstanding': Retention.balance(
                project=project, side='contractor'),
            'advance_outstanding': self.env[
                'realestate.construction.advance'].outstanding_for(project),
        }

    def _quality(self, project):
        Inspection = self.env['realestate.construction.inspection']
        NCR = self.env['realestate.construction.ncr']
        Observation = self.env['realestate.construction.quality.observation']
        base = [('project_id', '=', project.id)]

        inspections = Inspection.search(base + [('result', '!=', False)])
        passed = inspections.filtered(
            lambda i: i.result in ('accepted', 'accepted_with_comments'))
        first_pass = inspections.filtered(
            lambda i: i.reinspection_sequence == 0)
        first_pass_ok = first_pass.filtered(
            lambda i: i.result in ('accepted', 'accepted_with_comments'))
        ncrs = NCR.search(base)
        open_ncrs = ncrs.filtered(lambda n: n.state in NCR.OPEN_STATES)
        observations = Observation.search(base)
        closure_times = [n.closure_time_days for n in ncrs
                         if n.closure_time_days]
        return {
            'inspection_count': len(inspections),
            'pass_rate_pct': round(
                len(passed) / len(inspections) * 100.0, 1)
            if inspections else None,
            'first_pass_yield_pct': round(
                len(first_pass_ok) / len(first_pass) * 100.0, 1)
            if first_pass else None,
            'reinspection_count': len(inspections.filtered(
                lambda i: i.reinspection_sequence > 0)),
            'open_observations': len(observations.filtered(
                lambda o: o.state != 'closed')),
            'overdue_observations': len(observations.filtered('is_overdue')),
            'open_ncrs': len(open_ncrs),
            'high_severity_ncrs': len(open_ncrs.filtered(
                lambda n: n.severity in ('major', 'critical'))),
            'overdue_ncrs': len(ncrs.filtered('is_overdue')),
            'average_ncr_closure_days': round(
                sum(closure_times) / len(closure_times), 1)
            if closure_times else None,
            # Quality exposure is reported, never consumed: an NCR estimate
            # that moved a forecast would be a quality record changing money.
            'quality_exposure': NCR.quality_exposure(project),
        }

    def _information(self, project):
        RFI = self.env['realestate.construction.rfi']
        Submittal = self.env['realestate.construction.submittal']
        Document = self.env['realestate.construction.document']
        Transmittal = self.env['realestate.construction.transmittal']
        base = [('project_id', '=', project.id)]

        rfis = RFI.search(base)
        open_rfis = rfis.filtered(lambda r: r.state not in ('closed',
                                                            'cancelled'))
        submittals = Submittal.search(base)
        response_times = [r.response_time_days for r in rfis
                          if 'response_time_days' in RFI._fields
                          and r.response_time_days]
        return {
            'open_rfis': len(open_rfis),
            'overdue_rfis': len(open_rfis.filtered('is_overdue'))
            if 'is_overdue' in RFI._fields else 0,
            'average_rfi_response_days': round(
                sum(response_times) / len(response_times), 1)
            if response_times else None,
            'pending_submittals': len(submittals.filtered(
                lambda s: s.state not in ('approved', 'closed', 'cancelled'))),
            'overdue_submittals': len(submittals.filtered('is_overdue'))
            if 'is_overdue' in Submittal._fields else 0,
            'revise_and_resubmit': len(submittals.filtered(
                lambda s: s.state == 'revise_resubmit'))
            if 'state' in Submittal._fields else 0,
            'documents': Document.search_count(base),
            'transmittals_awaiting_acknowledgement': Transmittal.search_count(
                base + [('state', '=', 'sent')]),
        }

    def _claims(self, project):
        kpis = self.env['realestate.construction.claim.kpi'].for_project(
            project)
        Claim = self.env['realestate.construction.claim']
        implemented = Claim.search([
            ('project_id', '=', project.id),
            ('change_order_id.state', '=', 'implemented')])
        packages = self.env[
            'realestate.construction.contract.package'].search(
                [('project_id', '=', project.id)])
        return {
            'open_claims': kpis['open_claims'],
            'claimed_cost': kpis['claimed_cost'],
            # Assessed is commercial-only; it is fetched through the field,
            # so a user without the group simply does not get it.
            'assessed_cost': sum(Claim.search([
                ('project_id', '=', project.id)]).mapped('assessed_cost'))
            if self._may('group_construction_commercial') else None,
            'determined_cost': kpis['determined_cost'],
            'implemented_cost': sum(
                implemented.mapped('determined_cost')),
            'claims_awaiting_response': kpis['claims_awaiting_response'],
            'average_claim_age_days': kpis['average_claim_age_days'],
            'late_notices': kpis['late_notices'],
            'claimed_eot_days': sum(packages.mapped('claimed_eot_days')),
            'approved_eot_days': sum(packages.mapped('approved_eot_days')),
            'open_delay_events': kpis['open_delay_events'],
            'ongoing_delays': kpis['ongoing_delays'],
            'delays_without_notice_review':
                kpis['delays_without_notice_review'],
            'current_completion_dates': [
                {'package': package.display_name,
                 'current': package.current_completion_date}
                for package in packages if package.current_completion_date],
        }

    def _risk(self, project):
        kpis = self.env['realestate.construction.claim.kpi'].for_project(
            project)
        Risk = self.env['realestate.construction.risk']
        risks = Risk.search([('project_id', '=', project.id),
                             ('state', '!=', 'closed')])
        heatmap = {}
        for risk in risks:
            key = '%s,%s' % (risk.probability, risk.overall_impact)
            heatmap[key] = heatmap.get(key, 0) + 1
        return {
            'open_risks': kpis['open_risks'],
            'high_risks': kpis['high_risks'],
            'risks_without_mitigation': kpis['risks_without_mitigation'],
            'overdue_mitigation_actions': kpis['overdue_mitigation_actions'],
            'materialised_risks': Risk.search_count([
                ('project_id', '=', project.id),
                ('state', '=', 'materialised')]),
            'selected_risk_exposure': kpis['selected_risk_exposure'],
            'open_issues': kpis['open_issues'],
            'critical_issues': kpis['critical_issues'],
            'overdue_issues': kpis['overdue_issues'],
            'heatmap': heatmap,
            'scale_maximum': Risk._scale_maximum(),
            # M8's deduplicated ladder, reported as the one exposure figure.
            'exposure': kpis['exposure'],
        }

    def _procurement(self, project):
        """Signals only. `real_estate_procurement` owns procurement."""
        totals = self.env[
            'realestate.construction.cost.sheet'].totals_for(project)
        Line = self.env['purchase.order.line']
        uncoded = Line.search_count([
            ('order_id.re_project_id', '=', project.id),
            ('order_id.state', 'in', ('purchase', 'done')),
            ('re_cost_code_id', '=', False)])
        return {
            'uncommitted_budget': totals['available_before_commitment'],
            'over_committed': totals['over_committed'],
            'confirmed_po_count': self.env['purchase.order'].search_count([
                ('re_project_id', '=', project.id),
                ('state', 'in', ('purchase', 'done'))]),
            'open_rfq_count': self.env['purchase.order'].search_count([
                ('re_project_id', '=', project.id),
                ('state', 'in', ('draft', 'sent', 'to approve'))]),
            'uncoded_po_lines': uncoded,
        }

    def _exceptions(self, project):
        return self.env[
            'realestate.construction.exceptions'].for_project(project)

    # ------------------------------------------------------------------
    # Health — transparent rules, stated reasons
    # ------------------------------------------------------------------
    def _health(self, project):
        """A status with its reasons attached. Never a black-box score.

        Each dimension answers separately and says why. There is no weighted
        composite index: a single number would hide which dimension is in
        trouble, and nobody can act on 68/100.

        Health only consults the dimensions this user may read. A site
        engineer's overall status is therefore built from quality,
        information and risk — which is honest — rather than from a cost
        figure smuggled in through a summary.
        """
        thresholds = self.thresholds()
        allowed = self.sections_for_user()
        dimensions = []

        # -- Cost / forecast
        if 'cost' not in allowed:
            totals = None
        else:
            totals = self.env[
                'realestate.construction.cost.sheet'].totals_for(project)
        if totals is None:
            pass
        elif not totals['has_forecast']:
            dimensions.append({
                'key': 'cost', 'status': 'no_data',
                'reason': _("No approved forecast, so there is no EAC to "
                            "compare with the budget."),
                'metric': None})
        else:
            budget = totals['current_budget']
            variance = totals['forecast_variance']
            pct = (variance / budget * 100.0) if budget else 0.0
            if pct < -thresholds['forecast_variance_risk_pct']:
                status = 'critical'
            elif pct < -thresholds['forecast_variance_attention_pct']:
                status = 'at_risk'
            elif pct < 0:
                status = 'attention'
            else:
                status = 'on_track'
            dimensions.append({
                'key': 'cost', 'status': status,
                'reason': (_("EAC exceeds current budget by %.1f%%.", -pct)
                           if pct < 0 else
                           _("EAC is %.1f%% within current budget.", pct)),
                'metric': round(pct, 1)})

        # -- Forecast freshness and coverage
        forecast = self._forecast(project) if 'forecast' in allowed else None
        if forecast is None:
            pass
        elif not forecast['has_forecast']:
            dimensions.append({'key': 'forecast', 'status': 'no_data',
                               'reason': forecast['reason'], 'metric': None})
        else:
            problems = []
            if forecast['is_stale']:
                problems.append(_("forecast is %s days old (threshold %s)",
                                  forecast['age_days'],
                                  thresholds['forecast_stale_days']))
            if forecast['coverage_pct'] < thresholds[
                    'forecast_coverage_min_pct']:
                problems.append(_("%s cost code(s) have no ETC",
                                  forecast['missing_line_count']))
            dimensions.append({
                'key': 'forecast',
                'status': 'attention' if problems else 'on_track',
                'reason': '; '.join(problems) if problems else _(
                    "Forecast is current and covers every cost code."),
                'metric': forecast['coverage_pct']})

        # -- Quality
        quality = self._quality(project)
        high = quality['high_severity_ncrs']
        dimensions.append({
            'key': 'quality',
            'status': ('at_risk' if high >= thresholds['ncr_high_attention']
                       else 'attention' if quality['overdue_ncrs']
                       else 'on_track'),
            'reason': _("%(high)s high-severity NCR(s), %(overdue)s overdue.",
                        high=high, overdue=quality['overdue_ncrs']),
            'metric': high})

        # -- Information
        information = self._information(project)
        overdue_rfis = information['overdue_rfis']
        dimensions.append({
            'key': 'information',
            'status': ('attention'
                       if overdue_rfis >= thresholds['rfi_overdue_attention']
                       else 'on_track'),
            'reason': _("%(rfis)s overdue RFI(s), %(subs)s overdue "
                        "submittal(s).", rfis=overdue_rfis,
                        subs=information['overdue_submittals']),
            'metric': overdue_rfis})

        # -- Risk
        risk = self._risk(project)
        dimensions.append({
            'key': 'risk',
            'status': ('at_risk'
                       if risk['high_risks'] >= thresholds[
                           'high_risk_attention']
                       else 'attention' if risk['overdue_issues']
                       else 'on_track'),
            'reason': _("%(high)s high risk(s), %(issues)s overdue issue(s).",
                        high=risk['high_risks'],
                        issues=risk['overdue_issues']),
            'metric': risk['high_risks']})

        # -- Claims
        claims = self._claims(project) if 'claims' in allowed else None
        if claims:
            dimensions.append({
                'key': 'claims',
                'status': ('attention' if claims['open_claims']
                           else 'on_track'),
                'reason': _("%(open)s open claim(s); %(days)s day(s) of EOT "
                            "claimed and not granted.",
                            open=claims['open_claims'],
                            days=claims['claimed_eot_days']),
                'metric': claims['open_claims']})

        # `no_data` ranks above `on_track` deliberately: not knowing is not
        # the same as being fine, and a green light over an empty project is
        # the most expensive kind of reassurance.
        order = ['critical', 'at_risk', 'attention', 'no_data', 'on_track']
        statuses = [d['status'] for d in dimensions]
        overall = next((s for s in order if s in statuses), 'no_data')
        drivers = [d for d in dimensions if d['status'] == overall]
        return {
            'status': overall,
            'dimensions': dimensions,
            # The status is only as useful as the sentence under it.
            'reasons': [d['reason'] for d in drivers],
            'thresholds': thresholds,
        }
