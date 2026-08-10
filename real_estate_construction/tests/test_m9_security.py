# -*- coding: utf-8 -*-
"""M9 — the security matrix, and the audit that keeps it honest.

Phase 0 found no record rule of any kind. This file is the proof that the gap
is closed and the guard that stops it reopening: a new model added in M10 or
later must appear in one of the decision registers below, or `test_every_model
_has_a_deliberate_security_decision` fails. The point is not that every model
needs a company — some catalogues are legitimately global — but that every
omission is somebody's decision rather than an oversight.
"""
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import ConstructionCommon

#: Models that hold project data and must carry a company plus record rules.
COMPANY_SCOPED = {
    'realestate.construction.advance',
    'realestate.construction.advance.recovery',
    'realestate.boq',
    'realestate.boq.line',
    'realestate.boq.line.variation',
    'realestate.construction.budget',
    'realestate.construction.budget.line',
    'realestate.construction.budget.change.line',
    'realestate.construction.change.event',
    'realestate.construction.change.order',
    'realestate.construction.change.order.line',
    'realestate.construction.claim',
    'realestate.construction.claim.cost.line',
    'realestate.construction.claim.determination',
    'realestate.construction.claim.evidence',
    'realestate.construction.claim.submission',
    'realestate.construction.checklist.template',
    'realestate.construction.commitment.change',
    'realestate.construction.contract.package',
    'realestate.construction.cost.code',
    'realestate.construction.daily.delay',
    'realestate.construction.daily.delivery',
    'realestate.construction.daily.equipment',
    'realestate.construction.daily.report',
    'realestate.construction.daily.work',
    'realestate.construction.delay.event',
    'realestate.construction.document',
    'realestate.construction.document.revision',
    'realestate.construction.eot',
    'realestate.construction.forecast',
    'realestate.construction.forecast.line',
    'realestate.construction.inspection',
    'realestate.construction.inspection.line',
    'realestate.construction.inspection.request',
    'realestate.construction.issue',
    'realestate.construction.itp',
    'realestate.construction.itp.item',
    'realestate.construction.ncr',
    'realestate.construction.notice',
    'realestate.construction.payment.certificate',
    'realestate.construction.payment.certificate.line',
    'realestate.construction.quality.observation',
    'realestate.construction.retention',
    'realestate.construction.retention.release',
    'realestate.construction.revenue.change',
    'realestate.construction.rfi',
    'realestate.construction.risk',
    'realestate.construction.risk.action',
    'realestate.construction.submittal',
    'realestate.construction.submittal.package',
    'realestate.construction.submittal.revision',
    'realestate.construction.transmittal',
    'realestate.construction.transmittal.line',
    'realestate.construction.wbs',
    'realestate.owner.progress.billing',
}

#: Deliberately global. A work item catalogue or a contractor list is shared
#: reference data; giving it a company would fragment the catalogue rather
#: than protect anything.
GLOBAL_BY_DESIGN = {
    'realestate.work.item': 'Shared work-item catalogue.',
    'realestate.work.item.category': 'Shared work-item catalogue.',
    'realestate.contractor': 'Vendor directory; the partner carries company.',
    'realestate.construction.checklist.template.line':
        'Child of a company-scoped template.',
    'realestate.construction.change.order.markup':
        'Child of a company-scoped order.',
    'realestate.construction.change.order.revision':
        'Child of a company-scoped order.',
    'realestate.construction.change.approval':
        'Child of a company-scoped order.',
    'realestate.construction.forecast.adjustment':
        'Child of a company-scoped forecast line.',
    'realestate.construction.rfi.response': 'Child of a company-scoped RFI.',
    'realestate.construction.submittal.review':
        'Child of a company-scoped submittal revision.',
    'realestate.owner.progress.billing.deduction':
        'Child of a company-scoped billing.',
    'realestate.construction.reason.wizard': 'Transient prompt.',
}

#: Pre-M1 models that never carried a company. Recorded rather than quietly
#: tolerated: they are project-scoped through their project and are covered by
#: the project rules, and giving them a company is M10 clean-up.
LEGACY_WITHOUT_COMPANY = {
    'realestate.construction.milestone',
    'realestate.construction.task',
    'realestate.construction.cost.line',
    'realestate.construction.labor.log',
}


