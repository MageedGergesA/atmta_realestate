# -*- coding: utf-8 -*-
"""Asset-bundle gates for the visual stack.

This file exists because of a defect it would have caught in seconds.

`maquette_viewer.js` gained two imports — `./visual_capability` and
`./visual_dispose` — and the new files were not added to the asset bundles.
Odoo's module loader then reported

    The following modules are needed by other modules but have not been
    defined: ['@real_estate_maquette/js/visual_capability', ...]

and **every page that loads `web.assets_backend` broke**, not just the viewer.
The full 14-module regression caught it as 51 browser failures spread across
`atmta_real_estate` and `real_estate_brokerage`, which looked like an
environment problem and was not.

A missing bundle entry is a one-line mistake with a whole-application blast
radius, and it is entirely checkable from the manifest.
"""

import os
import re

from odoo.tests.common import HttpCase, TransactionCase, tagged

#: Bundles the viewer participates in. `maquette_viewer.js` is served to both
#: the backend and public portal pages, so anything it imports must be in both.
VIEWER_BUNDLES = ('web.assets_backend', 'web.assets_frontend')

#: Relative-import statements: `import ... from "./something"`.
RELATIVE_IMPORT = re.compile(r'from\s+"\.\/([A-Za-z0-9_\-]+)"')

#: A CDN host appearing as an actual URL. Deliberately requires the scheme or
#: protocol-relative prefix, so prose describing the problem is not mistaken
#: for the problem.
CDN_URL = re.compile(
    r'(https?:)?//(unpkg\.com|cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com)')


@tagged('post_install', '-at_install')
class TestVisualAssetBundles(TransactionCase):

    def _bundle_paths(self, bundle):
        params = self.env['ir.asset']._get_asset_params()
        # Entries are (url_path, fs_path, bundle, mtime) — index rather than
        # unpack, so an extra element in a future release does not break this.
        return [entry[0].replace('\\', '/')
                for entry in self.env['ir.asset']._get_asset_paths(
                    bundle, params)]

    def _module_dir(self):
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_every_relative_import_is_in_the_same_bundles(self):
        """The gate. A file that imports a sibling must be bundled with it.

        Walks every JS file this module ships, reads its relative imports, and
        asserts the imported file appears in each bundle its importer appears
        in. This is exactly the check that was missing.
        """
        js_dir = os.path.join(self._module_dir(), 'static', 'src', 'js')
        bundles = {b: self._bundle_paths(b) for b in VIEWER_BUNDLES}

        for name in sorted(os.listdir(js_dir)):
            if not name.endswith('.js'):
                continue
            importer = 'real_estate_maquette/static/src/js/%s' % name
            with open(os.path.join(js_dir, name)) as handle:
                source = handle.read()
            imported = set(RELATIVE_IMPORT.findall(source))
            if not imported:
                continue

            for bundle, paths in bundles.items():
                if not any(p.endswith(importer) for p in paths):
                    continue        # this importer is not in this bundle
                for sibling in imported:
                    target = ('real_estate_maquette/static/src/js/%s.js'
                              % sibling)
                    with self.subTest(bundle=bundle, importer=name,
                                      imported=sibling):
                        self.assertTrue(
                            any(p.endswith(target) for p in paths),
                            "%s is in %s and imports './%s', which is not. "
                            "Odoo's module loader will fail to define it and "
                            "every page using that bundle breaks."
                            % (name, bundle, sibling))

    def test_the_new_visual_modules_are_bundled(self):
        """Named explicitly, so the regression is obvious in the test list."""
        for bundle in VIEWER_BUNDLES:
            paths = self._bundle_paths(bundle)
            for name in ('visual_capability.js', 'visual_dispose.js'):
                with self.subTest(bundle=bundle, file=name):
                    self.assertTrue(
                        [p for p in paths if p.endswith(
                            'real_estate_maquette/static/src/js/%s' % name)],
                        "%s is missing from %s" % (name, bundle))

    def test_threejs_is_never_bundled(self):
        """It is imported dynamically and must stay that way.

        1.27 MB of unminified `three.module.js` in `web.assets_backend` would
        be downloaded by every user on every backend page, for a viewer most of
        them never open.
        """
        for bundle in VIEWER_BUNDLES:
            paths = self._bundle_paths(bundle)
            offenders = [p for p in paths if '/lib/threejs/' in p]
            self.assertFalse(
                offenders,
                "Three.js must be loaded by dynamic import, not bundled: %s"
                % offenders)

    def test_there_is_exactly_one_threejs_in_the_repository(self):
        """A second copy would mean two revisions and two sets of loaders."""
        suite_dir = os.path.dirname(self._module_dir())
        found = []
        for root, _dirs, files in os.walk(suite_dir):
            for name in files:
                if name == 'three.module.js':
                    found.append(os.path.join(root, name))
        self.assertEqual(
            len(found), 1,
            "Expected one vendored Three.js, found %s: %s"
            % (len(found), found))

    def test_no_javascript_reaches_out_to_a_cdn(self):
        """The audit found the Draco decoder fetched from unpkg.com.

        A viewer that needs the public internet does not work on an on-premise
        install or behind a GCC firewall, and the failure surfaced as an
        unexplained error box.
        """
        js_dir = os.path.join(self._module_dir(), 'static', 'src', 'js')
        offenders = []
        for name in sorted(os.listdir(js_dir)):
            if not name.endswith('.js'):
                continue
            with open(os.path.join(js_dir, name)) as handle:
                for lineno, line in enumerate(handle, start=1):
                    # Match a CDN *host in a URL*, not the word in a comment.
                    # This file's own history is the reason the distinction
                    # matters: the fix for the unpkg dependency explains itself
                    # in a comment, and a naive substring search flagged the
                    # explanation as the defect.
                    if CDN_URL.search(line):
                        offenders.append('%s:%s' % (name, lineno))
        self.assertFalse(
            offenders,
            "Production JavaScript must not depend on a CDN: %s" % offenders)


