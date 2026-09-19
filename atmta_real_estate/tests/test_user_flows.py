"""The seven user flows, run by the roles that do them (Phase 5).

Each flow drives the real workflow actions as separate users with ordinary
rental roles -- a Leasing Agent, a Property Manager, a Rental Manager and a
billing user -- rather than as the superuser, so every server-side role check
and record rule on the way is exercised.
"""

from dateutil.relativedelta import relativedelta

from odoo.tests.common import new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from .common import LeaseCase, shown_menu_ids


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestRentalUserFlows(LeaseCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        def user(login, *groups):
            account = new_test_user(
                cls.env, login=login, groups=','.join(('base.group_user',) + groups),
                company_id=cls.company.id)
            account.company_ids = [(4, cls.company.id)]
            # `cls.today` is the fixture user's date (see LeaseCase). A user
            # with no timezone dates things in UTC, so between midnight in the
            # fixture's timezone and midnight UTC a lease "starting today" had
            # not started for these users: flows 1 and 2 failed for that hour
            # or two every night. Same timezone, same today.
            account.tz = cls.env.user.tz
            return account

        cls.agent = user('flow_agent', 'atmta_real_estate.group_rental_agent')
        # Billing works across every lease, not only the ones it is responsible for.
        cls.billing = user('flow_billing', 'atmta_real_estate.group_rental_agent',
                           'atmta_real_estate.group_rental_all_portfolios',
                           'account.group_account_invoice')
        cls.property_manager = user('flow_pm', 'atmta_real_estate.group_property_manager')
        cls.manager = user('flow_manager', 'atmta_real_estate.group_rental_manager',
                           'account.group_account_invoice')

    def as_user(self, record, user):
        return record.with_user(user)

    def _live_lease(self, unit, start, end, **values):
        lease = self.make_lease(prop=unit, start=start, end=end, **values)
        return self.activate(lease)

    # ------------------------------------------------------------------
    # Flow 1
    # ------------------------------------------------------------------
    def test_flow_1_find_a_unit_lease_it_and_take_it_live(self):
        """Find available property → create lease → submit → approve → signature → activate."""
        action = self.env.ref('atmta_real_estate.action_available_units')
        search = self.env.ref('atmta_property_core.view_property_search')
        Property = self.env['realestate.property'].with_user(self.agent)
        arch = Property.get_view(search.id, 'search')['arch']
        self.assertIn('available_to_lease', arch)
        available = Property.search(safe_eval(action.domain) + [('is_available_for_lease', '=', True)])
        self.assertIn(self.unit_b, available)

        context = self.unit_b.with_user(self.agent).action_new_lease()['context']
        lease = self.env['realestate.contract'].with_user(self.agent).with_context(**context).create({
            'partner_id': self.tenant.id,
            'start_date': self.today,
            'end_date': self.today + relativedelta(years=1, days=-1),
            'price': 1200.0,
            'currency_id': self.currency.id,
        })
        self.assertEqual(lease.lifecycle_state, 'draft')

        lease.with_user(self.agent).action_submit_for_approval()
        self.assertEqual(lease.lifecycle_state, 'pending_approval')
        lease.with_user(self.manager).action_approve_lease()
        self.assertEqual(lease.lifecycle_state, 'pending_signature')
        lease.with_user(self.agent).action_mark_signed()
        lease.with_user(self.property_manager).action_activate_lease()

        self.assertEqual(lease.lifecycle_state, 'active')
        self.assertEqual(self.allocations_of(lease).property_id, self.unit_b)
        self.assertEqual(self.unit_b.rental_status, 'rented')
        self.unit_b.invalidate_recordset()
        self.assertFalse(self.unit_b.is_available_for_lease)

    # ------------------------------------------------------------------
    # Flow 2
    # ------------------------------------------------------------------
    def test_flow_2_bill_an_active_lease_and_invoice_it_once(self):
        """Active lease → generate rent billing → invoice (and never twice)."""
        lease = self._live_lease(
            self.parking, self.today, self.today + relativedelta(years=1, days=-1),
            rent=800.0, use_billing_engine=True, user_id=self.agent.id)

        lease.with_user(self.agent).action_generate_billing_schedule()
        obligations = lease.contract_payment_ids
        self.assertTrue(obligations)
        due = obligations.filtered(lambda o: o.date_due <= self.today)
        self.assertTrue(due, "The first period is due on the lease start.")

        invoices = lease.with_user(self.billing).action_invoice_due_obligations()
        self.assertEqual(len(invoices), len(due))
        self.assertEqual(set(invoices.mapped('state')), {'posted'})
        self.assertEqual(due.move_id, invoices)

        again = lease.with_user(self.billing).action_invoice_due_obligations()
        self.assertFalse(again, "Invoicing the same obligations twice must create nothing.")
        self.assertEqual(
            self.env['account.move'].search_count([('contract_id', '=', lease.id)]), len(due))

    # ------------------------------------------------------------------
    # Flow 3
    # ------------------------------------------------------------------
    def test_flow_3_renew_an_active_lease(self):
        """Active lease → renewal."""
        start = self.today - relativedelta(months=11)
        end = self.today + relativedelta(days=20)
        lease = self._live_lease(self.unit_b, start, end, rent=1000.0, user_id=self.agent.id)

        action = lease.with_user(self.agent).action_start_renewal()
        renewal = self.env['realestate.contract.renewal'].with_user(self.agent).with_context(
            **action.get('context', {})).create({
                'proposed_start_date': end + relativedelta(days=1),
                'proposed_end_date': end + relativedelta(years=1),
                'proposed_rent': 1080.0,
            })
        renewal.action_propose()
        renewal.with_user(self.manager).action_approve()
        renewal.with_user(self.agent).action_accept()
        renewal.with_user(self.manager).action_create_renewal_lease()

        new_lease = renewal.new_contract_id
        self.assertEqual(renewal.state, 'renewed')
        self.assertEqual(new_lease.old_contract_id, lease)
        self.assertEqual(new_lease.start_date, end + relativedelta(days=1))
        self.assertAlmostEqual(new_lease.price, 1080.0, places=2)
        self.assertEqual(lease.end_date, end, "The current lease is not rewritten.")

    # ------------------------------------------------------------------
    # Flow 4
    # ------------------------------------------------------------------
    def test_flow_4_notice_move_out_and_end(self):
        """Active lease → notice → move-out → ended."""
        start = self.today - relativedelta(months=11)
        end = self.today + relativedelta(days=20)
        lease = self._live_lease(self.unit_a, start, end, rent=1000.0)
        PM = self.property_manager

        move_in = self.env['realestate.move.in'].with_user(PM).create({
            'contract_id': lease.id, 'property_id': self.unit_a.id,
            'scheduled_date': self.today, 'tenant_acknowledged': True,
        })
        move_in.action_complete()

        termination = self.env['realestate.contract.termination'].with_user(PM).create({
            'contract_id': lease.id,
            'requested_end_date': end,
            'effective_date': end,
            'reason': 'expiry',
        })
        self.assertFalse(termination.is_early)
        termination.action_give_notice()
        self.assertEqual(lease.lifecycle_state, 'notice')
        termination.with_user(self.manager).action_approve()
        termination.with_user(self.manager).action_settle()

        move_out = self.env['realestate.move.out'].with_user(PM).create({
            'contract_id': lease.id, 'property_id': self.unit_a.id,
            'scheduled_date': self.today, 'tenant_acknowledged': True,
        })
        move_out.action_start_inspection()
        move_out.action_complete()
        self.assertEqual(self.allocations_of(lease).occupancy_status, 'vacated')
        self.assertTrue(move_out.unit_turn_id, "The unit goes to a turn, not straight to market.")

        termination.with_user(PM).action_complete()
        self.assertEqual(termination.state, 'completed')
        self.assertEqual(lease.lifecycle_state, 'ended')

    # ------------------------------------------------------------------
    # Flow 5
    # ------------------------------------------------------------------
    def test_flow_5_terminate_early(self):
        """Active lease → early termination."""
        start = self.today.replace(day=1) - relativedelta(months=3)
        end = start + relativedelta(years=1, days=-1)
        lease = self._live_lease(self.unit_b, start, end, rent=1000.0, use_billing_engine=True)
        lease.action_generate_billing_schedule()
        cutoff = self.today.replace(day=1) + relativedelta(months=1)

        termination = self.env['realestate.contract.termination'].with_user(
            self.property_manager).create({
                'contract_id': lease.id,
                'requested_end_date': cutoff,
                'effective_date': cutoff,
                'reason': 'tenant_notice',
                'requested_by': 'tenant',
            })
        self.assertTrue(termination.is_early)
        termination.action_give_notice()
        termination.with_user(self.manager).action_approve()
        termination.with_user(self.manager).action_settle()
        termination.with_user(self.property_manager).action_complete()

        self.assertEqual(lease.lifecycle_state, 'terminated')
        self.assertEqual(lease.end_date, cutoff)
        after = lease.contract_payment_ids.filtered(
            lambda o: o.date_due and o.date_due > cutoff and o.state != 'cancelled')
        self.assertFalse(after, "Nothing may stay billable after the termination date.")

    # ------------------------------------------------------------------
    # Flow 6
    # ------------------------------------------------------------------
    def test_flow_6_manager_sees_expiries_and_overdue_obligations(self):
        """Manager → see upcoming expiries and overdue obligations."""
        expiring = self._live_lease(
            self.unit_a, self.today - relativedelta(months=11),
            self.today + relativedelta(days=20))
        overdue_lease = self._live_lease(
            self.parking, self.today - relativedelta(days=45),
            self.today + relativedelta(months=10), rent=500.0, use_billing_engine=True)
        overdue_lease.action_generate_billing_schedule()
        first = overdue_lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        self.env.invalidate_all()

        Dashboard = self.env['realestate.rental.dashboard'].with_user(self.manager)
        tiles = {tile['key']: tile for section in Dashboard.get_work('team')['sections']
                 for tile in section['tiles']}
        self.assertGreaterEqual(tiles['expiring_30_no_renewal']['value'], 1)
        self.assertGreaterEqual(tiles['overdue_obligations']['value'], 1)

        def opened(key):
            action = Dashboard.action_drill(key, 'team')
            return self.env[action['res_model']].with_user(self.manager).search(action['domain'])

        self.assertIn(expiring, opened('expiring_30_no_renewal'))
        self.assertIn(first, opened('overdue_obligations'))

    # ------------------------------------------------------------------
    # Flow 7
    # ------------------------------------------------------------------
    def test_flow_7_a_leasing_agent_does_not_see_admin_configuration(self):
        """Normal leasing agent → cannot see irrelevant admin configuration."""
        visible = shown_menu_ids(self.env, self.agent)
        ref = self.env.ref
        for xmlid in ('atmta_real_estate.menu_contract_management',
                      'atmta_real_estate.menu_property_management',
                      'atmta_real_estate.menu_rental_tenants',
                      'atmta_real_estate.menu_leasing_billing',
                      'atmta_real_estate.menu_rental_dashboard'):
            self.assertIn(ref(xmlid).id, visible, xmlid)
        for xmlid in ('atmta_real_estate.menu_configuration',
                      'atmta_real_estate.menu_rental_settings',
                      'atmta_real_estate.menu_lease_types',
                      'atmta_real_estate.menu_property_operations',
                      'atmta_real_estate.menu_move_ins',
                      'atmta_real_estate.menu_property_meters',
                      'atmta_real_estate.menu_leasing_reporting',
                      'atmta_real_estate.menu_rent_roll'):
            self.assertNotIn(ref(xmlid).id, visible, xmlid)
        # Every Leasing Agent holds the legacy User alias, so a section that
        # also named it was open to agents whatever its role said.
        legacy_user = ref('atmta_real_estate.group_realestate_user')
        for xmlid in ('atmta_real_estate.menu_property_operations',
                      'atmta_real_estate.menu_leasing_reporting'):
            self.assertNotIn(legacy_user, ref(xmlid).groups_id, xmlid)
        pm_visible = shown_menu_ids(self.env, self.property_manager)
        for xmlid in ('atmta_real_estate.menu_move_ins',
                      'atmta_real_estate.menu_rent_roll'):
            self.assertIn(ref(xmlid).id, pm_visible, xmlid)
        self.assertNotIn(ref('atmta_real_estate.menu_configuration').id, pm_visible)
        manager_visible = shown_menu_ids(self.env, self.manager)
        self.assertIn(ref('atmta_real_estate.menu_lease_types').id, manager_visible)
