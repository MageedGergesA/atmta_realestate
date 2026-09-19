"""Browser-driven verification of the Rental Dashboard V2 front end.

Two layers, both running in a real (headless) Chrome:

1. :class:`TestRentalDashboardUnits` runs the HOOT unit suite
   (``static/tests/rental_dashboard.test.js``) — rendering, charts, loading,
   error, empty, drilldowns, refresh and lifecycle, against mocked RPCs.

2. :class:`TestRentalDashboardTour` runs an integration tour against REAL
   seeded data: open the menu, wait for the dashboard, assert that KPI values
   actually rendered, click a KPI and assert the filtered record list opens.

Both are tagged ``post_install`` and use ``browser_js``, which authenticates
server-side — no credentials are typed anywhere.
"""

import odoo.tests
from odoo.tests.common import HttpCase, tagged


def _js_error_checker(message):
    """Ignore the network/asset noise a dev instance emits, fail on real errors."""
    if not message:
        return False
    ignored = (
        "Failed to load resource",
        "favicon",
        "net::ERR_",
    )
    return not any(token in message for token in ignored)


@tagged('post_install', '-at_install', 'atmta_dashboard_js')
class TestRentalDashboardUnits(HttpCase):
    """Run the HOOT unit suite for this module only."""

    def test_dashboard_hoot_suite(self):
        self.browser_js(
            # `filter` matches a suite's full name. Without it the whole
            # web/* suite runs, and an unrelated core failure would be
            # reported as ours.
            "/web/tests?headless&loglevel=2&preset=desktop&timeout=30000"
            "&filter=atmta_real_estate",
            "",
            "",
            login="admin",
            timeout=600,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=_js_error_checker,
        )


@tagged('post_install', '-at_install', 'atmta_dashboard_tour')
class TestRentalDashboardTour(HttpCase):
    """End-to-end: menu → dashboard → KPI click → filtered records."""

    def test_dashboard_tour(self):
        # The tour asserts on rendered numbers, so it needs at least one
        # leasable property to exist; without data the assertions would be
        # vacuously true.
        self.env['realestate.property'].create({
            'name': 'Tour Unit',
            'property_code': 'TOUR-U-001',
            'hierarchy_level': 'unit',
            'usage_category': 'apartment',
            'area_sqm': 75.0,
            'company_id': self.env.company.id,
            # Placed on the map card, which the tour also checks.
            'latitude': 24.7136,
            'longitude': 46.6753,
        })
        # The browser logs in as base.user_admin, not as self.env.user
        # (OdooBot), so the group must go on the user the tour will actually be.
        self.env.ref('base.user_admin').groups_id |= self.env.ref(
            'atmta_real_estate.group_rental_manager')

        self.start_tour("/odoo", "atmta_rental_dashboard_tour",
                        login="admin", timeout=180)
