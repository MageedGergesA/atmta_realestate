# -*- coding: utf-8 -*-
"""M4.5-B — the fallback decision, run as a real JS suite.

`chooseExperience()` decides whether a customer sees 3D, the 2D plan or the
unit list. It is a pure function of two plain objects so it can be verified
without a GPU, and this runs that verification in the browser's own module
system rather than trusting that the file parses.
"""

from odoo.tests.common import HttpCase, tagged


def _js_error_checker(message):
    ignored = ("Failed to load resource", "favicon", "net::ERR_")
    return not any(token in message for token in ignored)


@tagged('post_install', '-at_install', 'atmta_visual_js')
class TestVisualFallbackUnits(HttpCase):
    """The HOOT suite for this module only."""

    def test_visual_hoot_suite(self):
        self.browser_js(
            # `filter` scopes the run to this module's suites. Without it the
            # whole web/* suite runs and an unrelated core failure would be
            # reported as ours.
            "/web/tests?headless&loglevel=2&preset=desktop&timeout=30000"
            "&filter=real_estate_maquette",
            "",
            "",
            login="admin",
            timeout=600,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=_js_error_checker,
        )
