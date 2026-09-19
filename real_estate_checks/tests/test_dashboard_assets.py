# -*- coding: utf-8 -*-
"""Asset-bundle gates for the Treasury Dashboard release.

Four things are asserted here, all release blockers, none of which any other
test would catch:

1. The dashboard's own files are actually in `web.assets_backend`.
2. Chart.js exists exactly once and comes from Odoo's own `web.chartjs_lib` —
   not a vendored duplicate, not a CDN. This is the pattern Module 1
   established when its duplicate copy was removed in 0.4.
3. Production (non-`--dev`) assets build, serve minified, and the dashboard's
   own code survives minification.
4. The Treasury Dashboard does not depend on any other ATMTA dashboard's
   assets — loading it must not require the Rental or Developer bundle.
"""

import re

from odoo.tests.common import HttpCase, TransactionCase, tagged

CHECKS_ASSETS = (
    'real_estate_checks/static/src/js/check_dashboard.js',
    'real_estate_checks/static/src/xml/check_dashboard.xml',
    'real_estate_checks/static/src/scss/check_dashboard.scss',
)

#: Other ATMTA dashboards. The Treasury component must not import from these.
OTHER_DASHBOARDS = (
    'atmta_real_estate/static/src/js/dashboard/',
    'real_estate_developer/static/src/js/developer_dashboard',
    'real_estate_brokerage/static/src/js/brokerage_dashboard',
)


@tagged('post_install', '-at_install', 'atmta_checks_assets')
class TestTreasuryBundleComposition(TransactionCase):
    """What is, and is not, in web.assets_backend."""

    def _backend_paths(self):
        params = self.env['ir.asset']._get_asset_params()
        # Entries are (url_path, fs_path, bundle, mtime) — index, never unpack,
        # so an extra element in a future release does not break this gate.
        paths = self.env['ir.asset']._get_asset_paths(
            'web.assets_backend', params)
        return [entry[0].replace('\\', '/') for entry in paths]

    def test_the_dashboard_is_bundled(self):
        bundled = self._backend_paths()
        for wanted in CHECKS_ASSETS:
            self.assertTrue(
                [p for p in bundled if p.endswith(wanted)],
                "Treasury Dashboard file %s is missing from web.assets_backend"
                % wanted)

    def test_no_duplicate_chartjs(self):
        """Chart.js appears once, in Odoo's own lazy bundle, and never here."""
        bundled = self._backend_paths()
        offenders = [p for p in bundled
                     if 'chartjs' in p.lower() or p.endswith('/Chart/Chart.js')]
        self.assertFalse(
            offenders,
            "Chart.js must not be in web.assets_backend (found %s). The "
            "Treasury Dashboard lazy-loads web.chartjs_lib instead." % offenders)

        lib = [entry[0] for entry in self.env['ir.asset']._get_asset_paths(
            'web.chartjs_lib', self.env['ir.asset']._get_asset_params())]
        self.assertTrue(lib, "web.chartjs_lib is empty; loadBundle would no-op")

    def test_the_module_ships_no_vendored_chartjs(self):
        import os
        module_dir = os.path.dirname(os.path.dirname(__file__))
        for dirpath, _dirs, files in os.walk(os.path.join(module_dir, 'static')):
            for name in files:
                self.assertNotIn(
                    'chart', name.lower(),
                    "real_estate_checks ships a Chart.js copy at %s"
                    % os.path.join(dirpath, name))

    def test_the_dashboard_does_not_borrow_another_dashboard(self):
        """Loading Treasury must not require Rental's or Developer's assets.

        Asserted from the source rather than the bundle: an import that happens
        to resolve today because both modules are installed would be a hidden
        dependency the moment one is not.
        """
        import os
        module_dir = os.path.dirname(os.path.dirname(__file__))
        js_path = os.path.join(module_dir, 'static', 'src', 'js',
                               'check_dashboard.js')
        with open(js_path) as handle:
            source = handle.read()
        for foreign in OTHER_DASHBOARDS:
            self.assertNotIn(
                foreign, source,
                "The Treasury Dashboard imports from %s" % foreign)
        # It may import from `@web/...` and `@odoo/owl`, and from nothing else.
        imports = re.findall(r'from\s+"([^"]+)"', source)
        for module in imports:
            self.assertTrue(
                module.startswith('@web/') or module == '@odoo/owl',
                "Unexpected import %r in the Treasury Dashboard" % module)

    def test_the_client_action_is_registered_once(self):
        """Two registrations of the same key would race."""
        import os
        module_dir = os.path.dirname(os.path.dirname(__file__))
        hits = []
        for dirpath, _dirs, files in os.walk(os.path.join(module_dir, 'static')):
            for name in files:
                if not name.endswith('.js'):
                    continue
                path = os.path.join(dirpath, name)
                with open(path) as handle:
                    if 'realestate_check_dashboard' in handle.read():
                        hits.append(path)
        registrations = [p for p in hits if 'tests' not in p]
        self.assertEqual(
            len(registrations), 1,
            "The client action key is registered in %s files: %s"
            % (len(registrations), registrations))

    def test_the_action_points_at_the_registered_tag(self):
        action = self.env.ref('real_estate_checks.action_check_dashboard')
        self.assertEqual(action.type, 'ir.actions.client')
        self.assertEqual(action.tag, 'realestate_check_dashboard')


