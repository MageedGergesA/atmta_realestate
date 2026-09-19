# -*- coding: utf-8 -*-
"""Every Developer app screen's form opens on a new record, for its roles.

Opening a form on a new record runs the default-values onchange and computes
every field on the view for an unsaved record -- where the price book's premium
compute crashed on unsaved rules. Doing it as a Sales Agent also runs those
computes under an agent's access rights.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import Form
from odoo.tests.common import new_test_user, tagged
from odoo.tools.safe_eval import datetime, safe_eval

from .common import DeveloperCommon, shown_menu_ids

#: Screens whose form is not a record form a user fills in.
NOT_A_RECORD_FORM = {'res.config.settings'}


@tagged('post_install', '-at_install', 'atmta_developer')
class TestEveryDeveloperFormOpens(DeveloperCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dev_manager = new_test_user(
            cls.env, login='dev_forms_manager', company_id=cls.company.id,
            groups='base.group_user,real_estate_developer.group_dev_manager')
        cls.dev_agent = new_test_user(
            cls.env, login='dev_forms_agent', company_id=cls.company.id,
            groups='base.group_user,real_estate_developer.group_dev_agent')

    def _screens(self, user):
        root = self.env.ref('real_estate_developer.menu_developer_root')
        menus = self.env['ir.ui.menu'].with_context(**{'ir.ui.menu.full_list': True}).search(
            [('id', 'child_of', root.id)])
        visible = shown_menu_ids(self.env, user)
        for menu in menus:
            action = menu.action
            if (menu.id in visible and action and action._name == 'ir.actions.act_window'
                    and 'form' in (action.view_mode or '')
                    and action.res_model not in NOT_A_RECORD_FORM):
                yield menu, action

    def _defaults(self, action, user):
        try:
            context = safe_eval(action.context or '{}', {
                'uid': user.id, 'active_id': False, 'active_ids': [],
                'context_today': lambda: fields.Date.context_today(self.env['res.partner']),
                'relativedelta': relativedelta, 'datetime': datetime,
            })
        except Exception:  # noqa: BLE001 - a context we cannot evaluate adds no defaults
            context = {}
        return {k: v for k, v in context.items() if k.startswith('default_')}

    def _open_all(self, user):
        opened, failures = set(), []
        for menu, action in self._screens(user):
            if action.res_model in opened:
                continue
            opened.add(action.res_model)
            model = self.env[action.res_model].with_user(user).with_context(
                **self._defaults(action, user))
            view = action.view_id if action.view_id and action.view_id.type == 'form' else None
            try:
                Form(model, view=view)
            except Exception as exc:  # noqa: BLE001 - collect every screen, then report
                failures.append('%s (%s): %s: %s' % (
                    menu.complete_name, action.res_model, type(exc).__name__,
                    str(exc).splitlines()[0] if str(exc) else ''))
        return opened, failures

    def test_a_developer_manager_opens_every_form(self):
        opened, failures = self._open_all(self.dev_manager)
        self.assertGreater(len(opened), 5, opened)
        self.assertFalse(failures, '\n'.join(failures))

    def test_a_sales_agent_opens_the_forms_of_their_screens(self):
        opened, failures = self._open_all(self.dev_agent)
        self.assertTrue(opened)
        self.assertFalse(failures, '\n'.join(failures))