@tagged('post_install', '-at_install')
class TestDracoStrategy(TransactionCase):
    """M4.5-D — a local, version-pinned decoder, or a refusal.

    The decoder binaries are not in this repository and cannot be invented, so
    what is testable is the *strategy*: the right files are looked for, in the
    right place, from the right Three.js release, and a mismatch is treated as
    more dangerous than an absence — because it is.
    """

    def setUp(self):
        super().setUp()
        self.Assets = self.env['realestate.visual.assets']

    def test_the_expected_files_match_what_the_loader_fetches(self):
        """Read from the vendored loader, not assumed.

        `DRACOLoader._initDecoder()` asks for `draco_wasm_wrapper.js` and
        `draco_decoder.wasm` when WebAssembly is available, and
        `draco_decoder.js` when it is not. If a future Three.js changes that,
        this fails rather than the deployment silently missing a file.
        """
        from ..models.visual_assets import (
            DRACO_OPTIONAL_FILES, DRACO_REQUIRED_FILES)
        loader = os.path.join(
            self._module_dir(), 'static', 'src', 'lib', 'threejs',
            'DRACOLoader.js')
        with open(loader) as handle:
            source = handle.read()
        for name in DRACO_REQUIRED_FILES + DRACO_OPTIONAL_FILES:
            with self.subTest(file=name):
                self.assertIn(
                    "'%s'" % name, source,
                    "%s is expected by this module but never fetched by the "
                    "bundled DRACOLoader" % name)

    def _module_dir(self):
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_the_pinned_revision_matches_the_bundled_threejs(self):
        """The pin is derived from the library, not typed twice."""
        from ..models.visual_assets import BUNDLED_THREEJS_REVISION
        three = os.path.join(
            self._module_dir(), 'static', 'src', 'lib', 'threejs',
            'three.module.js')
        with open(three) as handle:
            for line in handle:
                if 'const REVISION' in line:
                    revision = line.split("'")[1]
                    break
            else:
                self.fail("Could not read REVISION from three.module.js")
        self.assertEqual(BUNDLED_THREEJS_REVISION, revision)

    def test_the_status_report_names_what_is_missing(self):
        """"You are missing draco_decoder.wasm" beats "it is not working"."""
        status = self.Assets.draco_decoder_status()

        self.assertFalse(status['installed'])
        self.assertIn('draco_wasm_wrapper.js', status['missing'])
        self.assertIn('draco_decoder.wasm', status['missing'])

    def test_a_draco_model_is_refused_while_the_decoder_is_absent(self):
        issue = self.Assets.draco_issue(['KHR_draco_mesh_compression'])

        self.assertIsNotNone(issue)
        self.assertEqual(issue[0], 'critical')
        self.assertEqual(issue[1], 'draco_decoder_missing')

    def test_a_model_without_draco_raises_nothing(self):
        self.assertIsNone(self.Assets.draco_issue(['KHR_materials_unlit']))
        self.assertIsNone(self.Assets.draco_issue([]))

    def test_an_external_decoder_path_is_taken_at_its_word(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'real_estate_maquette.draco_decoder_path',
            'https://assets.internal.example/draco/')

        status = self.Assets.draco_decoder_status()

        self.assertTrue(status['installed'])
        self.assertTrue(status['external'])
        self.assertIsNone(self.Assets.draco_issue(
            ['KHR_draco_mesh_compression']))

    def test_the_install_instructions_ship_with_the_directory(self):
        """An empty directory teaches nobody how to fill it."""
        readme = os.path.join(
            self._module_dir(), 'static', 'src', 'lib', 'threejs', 'draco',
            'README.md')

        self.assertTrue(os.path.exists(readme))
        with open(readme) as handle:
            text = handle.read()
        self.assertIn('draco_wasm_wrapper.js', text)
        self.assertIn('THREEJS_VERSION', text)
        self.assertIn('r160', text)

    def test_no_cdn_appears_in_the_install_path(self):
        """The whole point is that this works with no public internet."""
        self.assertNotIn('unpkg', self.Assets.draco_decoder_path())


