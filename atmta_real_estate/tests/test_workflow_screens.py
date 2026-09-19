"""Work-queue screens open on the work (Phase 4; RENTAL_UX_SPEC.md §4, §10-§12, §14)."""

from datetime import timedelta

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo.tests.common import tagged
from odoo.tools.safe_eval import safe_eval

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestWorkflowScreens(LeaseCase):

    def _search_arch(self, model, view=None):
        return etree.fromstring(self.env[model].get_view(
            view.id if view else None, 'search')['arch'])

    def _filter_domain(self, model, name):
        node = self._search_arch(model).xpath("//filter[@name='%s']" % name)
        self.assertTrue(node, (model, name))
        return safe_eval(node[0].get('domain'), {
            'context_today': lambda: self.today, 'relativedelta': relativedelta,
            'uid': self.env.uid})

    def _rental_actions(self):
        root = self.env.ref('atmta_real_estate.real_estate_menu_root')
        actions = []
        for menu in self.env['ir.ui.menu'].search([('id', 'child_of', root.id)]):
            action = menu.action
            if action and action._name == 'ir.actions.act_window' and action not in actions:
                actions.append(action)
        return actions

    def test_every_default_filter_of_a_rental_screen_exists(self):
        """A default filter naming no filter is silently ignored: the screen opens on everything."""
        checked = 0
        for action in self._rental_actions():
            context = safe_eval(action.context or '{}', {'uid': self.env.uid})
            defaults = [key[len('search_default_'):] for key in context
                        if key.startswith('search_default_')]
            if not defaults:
                continue
            arch = self._search_arch(action.res_model, action.search_view_id)
            names = set(arch.xpath('//filter/@name'))
            for name in defaults:
                self.assertIn(name, names, "%s opens on a missing filter '%s'" % (action.name, name))
                checked += 1
        self.assertGreaterEqual(checked, 8)

    def test_work_screens_open_on_their_spec_default(self):
        expected = {
            'atmta_real_estate.action_realestate_contract': 'current',
            'atmta_real_estate.action_available_units': 'available_to_lease',
            'atmta_real_estate.action_renewals': 'open',
            'atmta_real_estate.action_billing_obligations': 'outstanding',
            'atmta_real_estate.action_deposits': 'needs_action',
            'atmta_real_estate.action_move_ins': 'upcoming',
            'atmta_real_estate.action_move_outs': 'upcoming',
            'atmta_real_estate.action_unit_turns': 'open',
        }
        for xmlid, name in expected.items():
            context = safe_eval(self.env.ref(xmlid).context or '{}')
            self.assertIn('search_default_%s' % name, context, xmlid)

    def test_rental_screens_explain_an_empty_list(self):
        for xmlid, title in (
                ('atmta_real_estate.action_realestate_contract', 'Create your first lease'),
                ('atmta_real_estate.action_available_units', 'No units are available to lease'),
                ('atmta_real_estate.action_renewals', 'No renewals in progress'),
                ('atmta_real_estate.action_billing_obligations', 'Everything is collected'),
                ('atmta_real_estate.action_deposits', 'No deposits need action'),
                ('atmta_real_estate.action_move_ins', 'No upcoming move-ins'),
                ('atmta_real_estate.action_move_outs', 'No upcoming move-outs'),
                ('atmta_real_estate.action_unit_turns', 'No units are being turned')):
            self.assertIn(title, self.env.ref(xmlid).help or '', xmlid)

    def test_moves_have_a_calendar_on_their_scheduled_date(self):
        for model, xmlid in (('realestate.move.in', 'atmta_real_estate.action_move_ins'),
                             ('realestate.move.out', 'atmta_real_estate.action_move_outs')):
            arch = etree.fromstring(self.env[model].get_view(None, 'calendar')['arch'])
            self.assertEqual(arch.get('date_start'), 'scheduled_date')
            self.assertIn('calendar', self.env.ref(xmlid).view_mode)
            filters = set(self._search_arch(model).xpath('//filter/@name'))
            self.assertTrue({'upcoming', 'today', 'next_7_days', 'overdue', 'completed'} <= filters)

    def test_open_renewals_exclude_decided_ones(self):
        lease = self.activate(self.make_lease())
        Renewal = self.env['realestate.contract.renewal']
        renewal = Renewal.create({
            'contract_id': lease.id,
            'proposed_start_date': lease.end_date + timedelta(days=1),
            'proposed_end_date': lease.end_date + relativedelta(years=1),
            'proposed_rent': 1100.0,
        })
        domain = self._filter_domain('realestate.contract.renewal', 'open')
        self.assertIn(renewal, Renewal.search(domain))
        renewal.rejection_reason = 'Tenant is relocating.'
        renewal.action_reject()
        self.assertNotIn(renewal, Renewal.search(domain))

    def test_deposits_needing_action_include_requested_ones(self):
        lease = self.make_lease()
        Deposit = self.env['realestate.contract.deposit']
        deposit = Deposit.create({
            'contract_id': lease.id,
            'partner_id': self.tenant.id,
            'requested_amount': 500.0,
        })
        domain = self._filter_domain('realestate.contract.deposit', 'needs_action')
        self.assertNotIn(deposit, Deposit.search(domain), "A draft deposit needs nothing yet.")
        deposit.action_request()
        self.assertIn(deposit, Deposit.search(domain))

    def test_obligations_due_soon_are_outstanding_ones_due_within_14_days(self):
        lease = self.activate(self.make_lease(
            prop=self.parking, start=self.today, end=self.today + relativedelta(years=1),
            rent=500.0, use_billing_engine=True))
        lease.action_generate_billing_schedule()
        Obligation = self.env['realestate.contract.payment']
        domain = self._filter_domain('realestate.contract.payment', 'due_soon')
        found = Obligation.search(domain + [('contract_id', '=', lease.id)])
        self.assertTrue(found)
        for obligation in found:
            self.assertLessEqual(obligation.date_due, self.today + timedelta(days=14))
            self.assertGreaterEqual(obligation.date_due, self.today)

    def test_unit_turn_and_renewal_queues_are_kanbans_that_are_not_dragged(self):
        for model, xmlid in (('realestate.unit.turn', 'atmta_real_estate.action_unit_turns'),
                             ('realestate.contract.renewal', 'atmta_real_estate.action_renewals')):
            arch = etree.fromstring(self.env[model].get_view(None, 'kanban')['arch'])
            self.assertEqual(arch.get('default_group_by'), 'state')
            self.assertEqual(arch.get('records_draggable'), 'false')
            self.assertTrue(self.env.ref(xmlid).view_mode.startswith('kanban'))
