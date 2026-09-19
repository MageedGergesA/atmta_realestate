"""The lease screens follow the UX spec (Phase 4; RENTAL_UX_SPEC.md §6-§7)."""

from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLeaseForm(LeaseCase):

    def _user(self, login, group):
        user = new_test_user(
            self.env, login=login, groups='base.group_user,atmta_real_estate.%s' % group,
            company_id=self.company.id)
        user.company_ids = [(4, self.company.id)]
        return user

    def _arch(self, xmlid, view_type, user=None):
        Contract = self.env['realestate.contract']
        if user:
            Contract = Contract.with_user(user)
        view = self.env.ref(xmlid)
        return etree.fromstring(Contract.get_view(view.id, view_type)['arch'])

    def _header_buttons(self, user, **values):
        """Header buttons ``user`` sees for a lease with ``values``."""
        values.setdefault('signature_status', 'pending')
        values.setdefault('move_out_count', 0)
        arch = self._arch('atmta_real_estate.view_realestate_contract_form', 'form', user)
        visible = []
        for button in arch.xpath('//header/button'):
            expression = button.get('invisible')
            if expression and safe_eval(expression, dict(values)):
                continue
            visible.append((button.get('string'), 'oe_highlight' in (button.get('class') or '')))
        return visible

    # ------------------------------------------------------------------
    # Header: only the next sensible actions (§6.1)
    # ------------------------------------------------------------------
    def test_header_buttons_follow_the_lease_status(self):
        manager = self._user('re_form_mgr', 'group_rental_manager')
        expected = {
            ('draft', 'pending'): {'Submit for Approval', 'Propose', 'Cancel'},
            ('proposal', 'pending'): {'Submit for Approval', 'Back to Draft', 'Cancel'},
            ('pending_approval', 'pending'): {'Approve', 'Back to Draft', 'Cancel'},
            ('pending_signature', 'pending'): {'Mark Signed', 'Cancel'},
            ('pending_signature', 'signed'): {'Activate', 'Cancel'},
            ('active', 'signed'): {'Renew', 'Give Notice', 'Terminate'},
            ('notice', 'signed'): {'Terminate', 'Schedule Move-Out', 'Withdraw Notice'},
            ('ended', 'signed'): set(),
            ('terminated', 'signed'): set(),
            ('cancelled', 'pending'): {'Reopen'},
        }
        for (status, signature), names in expected.items():
            buttons = self._header_buttons(
                manager, lifecycle_state=status, signature_status=signature)
            self.assertEqual({name for name, _primary in buttons}, names, status)
            self.assertLessEqual(len(buttons), 3, status)
            self.assertLessEqual(sum(primary for _name, primary in buttons), 1, status)

    def test_a_scheduled_move_out_is_not_offered_twice(self):
        manager = self._user('re_form_mgr_mo', 'group_rental_manager')
        names = {name for name, _p in self._header_buttons(
            manager, lifecycle_state='notice', move_out_count=1)}
        self.assertNotIn('Schedule Move-Out', names)

    def test_an_agent_sees_only_the_actions_of_their_role(self):
        agent = self._user('re_form_agent', 'group_rental_agent')
        for status, names in (('draft', {'Submit for Approval', 'Propose'}),
                              ('pending_approval', set()),
                              ('active', {'Renew'})):
            self.assertEqual(
                {name for name, _p in self._header_buttons(agent, lifecycle_state=status)},
                names, status)

    # ------------------------------------------------------------------
    # Structure (§6.1-§6.4)
    # ------------------------------------------------------------------
    def test_the_form_has_one_status_one_button_box_and_six_tabs(self):
        arch = self._arch('atmta_real_estate.view_realestate_contract_form', 'form')
        statusbars = arch.xpath("//field[@widget='statusbar']")
        self.assertEqual([node.get('name') for node in statusbars], ['lifecycle_state'])
        self.assertEqual(len(arch.xpath(
            "//div[contains(concat(' ', @class, ' '), ' oe_button_box ')]")), 1)
        stat_buttons = [b.get('name') for b in arch.xpath("//div[@name='button_box']/button")]
        self.assertEqual(stat_buttons[:2], ['action_open_payments', 'action_get_invoices'])
        pages = [page.get('name') for page in arch.xpath('//notebook/page')]
        self.assertEqual(pages[:6], ['units', 'parties', 'billing', 'adjustments',
                                     'deposit_occupancy', 'terms'])
        self.assertTrue(arch.xpath("//group[@name='lease_summary']"))
        for legacy in ('increment_rule_ids', 'discount_rule_ids', 'use_manual_payment',
                       'utility_line_ids'):
            self.assertFalse(arch.xpath("//field[@name='%s']" % legacy), legacy)

    def test_the_legacy_deposit_tab_only_shows_legacy_data(self):
        arch = self._arch('atmta_real_estate.view_realestate_contract_form', 'form')
        page = arch.xpath("//page[@name='deposit']")
        self.assertTrue(page)
        self.assertEqual(page[0].get('invisible'), "deposit_state == 'none' and not deposit_amount")

    def test_lists_search_and_kanban_no_longer_use_the_legacy_status(self):
        for xmlid, view_type in (
                ('atmta_real_estate.view_realestate_contract_list', 'list'),
                ('atmta_real_estate.view_realestate_contract_kanban', 'kanban'),
                ('atmta_real_estate.view_contract_pivot', 'pivot'),
                ('atmta_real_estate.view_realestate_contract_search', 'search')):
            arch = self._arch(xmlid, view_type)
            self.assertFalse(arch.xpath("//field[@name='state']"), xmlid)
            for node in arch.xpath('//filter[@domain]'):
                self.assertNotIn("'state'", node.get('domain'), (xmlid, node.get('name')))

    def test_the_search_offers_the_spec_filters_and_a_status_panel(self):
        arch = self._arch('atmta_real_estate.view_realestate_contract_search', 'search')
        filters = {node.get('name') for node in arch.xpath('//filter')}
        for name in ('my_leases', 'current', 'draft_proposal', 'awaiting_approval',
                     'awaiting_signature', 'active_leases', 'on_notice', 'ended', 'cancelled',
                     'expiring_30', 'expiring_60', 'expiring_90', 'in_arrears',
                     'renewal_not_started', 'gb_lifecycle', 'gb_unit', 'group_partner',
                     'gb_manager', 'gb_end_month', 'group_expiry'):
            self.assertIn(name, filters)
        self.assertEqual(arch.xpath('//searchpanel/field/@name'), ['lifecycle_state'])
        board = self._arch('atmta_real_estate.view_lease_expiry_search', 'search')
        self.assertFalse(board.xpath('//searchpanel'))

    def test_occasional_actions_are_in_the_gear_menu(self):
        for xmlid in ('atmta_real_estate.action_server_print_lease',
                      'atmta_real_estate.action_server_amend_lease'):
            action = self.env.ref(xmlid)
            self.assertEqual(action.binding_model_id.model, 'realestate.contract')
            self.assertEqual(action.binding_view_types, 'form')

    # ------------------------------------------------------------------
    # New actions
    # ------------------------------------------------------------------
    def test_withdrawing_notice_returns_the_lease_to_active(self):
        lease = self.activate(self.make_lease())
        lease.action_give_notice()
        self.assertEqual(lease.lifecycle_state, 'notice')
        lease.action_withdraw_notice()
        self.assertEqual(lease.lifecycle_state, 'active')

    def test_only_a_property_manager_can_withdraw_notice(self):
        lease = self.activate(self.make_lease())
        lease.action_give_notice()
        agent = self._user('re_form_agent_wd', 'group_rental_agent')
        with self.assertRaises(AccessError):
            lease.with_user(agent).action_withdraw_notice()

    def test_notice_from_a_termination_is_withdrawn_by_cancelling_it(self):
        lease = self.activate(self.make_lease())
        termination = self.env['realestate.contract.termination'].create({
            'contract_id': lease.id,
            'requested_end_date': lease.end_date,
            'effective_date': lease.end_date,
            'reason': 'expiry',
        })
        termination.action_give_notice()
        self.assertEqual(lease.lifecycle_state, 'notice')
        with self.assertRaises(UserError):
            lease.action_withdraw_notice()

    def test_only_an_amendable_lease_opens_an_amendment(self):
        draft = self.make_lease()
        with self.assertRaises(UserError):
            draft.action_amend_lease()
        live = self.activate(self.make_lease(prop=self.unit_b))
        action = live.action_amend_lease()
        self.assertEqual(action['res_model'], 'realestate.contract.amendment')
        self.assertEqual(action['context']['default_contract_id'], live.id)
