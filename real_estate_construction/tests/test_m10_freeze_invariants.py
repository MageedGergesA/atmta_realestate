# -*- coding: utf-8 -*-
"""M10 — the five freeze invariants.

These are the contracts the product ships with. Everything else in M10 is
evidence-gathering; these five are the things that must be true on the day of
the freeze and every day after it, and each one is a defect this programme
actually found and fixed rather than a hypothetical.
"""

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestFreezeInvariants(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.CostSheet = self.env['realestate.construction.cost.sheet']
        self.Tower = self.env['realestate.construction.control.tower']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('FZ-CIV', 'Civil', 'subcontract')

    # -- A ------------------------------------------------------------------
    def test_a_every_surface_reports_the_same_reconciled_position(self):
        """One economic reality, four surfaces, no independent formula."""
        self._baselined(self.project, 100_000_000.0, code=self.civil)
        package = self._package(self.project, self.contractor,
                                value=80_000_000.0, award=True)
        self._approve_and_implement(self._change_order(
            self.project, lines=[(self.civil, 'budget', 5_000_000.0)]))
        self._approve_and_implement(self._change_order(
            self.project, lines=[(self.civil, 'commitment', 10_000_000.0)],
            package=package))
        self._post_bill(self.project, self.civil, 40_000_000.0)

        forecast = self._forecast(self.project)
        line = forecast.line_ids.filtered(
            lambda l: l.cost_code_id == self.civil)[:1]
        line.write({'method': 'manual', 'manual_etc': 55_000_000.0,
                    'manual_reason': 'Priced remaining scope.'})
        forecast.action_submit()
        forecast.action_approve()

        expected = {
            'current_budget': 105_000_000.0,
            'current_commitment': 90_000_000.0,
            'actual_cost': 40_000_000.0,
        }
        controls = self.Controls.project_totals(self.project)
        sheet = self.CostSheet.totals_for(self.project)
        tower = self.Tower.payload(self.project)['cost']
        summary = self.project.construction_position()

        for key, value in expected.items():
            self.assertEqual(controls[key], value, key)
            self.assertEqual(sheet[key], value, key)
            self.assertEqual(tower[key], value, key)
            self.assertEqual(summary[key], value, key)

        self.assertEqual(sheet['etc'], 55_000_000.0)
        self.assertEqual(sheet['eac'], 95_000_000.0)
        self.assertEqual(sheet['forecast_variance'], 10_000_000.0)
        self.assertEqual(tower['eac'], sheet['eac'])
        self.assertEqual(tower['forecast_variance'],
                         sheet['forecast_variance'])

        # The equations themselves, not four numbers that agree today.
        self.assertEqual(
            sheet['current_budget'],
            sheet['original_budget'] + sheet['approved_budget_changes'])
        self.assertEqual(
            sheet['current_commitment'],
            sheet['original_commitment']
            + sheet['approved_commitment_changes'])
        self.assertEqual(sheet['eac'], sheet['actual_cost'] + sheet['etc'])
        self.assertEqual(sheet['forecast_variance'],
                         sheet['current_budget'] - sheet['eac'])

    # -- B ------------------------------------------------------------------
    def test_b_the_legacy_dashboard_is_not_a_user_facing_financial_surface(self):
        """It computed budget from milestones and actual from cost lines.

        Both contradict M2. Two dashboards disagreeing about the same word is
        the failure M9AS forbids, so the obsolete one must not be reachable as
        an ordinary Construction surface.
        """
        legacy_action = self.env.ref(
            'real_estate_construction.action_construction_dashboard',
            raise_if_not_found=False)
        self.assertTrue(legacy_action, "Kept for compatibility, not deleted.")

        legacy_menus = self.env['ir.ui.menu'].sudo().search([
            ('action', '=', 'ir.actions.client,%s' % legacy_action.id)])
        for menu in legacy_menus:
            self.assertTrue(
                menu.groups_id,
                "%s leads to the obsolete financial surface and is open to "
                "everyone." % menu.complete_name)
            self.assertNotIn(
                self.env.ref('real_estate_construction.group_construction_user'),
                menu.groups_id,
                "An ordinary construction user must not be offered it.")

        manager = self.env['res.users'].create({
            'name': 'M10 manager', 'login': 'm10.manager',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_construction.group_construction_manager').id,
            ])],
        })
        visible = self.env['ir.ui.menu'].with_user(manager).search([
            ('action', '=', 'ir.actions.client,%s' % legacy_action.id)])
        self.assertFalse(
            visible,
            "Even a construction manager should reach the Control Tower, not "
            "a surface using pre-Project-Control definitions.")

        tower_action = self.env.ref(
            'real_estate_construction.action_control_tower')
        tower_menus = self.env['ir.ui.menu'].with_user(manager).search([
            ('action', '=', 'ir.actions.client,%s' % tower_action.id)])
        self.assertTrue(tower_menus, "The Control Tower is the canonical one.")

    # -- C ------------------------------------------------------------------
    def test_c_retention_never_understates_actual_cost(self):
        self._configure_construction_accounts()
        self._baselined(self.project, 1_000_000.0, code=self.civil)
        certificate = self._certificate(
            self.project, self.contractor, amount=100_000.0,
            retention_pct=5.0, cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        sheet = self.CostSheet.totals_for(self.project)
        self.assertEqual(sheet['actual_cost'], 100_000.0,
                         "The work cost what it cost. 95,000 was the defect.")

        retention = self.env['realestate.construction.retention'].balance(
            project=self.project, contractor=self.contractor)
        self.assertEqual(retention, 5_000.0)
        self.assertEqual(certificate.vendor_bill_id.amount_total, 95_000.0)

        account = self.company.construction_retention_account_id
        liability = certificate.vendor_bill_id.line_ids.filtered(
            lambda l: l.account_id == account)
        self.assertEqual(sum(liability.mapped('balance')), -5_000.0)

    # -- D ------------------------------------------------------------------
    def test_d_the_integrity_audit_is_read_only_and_repeatable(self):
        """Running the audit twice must change nothing and say the same thing.

        Migration idempotency is proved against a legacy fixture in
        `test_m10_migration.py`; this pins the part that runs on every
        production database — the audit itself must never mutate what it is
        inspecting.
        """
        Audit = self.env['realestate.construction.integrity.audit']
        self._baselined(self.project, 1_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(None, 250_000.0)])

        def snapshot():
            return {
                model: self.env[model].search_count([])
                for model in ('realestate.construction.budget',
                              'realestate.construction.budget.line',
                              'realestate.construction.wbs',
                              'realestate.construction.cost.code',
                              'realestate.construction.contract.package',
                              'purchase.order.line',
                              'account.move.line')
            }

        before = snapshot()
        first = Audit.run(self.project)
        after_first = snapshot()
        second = Audit.run(self.project)
        after_second = snapshot()

        self.assertEqual(before, after_first, "The audit mutated data.")
        self.assertEqual(after_first, after_second)
        self.assertEqual(
            [(f['key'], f['severity'], f['count']) for f in first['findings']],
            [(f['key'], f['severity'], f['count']) for f in second['findings']],
            "The same database must produce the same findings.")
        self.assertIn('uncoded_commitment',
                      [f['key'] for f in first['findings']])

    # -- E ------------------------------------------------------------------
    def test_e_a_site_engineer_payload_carries_no_commercial_confidence(self):
        package = self._package(self.project, self.contractor,
                                value=1_000_000.0, award=True)
        claim = self._claim(self.project, package, claimed_cost=5_000_000.0)
        claim.write({'assessed_cost': 3_250_000.0,
                     'internal_position': '<p>Settle at 3.4M.</p>'})

        engineer = self.env['res.users'].create({
            'name': 'M10 site', 'login': 'm10.site',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_construction.group_construction_user').id,
                self.env.ref('atmta_real_estate.group_realestate_user').id,
            ])],
        })

        payload = self.Tower.with_user(engineer).payload(self.project)
        blob = str(payload)

        self.assertIn('quality', payload)
        self.assertNotIn('claims', payload)
        self.assertNotIn('cost', payload)
        for secret in ('3250000', '5000000', 'Settle at'):
            self.assertNotIn(secret, blob.replace('.0', ''),
                             "Commercial confidence reached the client "
                             "payload.")

        with self.assertRaises(AccessError):
            claim.with_user(engineer).read(['assessed_cost'])
