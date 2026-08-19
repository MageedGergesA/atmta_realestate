# -*- coding: utf-8 -*-
"""M7 — concurrency, company isolation, and who may see an award.

The confidentiality question is the one that carries real risk here. M6 went to
some trouble to keep the commercial outcome away from technical evaluators —
field groups, a domain guard, an order guard, an aggregate guard — and an award
states, in one short document, **who won and for how much**. Undoing that
milestone's work by shipping a new model with a careless ACL would be the
easiest mistake in this programme to make and one of the hardest to notice.

The concurrency file states its limit the way M3 through M6 did: Odoo's harness
shares one cursor, so a genuine two-process race cannot run here. What is tested
is each mechanism that makes the race safe — the unique index that refuses the
duplicate, and the state machine that makes a second click a no-op rather than a
second commitment.
"""

import psycopg2

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .test_m7_award import M7Common


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7Concurrency(M7Common):

    def test_an_award_revision_number_is_taken_once(self):
        round_ = self._finalised_round()
        award = self._award(round_)

        with mute_logger('odoo.sql_db'):
            with self.assertRaises(psycopg2.IntegrityError):
                with self.env.cr.savepoint():
                    self.Award.sudo().create({
                        'sourcing_event_id': self.event.id,
                        'round_id': round_.id,
                        'revision': award.revision,
                    })

    def test_a_tender_line_is_allocated_once_per_award_line(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        allocation = award.line_ids.allocation_ids[:1]

        with mute_logger('odoo.sql_db'):
            with self.assertRaises(psycopg2.IntegrityError):
                with self.env.cr.savepoint():
                    self.env[
                        'realestate.procurement.award.allocation'].create({
                            'award_line_id': allocation.award_line_id.id,
                            'sourcing_line_id': allocation.sourcing_line_id.id,
                            'quantity': 1.0,
                        })

    def test_approving_twice_is_refused(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        approver = self._second_manager()
        award.with_user(approver).action_approve()
        approved_on = award.approved_on

        with self.assertRaises(UserError):
            award.with_user(approver).action_approve()

        self.assertEqual(award.approved_on, approved_on,
                         "A second approval moved the recorded moment.")

    def test_issuing_twice_does_not_commit_twice(self):
        """The one that would actually cost money."""
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        award.action_issue()
        commitment_after_first = self._commitment(self.project)
        issued_on = award.issued_on

        with self.assertRaises(UserError):
            award.action_issue()

        self.assertEqual(award.issued_on, issued_on)
        self.assertEqual(self._commitment(self.project),
                         commitment_after_first,
                         "Issuing twice committed the budget twice.")

    def test_a_second_live_award_on_one_evaluation_is_refused(self):
        round_ = self._finalised_round()
        self._award(round_)

        with self.assertRaises(UserError):
            self._award(round_, revision=1)

    def test_revising_twice_does_not_leave_two_live_awards(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()

        first = award.action_new_revision(reason='Quantity reduced')
        second = first.action_new_revision(reason='Vendor changed plant')

        live = self.Award.search([
            ('round_id', '=', round_.id),
            ('state', '!=', 'cancelled'),
            ('superseded', '=', False),
        ])
        self.assertEqual(live, second)
        self.assertEqual(second.revision, 2)

    def test_cancelling_the_order_after_issue_reverses_once(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        award.action_issue()
        order = award.line_ids.purchase_order_id

        order.button_cancel()
        first = self._position()
        order.button_cancel()

        self.assertEqual(self._position(), first,
                         "Cancelling twice moved the position twice.")
        self.assertEqual(self._commitment(self.project), 0.0)


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7CompanyIsolation(M7Common):

    def setUp(self):
        super().setUp()
        self.company_b = self.env['res.company'].create({'name': 'M7 Co B'})
        self.env.user.company_ids = [(4, self.company_b.id)]
        self.round = self._finalised_round()
        self.award = self._award(self.round)
        self.award.action_submit()
        self.award.with_user(self._second_manager()).action_approve()

    def _b_user(self):
        return self.env['res.users'].create({
            'name': 'm7.b.only', 'login': 'm7.b.only',
            'email': 'm7.b.only@example.com',
            'company_id': self.company_b.id,
            'company_ids': [(6, 0, [self.company_b.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('base.group_multi_company').id,
                self.env.ref(
                    'real_estate_procurement.group_procurement_manager').id,
            ])],
        })

    def test_a_company_b_manager_sees_no_company_a_award(self):
        user = self._b_user()
        for model in ('realestate.procurement.award',
                      'realestate.procurement.award.line',
                      'realestate.procurement.award.allocation'):
            found = self.env[model].with_user(user).search([])
            self.assertFalse(found, "%s leaked company A records: %s"
                                    % (model, found))

    def test_the_rpc_payload_for_company_b_carries_no_award_amount(self):
        user = self._b_user()
        rows = self.env['realestate.procurement.award'].with_user(
            user).with_context(
                allowed_company_ids=[self.company_b.id]).search_read(
                    [], ['amount_total', 'name'])
        self.assertEqual(rows, [])

    def test_an_award_carries_the_company_of_its_tender(self):
        self.assertEqual(self.award.company_id, self.company)
        self.assertEqual(self.award.line_ids.company_id, self.company)
        self.assertEqual(self.award.line_ids.allocation_ids.mapped(
            'company_id'), self.company)


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7AwardConfidentiality(M7Common):
    """M6 hid the commercial ordering. M7 must not hand it back.

    An award says who won and for how much. If a Technical Evaluator can read
    one, every guard M6 put on `financial_score`, `combined_score` and `rank`
    was pointless — they would simply read the answer off the award instead.
    """

    AWARD_MODELS = (
        'realestate.procurement.award',
        'realestate.procurement.award.line',
        'realestate.procurement.award.allocation',
    )

    def _issued_award(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager()).action_approve()
        return award

    def _tech(self, login='m7.tech'):
        return self._evaluator(login, 'technical_evaluator')

    def test_a_technical_evaluator_cannot_read_an_award(self):
        award = self._issued_award()
        tech = self._tech()

        for model in self.AWARD_MODELS:
            with self.assertRaises(AccessError, msg=model):
                self.env[model].with_user(tech).search([])
        with self.assertRaises(AccessError):
            award.with_user(tech).read(['amount_total'])

    def test_a_technical_evaluator_cannot_probe_the_award_by_domain(self):
        self._issued_award()
        tech = self._tech('m7.tech2')

        for domain in ([('amount_total', '>', 0)],
                       [('state', '=', 'approved')],
                       [('line_ids.partner_id', '!=', False)]):
            with self.assertRaises(AccessError, msg=str(domain)):
                self.env['realestate.procurement.award'].with_user(
                    tech).search(domain)

    def test_a_technical_evaluator_cannot_issue_the_award_document(self):
        award = self._issued_award()
        tech = self._tech('m7.tech3')

        with self.assertRaises(AccessError):
            award.with_user(tech).action_print_award()

    def test_the_award_document_is_not_offered_to_evaluation_groups(self):
        report = self.env.ref('real_estate_procurement.action_report_award')
        allowed = set(report.groups_id.mapped('id'))

        for xmlid in ('real_estate_procurement.group_evaluation_technical',
                      'real_estate_procurement.group_evaluation_commercial'):
            self.assertNotIn(
                self.env.ref(xmlid).id, allowed,
                "%s is offered the award document directly. Awarding is a "
                "buying decision, not an evaluation one." % xmlid)

    def test_a_commercial_evaluator_is_not_thereby_an_approver(self):
        """Seeing the money is not the same as authorising the spend."""
        award = self._issued_award()
        commercial = self._evaluator('m7.comm', 'commercial_evaluator')

        with self.assertRaises(AccessError):
            award.with_user(commercial).action_approve()
        with self.assertRaises(AccessError):
            award.with_user(commercial).action_issue()

    def test_no_award_model_carries_an_unrestricted_evaluation_score(self):
        """A structural sweep, so a convenience field cannot reopen M6."""
        banned = ('financial_score', 'combined_score', 'technical_score',
                  'evaluated_cost')
        for model in self.AWARD_MODELS:
            for name in self.env[model]._fields:
                self.assertNotIn(
                    name, banned,
                    "%s.%s copies an evaluation score onto the award, where "
                    "M6's field restrictions do not reach." % (model, name))


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m7')
class TestM7AwardRoleMatrix(M7Common):
    """Who may do what, asserted rather than described."""

    def _user(self, login, *groups):
        return self.env['res.users'].create({
            'name': login, 'login': login, 'email': '%s@example.com' % login,
            'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id]
                           + [self.env.ref(g).id for g in groups])],
        })

    def test_a_requester_reaches_no_award_at_all(self):
        round_ = self._finalised_round()
        self._award(round_)
        requester = self._user(
            'm7.req', 'real_estate_procurement.group_procurement_requester')

        with self.assertRaises(AccessError):
            self.env['realestate.procurement.award'].with_user(
                requester).search([])

    def test_a_buyer_may_raise_an_award_but_not_authorise_it(self):
        round_ = self._finalised_round()
        buyer = self._user(
            'm7.buy', 'real_estate_procurement.group_procurement_user',
            'purchase.group_purchase_user')
        award = self._award(round_)

        award.with_user(buyer).action_submit()
        self.assertEqual(award.state, 'review')

        with self.assertRaises(AccessError):
            award.with_user(buyer).action_approve()
        with self.assertRaises(AccessError):
            award.with_user(buyer).action_issue()
        with self.assertRaises(AccessError):
            award.with_user(buyer).action_cancel(reason='No')
        self.assertEqual(self._commitment(self.project), 0.0)

    def test_a_procurement_manager_may_authorise(self):
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        manager = self._second_manager('m7.mgr2')

        award.with_user(manager).action_approve()

        self.assertEqual(award.state, 'approved')

    def test_a_generic_purchase_user_reaches_no_award(self):
        round_ = self._finalised_round()
        self._award(round_)
        buyer = self._user('m7.pur', 'purchase.group_purchase_manager')

        with self.assertRaises(AccessError):
            self.env['realestate.procurement.award'].with_user(
                buyer).search([])
