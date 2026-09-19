"""The final quality gates (Phase 5).

* five-second test -- a real browser session as a Leasing Agent
* thirty-second test -- every everyday record is one menu away for its role
* consistency test -- one concept, one implementation
* noise test -- what a Leasing Agent is not shown
* safety test -- simplifying the screens weakened no server-side rule
"""

from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import HttpCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from .common import LeaseCase, shown_menu_ids


def _rental_user(env, login, company, *groups):
    user = new_test_user(
        env, login=login, groups=','.join(('base.group_user',) + groups),
        company_id=company.id)
    user.company_ids = [(4, company.id)]
    return user


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestUsabilityGates(LeaseCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = _rental_user(cls.env, 'gate_agent', cls.company,
                                 'atmta_real_estate.group_rental_agent')
        cls.property_manager = _rental_user(cls.env, 'gate_pm', cls.company,
                                            'atmta_real_estate.group_property_manager')

    def _visible(self, user):
        return shown_menu_ids(self.env, user)

    def _rental_menus(self):
        root = self.env.ref('atmta_real_estate.real_estate_menu_root')
        # full_list: a plain search hides every menu whose groups the test's
        # own user lacks -- all Rental sections -- leaving only their children.
        return self.env['ir.ui.menu'].with_context(**{'ir.ui.menu.full_list': True}).search(
            [('id', 'child_of', root.id)])

    # ------------------------------------------------------------------
    # 30-second test
    # ------------------------------------------------------------------
    def test_thirty_seconds_every_everyday_record_is_one_menu_away(self):
        """An active lease, a tenant, an available unit, an expiring lease and
        an overdue obligation from the menu; moves from the lease or, for the
        role that runs them, from the menu."""
        ref = self.env.ref
        agent_targets = {
            'active lease': ('atmta_real_estate.menu_contract_list', 'realestate.contract'),
            'tenant': ('atmta_real_estate.menu_rental_tenants', 'res.partner'),
            'available unit': ('atmta_real_estate.menu_available_units', 'realestate.property'),
            'expiring lease': ('atmta_real_estate.menu_lease_expiry_board', 'realestate.contract'),
            'overdue obligation': ('atmta_real_estate.menu_billing_obligations',
                                   'realestate.contract.payment'),
        }
        visible = self._visible(self.agent)
        for target, (menu_xmlid, model) in agent_targets.items():
            menu = ref(menu_xmlid)
            self.assertIn(menu.id, visible, target)
            self.assertEqual(menu.action.res_model, model, target)
            domain = safe_eval(menu.action.domain or '[]', {'uid': self.agent.id})
            # The screen opens for the role without an access error.
            self.env[model].with_user(self.agent).search_count(domain)

        form = self.env.ref('atmta_real_estate.view_realestate_contract_form')
        arch = etree.fromstring(self.env['realestate.contract'].with_user(self.agent).get_view(
            form.id, 'form')['arch'])
        for button in ('action_view_move_ins', 'action_view_move_outs'):
            self.assertTrue(arch.xpath("//button[@name='%s']" % button), button)
        pm_visible = self._visible(self.property_manager)
        for menu_xmlid in ('atmta_real_estate.menu_move_ins', 'atmta_real_estate.menu_move_outs'):
            self.assertIn(ref(menu_xmlid).id, pm_visible, menu_xmlid)

    # ------------------------------------------------------------------
    # Consistency test
    # ------------------------------------------------------------------
    def test_one_concept_one_implementation(self):
        env = self.env
        Contract = env['realestate.contract']
        # Lease lifecycle: one status, one statusbar.
        form = env.ref('atmta_real_estate.view_realestate_contract_form')
        arch = etree.fromstring(Contract.get_view(form.id, 'form')['arch'])
        self.assertEqual(arch.xpath("//field[@widget='statusbar']/@name"), ['lifecycle_state'])
        # Unit allocation: allocations only.
        self.assertNotIn('realestate.contract.line', env)
        self.assertFalse(arch.xpath("//field[@name='line_ids']"))
        # Rent escalation: escalation rules on the form; legacy price rules neither
        # on the form nor in a menu.
        self.assertTrue(arch.xpath("//field[@name='escalation_rule_ids']"))
        for legacy in ('increment_rule_ids', 'discount_rule_ids'):
            self.assertFalse(arch.xpath("//field[@name='%s']" % legacy), legacy)
        menu_names = set(self._rental_menus().mapped('name'))
        for retired in ('Payment Plans', 'Price Adjustment Rules', 'Contract Units',
                        'Payment Schedules', 'Rental History', 'Contract Lines'):
            self.assertNotIn(retired, menu_names, retired)
        # ...nor presented as a live concept on the billing obligation form: only
        # obligations that still record legacy rules show them, as read-only history.
        obligation_form = etree.fromstring(env['realestate.contract.payment'].get_view(
            env.ref('atmta_real_estate.view_contract_payment_form').id, 'form')['arch'])
        legacy_groups = obligation_form.xpath("//group[@name='legacy_price_rules']")
        self.assertEqual(len(legacy_groups), 1)
        self.assertEqual(legacy_groups[0].get('string'), 'Legacy Price Rules')
        self.assertIn('not increment_rule_ids and not discount_rule_ids',
                      legacy_groups[0].get('invisible', ''))
        for legacy in ('increment_rule_ids', 'discount_rule_ids'):
            fields_ = obligation_form.xpath("//field[@name='%s']" % legacy)
            self.assertTrue(fields_ and all(f.getparent() is legacy_groups[0] for f in fields_), legacy)
        # Billing obligations: one menu entry.
        obligation_menus = self._rental_menus().filtered(
            lambda m: m.action and m.action._name == 'ir.actions.act_window'
            and m.action.res_model == 'realestate.contract.payment')
        self.assertEqual(len(obligation_menus), 1)
        # Invoice generation: the retired per-payment path refuses.
        lease = self.make_lease()
        with self.assertRaises(UserError):
            lease.action_create_invoices()
        # Lease expiry: one scheduled job.
        expiry_jobs = env['ir.cron'].with_context(active_test=False).search(
            [('model_id.model', '=', 'realestate.contract'), ('code', 'ilike', 'expire')])
        self.assertEqual(expiry_jobs.mapped('code'), ['model._cron_expire_leases()'])

    # ------------------------------------------------------------------
    # Noise test
    # ------------------------------------------------------------------
    def test_a_leasing_agent_is_shown_no_noise(self):
        visible = set(self._visible(self.agent))
        rental = self._rental_menus()
        agent_menus = rental.filtered(lambda m: m.id in visible)
        names = set(agent_menus.mapped('name'))
        for hidden in ('Configuration', 'Settings', 'Lease Types', 'Property Types',
                       'Occupancy', 'Reporting', 'Meters', 'Meter Readings', 'Utility Costs'):
            self.assertNotIn(hidden, names, hidden)
        # No duplicate way to open the same screen.
        actions = [m.action for m in agent_menus if m.action]
        self.assertEqual(len(actions), len(set(actions)), "Two menus open the same action.")
        # No accounting configuration on the lease screens an agent uses.
        form = self.env.ref('atmta_real_estate.view_realestate_contract_form')
        arch = self.env['realestate.contract'].with_user(self.agent).get_view(
            form.id, 'form')['arch']
        for internal in ('journal_id', 'account_id', 'payment_term_id', 'sale_order_id'):
            self.assertNotIn('name="%s"' % internal, arch, internal)

    # ------------------------------------------------------------------
    # Safety test
    # ------------------------------------------------------------------
    def test_simpler_screens_did_not_weaken_server_rules(self):
        lease = self.make_lease(prop=self.unit_b, user_id=self.agent.id)
        Agent = lease.with_user(self.agent)
        Agent.action_submit_for_approval()
        with self.assertRaises(AccessError, msg="An agent cannot approve."):
            Agent.action_approve_lease()
        with self.assertRaises(AccessError, msg="Status cannot be written directly."):
            Agent.write({'lifecycle_state': 'active'})
        with self.assertRaises(AccessError, msg="A lease cannot be created already live."):
            self.env['realestate.contract'].with_user(self.agent).create({
                'partner_id': self.tenant.id, 'property_id': self.unit_a.id,
                'is_single_property': True, 'start_date': self.today,
                'end_date': self.today + relativedelta(years=1), 'price': 900.0,
                'currency_id': self.currency.id, 'lifecycle_state': 'active',
            })
        lease.action_approve_lease()
        with self.assertRaises(AccessError, msg="Only a property manager activates."):
            lease.with_user(self.agent).action_activate_lease()
        with self.assertRaises(UserError, msg="An unsigned lease cannot go live."):
            lease.with_user(self.property_manager).action_activate_lease()
        with self.assertRaises(UserError, msg="A hidden dashboard tile cannot be opened."):
            self.env['realestate.rental.dashboard'].with_user(self.agent).action_drill(
                'leases_to_approve', 'team')

    def test_companies_stay_isolated_on_the_new_screens(self):
        other = self.env['res.company'].create({'name': 'Gate Other Estate'})
        outsider = _rental_user(self.env, 'gate_outsider', other,
                                'atmta_real_estate.group_rental_manager')
        outsider.company_ids = [(6, 0, [other.id])]
        lease = self.activate(self.make_lease())
        self.assertFalse(self.env['realestate.contract'].with_user(outsider).search(
            [('id', '=', lease.id)]))
        work = self.env['realestate.rental.dashboard'].with_user(outsider).get_work('team')
        values = {tile['key']: tile['value'] for s in work['sections'] for tile in s['tiles']}
        self.assertEqual(values['active_leases'], 0)
        self.assertEqual(values['available_to_lease'], 0)


@tagged('post_install', '-at_install', 'atmta_usability_tour')
class TestFiveSecondTest(HttpCase):
    """What a Leasing Agent sees when the Rental app opens."""

    def test_five_seconds_where_attention_create_and_find(self):
        company = self.env.company
        agent = _rental_user(self.env, 'five_second_agent', company,
                             'atmta_real_estate.group_rental_agent')
        self.env['realestate.property'].create({
            'name': 'Five Second Unit', 'property_code': 'FIVE-U-001',
            'hierarchy_level': 'unit', 'usage_category': 'apartment',
            'area_sqm': 70.0, 'company_id': company.id,
        })
        self.start_tour("/odoo", "atmta_rental_five_second_tour",
                        login=agent.login, timeout=180)