@tagged('post_install', '-at_install', 'atmta_construction')
class TestSecurityAudit(ConstructionCommon):

    def _construction_models(self):
        models = set()
        for name, model in self.env.registry.items():
            if not name.startswith(('realestate.construction',
                                    'realestate.boq',
                                    'realestate.work.item',
                                    'realestate.contractor',
                                    'realestate.owner.progress')):
                continue
            if model._abstract or model._transient:
                continue
            models.add(name)
        return models

    def test_every_model_has_a_deliberate_security_decision(self):
        """A new model must be classified, not merely added."""
        known = COMPANY_SCOPED | set(GLOBAL_BY_DESIGN) | LEGACY_WITHOUT_COMPANY
        unclassified = sorted(self._construction_models() - known)
        self.assertFalse(
            unclassified,
            "These construction models carry no recorded security decision. "
            "Add them to COMPANY_SCOPED, GLOBAL_BY_DESIGN or "
            "LEGACY_WITHOUT_COMPANY in tests/test_m9_security.py — the point "
            "is that somebody chose: %s" % unclassified)

    def test_every_company_scoped_model_has_a_company_field(self):
        missing = [name for name in sorted(COMPANY_SCOPED)
                   if name in self.env
                   and 'company_id' not in self.env[name]._fields]
        self.assertFalse(missing, "No company field on: %s" % missing)

    def test_every_company_scoped_model_has_a_global_company_rule(self):
        Rule = self.env['ir.rule'].sudo()
        missing = []
        for name in sorted(COMPANY_SCOPED):
            if name not in self.env:
                continue
            rules = Rule.search([('model_id.model', '=', name)])
            if not any(not rule.groups for rule in rules):
                missing.append(name)
        self.assertFalse(
            missing,
            "Multi-company isolation must be a global rule, not a privilege "
            "somebody can be granted past: %s" % missing)

    def test_every_model_has_an_access_control_line(self):
        Access = self.env['ir.model.access'].sudo()
        missing = []
        for name in sorted(self._construction_models()):
            if not Access.search_count([('model_id.model', '=', name)]):
                missing.append(name)
        self.assertFalse(missing, "No ACL for: %s" % missing)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestRoleMatrix(ConstructionCommon):
    """Read and act, per role, against every family of record."""

    def _user(self, login, *groups):
        return self.env['res.users'].create({
            'name': login, 'login': login,
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id] + [
                self.env.ref('real_estate_construction.%s' % g).id
                for g in groups])],
        })

    def setUp(self):
        super().setUp()
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SEC-CIV', 'Civil', 'subcontract')
        self.package = self._package(self.project, self.contractor,
                                     value=1_000_000.0, award=True)
        self.site = self._user('m9.site', 'group_construction_user')
        self.qs = self._user('m9.qs', 'group_construction_cost')
        self.commercial = self._user('m9.commercial',
                                     'group_construction_commercial')
        self.manager = self._user('m9.manager', 'group_construction_manager')

    def test_a_site_role_reads_operations_and_not_commerce(self):
        rfi = self._rfi(self.project)
        rfi.with_user(self.site).read(['name'])

        inspection = self._inspection(self.project, self.contractor)
        inspection.with_user(self.site).read(['name'])

        claim = self._claim(self.project, self.package, claimed_cost=1.0)
        with self.assertRaises(AccessError):
            claim.with_user(self.site).read(['title'])

    def test_a_cost_role_reads_the_cost_sheet_and_not_claims(self):
        self._baselined(self.project, 1_000_000.0, code=self.civil)
        sheet = self.env['realestate.construction.cost.sheet'].with_user(
            self.qs)
        totals = sheet.totals_for(self.project)
        self.assertEqual(totals['current_budget'], 1_000_000.0)

        claim = self._claim(self.project, self.package, claimed_cost=1.0)
        with self.assertRaises(AccessError):
            claim.with_user(self.qs).read(['title'])

    def test_a_commercial_role_reads_claims(self):
        claim = self._claim(self.project, self.package, claimed_cost=1.0)
        claim.with_user(self.commercial).read(['title'])

    def test_the_tower_payload_follows_the_role(self):
        Tower = self.env['realestate.construction.control.tower']
        site_sections = Tower.with_user(self.site).sections_for_user()
        self.assertNotIn('cost', site_sections)
        self.assertNotIn('claims', site_sections)
        self.assertIn('quality', site_sections)

        qs_sections = Tower.with_user(self.qs).sections_for_user()
        self.assertIn('cost', qs_sections)
        self.assertNotIn('claims', qs_sections)

        manager_sections = Tower.with_user(self.manager).sections_for_user()
        self.assertIn('cost', manager_sections)
        self.assertIn('claims', manager_sections)

    def test_a_restricted_payload_does_not_carry_claim_data(self):
        """M9AP — the confidential fields must be absent, not merely unrendered."""
        claim = self._claim(self.project, self.package,
                            claimed_cost=5_000_000.0)
        claim.assessed_cost = 3_000_000.0

        payload = self.env[
            'realestate.construction.control.tower'].with_user(
                self.site).payload(self.project)

        self.assertNotIn('claims', payload)
        self.assertNotIn('cost', payload)
        blob = str(payload)
        self.assertNotIn('5000000', blob.replace('.0', ''))
        self.assertNotIn('3000000', blob.replace('.0', ''))

    def test_a_project_with_no_team_stays_visible(self):
        """Naming nobody keeps the old behaviour. That is the point."""
        rfi = self._rfi(self.project)
        self.assertFalse(self.project.construction_member_ids)
        rfi.with_user(self.site).read(['name'])

    def test_naming_a_team_restricts_the_project(self):
        other_user = self._user('m9.outsider', 'group_construction_user')
        self.project.construction_member_ids = [(6, 0, [self.site.id])]
        rfi = self._rfi(self.project)

        rfi.with_user(self.site).read(['name'])
        self.assertFalse(
            rfi.with_user(other_user).search([('id', '=', rfi.id)]),
            "A named team is what turns access control on for a project.")

    def test_a_manager_sees_every_project(self):
        self.project.construction_member_ids = [(6, 0, [self.site.id])]
        rfi = self._rfi(self.project)
        self.assertTrue(
            rfi.with_user(self.manager).search([('id', '=', rfi.id)]))


