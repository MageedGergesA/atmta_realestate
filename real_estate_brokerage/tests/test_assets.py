# -*- coding: utf-8 -*-
"""Asset-bundle gates for the Brokerage release.

The browser tours run against whatever bundle the test server happens to build.
These assert the bundle a *real* user is served: compiled, minified, cold
cache, no debug flag. A module can pass every tour and still ship a dashboard
that never mounts because its template was left out of the bundle.
"""

import os

from odoo.tests.common import HttpCase, TransactionCase, tagged

BROKERAGE_ASSETS = (
    'real_estate_brokerage/static/src/js/brokerage_dashboard.js',
    'real_estate_brokerage/static/src/xml/brokerage_dashboard.xml',
    'real_estate_brokerage/static/src/scss/brokerage_dashboard.scss',
)


@tagged('post_install', '-at_install')
class TestBrokerageBundleComposition(TransactionCase):

    def _paths(self, bundle):
        params = self.env['ir.asset']._get_asset_params()
        # Entries are (url_path, fs_path, bundle, mtime) — index rather than
        # unpack, so an extra element in a future release does not break this.
        return [entry[0].replace('\\', '/')
                for entry in self.env['ir.asset']._get_asset_paths(
                    bundle, params)]

    def test_the_dashboard_files_are_bundled(self):
        bundled = self._paths('web.assets_backend')
        for wanted in BROKERAGE_ASSETS:
            self.assertTrue(
                [p for p in bundled if p.endswith(wanted)],
                "%s is missing from web.assets_backend" % wanted)

    def test_the_owl_template_is_bundled(self):
        """A component whose template is absent mounts and renders nothing —
        a blank page with no error anywhere."""
        bundled = self._paths('web.assets_backend')
        self.assertTrue(
            [p for p in bundled if p.endswith(
                'real_estate_brokerage/static/src/xml/'
                'brokerage_dashboard.xml')])

    def test_the_tours_are_in_the_test_bundle_and_not_the_backend_one(self):
        """Test code must not ship to users."""
        tour = 'real_estate_brokerage/static/tests/tours/brokerage_tour.js'
        self.assertTrue(
            [p for p in self._paths('web.assets_tests') if p.endswith(tour)],
            "The tours are not in web.assets_tests; they would never run")
        self.assertFalse(
            [p for p in self._paths('web.assets_backend') if p.endswith(tour)],
            "The tour file is shipped to real users in web.assets_backend")

    def test_the_client_action_is_registered_once(self):
        module_dir = os.path.dirname(os.path.dirname(__file__))
        hits = []
        for dirpath, _dirs, files in os.walk(
                os.path.join(module_dir, 'static')):
            for name in files:
                if not name.endswith('.js'):
                    continue
                path = os.path.join(dirpath, name)
                with open(path) as handle:
                    if 'realestate.brokerage_dashboard' in handle.read():
                        hits.append(path)
        registrations = [p for p in hits if 'tests' not in p]
        self.assertEqual(
            len(registrations), 1,
            "The client-action key is registered in %s files: %s"
            % (len(registrations), registrations))

    def test_the_action_points_at_the_registered_tag(self):
        action = self.env.ref(
            'real_estate_brokerage.action_brokerage_dashboard')
        self.assertEqual(action.type, 'ir.actions.client')
        self.assertEqual(action.tag, 'realestate.brokerage_dashboard')


@tagged('post_install', '-at_install')
class TestBrokerageProductionAssets(HttpCase):
    """The bundle a real user gets — minified, cold cache, no debug flag."""

    def _production_js(self):
        bundle = self.env['ir.qweb']._get_asset_bundle(
            'web.assets_backend',
            assets_params=self.env['ir.asset']._get_asset_params())
        content = bundle.js().raw
        return content.decode() if isinstance(content, bytes) else content

    def test_the_production_bundle_is_minified_and_carries_the_dashboard(self):
        content = self._production_js()
        self.assertTrue(content, "The production JS bundle is empty")

        self.assertIn(
            'realestate.brokerage_dashboard', content,
            "The client-action key did not survive into the production bundle")

        # Minified: one newline every ~50 characters is a dev concatenation,
        # not compiled output.
        self.assertLess(
            content.count('\n') / max(len(content), 1), 0.02,
            "The bundle is not minified")

    def test_the_app_loads_on_a_cold_cache(self):
        """A fresh browser, no debug mode, no pre-warmed assets."""
        self.env['ir.attachment'].sudo().search(
            [('url', '=like', '/web/assets/%')]).unlink()
        self.authenticate('admin', 'admin')

        response = self.url_open(
            '/odoo/action-real_estate_brokerage.action_realestate_listing')

        self.assertEqual(response.status_code, 200)
        # And the bundle rebuilds rather than 404ing after the purge.
        self.assertTrue(self._production_js())

    def test_the_commission_migration_page_loads_on_a_cold_cache(self):
        self.env['ir.attachment'].sudo().search(
            [('url', '=like', '/web/assets/%')]).unlink()
        self.authenticate('admin', 'admin')

        response = self.url_open(
            '/odoo/action-real_estate_brokerage.'
            'action_commission_share_migration')

        self.assertEqual(response.status_code, 200)
