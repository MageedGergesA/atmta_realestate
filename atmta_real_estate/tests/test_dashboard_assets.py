"""Asset-bundle gates for the Rental Dashboard V2 release.

Three things are asserted here, all of which are release blockers and none of
which any other test would catch:

1. There is one dashboard implementation. The first-generation front end,
   which registered the same ``realestate.rental_dashboard`` client-action
   key, was removed in 0.10 and must not come back, on disk or in a bundle.
2. Chart.js must exist exactly once, and must come from Odoo's own
   ``web.chartjs_lib`` — not from a vendored duplicate and not from a CDN.
3. Production (non-``--dev``) assets must actually build and serve minified,
   and the dashboard's own code must survive minification.
"""

import os
import re

from odoo.modules.module import get_module_path
from odoo.tests.common import HttpCase, TransactionCase, tagged

REMOVED_V1_FILES = (
    'atmta_real_estate/static/src/js/rental_dashboard.js',
    'atmta_real_estate/static/src/xml/rental_dashboard.xml',
    'atmta_real_estate/static/src/scss/rental_dashboard.scss',
)

V2_FILES = (
    'atmta_real_estate/static/src/js/dashboard/rental_dashboard.js',
    'atmta_real_estate/static/src/js/dashboard/dashboard_schema.js',
    'atmta_real_estate/static/src/js/dashboard/dashboard_chart.js',
    'atmta_real_estate/static/src/js/dashboard/dashboard_map.js',
    'atmta_real_estate/static/src/js/map_tiles.js',
    'atmta_real_estate/static/src/js/dashboard/kpi_card.js',
    'atmta_real_estate/static/src/xml/dashboard/rental_dashboard.xml',
    'atmta_real_estate/static/src/scss/dashboard/rental_dashboard.scss',
)


@tagged('post_install', '-at_install', 'atmta_dashboard_assets')
class TestDashboardBundleComposition(TransactionCase):
    """What is, and is not, in web.assets_backend."""

    def _backend_paths(self):
        params = self.env['ir.asset']._get_asset_params()
        paths = self.env['ir.asset']._get_asset_paths(
            'web.assets_backend', params)
        # (path, addon, bundle) tuples; normalise to forward-slash fragments.
        return [entry[0].replace('\\', '/') for entry in paths]

    def test_only_one_dashboard_implementation_exists(self):
        """The first-generation dashboard is gone from disk and from the bundle."""
        addons_root = os.path.dirname(get_module_path('atmta_real_estate'))
        bundled = self._backend_paths()
        for stale in REMOVED_V1_FILES:
            self.assertFalse(
                os.path.exists(os.path.join(addons_root, stale)),
                "Removed Rental Dashboard V1 file %s is back on disk." % stale)
            self.assertFalse(
                [p for p in bundled if p.endswith(stale)],
                "Removed Rental Dashboard V1 file %s is in web.assets_backend. "
                "It registers the same client-action key as the dashboard."
                % stale)

    def test_v2_dashboard_is_bundled(self):
        """Guards the inverse mistake: removing V1 must not remove V2."""
        bundled = self._backend_paths()
        for wanted in V2_FILES:
            self.assertTrue(
                [p for p in bundled if p.endswith(wanted)],
                "Rental Dashboard V2 file %s is missing from web.assets_backend"
                % wanted)

    def test_no_duplicate_chartjs(self):
        """Chart.js appears once, in Odoo's own lazy bundle, and never here."""
        bundled = self._backend_paths()
        offenders = [
            p for p in bundled
            if 'chartjs' in p.lower() or p.endswith('/Chart/Chart.js')
        ]
        self.assertFalse(
            offenders,
            "Chart.js must not be in web.assets_backend (found %s). The "
            "dashboard lazy-loads web.chartjs_lib instead." % offenders)

        # Entries are (url_path, fs_path, bundle, mtime) — index, do not unpack,
        # so a future extra element does not break this gate.
        lib = [entry[0] for entry in self.env['ir.asset']._get_asset_paths(
            'web.chartjs_lib', self.env['ir.asset']._get_asset_params())]
        # The bundle legitimately also carries the luxon date adapter, so the
        # assertion is "exactly one copy of the library itself", not "one file".
        copies = [p for p in lib if p.endswith('/Chart.js')
                  or p.endswith('/chart.umd.js')]
        self.assertEqual(
            copies, ['/web/static/lib/Chart/Chart.js'],
            "Chart.js must be served exactly once, from Odoo's own "
            "web/static/lib. Bundle contains: %s" % (lib,))

    def test_no_cdn_references_in_dashboard_assets(self):
        """Production must render with no outbound internet access.

        One exception, by decision (15 Sep 2026): the map card's street
        background comes from the same online tile server as Units → Map,
        defined once in ``static/src/js/map_tiles.js``. Without internet the
        card still shows its units, counts and legend on a blank background.
        That file is scanned too, and only this exact host is allowed.
        """
        import os
        module_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        url_pattern = re.compile(r'https?://([^\s"\'/]+)[^\s"\']*', re.I)
        cdn_pattern = re.compile(r'cdn|jsdelivr|unpkg|cloudflare|googleapis', re.I)
        allowed_tile_host = 'tile.openstreetmap.org'
        paths = [os.path.join(module_path, 'static', 'src', 'js', 'map_tiles.js')]
        for root, _dirs, files in os.walk(os.path.join(
                module_path, 'static', 'src', 'js', 'dashboard')):
            paths.extend(os.path.join(root, name) for name in files)
        offenders = []
        allowed = []
        for path in paths:
            with open(path, encoding='utf-8') as fh:
                for lineno, line in enumerate(fh, 1):
                    for match in url_pattern.finditer(line):
                        if (path.endswith('map_tiles.js')
                                and match.group(1) == allowed_tile_host):
                            # The one tile server, recognised by host. It used
                            # to be caught by the CDN pattern only because
                            # CARTO's host happened to contain "cdn".
                            allowed.append('%s:%d' % (path, lineno))
                        elif cdn_pattern.search(match.group(0)):
                            offenders.append('%s:%d' % (path, lineno))
        self.assertEqual(len(allowed), 1,
                         "Exactly one tile address is allowed, in map_tiles.js: %s" % allowed)
        self.assertFalse(
            offenders,
            "Dashboard assets reference a CDN, so the dashboard would degrade "
            "without internet access: %s" % offenders)


