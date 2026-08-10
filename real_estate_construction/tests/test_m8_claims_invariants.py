# -*- coding: utf-8 -*-
"""M8 — the five invariants, written before the models that must satisfy them.

Claims are where a construction system is most tempted to be clever, and where
being clever is most dangerous: entitlement is decided by a contract, a
governing law and a human, not by a Selection field. Everything below states
what the software must *refuse* to conclude on its own.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestClaimInvariants(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        self.completion = fields.Date.to_date('2027-12-31')
        self.package = self._package(
            self.project, self.contractor, value=10_000_000.0, award=True,
            current_completion_date=self.completion)

    # -- A ------------------------------------------------------------------
    def test_a_a_claim_moves_no_money(self):
        """A claim is a request. Nothing about it is authorised yet.

        M4 owns the budget, M4 owns the commitment, the ledger owns the
        actual. A claim that moved any of them would be the software granting
        itself an entitlement nobody determined.
        """
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 3_000_000.0)])
        self._post_bill(self.project, self.civil, 1_000_000.0)
        before = self.Controls.project_totals(self.project)

        claim = self._claim(self.project, self.package,
                            claimed_cost=5_000_000.0)
        claim.action_submit()

        after = self.Controls.project_totals(self.project)
        self.assertEqual(after['current_budget'], before['current_budget'])
        self.assertEqual(after['current_commitment'],
                         before['current_commitment'])
        self.assertEqual(after['actual_cost'], before['actual_cost'])

    # -- B ------------------------------------------------------------------
    def test_b_claimed_days_do_not_move_the_completion_date(self):
        """Sixty days claimed is sixty days asked for, not sixty days granted."""
        claim = self._claim(self.project, self.package, claimed_days=60.0)
        claim.action_submit()

        self.package.invalidate_recordset()
        self.assertEqual(self.package.original_completion_date, self.completion)
        self.assertEqual(self.package.current_completion_date, self.completion)
        self.assertEqual(self.package.approved_eot_days, 0.0)
        self.assertEqual(self.package.claimed_eot_days, 60.0,
                         "Claimed days are reported — beside the contract "
                         "date, never inside it.")

    # -- C ------------------------------------------------------------------
    def test_c_only_an_implemented_eot_moves_the_current_date(self):
        claim = self._claim(self.project, self.package, claimed_days=60.0)
        claim.action_submit()
        eot = self._eot(claim, claimed_days=60.0, determined_days=30.0)

        # Determined, but not yet implemented.
        eot.action_determine()
        self.package.invalidate_recordset()
        self.assertEqual(self.package.current_completion_date, self.completion)

        eot.action_implement()
        self.package.invalidate_recordset()
        self.assertEqual(self.package.approved_eot_days, 30.0)
        self.assertEqual(self.package.current_completion_date,
                         self.completion + relativedelta(days=30))
        self.assertEqual(
            self.package.original_completion_date, self.completion,
            "The original completion date is the contract. It never moves.")

    # -- D ------------------------------------------------------------------
    def test_d_a_late_notice_is_flagged_not_judged(self):
        """The consequence of a late notice is a matter of contract and law.

        ATMTA says the notice was late. It does not say there is no
        entitlement, and it does not close the claim.
        """
        self.package.write({'notice_required': True, 'notice_period_days': 28})
        awareness = fields.Date.to_date('2027-03-01')
        notice = self._notice(self.project, self.package,
                              awareness_date=awareness,
                              notice_date=awareness + relativedelta(days=35))

        self.assertEqual(notice.status, 'late')
        self.assertTrue(notice.is_late)
        self.assertIn('after', notice.deadline_warning or '')

        claim = self._claim(self.project, self.package,
                            claimed_cost=1_000_000.0, notice=notice)
        claim.action_submit()

        self.assertEqual(claim.state, 'submitted',
                         "A late notice does not reject a claim. Only a "
                         "determination does that, and a person makes it.")
        self.assertTrue(claim.has_late_notice)

    # -- E ------------------------------------------------------------------
    def test_e_a_materialised_risk_is_preserved_and_counted_once(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        before = self.Controls.project_totals(self.project)

        risk = self._risk(self.project, cost_exposure=2_000_000.0)
        risk.action_assess()
        risk.action_plan_response()
        risk.action_monitor()

        issue = risk.action_materialise(
            reason='The supplier confirmed the plant will not ship.')

        self.assertEqual(risk.state, 'materialised')
        self.assertTrue(risk.exists(), "The risk is history, not a draft.")
        self.assertEqual(issue.source_risk_id, risk)
        self.assertEqual(risk.issue_id, issue)
        self.assertTrue(risk.materialised_snapshot,
                        "What the risk said when it came true is the part "
                        "worth keeping.")

        event = issue.action_create_change_event()

        after = self.Controls.project_totals(self.project)
        self.assertEqual(after['current_budget'], before['current_budget'])
        self.assertEqual(after['current_commitment'],
                         before['current_commitment'])

        exposure = self.env['realestate.construction.exposure'].for_project(
            self.project)
        self.assertEqual(
            exposure['potential_commercial'], 2_000_000.0,
            "Risk, issue and change event are one exposure seen three times, "
            "not three exposures.")
        self.assertEqual(exposure['risk_only'], 0.0,
                         "A risk that became a change event is no longer "
                         "counted as an open risk exposure.")
        self.assertEqual(event.project_id, self.project)