@tagged('post_install', '-at_install', 'atmta_checks_assets')
class TestTreasuryProductionAssets(HttpCase):
    """The bundle a real user gets — minified, cold cache, no debug flag."""

    def _production_js(self):
        """The compiled JS bundle a real user is served.

        No `debug=assets`: Odoo builds and minifies on demand, and the URL is
        what a browser actually requests.
        """
        bundle = self.env['ir.qweb']._get_asset_bundle(
            'web.assets_backend',
            assets_params=self.env['ir.asset']._get_asset_params())
        attachment = bundle.js()
        content = attachment.raw
        return content.decode() if isinstance(content, bytes) else content

    def test_production_bundle_is_minified_and_contains_the_dashboard(self):
        self.authenticate('admin', 'admin')
        content = self._production_js()
        self.assertTrue(content, "The production JS bundle is empty")

        self.assertIn(
            'realestate_check_dashboard', content,
            "The Treasury Dashboard's client-action key did not survive into "
            "the production bundle")
        self.assertIn(
            'o_re_check_dashboard', content,
            "The dashboard's root class is absent from the production bundle")

        # Minified: the source comments are gone.
        self.assertNotIn(
            'the treasury dashboard', content.lower(),
            "The production bundle still carries source comments; it is not "
            "minified")
        # And it really is one compiled file rather than a dev concatenation.
        self.assertLess(
            content.count('\n') / max(len(content), 1), 0.02,
            "The bundle has one newline every ~50 characters, which is not "
            "minified output")

    def test_the_template_is_in_the_qweb_bundle(self):
        """An OWL component whose template is missing mounts and renders
        nothing at all — a blank page with no error."""
        params = self.env['ir.asset']._get_asset_params()
        paths = [entry[0] for entry in self.env['ir.asset']._get_asset_paths(
            'web.assets_backend', params)]
        self.assertTrue(
            [p for p in paths if p.endswith(
                'real_estate_checks/static/src/xml/check_dashboard.xml')],
            "The dashboard's OWL template is not in web.assets_backend; the "
            "component would mount and render nothing")

    def test_the_dashboard_loads_on_a_cold_cache(self):
        """A fresh browser, no debug mode, no pre-warmed assets."""
        self.env['ir.attachment'].sudo().search(
            [('url', '=like', '/web/assets/%')]).unlink()
        self.authenticate('admin', 'admin')
        response = self.url_open(
            '/odoo/action-real_estate_checks.action_check_dashboard')
        self.assertEqual(response.status_code, 200)
        # And the bundle rebuilds rather than 404ing after the purge.
        self.assertTrue(self._production_js())

    def test_the_template_survives_the_bundle(self):
        """The OWL template is XML compiled into the bundle; if it is missing
        the component mounts and renders a blank page with no error."""
        from lxml import etree
        bundle = self.env['ir.qweb']._get_asset_bundle(
            'web.assets_backend',
            assets_params=self.env['ir.asset']._get_asset_params())
        # `xml()` returns structured template data, not a string.
        rendered = []
        for entry in bundle.xml():
            for item in entry.get('templates', []):
                # Entries are variable-length tuples; index, never unpack.
                node = item[0] if isinstance(item, (list, tuple)) else item
                try:
                    rendered.append(etree.tostring(node, encoding='unicode'))
                except TypeError:
                    rendered.append(str(node))
        content = '\n'.join(rendered)
        self.assertIn('real_estate_checks.TreasuryDashboard', content,
                      "The dashboard template is not in the compiled bundle")
        self.assertIn('o_re_check_kpi', content)
        self.assertIn('o_re_check_body', content)
