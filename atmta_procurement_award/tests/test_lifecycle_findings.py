# -*- coding: utf-8 -*-
"""Regressions for what the procurement lifecycle run found in Award."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged

from odoo.addons.atmta_procurement_evaluation.tests.common import \
    EvaluationLifecycleCommon


@tagged('post_install', '-at_install', 'atmta_award')
class TestAwardLifecycleFindings(EvaluationLifecycleCommon):

    def _create_award_action(self, round_, user=None):
        return round_.with_user(user or self.env.user).action_create_award()

    # -- item 2: an award can be started from the round, and made partial ---
    def test_a_finalised_round_offers_to_create_its_award(self):
        arch = self.env['realestate.procurement.evaluation.round'].get_view(
            view_type='form')['arch']
        self.assertIn('name="action_create_award"', arch)

        round_ = self._finalised_round()
        action = self._create_award_action(round_)
        self.assertEqual(action['res_model'], 'realestate.procurement.award')
        context = action['context']
        self.assertEqual(context['default_round_id'], round_.id)
        self.assertEqual(context['default_sourcing_event_id'],
                         round_.sourcing_event_id.id)

    def test_the_award_form_records_a_partial_award(self):
        round_ = self._finalised_round()
        action = self._create_award_action(round_)
        top = round_.candidate_ids.filtered(lambda c: c.rank == 1)
        form = Form(self.env['realestate.procurement.award'].with_context(
            **action['context']))
        form.award_type = 'partial'
        with form.line_ids.new() as line:
            line.candidate_id = top
        award = form.save()
        self.assertEqual(award.award_type, 'partial')
        self.assertEqual(award.round_id, round_)
        self.assertEqual(award.sourcing_event_id, round_.sourcing_event_id)

        award.line_ids.allocation_ids.quantity = 600
        award.action_submit()
        self.assertEqual(award.state, 'review')

        # Decided once submitted: the type is part of what is approved.
        form = Form(award)
        self.assertTrue(form._get_modifier('award_type', 'readonly'))

    def test_an_award_cannot_be_created_from_an_unfinished_round(self):
        round_ = self._commercial_round()
        with self.assertRaises(UserError):
            self._create_award_action(round_)

    def test_only_award_drafters_may_start_one(self):
        round_ = self._finalised_round()
        tech = self._user('lcf.award.tech',
                          'atmta_roles.group_procurement_technical_evaluator')
        with self.assertRaises(AccessError):
            self._create_award_action(round_, user=tech)

    def test_a_second_live_award_is_refused_at_the_button(self):
        round_ = self._finalised_round()
        top = round_.candidate_ids.filtered(lambda c: c.rank == 1)
        self.env['realestate.procurement.award'].create({
            'sourcing_event_id': round_.sourcing_event_id.id,
            'round_id': round_.id,
            'line_ids': [(0, 0, {'candidate_id': top.id})]})
        with self.assertRaises(UserError):
            self._create_award_action(round_)
