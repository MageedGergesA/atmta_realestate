# -*- coding: utf-8 -*-
"""What the Rental Overview does for a user who may not read what it counts.

Driving both Rental dashboards as every role showed one gap. The Overview's
menu -- alone among the Rental menus -- named no group, so any internal user
was offered it. The dashboard then counted tiles on ``realestate.property``
and ``realestate.contract``, and the whole screen died with an
``AccessError``; the trend charts did the same on the billing obligations.

``atmta_dashboard/models/dashboard_provider.py`` sets the convention this
module follows: a figure on a model the user cannot read is *dropped*, never
raised at the user and never drawn as a zero that reads as "nothing to do".
The same rule covers the quick-action buttons -- a button that could only end
in an access error is not offered -- and the Rental menu now names its roles
like every other Rental menu, so the question stops arising in the first place.
"""

import os

from odoo.exceptions import UserError
from odoo.modules.module import get_module_path
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase, shown_menu_ids

#: Overview tiles counted on a model only a Rental role may read.
NEEDS_RENTAL_ACCESS = ('available_to_lease', 'occupied_units', 'occupancy_rate',
                       'active_leases')

#: Every role the Overview menu is offered to.
RENTAL_ROLES = ('group_realestate_readonly', 'group_rental_user',
                'group_rental_agent', 'group_property_manager',
                'group_rental_manager')


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestDashboardAccess(LeaseCase):

    def setUp(self):
        super().setUp()
        self.Dashboard = self.env['realestate.rental.dashboard']
        # An internal user with no Rental role at all -- the case the Overview
        # was offered to and could not serve.
        self.outsider = new_test_user(
            self.env, login='re_dash_outsider', groups='base.group_user',
            company_id=self.company.id)
        self.assertFalse(
            self.env['realestate.property'].with_user(self.outsider).has_access('read'),
            "The outsider can read units; these tests would prove nothing.")

    def _tiles(self, user, scope='team'):
        work = self.Dashboard.with_user(user).get_work(scope)
        return {tile['key']: tile
                for section in work['sections'] for tile in section['tiles']}

    # ------------------------------------------------------------------
    # The app is offered to the roles that can use it
    # ------------------------------------------------------------------
    def test_the_overview_is_not_offered_without_a_rental_role(self):
        visible = shown_menu_ids(self.env, self.outsider)
        for xmlid in ('atmta_real_estate.real_estate_menu_root',
                      'atmta_real_estate.menu_rental_dashboard'):
            self.assertNotIn(self.env.ref(xmlid).id, visible, xmlid)

    def test_every_rental_role_still_gets_the_overview(self):
        overview = self.env.ref('atmta_real_estate.menu_rental_dashboard').id
        for group in RENTAL_ROLES:
            user = new_test_user(
                self.env, login='re_dash_menu_%s' % group, company_id=self.company.id,
                groups='base.group_user,atmta_real_estate.%s' % group)
            self.assertIn(overview, shown_menu_ids(self.env, user), group)

    # ------------------------------------------------------------------
    # An unreadable model drops its figure; it does not raise and does not zero
    # ------------------------------------------------------------------
    def test_a_tile_on_an_unreadable_model_is_dropped_not_raised(self):
        tiles = self._tiles(self.outsider)
        for key in NEEDS_RENTAL_ACCESS:
            self.assertNotIn(
                key, tiles,
                "%s counts a model the user cannot read: it must be dropped, "
                "not shown as a zero that reads as 'nothing to do'" % key)
        # Activities are readable by every internal user, so that tile stays:
        # the guard drops what cannot be read, not the whole dashboard.
        self.assertIn('my_activities', tiles)
        self.assertTrue(self.Dashboard.with_user(self.outsider).get_work('mine'))

    def test_a_trend_chart_on_an_unreadable_model_is_dropped_not_raised(self):
        self.assertEqual(
            self.Dashboard.with_user(self.outsider).get_trends()['charts'], {})

    def test_a_quick_action_that_could_only_fail_is_not_offered(self):
        work = self.Dashboard.with_user(self.outsider).get_work('team')
        self.assertEqual(
            work['quick_actions'], [],
            "Find Available Unit was offered to a user who cannot read a unit")
        with self.assertRaises(UserError):
            self.Dashboard.with_user(self.outsider).action_quick('available_units')

    def test_the_guard_costs_the_real_roles_nothing(self):
        manager = new_test_user(
            self.env, login='re_dash_guard_manager', company_id=self.company.id,
            groups='base.group_user,atmta_real_estate.group_rental_manager')
        Manager = self.Dashboard.with_user(manager)
        tiles = self._tiles(manager)
        for key in NEEDS_RENTAL_ACCESS + ('leases_to_approve', 'deposits_to_settle',
                                          'outstanding_rent', 'units_in_turnaround'):
            self.assertIn(key, tiles, key)
        self.assertEqual(
            set(Manager.get_trends()['charts']),
            {'billed_vs_collected', 'arrears_aging', 'expiries_by_month',
             'occupancy_trend'})
        self.assertEqual(
            [quick['key'] for quick in Manager.get_work('team')['quick_actions']],
            ['new_lease', 'available_units', 'move_in', 'move_out'])

    def test_the_legacy_read_only_role_keeps_the_figures_it_can_read(self):
        """The bottom rung reads units, leases and obligations; it keeps them."""
        readonly = new_test_user(
            self.env, login='re_dash_legacy_ro', company_id=self.company.id,
            groups='base.group_user,atmta_real_estate.group_realestate_readonly')
        tiles = self._tiles(readonly)
        for key in NEEDS_RENTAL_ACCESS + ('my_activities',):
            self.assertIn(key, tiles, key)
        self.assertEqual(
            set(self.Dashboard.with_user(readonly).get_trends()['charts']),
            {'billed_vs_collected', 'arrears_aging', 'expiries_by_month',
             'occupancy_trend'})

    # ------------------------------------------------------------------
    # A figure must not claim to answer a question it does not answer
    # ------------------------------------------------------------------
    def test_the_collected_series_does_not_claim_money_it_never_counted(self):
        """``_billed_vs_collected`` groups by the obligation's DUE date.

        Its third series is therefore "how much of what fell due that month has
        been paid", not "how much money came in that month" -- rent banked today
        against an invoice due in March moves March, not today. The front end
        described it as money "actually collected", which is a different
        question. The cash-basis figure cannot replace it here: no Rental role
        may read ``account.payment``, by design (leasing_groups.xml).
        """
        schema = os.path.join(
            get_module_path('atmta_real_estate'),
            'static', 'src', 'js', 'dashboard', 'dashboard_schema.js')
        with open(schema, encoding='utf-8') as handle:
            source = handle.read()
        self.assertNotIn(
            'actually collected', source,
            "Billed vs Collected is grouped by due date; it must not describe "
            "itself as money actually collected.")
