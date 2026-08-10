# -*- coding: utf-8 -*-
"""M10 — run this module's HOOT tests, and only this module's.

The web module's full unit suite is not a Construction gate: it contains
thousands of core tests, and one of them
(`@web/core/resizable_panel/handles resize handle at start in fixed panel`)
fails in this environment for reasons that have nothing to do with this
module. Running the whole suite to prove six of our own assertions would make
a Construction release depend on a core layout test.

So this runs the HOOT suite filtered to our own tests, in a real browser, on
the same runner. It is a Construction gate because it tests Construction code.
"""

from odoo.addons.web.tests.test_js import unit_test_error_checker
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install', 'atmta_construction')
class TestConstructionHoot(HttpCase):

    def test_the_control_tower_unit_tests_pass(self):
        self.browser_js(
            '/web/tests?headless&loglevel=2&preset=desktop&timeout=15000'
            '&filter=Control%20Tower%20formatting',
            "", "", login='admin', timeout=600,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=unit_test_error_checker)
