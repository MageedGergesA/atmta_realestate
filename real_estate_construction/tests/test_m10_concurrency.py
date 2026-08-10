# -*- coding: utf-8 -*-
"""M10M — the concurrency controls, re-proved before freeze.

Each of these guards a place where two people doing a normal thing at the same
moment would otherwise produce two authoritative answers: two baselines, two
certificates against the same remaining quantity, two releases of the same
retention. The protections are partial unique indexes and advisory locks put
in place across M2–M8; this file exists so that weakening one of them fails a
test rather than surfacing as a duplicate payment.

Odoo's test transaction cannot run two real sessions, so these tests attack
the guard directly: they replay the second actor's decision against state the
first has already changed, which is exactly what the guard exists to catch.
"""

from psycopg2 import IntegrityError

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestConcurrencyGuards(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('CC-CIV', 'Civil', 'subcontract')

    # -- Budget ---------------------------------------------------------
    @mute_logger('odoo.sql_db')
    def test_only_one_budget_can_be_baselined(self):
        first = self._baselined(self.project, 1_000_000.0, code=self.civil)
        second = self._budget(self.project, 2_000_000.0, code=self.civil)
        second.action_submit()
        second.action_approve()

        # Odoo's `assertRaises` override does not accept a tuple, and the
        # guard may surface either as the model's refusal or as the partial
        # unique index rejecting the row. Both are correct answers.
        refused = False
        try:
            with self.cr.savepoint():
                second.action_baseline()
        except (IntegrityError, UserError):
            refused = True
        self.assertTrue(refused, "Two baselines were allowed.")
        self.assertEqual(first.state, 'baselined')

    # -- BOQ certification ----------------------------------------------
    def test_two_certificates_cannot_consume_the_same_quantity(self):
        boq = self._boq(self.project, quantities=((100.0, 1_000.0),))
        line = boq.line_ids[0]
        first = self._certificate(self.project, self.contractor,
                                  boq_line=line, qty=100.0,
                                  contract_value=10_000_000.0)
        second = self._certificate(self.project, self.contractor,
                                   boq_line=line, qty=100.0,
                                   contract_value=10_000_000.0)
        first.action_certify()

        with self.assertRaises(UserError):
            second.action_certify()

        line.invalidate_recordset()
        self.assertEqual(line.certified_qty, 100.0)

    # -- Change ----------------------------------------------------------
    def test_implementing_a_change_order_twice_moves_the_baseline_once(self):
        self._baselined(self.project, 5_000_000.0, code=self.civil)
        Controls = self.env['realestate.construction.controls']
        order = self._change_order(
            self.project, lines=[(self.civil, 'budget', 500_000.0)])
        self._approve_and_implement(order)
        after_first = Controls.project_totals(self.project)['current_budget']

        order.action_implement()
        after_second = Controls.project_totals(self.project)['current_budget']

        self.assertEqual(after_first, 5_500_000.0)
        self.assertEqual(after_second, after_first,
                         "Implementation is idempotent by design.")

    # -- EOT -------------------------------------------------------------
    def test_implementing_an_extension_twice_moves_the_date_once(self):
        from odoo import fields
        completion = fields.Date.to_date('2027-12-31')
        package = self._package(self.project, self.contractor,
                                value=1_000_000.0, award=True,
                                current_completion_date=completion)
        claim = self._claim(self.project, package, claimed_days=30.0)
        claim.action_submit()
        eot = self._eot(claim, claimed_days=30.0, determined_days=30.0)
        eot.action_determine()
        eot.action_implement()
        package.invalidate_recordset()
        first = package.current_completion_date

        eot.action_implement()
        package.invalidate_recordset()

        self.assertEqual(package.current_completion_date, first)
        self.assertEqual(package.approved_eot_days, 30.0)

    # -- ITP -------------------------------------------------------------
    @mute_logger('odoo.sql_db')
    def test_only_one_itp_revision_can_be_active(self):
        itp = self._active_itp(self.project, self.contractor)
        revision = itp.action_create_revision()
        revision.action_submit()
        revision.action_approve()

        # The model refuses to activate a second revision while the first is
        # still in force — the plan in force must be superseded deliberately,
        # not displaced by whoever activates next. `action_supersede()` is the
        # one transition that does both, under a lock.
        with self.assertRaises(UserError):
            revision.action_activate()

        itp.action_supersede()
        itp.invalidate_recordset()
        revision.invalidate_recordset()
        self.assertEqual(itp.state, 'superseded')
        self.assertEqual(revision.state, 'active')

        refused = False
        try:
            with self.cr.savepoint():
                itp.write({'state': 'active'})
        except (IntegrityError, UserError):
            refused = True
        self.assertTrue(refused, "A superseded plan was reactivated beside "
                                 "the live one.")

    # -- Retention -------------------------------------------------------
    def test_two_releases_cannot_exceed_the_retention_held(self):
        self._configure_construction_accounts()
        certificate = self._certificate(
            self.project, self.contractor, amount=100_000.0,
            retention_pct=5.0, cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        first = self._retention_release(self.project, self.contractor,
                                        amount=3_000.0)
        second = self._retention_release(self.project, self.contractor,
                                         amount=3_000.0)
        first.action_confirm()

        with self.assertRaises(UserError):
            second.action_confirm()

        Retention = self.env['realestate.construction.retention']
        self.assertEqual(
            Retention.balance(project=self.project,
                              contractor=self.contractor), 2_000.0)

    # -- Advance ---------------------------------------------------------
    def test_two_recoveries_cannot_exceed_the_advance(self):
        self._configure_construction_accounts()
        advance = self._advance(self.project, self.contractor, 100_000.0)
        advance.action_confirm()
        advance.action_create_vendor_bill()

        first = self._certificate(self.project, self.contractor,
                                  amount=500_000.0, retention_pct=0.0,
                                  cost_code=self.civil)
        first.write({'recovery_line_ids': [(0, 0, {
            'advance_id': advance.id, 'amount': 70_000.0})]})
        second = self._certificate(self.project, self.contractor,
                                   amount=500_000.0, retention_pct=0.0,
                                   cost_code=self.civil)
        second.write({'recovery_line_ids': [(0, 0, {
            'advance_id': advance.id, 'amount': 70_000.0})]})

        first.action_certify()
        with self.assertRaises(UserError):
            second.action_certify()

        advance.invalidate_recordset()
        self.assertEqual(advance.recovered_amount, 70_000.0)
        self.assertEqual(advance.outstanding_amount, 30_000.0)

    # -- Certification ceiling -------------------------------------------
    def test_two_certificates_cannot_exceed_the_contract(self):
        package = self._package(self.project, self.contractor,
                                value=1_000_000.0, award=True)
        first = self._certificate(self.project, self.contractor,
                                  amount=800_000.0, package=package)
        second = self._certificate(self.project, self.contractor,
                                   amount=800_000.0, package=package)
        first.action_certify()

        with self.assertRaises(UserError):
            second.action_certify()

    # -- Document control -------------------------------------------------
    def test_a_document_keeps_exactly_one_current_revision(self):
        document = self._document(self.project)
        first = self._revision(document, code='A', issue=True)
        second = self._revision(document, code='B', issue=True)
        document.invalidate_recordset()

        current = document.revision_ids.filtered('is_current')
        self.assertEqual(len(current), 1)
        self.assertEqual(current, second)
        self.assertFalse(first.is_current)
