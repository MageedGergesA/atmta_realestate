"""The dashboard shows each role its work (Phase 4; RENTAL_UX_SPEC.md §5)."""

from datetime import datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase

EVERYONE = {'my_activities', 'available_to_lease', 'occupied_units', 'occupancy_rate',
            'active_leases'}
AGENT = EVERYONE | {'signatures_waiting', 'overdue_obligations', 'expiring_30_no_renewal',
                    'renewals_in_progress', 'outstanding_rent', 'expiring_30', 'expiring_60',
                    'expiring_90'}
PROPERTY_MANAGER = AGENT | {'move_ins_next_7_days', 'move_outs_next_7_days',
                            'notices_to_process', 'deposits_to_settle',
                            'units_in_turnaround', 'out_of_service'}
RENTAL_MANAGER = PROPERTY_MANAGER | {'leases_to_approve', 'amendments_to_approve',
                                     'terminations_to_approve'}

COMPLETE_ACTION_KEYS = ('type', 'name', 'res_model', 'views', 'target')


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestDashboardWork(LeaseCase):

    def setUp(self):
        super().setUp()
        self.Dashboard = self.env['realestate.rental.dashboard']

    def _user(self, login, group):
        user = new_test_user(
            self.env, login=login, groups='base.group_user,atmta_real_estate.%s' % group,
            company_id=self.company.id)
        user.company_ids = [(4, self.company.id)]
        return user

    def _tiles(self, scope='team', dashboard=None):
        # An abstract model's recordset is empty and therefore falsy, so test
        # for None: `dashboard or ...` silently fell back to the superuser.
        work = (self.Dashboard if dashboard is None else dashboard).get_work(scope)
        return {tile['key']: tile for section in work['sections'] for tile in section['tiles']}

    # ------------------------------------------------------------------
    # Layout and roles
    # ------------------------------------------------------------------
    def test_work_comes_before_portfolio_health(self):
        work = self.Dashboard.get_work('team')
        self.assertEqual([s['id'] for s in work['sections']], ['work', 'portfolio'])
        self.assertEqual(set(self._tiles()), RENTAL_MANAGER)

    def test_each_role_sees_the_tiles_it_acts_on(self):
        for group, expected in (('group_rental_user', EVERYONE),
                                ('group_rental_agent', AGENT),
                                ('group_property_manager', PROPERTY_MANAGER),
                                ('group_rental_manager', RENTAL_MANAGER)):
            user = self._user('re_dash_%s' % group, group)
            self.assertEqual(
                set(self._tiles(dashboard=self.Dashboard.with_user(user))), expected, group)

    def test_statistics_the_spec_moved_out_are_gone(self):
        keys = set(self._tiles())
        for removed in ('economic_occupancy_rate', 'arrears_rate', 'average_rent_per_sqm',
                        'monthly_contracted_rent', 'deposits_held', 'average_vacant_days',
                        'move_ins_this_month', 'move_outs_this_month'):
            self.assertNotIn(removed, keys)
        charts = self.Dashboard.get_trends()['charts']
        self.assertEqual(set(charts), {'billed_vs_collected', 'arrears_aging',
                                       'expiries_by_month', 'occupancy_trend'})

    def test_a_hidden_tile_cannot_be_opened(self):
        agent = self._user('re_dash_agent_hidden', 'group_rental_agent')
        with self.assertRaises(UserError):
            self.Dashboard.with_user(agent).action_drill('leases_to_approve', 'team')

    # ------------------------------------------------------------------
    # Tiles open exactly what they count
    # ------------------------------------------------------------------
    def _seed_work(self):
        colleague = self._user('re_dash_colleague', 'group_rental_agent')
        mine = self.activate(self.make_lease(prop=self.unit_a, user_id=self.env.uid))
        theirs = self.activate(self.make_lease(prop=self.unit_b, user_id=colleague.id))
        self.env['realestate.property'].create({
            'name': 'Company-less Unit', 'property_code': 'DASH-NOCO-1',
            'hierarchy_level': 'unit', 'usage_category': 'apartment',
            'area_sqm': 50.0, 'company_id': False,
        })
        return mine, theirs

    def test_every_count_tile_matches_its_drilldown_in_both_scopes(self):
        self._seed_work()
        for scope in ('mine', 'team'):
            for key, tile in self._tiles(scope).items():
                if not tile['drill'] or tile['format'] != 'integer':
                    continue
                action = self.Dashboard.action_drill(key, scope)
                self.assertEqual(
                    tile['value'], self.env[action['res_model']].search_count(action['domain']),
                    "%s (%s): tile and drilldown disagree" % (key, scope))

    def test_outstanding_rent_equals_the_residual_it_opens(self):
        lease = self.activate(self.make_lease(
            prop=self.parking, start=self.today, end=self.today + relativedelta(years=1),
            rent=500.0, use_billing_engine=True))
        lease.action_generate_billing_schedule()
        lease.contract_payment_ids.sorted('date_due')[0]._create_invoices()
        self.env.invalidate_all()
        tile = self._tiles()['outstanding_rent']
        action = self.Dashboard.action_drill('outstanding_rent', 'team')
        total = sum(self.env[action['res_model']].search(action['domain']).mapped('amount_residual'))
        self.assertTrue(total)
        self.assertAlmostEqual(tile['value'], total, places=2)

    def test_mine_counts_only_my_leases_team_counts_all(self):
        mine, theirs = self._seed_work()
        self.assertEqual(self._tiles('team')['active_leases']['value'], 2)
        self.assertEqual(self._tiles('mine')['active_leases']['value'], 1)
        domain = self.Dashboard.action_drill('active_leases', 'mine')['domain']
        self.assertEqual(self.env['realestate.contract'].search(domain), mine)

    def test_my_activities_counts_rental_activities_due_for_me(self):
        lease = self.make_lease()
        lease.activity_schedule(
            'mail.mail_activity_data_todo', date_deadline=self.today, user_id=self.env.uid)
        lease.activity_schedule(
            'mail.mail_activity_data_todo', date_deadline=self.today + timedelta(days=5),
            user_id=self.env.uid)
        self.assertEqual(self._tiles()['my_activities']['value'], 1)

    def test_move_ins_count_the_next_seven_days_of_the_users_calendar(self):
        lease = self.activate(self.make_lease())
        MoveIn = self.env['realestate.move.in']
        tz_start = self.Dashboard._day_start_utc(self.today)
        tomorrow = MoveIn.create({
            'contract_id': lease.id, 'property_id': self.unit_a.id,
            'scheduled_date': tz_start + timedelta(days=1, hours=10),
        })
        MoveIn.create({
            'contract_id': lease.id, 'property_id': self.unit_a.id,
            'scheduled_date': tz_start + timedelta(days=8, hours=10),
        })
        action = self.Dashboard.action_drill('move_ins_next_7_days', 'team')
        self.assertEqual(MoveIn.search(action['domain']), tomorrow)
        self.assertEqual(self._tiles()['move_ins_next_7_days']['value'], 1)

    def test_the_users_day_starts_at_local_midnight(self):
        self.env.user.tz = 'Asia/Riyadh'
        start = self.Dashboard._day_start_utc(self.today)
        self.assertEqual(start, datetime.combine(self.today, time.min) - timedelta(hours=3))

    def test_a_scope_that_does_not_exist_is_refused(self):
        with self.assertRaises(UserError):
            self.Dashboard.get_work('everyone')

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def test_every_drilldown_and_quick_action_is_a_complete_action(self):
        for key, tile in self._tiles().items():
            if not tile['drill']:
                continue
            action = self.Dashboard.action_drill(key, 'team')
            for required in COMPLETE_ACTION_KEYS:
                self.assertIn(required, action, key)
            self.assertEqual(action['views'], [(False, 'list'), (False, 'form')])
            self.env[action['res_model']].search(action['domain'], limit=1)
        for quick in self.Dashboard.get_work()['quick_actions']:
            action = self.Dashboard.action_quick(quick['key'])
            self.assertEqual(action['type'], 'ir.actions.act_window', quick['key'])
            self.assertTrue(action.get('views'), quick['key'])
        bucket = self.Dashboard.action_drill_arrears_bucket('31_60')
        self.assertEqual(bucket['res_model'], 'realestate.contract.payment')

    def test_quick_actions_follow_the_role(self):
        agent = self._user('re_dash_agent_quick', 'group_rental_agent')
        Agent = self.Dashboard.with_user(agent)
        self.assertEqual([q['key'] for q in Agent.get_work()['quick_actions']],
                         ['new_lease', 'available_units'])
        self.assertEqual(Agent.action_quick('new_lease')['res_model'], 'realestate.contract')
        with self.assertRaises(UserError):
            Agent.action_quick('move_in')