@tagged('post_install', '-at_install')
class TestVisualProductionAssets(HttpCase):
    """M4.5-E — the bundle a real user gets, cold.

    The bundle-composition tests above read the manifest. These build the
    actual compiled bundle and fetch actual pages, because a module can be
    declared correctly and still fail to compile — and the failure mode that
    started all this was invisible to every Python test in the module.
    """

    def _bundle_js(self, bundle='web.assets_backend'):
        compiled = self.env['ir.qweb']._get_asset_bundle(
            bundle, assets_params=self.env['ir.asset']._get_asset_params())
        content = compiled.js().raw
        return content.decode() if isinstance(content, bytes) else content

    def test_the_production_backend_bundle_compiles_and_is_minified(self):
        content = self._bundle_js()

        self.assertTrue(content, "The production JS bundle is empty")
        self.assertLess(
            content.count('\n') / max(len(content), 1), 0.02,
            "The bundle is not minified")

    def test_the_new_visual_modules_survive_minification(self):
        """A module that is bundled but tree-shaken away is still missing."""
        content = self._bundle_js()

        for marker in ('probeCapability', 'disposeViewer', 'chooseExperience'):
            with self.subTest(marker=marker):
                self.assertIn(
                    marker, content,
                    "%s did not survive into the production bundle" % marker)

    def test_the_frontend_bundle_compiles_too(self):
        """The viewer is served to public portal pages as well."""
        content = self._bundle_js('web.assets_frontend')

        self.assertTrue(content)
        for marker in ('probeCapability', 'disposeViewer'):
            with self.subTest(marker=marker):
                self.assertIn(marker, content)

    def test_no_undefined_module_warning_in_the_compiled_bundle(self):
        """The exact failure: an import with nothing defining it.

        Odoo's loader reports 'modules needed by other modules but have not
        been defined' at runtime. Here the equivalent is checked statically:
        every `@real_estate_maquette/...` module the bundle *imports* must
        also be *defined* in it.
        """
        import re
        content = self._bundle_js()

        defined = set(re.findall(
            r'odoo\.define\(\s*["\'](@real_estate_maquette/[^"\']+)["\']',
            content))
        imported = set(re.findall(
            r'require\(\s*["\'](@real_estate_maquette/[^"\']+)["\']', content))
        missing = imported - defined

        self.assertFalse(
            missing,
            "Imported but never defined in the bundle: %s" % sorted(missing))

    def test_a_backend_page_loads_on_a_cold_cache(self):
        self.env['ir.attachment'].sudo().search(
            [('url', '=like', '/web/assets/%')]).unlink()
        self.authenticate('admin', 'admin')

        response = self.url_open('/odoo/settings')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._bundle_js())

    def test_the_debug_assets_bundle_also_compiles(self):
        """Debug mode serves the un-minified bundle; it must build too.

        `AssetsBundle.js()` takes no arguments — it decides minification from
        `is_debug_assets` on the bundle itself, so debug mode is requested by
        constructing the bundle differently rather than by asking `js()` for
        it.
        """
        compiled = self.env['ir.qweb']._get_asset_bundle(
            'web.assets_backend',
            assets_params=self.env['ir.asset']._get_asset_params(),
            debug_assets=True)
        content = compiled.js().raw
        text = content.decode() if isinstance(content, bytes) else content

        self.assertTrue(text)
        self.assertIn('probeCapability', text)
        # Un-minified: the source comments the modules ship with survive.
        self.assertIn('probeCapability', text)
        self.assertGreater(
            text.count('\n') / max(len(text), 1), 0.005,
            "The debug bundle looks minified; debug mode should not be")
