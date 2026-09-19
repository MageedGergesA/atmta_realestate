# -*- coding: utf-8 -*-
"""The 3D maquette explains itself to a user who may not read projects.

Project Plans is shown to every internal user, but only Developer and
Brokerage roles may read development projects. Opening 3D Maquette without
them crashed the screen ("The following error occurred in onWillStart: Odoo
Server Error"); it now says why nothing can be shown, as the 2D plan already
did. Found by a browser crawl of every app (decision 15 Sep 2026: keep the menu
visible and soften the screen).
"""

from odoo.tests.common import HttpCase, new_test_user, tagged

OPEN_3D = """
(async () => {
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const env = odoo.__WOWL_DEBUG__.root.env;
    await env.services.action.doAction('real_estate_maquette.action_maquette_preview_3d');
    for (let i = 0; i < 100 && !document.querySelector('.o_maquette_load_error'); i++) {
        await wait(100);
    }
    const message = document.querySelector('.o_maquette_load_error');
    if (!message || !document.querySelector('.o_maquette_preview')) {
        console.error('3D maquette did not show its access message');
        return;
    }
    console.log('message: ' + message.textContent.trim());
    console.log('test successful');
})();
"""


@tagged('post_install', '-at_install')
class TestPreviewWithoutProjectAccess(HttpCase):

    def test_3d_maquette_without_project_access(self):
        user = new_test_user(self.env, login='maquette_no_projects', groups='base.group_user')
        self.assertFalse(self.env['realestate.project'].with_user(user).has_access('read'))
        self.browser_js('/odoo', OPEN_3D, ready="odoo.isReady", login=user.login, timeout=120)
