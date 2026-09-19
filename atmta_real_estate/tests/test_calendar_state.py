"""Date-derived values stay correct as the calendar moves (dead-code batch).

Each test moves the clock without touching the records, then checks the value
a user would read: live values immediately, stored values once the daily
refresh has run, and dashboard tiles against the records they open.
"""

import datetime
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from freezegun import freeze_time

from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestCalendarState(LeaseCase):

    def _on(self, day):
        # Noon, so the user's timezone cannot move the date either way.
        return freeze_time(datetime.datetime.combine(day, datetime.time(12)))

    def _tile_values(self, scope='team'):
        work = self.env['realestate.rental.dashboard'].get_work(scope)
        return {tile['key']: tile['value']
                for section in work['sections'] for tile in section['tiles']}

    def _refresh(self, since, today):
        self.env['realestate.calendar.refresh']._refresh_window(since, today)
        self.env.invalidate_all()

    def _unit(self, code):
        return self.env['realestate.property'].create({
            'name': 'Calendar unit %s' % code,
            'property_code': 'CAL-%s' % code,
            'hierarchy_level': 'unit',
            'usage_category': 'apartment',
            'area_sqm': 60.0,
            'company_id': self.company.id,
        })

    def _live_lease(self, unit, start, end, **values):
        return self.activate(self.make_lease(prop=unit, start=start, end=end, **values))

    # ------------------------------------------------------------------
    # Lease expiry
    # ------------------------------------------------------------------
    def test_days_to_expiry_and_its_bucket_follow_the_calendar(self):
        lease = self._live_lease(
            self.unit_b, self.today - relativedelta(months=6),
            self.today + timedelta(days=45))
        Contract = self.env['realestate.contract']
        self.assertEqual(lease.days_to_expiry, 45)
        self.assertEqual(lease.expiry_bucket, '31_60')

        later = self.today + timedelta(days=20)
        with self._on(later):
            self.env.invalidate_all()
            self.assertEqual(lease.days_to_expiry, 25)
            self.assertEqual(
                Contract.search([('id', '=', lease.id), ('days_to_expiry', '<=', 30)]), lease)
            self.assertFalse(
                Contract.search([('id', '=', lease.id), ('days_to_expiry', '>', 30)]))
            self.assertEqual(lease.expiry_bucket, '31_60', "Stored until the refresh runs.")
            self._refresh(self.today, later)
            self.assertEqual(lease.expiry_bucket, 'lte_30')

    def test_a_lease_reads_expired_the_day_after_its_end(self):
        end = self.today + timedelta(days=1)
        lease = self._live_lease(self.unit_b, self.today - relativedelta(months=6), end)
        day_after = end + timedelta(days=1)
        with self._on(day_after):
            self._refresh(self.today, day_after)
            self.assertEqual(lease.days_to_expiry, -1)
            self.assertEqual(lease.expiry_bucket, 'expired')

    # ------------------------------------------------------------------
    # Arrears
    # ------------------------------------------------------------------
    def _invoiced_first_obligation(self):
        lease = self._live_lease(
            self.parking, self.today, self.today + relativedelta(years=1),
            rent=500.0, use_billing_engine=True)
        lease.action_generate_billing_schedule()
        first = lease.contract_payment_ids.sorted('date_due')[0]
        first._create_invoices()
        self.env.invalidate_all()
        return lease, first

    def test_overdue_days_bucket_and_collection_status_follow_the_calendar(self):
        lease, first = self._invoiced_first_obligation()
        due = first.date_due
        self.assertGreaterEqual(due, self.today, "The fixture needs an obligation not yet due.")
        self.assertEqual(first.days_overdue, 0)
        self.assertNotEqual(lease.payment_status, 'overdue')
        Obligation = self.env['realestate.contract.payment']

        next_day = due + timedelta(days=1)
        with self._on(next_day):
            self.env.invalidate_all()
            self.assertEqual(first.days_overdue, 1)
            self.assertEqual(first.overdue_bucket, '1_30')
            self._refresh(due, next_day)
            self.assertEqual(lease.payment_status, 'overdue')

        much_later = due + timedelta(days=40)
        with self._on(much_later):
            self.env.invalidate_all()
            self.assertEqual(first.days_overdue, 40)
            self.assertEqual(first.overdue_bucket, '31_60')
            mine = [('id', '=', first.id)]
            self.assertEqual(Obligation.search(mine + [('overdue_bucket', '=', '31_60')]), first)
            self.assertFalse(Obligation.search(mine + [('overdue_bucket', '=', '1_30')]))
            self.assertEqual(Obligation.search(mine + [('days_overdue', '>=', 40)]), first)
            self.assertFalse(Obligation.search(mine + [('days_overdue', '>', 40)]))
            self.assertEqual(Obligation.search(mine + [('days_overdue', '=', 40)]), first)

    # ------------------------------------------------------------------
    # Occupancy
    # ------------------------------------------------------------------
    def test_a_future_lease_occupies_its_unit_only_from_its_start_date(self):
        start = self.today + timedelta(days=10)
        lease = self._live_lease(self.unit_b, start, start + relativedelta(years=1))
        allocation = self.allocations_of(lease)
        Dashboard = self.env['realestate.rental.dashboard']
        Property = self.env['realestate.property']
        company_ids = self.env.companies.ids

        self.assertFalse(allocation.occupies_property)
        self.assertEqual(self.unit_b.occupancy_status, 'vacant')
        self.assertNotIn(self.unit_b, Property.search(Dashboard._occupied_on(self.today)))

        with self._on(start):
            self.env.invalidate_all()
            # The dashboard reads the dates, so it is right before any refresh.
            domain = Dashboard.action_drill('occupied_units', 'team')['domain']
            self.assertIn(self.unit_b, Property.search(domain))
            self.assertEqual(self._tile_values()['occupied_units'],
                             Property.search_count(domain))
            self._refresh(self.today, start)
            self.assertTrue(allocation.occupies_property)
            self.assertEqual(self.unit_b.occupancy_status, 'occupied')

    def test_a_lease_stops_occupying_the_day_after_it_ends(self):
        lease = self._live_lease(
            self.unit_b, self.today - relativedelta(months=3), self.today)
        allocation = self.allocations_of(lease)
        self.assertTrue(allocation.occupies_property)
        tomorrow = self.today + timedelta(days=1)
        with self._on(tomorrow):
            self._refresh(self.today, tomorrow)
            self.assertFalse(allocation.occupies_property)
            self.assertEqual(self.unit_b.occupancy_status, 'vacant')

    # ------------------------------------------------------------------
    # Unit turns, parties, escalations
    # ------------------------------------------------------------------
    def test_vacant_days_count_up_until_the_unit_is_ready(self):
        turn = self.env['realestate.unit.turn'].create({
            'property_id': self.unit_b.id,
            'start_date': self.today - timedelta(days=3),
        })
        self.assertEqual(turn.vacant_days, 3)
        with self._on(self.today + timedelta(days=7)):
            self.env.invalidate_all()
            self.assertEqual(turn.vacant_days, 10)

        turn.action_start()
        turn.action_pass_inspection()
        turn.action_mark_ready()
        self.assertEqual(turn.turnaround_days, 3)
        with self._on(self.today + timedelta(days=30)):
            self.env.invalidate_all()
            self.assertEqual(turn.vacant_days, 3)
            self.assertEqual(turn.turnaround_days, 3)

    def test_a_party_stops_being_active_after_its_until_date(self):
        lease = self.make_lease(prop=self.unit_b)
        guarantor = self.env['res.partner'].create({'name': 'Calendar Co-Tenant'})
        party = self.env['realestate.contract.party'].create({
            'contract_id': lease.id,
            'partner_id': guarantor.id,
            'role': 'co_tenant',
            'end_date': self.today + timedelta(days=5),
        })
        Party = self.env['realestate.contract.party']
        self.assertTrue(party.is_active_party)
        with self._on(self.today + timedelta(days=6)):
            self.env.invalidate_all()
            self.assertFalse(party.is_active_party)
            mine = [('id', '=', party.id)]
            self.assertFalse(Party.search(mine + [('is_active_party', '=', True)]))
            self.assertEqual(Party.search(mine + [('is_active_party', '=', False)]), party)

    def test_an_escalation_takes_effect_on_its_date(self):
        lease = self._live_lease(
            self.unit_b, self.today - timedelta(days=30),
            self.today + relativedelta(months=11), rent=1000.0)
        effective = self.today + timedelta(days=10)
        self.env['realestate.rent.escalation.rule'].create({
            'contract_id': lease.id,
            'effective_date': effective,
            'escalation_type': 'percentage',
            'percentage': 10.0,
        })
        self.assertAlmostEqual(lease.current_rent, 1000.0, places=2)
        self.assertEqual(lease.next_escalation_date, effective)
        with self._on(effective):
            self._refresh(self.today, effective)
            self.assertAlmostEqual(lease.current_rent, 1100.0, places=2)
            self.assertFalse(lease.next_escalation_date)

    # ------------------------------------------------------------------
    # Dashboard tiles open the records they count
    # ------------------------------------------------------------------
    def test_expiry_tiles_match_their_drilldowns_on_any_day(self):
        start = self.today - timedelta(days=30)
        for code, days in (('E20', 20), ('E50', 50), ('E80', 80), ('E100', 100)):
            self._live_lease(self._unit(code), start, self.today + timedelta(days=days))
        Dashboard = self.env['realestate.rental.dashboard']
        Contract = self.env['realestate.contract']

        def tiles_and_drilldowns(day):
            values = self._tile_values()
            counts = {}
            for key, tile in (('expiring_30', 'd30'), ('expiring_60', 'd60'),
                              ('expiring_90', 'd90')):
                domain = Dashboard.action_drill(key, 'team')['domain']
                self.assertEqual(values[key], Contract.search_count(domain), (day, key))
                counts[tile] = values[key]
            return counts

        today_counts = tiles_and_drilldowns(self.today)
        later = self.today + timedelta(days=15)
        with self._on(later):
            self.env.invalidate_all()
            later_counts = tiles_and_drilldowns(later)
        # The lease ending in 100 days enters the 90-day window.
        self.assertEqual(later_counts['d90'], today_counts['d90'] + 1)

    def test_arrears_chart_matches_its_bucket_drilldowns_on_any_day(self):
        lease = self._live_lease(
            self.parking, self.today - timedelta(days=100),
            self.today + relativedelta(months=9), rent=500.0, use_billing_engine=True)
        lease.action_generate_billing_schedule()
        for obligation in lease.contract_payment_ids.filtered(
                lambda o: o.date_due <= self.today).sorted('date_due'):
            obligation._create_invoices()
        Dashboard = self.env['realestate.rental.dashboard']
        Obligation = self.env['realestate.contract.payment']
        company_ids = self.env.companies.ids

        def chart_matches(day):
            self.env.invalidate_all()
            chart = Dashboard._arrears_aging(company_ids)
            for index, key in enumerate(chart['keys']):
                domain = Dashboard.action_drill_arrears_bucket(key)['domain']
                self.assertEqual(chart['counts'][index], Obligation.search_count(domain),
                                 (day, key))
            self.assertEqual(sum(chart['counts']),
                             Obligation.search_count(Dashboard._arrears_domain(company_ids)))
            return chart

        chart_now = chart_matches(self.today)
        self.assertTrue(sum(chart_now['counts'][1:]), "The fixture needs overdue obligations.")
        with self._on(self.today + timedelta(days=45)):
            chart_later = chart_matches(self.today + timedelta(days=45))
        self.assertNotEqual(chart_now['counts'], chart_later['counts'])

    # ------------------------------------------------------------------
    # The daily job
    # ------------------------------------------------------------------
    def test_the_daily_job_runs_once_per_day(self):
        Refresh = self.env['realestate.calendar.refresh']
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('atmta_real_estate.calendar_refresh_date', False)
        self.assertIsInstance(Refresh._cron_refresh(), dict)
        self.assertEqual(Param.get_param('atmta_real_estate.calendar_refresh_date'),
                         str(self.today))
        self.assertEqual(Refresh._cron_refresh(), {})
