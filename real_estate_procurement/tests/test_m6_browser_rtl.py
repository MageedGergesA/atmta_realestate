# -*- coding: utf-8 -*-
"""M6 — the evaluation screens in an RTL session and on a tablet viewport.

Two separate questions, and it is worth being precise about which one each
class answers, because the obvious assertion for the first is wrong.

**RTL.** Odoo 18 does not mark the backend with ``html[dir="rtl"]``. Direction
is delivered by serving a *different CSS bundle* — ``…​.rtl.css``, produced by
running the compiled stylesheet through ``rtlcss``. Only report templates use
``t-att-dir``. A test that asserted ``dir="rtl"`` on the document would fail on
a perfectly correct Odoo, which is how three unrelated failures in this
worktree were misread before the mechanism was traced.

So the RTL gate asserts the mechanism that actually exists: an Arabic user is
served the ``.rtl`` bundle, and the evaluation screens then run a full tour
without an error dialog or a broken value.

There is a limit here and it is stated rather than papered over. ``rtlcss`` is
a Node tool, and where it is absent Odoo logs a warning and serves the
unflipped stylesheet under the ``.rtl`` name. On such a host this gate proves
the RTL code path is selected and the screens function; it does **not** prove
the pixels are mirrored. `test_the_rtl_bundle_is_actually_flipped` is the check
that closes that gap, and it fails loudly — rather than skipping quietly — on
any host where the tooling is installed but the flip did not happen.

**Tablet.** ``HttpCase`` drives Chrome at ``browser_size`` with optional touch
emulation. The evaluation forms are notebook-heavy, and a notebook that
collapses its tabs off-screen at 768px would make the commercial figures
unreachable on the device a site engineer actually carries.
"""

import logging

from odoo.tests.common import tagged
from odoo.tools.misc import find_in_path

from .test_m6_browser import M6BrowserCommon

_logger = logging.getLogger(__name__)


def _rtlcss_available():
    # `find_in_path` uses Odoo's own `which` (odoo/tools/which.py), not
    # `shutil.which`: it raises IOError when the binary is absent rather than
    # answering None.
    try:
        return bool(find_in_path('rtlcss'))
    except IOError:
        return False


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6EvaluationRtl(M6BrowserCommon):

    def _arabic_admin(self):
        self.env['res.lang']._activate_lang('ar_001')
        admin = self._browser_admin()
        admin.lang = 'ar_001'
        self.env.flush_all()
        return admin

    def test_arabic_is_a_right_to_left_language_in_this_database(self):
        """The premise of every other assertion in this file."""
        self.env['res.lang']._activate_lang('ar_001')
        lang = self.env['res.lang']._lang_get('ar_001')

        self.assertTrue(lang, "ar_001 did not activate.")
        self.assertEqual(lang.direction, 'rtl')

    def test_an_arabic_user_is_served_the_rtl_asset_bundle(self):
        self._arabic_admin()
        self.authenticate('admin', 'admin')

        page = self.url_open('/odoo').text

        self.assertIn('.rtl.', page,
                      "An Arabic session was served the LTR stylesheet. "
                      "Direction in the Odoo 18 backend is the bundle, not a "
                      "dir attribute — so this is the whole mechanism.")

    def test_the_rtl_bundle_is_actually_flipped(self):
        """Content, not naming — and honest about the host it runs on.

        The name `…rtl.css` is applied whether or not `rtlcss` ran, so the URL
        assertion above cannot see a failed flip. This one compares the served
        bytes. Where the tool is missing the mirroring genuinely cannot happen
        and saying otherwise would be a lie, so the shortfall is recorded in
        the log and the gate reports what it did check.
        """
        self._arabic_admin()
        self.authenticate('admin', 'admin')
        page = self.url_open('/odoo').text

        rtl_urls = [part.split('"')[0] for part in page.split('href="')[1:]
                    if '.rtl.' in part.split('"')[0]]
        self.assertTrue(rtl_urls, "No .rtl stylesheet was linked at all.")
        rtl_css = self.url_open(rtl_urls[0]).text
        ltr_css = self.url_open(rtl_urls[0].replace('.rtl.', '.')).text

        if not _rtlcss_available():
            _logger.warning(
                "M6 RTL gate: `rtlcss` is not installed on this host, so Odoo "
                "served the unflipped stylesheet under the .rtl name. Verified "
                "here: the RTL bundle is selected and the evaluation screens "
                "run. NOT verified here: visual mirroring. Install rtlcss "
                "(`npm install -g rtlcss`) to make this assertion meaningful.")
            self.assertTrue(rtl_css, "The RTL bundle served nothing at all.")
            return

        self.assertNotEqual(
            rtl_css, ltr_css,
            "`rtlcss` is installed, yet the .rtl bundle is byte-identical to "
            "the LTR one. The flip did not run.")

    def test_the_evaluation_screens_work_in_an_rtl_session(self):
        round_ = self._finalised_round()
        self._arabic_admin()

        self.start_tour('/odoo', 'procurement_evaluation_tour', login='admin',
                        timeout=300)

        self.assertEqual(round_.state, 'finalised')


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m6')
class TestM6EvaluationTablet(M6BrowserCommon):
    """The same tour at tablet width, with touch emulation on.

    Nothing is relaxed for the smaller viewport: the tour still has to reach
    the Commercial notebook page and read the evaluated cost. If the notebook
    becomes unreachable at this width the tour cannot finish, which is the
    failure worth catching.
    """

    browser_size = '768x1024'
    touch_enabled = True

    def test_the_evaluation_screens_work_on_a_tablet(self):
        round_ = self._finalised_round()
        self._browser_admin()

        self.start_tour('/odoo', 'procurement_evaluation_tour', login='admin',
                        timeout=300)

        self.assertEqual(round_.state, 'finalised')
        self.assertTrue(round_.candidate_ids.filtered('rank'))
