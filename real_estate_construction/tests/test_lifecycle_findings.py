# -*- coding: utf-8 -*-
"""Regressions for what the developer lifecycle run found.

Construction adds a page to the project and phase forms that Developer owns.
A Developer-only user (sales agent, commercial manager) opens those forms all
day; the milestones and cost lines on the page are Construction records they
hold no right to read, so the form itself failed to open with

    AccessError: You are not allowed to access 'Construction Milestone'

Each test opens the form the way the web client does (``odoo.tests.Form``),
as the user who reported it, on a new record and on an existing one.
"""

import unittest

from odoo.exceptions import AccessError, UserError
from odoo.tests import Form
from odoo.tests.common import new_test_user, tagged

from .common import ConstructionCommon, module_installed


@tagged('post_install', '-at_install')
class TestDeveloperOnlyUserOpensProjectForms(ConstructionCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The users under test are Developer's; Construction does not depend
        # on Developer, so without it there is nobody who reads a project and
        # not its milestones.
        if not module_installed(cls.env, 'real_estate_developer'):
            raise unittest.SkipTest('real_estate_developer is not installed')
        cls.agent = new_test_user(
            cls.env, login='cons_find_agent',
            groups='base.group_user,real_estate_developer.group_dev_agent')
        cls.commercial = new_test_user(
            cls.env, login='cons_find_commercial',
            groups='base.group_user,'
                   'real_estate_developer.group_dev_commercial_manager')
        # Construction users keep the page. Developer read access is added
        # because the project form itself is Developer's (it shows units).
        cls.builder = new_test_user(
            cls.env, login='cons_find_builder',
            groups='base.group_user,'
                   'real_estate_developer.group_dev_readonly,'
                   'real_estate_construction.group_construction_user')

    def _existing_project(self):
        project = self._project()
        self._milestone(project)
        self.CostLine.create({
            'name': 'Concrete', 'project_id': project.id, 'amount': 1000.0})
        phase = self.env['realestate.phase'].create({
            'name': 'Phase A', 'project_id': project.id})
        self._milestone(project, phase_id=phase.id)
        return project, phase

    def test_developer_users_open_the_project_form(self):
        project, _phase = self._existing_project()
        for user in (self.agent, self.commercial):
            with self.subTest(user=user.login):
                Form(self.Project.with_user(user))
                Form(project.with_user(user))

    def test_developer_users_open_the_phase_form(self):
        _project, phase = self._existing_project()
        for user in (self.agent, self.commercial):
            with self.subTest(user=user.login):
                Form(self.env['realestate.phase'].with_user(user))
                Form(phase.with_user(user))

    def test_construction_users_still_get_the_page(self):
        project, phase = self._existing_project()
        form = Form(project.with_user(self.builder))
        self.assertEqual(len(form.milestone_ids), 2)
        self.assertEqual(len(form.cost_line_ids), 1)
        self.assertEqual(len(Form(phase.with_user(self.builder)).milestone_ids), 1)

    def test_the_analytic_button_is_refused_where_the_page_is_hidden(self):
        """The button lives on the hidden page, so the method is guarded too."""
        project, _phase = self._existing_project()
        with self.assertRaises(AccessError):
            project.with_user(self.agent).action_generate_analytic_account()
        self.assertFalse(project.analytic_account_id)
        project.with_user(self.builder).action_generate_analytic_account()
        self.assertTrue(project.analytic_account_id)


@tagged('post_install', '-at_install')
class TestOmissionConsumedValueIsPerPackage(ConstructionCommon):
    """What an omission is checked against is the package's own certified value.

    The construction lifecycle run found an omission on a contractor's second
    package refused with the amount certified under the first: the consumed
    value was summed by project and contractor, not by package.
    """

    def setUp(self):
        super().setUp()
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('LF-CIV', 'Civil', 'subcontract')
        self.first = self._package(self.project, self.contractor,
                                   value=10_000_000.0, award=True)
        self.second = self._package(self.project, self.contractor,
                                    value=10_000_000.0, award=True)
        (self.first | self.second).cost_code_ids = self.civil

    def _omit(self, package, amount):
        order = self._change_order(
            self.project, contractor=self.contractor, package=package,
            lines=[(self.civil, 'commitment', -amount)])
        order.action_submit()
        order.action_request_approval()
        order.action_approve()
        return order

    def test_another_package_certified_value_does_not_block_an_omission(self):
        certificate = self._certificate(
            self.project, self.contractor, contract_value=10_000_000.0,
            pct=80.0, package=self.first)
        certificate.action_certify()
        self.assertEqual(self.first._consumed_value(), 8_000_000.0)
        self.assertEqual(self.second._consumed_value(), 0.0)

        self._omit(self.second, 5_000_000.0).action_implement()
        self.second.invalidate_recordset()
        self.assertEqual(self.second.current_contract_value, 5_000_000.0)

    def test_the_package_own_certified_value_still_blocks_it(self):
        certificate = self._certificate(
            self.project, self.contractor, contract_value=10_000_000.0,
            pct=80.0, package=self.second)
        certificate.action_certify()
        with self.assertRaises(UserError):
            self._omit(self.second, 5_000_000.0).action_implement()


@tagged('post_install', '-at_install')
class TestContractorRetentionHeld(ConstructionCommon):
    """The contractor's Retention Held follows the register.

    Found by the lifecycle run: the stored figure did not move when a release
    was confirmed, and a certificate that was certified but not yet billed
    already counted as retention held.
    """

    def setUp(self):
        super().setUp()
        self._configure_construction_accounts()
        self.project = self._project()
        self.contractor = self._contractor(retention=10.0)
        self.certificate = self._certificate(
            self.project, self.contractor, contract_value=500_000.0, pct=20.0)

    def test_a_certified_but_unbilled_certificate_holds_nothing(self):
        self.certificate.action_certify()
        self.assertEqual(self.contractor.total_certified, 100_000.0)
        self.assertEqual(self.contractor.total_retention_held, 0.0)

    def test_a_confirmed_release_reduces_retention_held(self):
        self.certificate.action_certify()
        self.certificate.action_create_vendor_bill()
        self.assertEqual(self.contractor.total_retention_held, 10_000.0)

        release = self._retention_release(
            self.project, self.contractor, amount=2_500.0)
        release.action_confirm()
        self.assertEqual(self.contractor.total_retention_held, 7_500.0)


@tagged('post_install', '-at_install')
class TestExceptionsPanelForConstructionUser(ConstructionCommon):
    """A site engineer holds no purchase rights but sees the exceptions panel.

    The lifecycle run found the panel failing for a Construction User with
    AccessError on 'Purchase Order Line'. The panel only needs the count.
    """

    def test_a_construction_user_gets_the_uncoded_purchase_line_count(self):
        project = self._project()
        contractor = self._contractor()
        self._po(project, contractor, [(None, 500_000.0)])
        site = new_test_user(
            self.env, login='cons_find_site',
            groups='base.group_user,'
                   'real_estate_construction.group_construction_user')
        payload = self.env['realestate.construction.control.tower'].with_user(
            site).payload(project.id, sections=['exceptions'])
        self.assertNotIn('failed', payload['exceptions'])
        entry = [e for e in payload['exceptions']['exceptions']
                 if e['key'] == 'uncoded_po_lines']
        self.assertEqual(entry and entry[0]['count'], 1)

    def test_the_count_only_offers_a_click_the_reader_can_make(self):
        """The figure reaches the site engineer; the lines do not.

        A count taken with sudo and an action on `purchase.order.line` is
        half a fix: the tile was right and opening it raised
        AccessError. An exception whose records this reader may not open
        carries no action rather than one that can only fail.
        """
        project = self._project()
        contractor = self._contractor()
        self._po(project, contractor, [(None, 500_000.0)])
        site = new_test_user(
            self.env, login='cons_find_site_drill',
            groups='base.group_user,'
                   'real_estate_construction.group_construction_user')
        Exceptions = self.env['realestate.construction.exceptions']

        entry = [e for e in Exceptions.with_user(site).for_project(
            project)['exceptions'] if e['key'] == 'uncoded_po_lines'][0]
        self.assertEqual(entry['count'], 1)
        self.assertFalse(entry['openable'])
        self.assertFalse(entry['action'])

        # Every exception the panel still offers this reader opens without an
        # access error. (The counts are not asserted here: some entries count
        # occurrences of a condition rather than rows — "no baselined budget"
        # is one exception and zero budget records — and the sentence carries
        # the number in those cases.)
        for other in Exceptions.with_user(site).for_project(
                project)['exceptions']:
            if not other['openable']:
                continue
            with self.subTest(exception=other['key']):
                self.env[other['action']['res_model']].with_user(
                    site).search(other['action']['domain'])

    def test_a_reader_with_purchase_rights_still_opens_the_lines(self):
        project = self._project()
        contractor = self._contractor()
        self._po(project, contractor, [(None, 500_000.0)])
        entry = [e for e in self.env[
            'realestate.construction.exceptions'].for_project(
                project)['exceptions']
            if e['key'] == 'uncoded_po_lines'][0]
        self.assertTrue(entry['openable'])
        self.assertEqual(entry['action']['res_model'], 'purchase.order.line')
        self.assertEqual(self.env['purchase.order.line'].search_count(
            entry['action']['domain']), entry['count'])


@tagged('post_install', '-at_install')
class TestProcurementPanelFollowsTheRole(ConstructionCommon):
    """A panel the reader may not read is absent, not an error banner.

    The tower's `procurement` panel counts purchase orders and order lines
    with the reader's own rights. No construction role carries Purchase, so
    for a Construction Manager the panel raised inside its savepoint and the
    screen showed "The procurement panel failed to load: You are not allowed
    to access 'Purchase Order Line'".
    """

    def test_a_manager_without_purchase_rights_gets_no_procurement_panel(self):
        project = self._project()
        manager = new_test_user(
            self.env, login='cons_find_tower_manager',
            groups='base.group_user,'
                   'real_estate_construction.group_construction_manager')
        Tower = self.env['realestate.construction.control.tower'].with_user(
            manager)
        self.assertFalse(
            self.env['purchase.order.line'].with_user(manager).has_access(
                'read'),
            "the finding assumes no construction role carries Purchase")
        self.assertNotIn('procurement', Tower.sections_for_user())
        payload = Tower.payload(project.id)
        self.assertNotIn('procurement', payload['sections'])
        self.assertNotIn('procurement', payload)

    def test_a_manager_with_purchase_rights_keeps_it(self):
        project = self._project()
        manager = new_test_user(
            self.env, login='cons_find_tower_buyer',
            groups='base.group_user,purchase.group_purchase_user,'
                   'real_estate_construction.group_construction_manager')
        Tower = self.env['realestate.construction.control.tower'].with_user(
            manager)
        self.assertIn('procurement', Tower.sections_for_user())
        payload = Tower.payload(project.id)
        self.assertNotIn('failed', payload['procurement'])


@tagged('post_install', '-at_install')
class TestEotDaysDoNotBreakTheTowerForSiteRoles(ConstructionCommon):
    """A site engineer's Risk panel must not fail on Extensions of Time.

    `approved_eot_days` is stored and `claimed_eot_days` is not, so Odoo
    computes the pair with and without sudo through the same method — the
    registry says as much at start-up ("inconsistent 'compute_sudo'"). The
    claims KPIs sum both over the project's packages, so the moment a site or
    cost reader hits the tower with a cold cache the Risk panel and the Health
    card come back as "You are not allowed to access 'Extension of Time'".

    Existing tests miss it because the fixture creates everything as admin in
    the same transaction and the record cache is shared: the figure is already
    computed by the time the restricted user reads it. `invalidate_all()` is
    what a fresh RPC does.
    """

    def setUp(self):
        super().setUp()
        self.project = self._project()
        self.contractor = self._contractor()
        self.package = self._package(self.project, self.contractor,
                                     value=1_000_000.0, award=True)
        claim = self._claim(self.project, self.package, claimed_days=30.0)
        claim.action_submit()
        self.site = new_test_user(
            self.env, login='cons_find_eot_site',
            groups='base.group_user,'
                   'real_estate_construction.group_construction_user')

    def test_the_risk_and_health_panels_answer_for_a_site_reader(self):
        self.assertEqual(self.package.claimed_eot_days, 30.0)
        self.env.invalidate_all()
        payload = self.env[
            'realestate.construction.control.tower'].with_user(
                self.site).payload(self.project.id)
        for section in ('risk', 'health', 'header'):
            with self.subTest(section=section):
                self.assertNotIn('failed', payload[section],
                                 payload[section].get('error', ''))

    def test_the_claim_amounts_stay_out_of_that_payload(self):
        """Reading the day count must not open the commercial position."""
        self.env.invalidate_all()
        payload = self.env[
            'realestate.construction.control.tower'].with_user(
                self.site).payload(self.project.id)
        self.assertNotIn('claims', payload)
        self.assertNotIn('commercial', payload)
