# -*- coding: utf-8 -*-
"""The portal's HOOT suite, run as a real JS suite in a real browser."""

from odoo.tests.common import HttpCase, tagged


def _js_error_checker(message):
    ignored = ("Failed to load resource", "favicon", "net::ERR_")
    return not any(token in message for token in ignored)


@tagged('post_install', '-at_install', 'atmta_portal_js')
class TestPortalJs(HttpCase):

    def test_portal_hoot_suite(self):
        self.browser_js(
            # `filter` scopes the run to this module. Without it the whole
            # web/* suite runs and an unrelated core failure is reported here.
            "/web/tests?headless&loglevel=2&preset=desktop&timeout=30000"
            "&filter=real_estate_portal",
            "",
            "",
            login="admin",
            timeout=600,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=_js_error_checker,
        )