@tagged('post_install', '-at_install', 'atmta_dashboard_assets')
class TestDashboardProductionAssets(HttpCase):
    """The bundle as a browser actually receives it, with no dev mode."""

    def test_backend_bundle_is_minified_and_contains_the_dashboard(self):
        self.authenticate('admin', 'admin')
        # Explicitly NOT debug mode: this is the production code path.
        bundle = self.env['ir.qweb']._get_asset_bundle(
            'web.assets_backend', css=False, js=True, debug_assets=False)
        # `get_link` returns the URL a browser is actually served, and picks
        # the `.min.js` variant whenever debug assets are off.
        url = bundle.get_link('js')
        self.assertIn(
            '.min.js', url,
            "Production backend bundle is not minified: %s" % url)

        response = self.url_open(url)
        self.assertEqual(response.status_code, 200,
                         "Minified backend bundle did not serve: %s" % url)
        content = response.content.decode('utf-8', 'replace')

        # The dashboard must be inside the minified bundle. Class names survive
        # Odoo's minifier; local variable names do not, so assert on the former.
        for marker in ('RentalDashboard', 'DashboardChart', 'KpiCard',
                       'realestate.rental_dashboard'):
            self.assertIn(
                marker, content,
                "Minified production bundle is missing %r — the dashboard "
                "would not load for a real user." % marker)

        # And the deprecated V1 marker must NOT be there. V1's payload keys are
        # the cleanest fingerprint, since the class name is shared.
        self.assertNotIn(
            'properties_by_city', content,
            "The deprecated V1 dashboard leaked into the production bundle.")

    def test_dashboard_loads_on_a_cold_cache(self):
        """A fresh browser, no warmed assets, no stale attachment.

        ``browser_js`` starts a brand-new Chrome with an empty profile, so the
        cache is cold by construction. Regenerating the bundles first makes the
        test also cover the first-request-after-deploy case, which is when
        asset bugs actually bite.
        """
        self.env['ir.attachment'].search([
            ('url', 'like', '/web/assets/%'),
            ('name', 'like', 'web.assets_backend%'),
        ]).unlink()

        # The browser logs in as base.user_admin, not as self.env.user
        # (OdooBot), so the group must go on the user the tour will actually be.
        self.env.ref('base.user_admin').groups_id |= self.env.ref(
            'atmta_real_estate.group_rental_manager')
        self.env['realestate.property'].create({
            'name': 'Cold Cache Unit',
            'property_code': 'COLD-U-001',
            'hierarchy_level': 'unit',
            'usage_category': 'apartment',
            'area_sqm': 55.0,
            'company_id': self.env.company.id,
        })

        self.start_tour("/odoo", "atmta_rental_dashboard_tour",
                        login="admin", timeout=240)
