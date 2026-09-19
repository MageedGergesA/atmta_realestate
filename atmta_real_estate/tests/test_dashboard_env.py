"""Real-browser environment verification for the Rental Dashboard V2.

Every class here drives a real (headless) Chrome through ``start_tour``. What
varies between them is the *environment*, which is exactly the part a code or
stylesheet review cannot vouch for:

* :class:`TestDashboardTablet1024` / ``991`` / ``768`` — viewport, via
  ``HttpCase.browser_size``, with touch emulation on.
* :class:`TestDashboardRTL` — a genuinely Arabic session, asserted from
  ``getComputedStyle().direction`` and real element geometry.
* :class:`TestDashboardEmptyCompany` — a company with no data at all, asserted
  to contain no ``NaN``/``undefined``/``Infinity``.
* :class:`TestDashboardMultiCompany` — two companies with deliberately
  different data, switched with Odoo's own company switcher, asserted for
  cross-company leakage.

None of these type credentials: ``start_tour`` authenticates server-side.
"""

from odoo.tests.common import HttpCase, tagged


def _js_error_checker(message):
    """Ignore asset/network noise; fail on anything that is a real JS error."""
    if not message:
        return False
    ignored = ("Failed to load resource", "favicon", "net::ERR_")
    return not any(token in message for token in ignored)


class DashboardEnvCommon(HttpCase):
    """Fixtures shared by every environment test."""

    def setUp(self):
        super().setUp()
        # `start_tour(login="admin")` authenticates as base.user_admin (uid 2),
        # NOT as self.env.user, which in a TransactionCase is OdooBot (uid 1).
        # Configuring the environment on self.env.user would leave the browser
        # session untouched and every assertion below would silently test the
        # default company in English.
        self.browser_user = self.env.ref('base.user_admin')

    def _grant_manager(self, user=None):
        user = user or self.browser_user
        user.groups_id |= self.env.ref('atmta_real_estate.group_rental_manager')

    def _seed_units(self, company, count, prefix):
        """Create ``count`` leasable units in ``company``.

        Units only — the tours assert on the *Leasable Units* KPI, and keeping
        the fixture to one record type makes the expected number unambiguous.
        """
        Property = self.env['realestate.property'].with_company(company)
        return Property.create([{
            'name': '%s Unit %02d' % (prefix, i),
            'property_code': '%s-U-%03d' % (prefix, i),
            'hierarchy_level': 'unit',
            'usage_category': 'apartment',
            'area_sqm': 60.0 + i,
            'company_id': company.id,
        } for i in range(1, count + 1)])


# ---------------------------------------------------------------------------
# Responsive layout — real viewports, real touch emulation
# ---------------------------------------------------------------------------

class _TabletCase(DashboardEnvCommon):
    """Shared body for the three tablet breakpoints."""

    # Tablets are touch devices; emulating that changes hover behaviour and
    # Odoo's own `.o_touch_device` handling, so testing at a tablet width with
    # a mouse would not be testing a tablet.
    touch_enabled = True

    def _run_layout_tour(self):
        self._grant_manager()
        self._seed_units(self.env.company, 6, 'TAB')
        self.start_tour("/odoo", "atmta_rental_dashboard_layout_tour",
                        login="admin", timeout=180)


@tagged('post_install', '-at_install', 'atmta_dashboard_env')
class TestDashboardTablet1024(_TabletCase):
    """Landscape tablet / small laptop."""
    browser_size = '1024x768'

    def test_layout_1024(self):
        self._run_layout_tour()


@tagged('post_install', '-at_install', 'atmta_dashboard_env')
class TestDashboardTablet991(_TabletCase):
    """The Bootstrap lg/md boundary — the breakpoint most likely to break."""
    browser_size = '991x768'

    def test_layout_991(self):
        self._run_layout_tour()


@tagged('post_install', '-at_install', 'atmta_dashboard_env')
class TestDashboardTablet768(_TabletCase):
    """Portrait tablet."""
    browser_size = '768x1024'

    def test_layout_768(self):
        self._run_layout_tour()


