"""Every Rental screen's form opens on a new record, for the roles that use it.

Opening a form on a new record runs the model's default-values onchange and
computes every field on the view for an unsaved record -- the path on which the
rent escalation and price book computes crashed. Doing it as a Leasing Agent
also runs those computes under an agent's access rights, so a computed field
that reads a model the agent may not read fails here rather than in the
browser.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import Form
from odoo.tests.common import new_test_user, tagged
from odoo.tools.safe_eval import datetime, safe_eval

from .common import LeaseCase, shown_menu_ids

#: Screens whose form is not a record form a user fills in.
NOT_A_RECORD_FORM = {'res.config.settings'}


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestEveryRentalFormOpens(LeaseCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = new_test_user(
            cls.env, login='forms_open_agent', company_id=cls.company.id,
            groups='base.group_user,atmta_real_estate.group_rental_agent')

    def _window_actions(self, user=None):
        """(menu, action) for every Rental menu that opens a form-capable screen."""
        root = self.env.ref('atmta_real_estate.real_estate_menu_root')
        menus = self.env['ir.ui.menu'].with_context(**{'ir.ui.menu.full_list': True}).search(
            [('id', 'child_of', root.id)])
        visible = shown_menu_ids(self.env, user) if user else None
        found = []
        for menu in menus:
            action = menu.action
            if (not action or action._name != 'ir.actions.act_window'
                    or 'form' not in (action.view_mode or '')
                    or action.res_model in NOT_A_RECORD_FORM):
                continue
            if visible is not None and menu.id not in visible:
                continue
            found.append((menu, action))
        return found

    def _defaults(self, action, user):
        """The ``default_*`` keys of the action's context, as the client passes them."""
        try:
            context = safe_eval(action.context or '{}', {
                'uid': user.id, 'active_id': False, 'active_ids': [],
                'context_today': lambda: fields.Date.context_today(self.env['res.partner']),
                'relativedelta': relativedelta, 'datetime': datetime,
            })
        except Exception:  # noqa: BLE001 - a context we cannot evaluate adds no defaults
            context = {}
        return {key: value for key, value in context.items() if key.startswith('default_')}

    def _open_all(self, user):
        failures = []
        opened = set()
        for menu, action in self._window_actions(user if user != self.env.user else None):
            model = action.res_model
            if model in opened:
                continue
            opened.add(model)
            env_model = self.env[model].with_user(user).with_context(**self._defaults(action, user))
            view = action.view_id if action.view_id and action.view_id.type == 'form' else None
            try:
                Form(env_model, view=view)
            except Exception as exc:  # noqa: BLE001 - collect every screen, then report
                failures.append('%s (%s): %s: %s' % (
                    menu.complete_name, model, type(exc).__name__, str(exc).splitlines()[0]))
        return opened, failures

    def test_every_screen_opens_a_blank_form(self):
        opened, failures = self._open_all(self.env.user)
        self.assertGreater(len(opened), 10, opened)
        self.assertFalse(failures, '\n'.join(failures))

    def test_a_leasing_agent_can_open_the_forms_of_their_screens(self):
        opened, failures = self._open_all(self.agent)
        self.assertIn('realestate.contract', opened)
        self.assertFalse(failures, '\n'.join(failures))

    def test_an_existing_lease_opens_for_an_agent(self):
        lease = self.make_lease(user_id=self.agent.id)
        form = Form(lease.with_user(self.agent),
                    view='atmta_real_estate.view_realestate_contract_form')
        self.assertEqual(form.partner_id, self.tenant)