@tagged('post_install', '-at_install', 'atmta_construction')
class TestMultiCompanyDashboard(ConstructionCommon):

    def test_the_tower_never_reaches_another_company(self):
        other_company = self.env['res.company'].create({'name': 'M9 Other Co'})
        civil = self._cost_code('MC-CIV', 'Civil', 'subcontract')

        mine = self._project()
        self._baselined(mine, 100_000_000.0, code=civil)

        theirs = self._project(company_id=other_company.id)
        self.env['realestate.construction.budget'].with_context(
            allowed_company_ids=[other_company.id]).create({
                'project_id': theirs.id,
                'company_id': other_company.id,
                'name': 'Other budget',
            })

        payload = self.env[
            'realestate.construction.control.tower'].with_context(
                allowed_company_ids=[self.company.id]).payload(mine)

        self.assertEqual(payload['cost']['current_budget'], 100_000_000.0)
        self.assertEqual(payload['project_id'], mine.id)
        self.assertNotIn(str(theirs.id), str(payload['cost']))


@tagged('post_install', '-at_install', 'atmta_construction')
class TestAccountingAccessBoundary(ConstructionCommon):
    """M9AO — aggregated project control is not the accounting ledger."""

    def test_the_aggregate_is_available_without_accounting_rights(self):
        civil = self._cost_code('ACC-CIV', 'Civil', 'subcontract')
        project = self._project()
        self._baselined(project, 5_000_000.0, code=civil)
        self._post_bill(project, civil, 750_000.0)

        controller = self.env['res.users'].create({
            'name': 'Controller no accounting', 'login': 'm9.noacc',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_construction.group_construction_cost').id,
            ])],
        })

        totals = self.env[
            'realestate.construction.cost.sheet'].with_user(
                controller).totals_for(project)
        self.assertEqual(totals['actual_cost'], 750_000.0,
                         "Aggregated project actual is a construction "
                         "control figure and is deliberately available.")

        # The drilldown is a different question, and the answer is Odoo's.
        action = self.env['realestate.construction.cost.sheet'].drilldown(
            project, 'actual_cost', civil.id)
        self.assertEqual(action['res_model'], 'account.analytic.line')
        with self.assertRaises(AccessError):
            self.env['account.analytic.line'].with_user(controller).search(
                action['domain'])

    def test_an_accounting_user_can_drill(self):
        civil = self._cost_code('ACC-CIV2', 'Civil', 'subcontract')
        project = self._project()
        self._post_bill(project, civil, 100_000.0)
        accountant = self.env['res.users'].create({
            'name': 'Accountant', 'login': 'm9.acc',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('account.group_account_readonly').id,
                self.env.ref(
                    'real_estate_construction.group_construction_cost').id,
            ])],
        })
        action = self.env['realestate.construction.cost.sheet'].drilldown(
            project, 'actual_cost', civil.id)
        lines = self.env['account.analytic.line'].with_user(accountant).search(
            action['domain'])
        self.assertTrue(lines)
