# -*- coding: utf-8 -*-
"""M6 — the real-browser gate for evaluation, plus role confidentiality.

The tour drives a finalised evaluation. The two guarantees a browser cannot
express — that a technical evaluator is refused commercial data at the RPC
layer, and that a finalised evaluation still cannot authorise a purchase — are
asserted here in Python around it.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import HttpCase, tagged

from .common import M6Common


class M6BrowserCommon(M6Common, HttpCase):

    def _finalised_round(self):
        self.vendor_a.name = 'Eval Vendor A'
        self.vendor_b.name = 'Eval Vendor B'
        self.vendor_c.name = 'Eval Vendor C'
        plan = self._plan()
        round_ = self._round(plan)
        # B fails a mandatory criterion, so the tour has a real exclusion to
        # show rather than three identical passes.
        failed = self._candidate(round_, self.vendor_b)
        self._score(round_, failed, rated=10.0, mandatory='fail')
        self._score_others(round_, exclude=failed)
        round_.action_finalise_technical()
        round_.action_open_commercial()
        round_.action_normalise()
        winner = round_.candidate_ids.filtered(
            lambda c: c.partner_id == self.vendor_a)
        winner.analysis_id.action_add_adjustment(
            adjustment_type='freight', amount=50_000.0,
            rationale='Freight to site, excluded from the quoted rate')
        round_.action_finalise()
        return round_

    def _browser_admin(self):
        admin = self.env.ref('base.user_admin')
        admin.groups_id |= (
            self.env.ref('real_estate_procurement.group_evaluation_manager')
            | self.env.ref('real_estate_procurement.group_procurement_manager')
            | self.env.ref('atmta_real_estate.group_realestate_user'))
        self.env.flush_all()
        return admin


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6EvaluationBrowser(M6BrowserCommon):

    def test_the_evaluation_screens_open_and_tell_the_truth(self):
        round_ = self._finalised_round()
        self._browser_admin()

        self.start_tour('/odoo', 'procurement_evaluation_tour', login='admin',
                        timeout=300)

        self.assertEqual(round_.state, 'finalised')
        self.assertTrue(round_.candidate_ids.filtered('rank'))

    def test_a_finalised_evaluation_still_authorises_no_purchase(self):
        round_ = self._finalised_round()
        winner = round_.candidate_ids.filtered(lambda c: c.rank == 1)[:1]
        order = winner.bid_response_id.invitation_id.purchase_order_id

        with self.assertRaises(UserError):
            order.button_confirm()
        with self.assertRaises(UserError):
            order.with_context(skip_alternative_check=True).button_confirm()

        self.assertEqual(order.state, 'draft')
        self.assertEqual(self._commitment(self.project), 0.0)
        self.assertEqual(self._actual(self.project), 0.0)
        self.assertEqual(self._reserved(self.project, self.concrete),
                         3_000_000.0)

    def test_the_raw_bid_is_shown_beside_the_evaluated_cost(self):
        round_ = self._finalised_round()
        analysis = round_.candidate_ids.filtered(
            lambda c: c.partner_id == self.vendor_a).analysis_id

        self.assertEqual(analysis.raw_amount, 2_800_000.0)
        self.assertEqual(analysis.adjustment_total, 50_000.0)
        self.assertEqual(analysis.evaluated_cost, 2_850_000.0)
        self.assertEqual(analysis.bid_response_id.amount_untaxed, 2_800_000.0,
                         "The submitted bid moved.")


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6BrowserRoles(M6BrowserCommon):
    """A technical evaluator is refused commercial data over real RPC."""

    def test_a_technical_evaluator_is_refused_commercial_data_over_rpc(self):
        round_ = self._finalised_round()
        evaluator = self.env['res.users'].create({
            'name': 'Eval Tech', 'login': 'eval.tech',
            'password': 'eval.tech.pw', 'email': 'eval.tech@example.com',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_procurement.group_evaluation_technical').id,
            ])],
        })
        self.env.flush_all()
        self.authenticate('eval.tech', 'eval.tech.pw')

        response = self.url_open(
            '/web/dataset/call_kw',
            data=('{"jsonrpc":"2.0","method":"call","params":{'
                  '"model":"realestate.procurement.commercial.analysis",'
                  '"method":"web_search_read","args":[],'
                  '"kwargs":{"domain":[],"specification":{"evaluated_cost":{}}}'
                  '}}'),
            headers={'Content-Type': 'application/json'})

        self.assertIn('error', response.json(),
                      "A technical evaluator read evaluated costs over RPC.")
        self.assertNotIn('2850000', response.text.replace(',', ''))
        with self.assertRaises(AccessError):
            self.env[
                'realestate.procurement.commercial.analysis'].with_user(
                    evaluator).search([])
        # And the round itself is readable — segregation is about money, not
        # about hiding that an evaluation exists.
        self.assertTrue(round_.with_user(evaluator).read(['name']))
