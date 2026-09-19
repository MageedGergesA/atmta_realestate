# -*- coding: utf-8 -*-
"""Findings of the operations lifecycle run, each pinned by a test.

Every test here failed before its fix, for the reason its docstring gives.
"""

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import new_test_user, tagged

from .common import HandoverCommon


@tagged('post_install', '-at_install')
class TestHandoverLifecycleFindings(HandoverCommon):

    def _complete(self, handover):
        handover.action_start_inspection()
        handover.action_complete()
        return handover

    # 1 ------------------------------------------------------------------
    def test_completing_hands_over_an_active_contract(self):
        """Only 'signed' contracts were handed over; an activated one stayed active."""
        contract = self._contract(self.units[0])
        contract.action_activate()
        self._complete(self._handover(contract))
        self.assertEqual(contract.state, 'handed_over')

    # 2 ------------------------------------------------------------------
    def test_an_unverified_or_rejected_snag_blocks_completion(self):
        """Only open/assigned/in-progress snags blocked; a resolved-but-unverified
        or a rejected fix let the unit be handed over."""
        for final in ('resolved', 'rejected'):
            with self.subTest(final=final):
                contract = self._contract(self.units[1 if final == 'resolved' else 2])
                handover = self._handover(contract)
                handover.action_start_inspection()
                snag = self._snag(handover, 'critical')
                snag.action_assign()
                snag.action_resolve()
                if final == 'rejected':
                    snag.action_reject()
                self.assertEqual(handover.open_snagging_count, 0)
                with self.assertRaises(UserError):
                    handover.action_complete()
                self.assertEqual(handover.state, 'inspection')

    def test_verified_snags_do_not_block_completion(self):
        handover = self._handover(self._contract(self.units[0]))
        handover.action_start_inspection()
        snag = self._snag(handover, 'major')
        snag.action_assign()
        snag.action_resolve()
        snag.action_verify()
        handover.action_complete()
        self.assertEqual(handover.state, 'completed')

    # 3 ------------------------------------------------------------------
    def test_a_completed_handover_cannot_be_cancelled(self):
        """action_cancel accepted a completed handover (the button is hidden)."""
        handover = self._complete(self._handover(self._contract(self.units[0])))
        with self.assertRaises(UserError):
            handover.action_cancel()
        self.assertEqual(handover.state, 'completed')

    def test_a_scheduled_handover_cannot_be_completed(self):
        """action_complete skipped inspection entirely (the button is hidden)."""
        handover = self._handover(self._contract(self.units[0]))
        with self.assertRaises(UserError):
            handover.action_complete()
        self.assertEqual(handover.state, 'scheduled')

    def test_snagging_is_marked_only_from_inspection(self):
        """action_mark_snagging worked from any state (the button is hidden)."""
        handover = self._handover(self._contract(self.units[0]))
        with self.assertRaises(UserError):
            handover.action_mark_snagging()
        handover.action_start_inspection()
        handover.action_mark_snagging()
        self.assertEqual(handover.state, 'snagging')

    # 4 ------------------------------------------------------------------
    def test_no_handover_for_an_unsigned_contract(self):
        """A draft sale contract could be scheduled for handover."""
        draft = self._contract(self.units[3], sign=False)
        self.assertEqual(draft.state, 'draft')
        with self.assertRaises(ValidationError):
            self._handover(draft)

    def test_one_live_handover_per_contract(self):
        """Several open handovers could be scheduled for the same contract."""
        contract = self._contract(self.units[0])
        first = self._handover(contract)
        with self.assertRaises(ValidationError):
            self._handover(contract)
        # A cancelled one does not stand in the way of rescheduling.
        first.action_cancel()
        second = self._handover(contract)
        self.assertEqual(second.state, 'scheduled')

    def test_the_contract_field_offers_only_handover_able_contracts(self):
        domain = self.Handover._fields['sale_contract_id'].domain
        self.assertTrue(domain)
        draft = self._contract(self.units[3], sign=False)
        signed = self._contract(self.units[0])
        found = self.Contract.search(domain)
        self.assertIn(signed, found)
        self.assertNotIn(draft, found)

    # 7 ------------------------------------------------------------------
    def test_snags_list_critical_first(self):
        """'severity desc' sorts the selection keys alphabetically: minor first."""
        handover = self._handover(self._contract(self.units[0]))
        handover.action_start_inspection()
        for severity in ('major', 'critical', 'minor'):
            self._snag(handover, severity)
        order = self.Snag.search([('handover_id', '=', handover.id)]).mapped('severity')
        self.assertEqual(order, ['critical', 'major', 'minor'])
        panel = self.env['realestate.handover.dashboard'].get_data()['snag_list']
        ours = [s['severity'] for s in panel if s['property'] == handover.property_id.display_name]
        self.assertEqual(ours, ['critical', 'major', 'minor'])

    # 8a -----------------------------------------------------------------
    def test_a_handover_user_opens_the_dashboard(self):
        """The dashboard read realestate.property, which a Handover user cannot."""
        user = new_test_user(self.env, login='ho_pure_user',
                             groups='base.group_user,real_estate_handover.group_handover_user')
        self.assertFalse(self.env['realestate.property'].with_user(user).has_access('read'))
        self.units[0].write({'latitude': 30.0, 'longitude': 31.0})
        handover = self._handover(self._contract(self.units[0]))
        handover.action_start_inspection()
        self._snag(handover, 'critical')
        data = self.env['realestate.handover.dashboard'].with_user(user).get_data()
        self.assertEqual(data['kpis']['open_snagging'], 1)
        self.assertEqual(data['snag_list'][0]['property'], handover.property_id.display_name)
        # Unit coordinates are property data the user has no right to read.
        self.assertEqual(data['map_props'], [])

    def test_a_handover_user_opens_the_dashboard_with_a_contractor_on_a_snag(self):
        """The snag panel names the contractor, and the snag form assigns one,
        but a Handover user had no right on realestate.contractor: the
        dashboard died with an AccessError on its own screen."""
        user = new_test_user(self.env, login='ho_contractor_user',
                             groups='base.group_user,real_estate_handover.group_handover_user')
        contractor = self.env['realestate.contractor'].create({
            'name': 'Nile Finishing',
            'partner_id': self.env['res.partner'].create({'name': 'Nile Finishing Co'}).id,
        })
        handover = self._handover(self._contract(self.units[0]))
        handover.action_start_inspection()
        snag = self._snag(handover, 'critical', contractor_id=contractor.id)
        # The fixture was built as the superuser, and a value already in the
        # cache is handed over without a right being checked. The screen this
        # user opens reads the contractor for the first time.
        self.env.invalidate_all()
        data = self.env['realestate.handover.dashboard'].with_user(user).get_data()
        row = [s for s in data['snag_list'] if s['id'] == snag.id]
        self.assertEqual(row[0]['contractor'], contractor.name)
        # The list and the form the same user works in show that contractor too.
        self.env.invalidate_all()
        self.assertEqual(self.Snag.with_user(user).browse(snag.id).contractor_id.name,
                         contractor.name)

    # 12 -----------------------------------------------------------------
    def test_warranty_months_are_calendar_months(self):
        """End date was start + 30 days per month: a 12-month warranty was 5 days short."""
        handover = self._complete(self._handover(self._contract(self.units[0])))
        warranty = handover.warranty_id
        self.assertEqual(warranty.end_date, warranty.start_date + relativedelta(months=12))

    # 13 -----------------------------------------------------------------
    def test_unit_milestones_follow_the_handover(self):
        """ready_to_deliver / ready_to_move only recomputed on readiness edits."""
        unit = self.units[0]
        unit.write({'readiness_structural': 100, 'readiness_finishing': 100,
                    'readiness_utilities': 100, 'readiness_inspection': 100})
        self.env.flush_all()
        self.assertTrue(unit.ready_to_deliver)
        self.assertFalse(unit.ready_to_move)
        self._complete(self._handover(self._contract(unit)))
        self.env.flush_all()
        self.env.cr.execute(
            'SELECT ready_to_deliver, ready_to_move FROM realestate_property WHERE id = %s', (unit.id,))
        self.assertEqual(self.env.cr.fetchone(), (False, True))

    # U1 -----------------------------------------------------------------
    def test_readiness_scores_are_not_shown_as_fractions(self):
        """widget="percentage" multiplies by 100: a 100 score showed 10000%."""
        arch = self.env['realestate.property'].get_view(
            self.env.ref('atmta_property_core.view_property_form').id, 'form')['arch']
        doc = etree.fromstring(arch)
        for name in ('readiness_structural', 'readiness_finishing',
                     'readiness_utilities', 'readiness_inspection'):
            for node in doc.xpath("//field[@name='%s']" % name):
                self.assertNotEqual(node.get('widget'), 'percentage', name)

    # U2 -----------------------------------------------------------------
    def test_a_fix_can_only_be_rejected_once_it_exists(self):
        """Reject was offered (and accepted) on open/assigned/in-progress snags."""
        handover = self._handover(self._contract(self.units[0]))
        handover.action_start_inspection()
        snag = self._snag(handover, 'major')
        with self.assertRaises(UserError):
            snag.action_reject()
        snag.action_assign()
        with self.assertRaises(UserError):
            snag.action_reject()
        snag.action_resolve()
        snag.action_reject()
        self.assertEqual(snag.state, 'rejected')
        arch = self.env['realestate.snagging.issue'].get_view(
            self.env.ref('real_estate_handover.view_snagging_form').id, 'form')['arch']
        button = etree.fromstring(arch).xpath("//button[@name='action_reject']")[0]
        self.assertEqual(button.get('invisible'), "state != 'resolved'")