# ---------------------------------------------------------------------------
# RTL — an actually-Arabic session
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'atmta_dashboard_env')
class TestDashboardRTL(DashboardEnvCommon):
    """Arabic session, asserted in the browser rather than in the stylesheet."""

    def test_dashboard_rtl(self):
        lang = self.env['res.lang']._activate_lang('ar_001')
        if not lang:
            self.env['res.lang'].with_context(active_test=False).search(
                [('code', '=', 'ar_001')]).write({'active': True})
            lang = self.env['res.lang']._lang_get('ar_001')
        self.assertTrue(lang, "Arabic (ar_001) could not be activated")
        self.assertEqual(
            lang.direction, 'rtl',
            "ar_001 is not flagged RTL; the test would prove nothing")

        self._grant_manager()
        self._seed_units(self.env.company, 5, 'RTL')
        # The session language comes from the user's own `lang`, which is what
        # a real Arabic-speaking user would have set.
        self.browser_user.lang = 'ar_001'

        self.start_tour("/odoo", "atmta_rental_dashboard_rtl_tour",
                        login="admin", timeout=180)


# ---------------------------------------------------------------------------
# Empty company — zero data must render as zero
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'atmta_dashboard_env')
class TestDashboardEmptyCompany(DashboardEnvCommon):
    """A brand-new company with no properties, contracts, invoices or payments."""

    def test_dashboard_empty_company(self):
        empty = self.env['res.company'].create({'name': 'ATMTA Empty Co'})
        # The user may see ONLY the empty company, so every KPI denominator is
        # genuinely zero rather than merely small.
        self.browser_user.write({
            'company_ids': [(6, 0, [empty.id])],
            'company_id': empty.id,
        })
        self._grant_manager()

        self.assertFalse(
            self.env['realestate.property'].with_company(empty).search_count(
                [('company_id', '=', empty.id)]),
            "The 'empty' company is not empty; the test would be vacuous")

        self.start_tour("/odoo", "atmta_rental_dashboard_empty_tour",
                        login="admin", timeout=180)


# ---------------------------------------------------------------------------
# Multi-company — the native switcher, and no leakage
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'atmta_dashboard_env')
class TestDashboardMultiCompany(DashboardEnvCommon):
    """Two companies with different data, switched the way a user switches."""

    def test_dashboard_multi_company(self):
        company_a = self.env['res.company'].create({'name': 'ATMTA Company A'})
        company_b = self.env['res.company'].create({'name': 'ATMTA Company B'})

        # Deliberately different counts: if the dashboard leaked across
        # companies, or ignored the switch, the two figures would match and the
        # tour fails on that comparison.
        self._seed_units(company_a, 3, 'CA')
        self._seed_units(company_b, 7, 'CB')

        self.browser_user.write({
            'company_ids': [(6, 0, [company_a.id, company_b.id])],
            'company_id': company_a.id,
        })
        self._grant_manager()

        self.start_tour("/odoo", "atmta_rental_dashboard_company_tour",
                        login="admin", timeout=180)

    def test_dashboard_kpis_are_company_scoped_server_side(self):
        """The UI switch is only half of it — prove the scoping on the server.

        A leak here would not be visible to the tour if the switcher happened
        to reload correctly, so this asserts the same thing one layer down.
        """
        company_a = self.env['res.company'].create({'name': 'ATMTA Scope A'})
        company_b = self.env['res.company'].create({'name': 'ATMTA Scope B'})
        self._seed_units(company_a, 3, 'SA')
        self._seed_units(company_b, 7, 'SB')

        Dashboard = self.env['realestate.rental.dashboard']

        def tiles(companies):
            work = Dashboard.with_company(companies[0]).with_context(
                allowed_company_ids=[c.id for c in companies]).get_work('team')
            return {tile['key']: tile['value']
                    for section in work['sections'] for tile in section['tiles']}

        key = 'available_to_lease'
        data_a = tiles([company_a])
        self.assertIn(key, data_a, "Dashboard payload no longer publishes %s" % key)
        self.assertEqual(data_a[key], 3)
        self.assertEqual(tiles([company_b])[key], 7)
        self.assertEqual(
            tiles([company_a, company_b])[key], 10,
            "With both companies allowed the dashboard must aggregate both")
