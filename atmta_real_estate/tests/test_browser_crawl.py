"""Every Rental screen in the web client, for every role, without an error.

Server-side sweeps cannot see what only the browser runs: field widgets, view
modifiers evaluated in JavaScript (which fail when they name a field the view
does not load), kanban cards, pivot, graph and calendar rendering, statinfo
buttons and form tabs. For each role this opens every Rental menu screen it
can see, switches to each of the screen's views, opens the first record and
clicks through its tabs. Any client error, failed RPC or console error fails
the test.
"""

import json

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests.common import HttpCase, new_test_user, tagged

from .common import shown_menu_ids

CRAWL_JS = """
(async () => {
    const actions = %(actions)s;
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const settle = async () => {
        await wait(150);
        for (let i = 0; i < 60 && document.querySelector('.o_loading_indicator, .o-overlay-container .o_loading'); i++) {
            await wait(100);
        }
        await wait(250);
    };
    const env = odoo.__WOWL_DEBUG__.root.env;
    const visited = [];
    for (const [label, actionId] of actions) {
        await env.services.action.doAction(actionId, { clearBreadcrumbs: true });
        await settle();
        const switches = [...document.querySelectorAll('.o_control_panel .o_switch_view')]
            .map((button) => [...button.classList].find((c) => c.startsWith('o_') && c !== 'o_switch_view'));
        for (const cls of switches) {
            const button = document.querySelector(`.o_control_panel .o_switch_view.${cls}`);
            if (button) {
                button.click();
                await settle();
            }
        }
        const listSwitch = document.querySelector('.o_control_panel .o_switch_view.o_list');
        if (listSwitch) {
            listSwitch.click();
            await settle();
        }
        const cell = document.querySelector('.o_list_view .o_data_row .o_data_cell');
        const card = document.querySelector('.o_kanban_view .o_kanban_record:not(.o_kanban_ghost)');
        if (cell || card) {
            (cell || card).click();
            await settle();
            for (const tab of [...document.querySelectorAll('.o_form_view .o_notebook .nav-link')]) {
                tab.click();
                await settle();
            }
        }
        visited.push(label);
    }
    console.log('crawled ' + visited.length + ' screens');
    console.log('test successful');
})();
"""


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestBrowserCrawl(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        company = env.company
        today = fields.Date.context_today(env['res.partner'])
        Account = env['account.account']
        deposit_account = Account.create({
            'name': 'Crawl Tenant Deposits', 'code': 'CRDEP01', 'account_type': 'liability_current',
            'reconcile': True, 'company_ids': [Command.link(company.id)],
        })
        forfeit_account = Account.create({
            'name': 'Crawl Forfeited Deposits', 'code': 'CRFRF01', 'account_type': 'income_other',
            'company_ids': [Command.link(company.id)],
        })
        journal = env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1) \
            or env['account.journal'].create({
                'name': 'Crawl Bank', 'type': 'bank', 'code': 'CRBK', 'company_id': company.id})
        company.write({
            're_deposit_account_id': deposit_account.id,
            're_deposit_journal_id': journal.id,
            're_deposit_forfeit_income_account_id': forfeit_account.id,
        })

        tenant = env['res.partner'].create({'name': 'Crawl Tenant', 'email': 'crawl@example.com'})
        Property = env['realestate.property']
        units = Property.create([{
            'name': 'Crawl Unit %s' % n, 'property_code': 'CRW-U-%03d' % n,
            'hierarchy_level': 'unit', 'usage_category': 'apartment', 'area_sqm': 70.0 + n,
            'company_id': company.id,
        } for n in (1, 2, 3)])
        start = today.replace(day=1) - relativedelta(months=4)
        lease = env['realestate.contract'].create({
            'partner_id': tenant.id, 'property_id': units[0].id,
            'is_single_property': True, 'is_multi_property': False,
            'start_date': start, 'end_date': start + relativedelta(years=1, days=-1),
            'price': 1000.0, 'company_id': company.id, 'currency_id': company.currency_id.id,
        })
        lease.action_to_proposal()
        lease.action_submit_for_approval()
        lease.action_approve_lease()
        lease.action_mark_signed()
        lease.action_activate_lease()
        lease.action_generate_billing_schedule()
        lease.action_invoice_due_obligations()
        env['realestate.rent.escalation.rule'].create({
            'contract_id': lease.id, 'effective_date': start + relativedelta(months=6),
            'escalation_type': 'percentage', 'percentage': 5.0,
        })
        deposit = env['realestate.contract.deposit'].create({
            'contract_id': lease.id, 'partner_id': tenant.id, 'requested_amount': 2000.0,
        })
        deposit.action_request()
        deposit.action_register_receipt()
        env['realestate.move.in'].create({
            'contract_id': lease.id, 'property_id': units[0].id,
            'scheduled_date': start, 'tenant_acknowledged': True,
        }).action_complete()
        env['realestate.contract.renewal'].create({
            'contract_id': lease.id,
            'proposed_start_date': lease.end_date + relativedelta(days=1),
            'proposed_end_date': lease.end_date + relativedelta(years=1),
            'proposed_rent': 1050.0,
        })
        env['realestate.contract.amendment'].create({
            'contract_id': lease.id, 'amendment_type': 'rent_change',
            'effective_date': today + relativedelta(months=1), 'new_rent': 1100.0,
            'reason': 'Crawl review',
        })
        meter = env['realestate.property.meter'].create({
            'name': 'CRW-EL-001', 'property_id': units[0].id,
        })
        env['realestate.property.meter.reading'].create({
            'meter_id': meter.id, 'reading_date': today, 'current_reading': 42.0,
        })
        env['realestate.maintenance.request'].create({
            'name': 'Crawl leak', 'property_id': units[0].id,
        })
        other = env['realestate.contract'].create({
            'partner_id': tenant.id, 'property_id': units[1].id,
            'is_single_property': True, 'is_multi_property': False,
            'start_date': today - relativedelta(months=6), 'end_date': today + relativedelta(months=6),
            'price': 900.0, 'company_id': company.id, 'currency_id': company.currency_id.id,
        })
        other.action_to_proposal()
        other.action_submit_for_approval()
        other.action_approve_lease()
        other.action_mark_signed()
        other.action_activate_lease()
        termination = env['realestate.contract.termination'].create({
            'contract_id': other.id,
            'requested_end_date': today + relativedelta(months=2),
            'effective_date': today + relativedelta(months=2),
            'reason': 'tenant_notice',
        })
        termination.action_give_notice()
        env['realestate.move.out'].create({
            'contract_id': other.id, 'property_id': units[1].id, 'scheduled_date': today,
        })

    def _crawl(self, login, *groups):
        user = new_test_user(self.env, login=login, company_id=self.env.company.id,
                             groups=','.join(('base.group_user',) + groups))
        root = self.env.ref('atmta_real_estate.real_estate_menu_root')
        menus = self.env['ir.ui.menu'].with_context(**{'ir.ui.menu.full_list': True}).search(
            [('id', 'child_of', root.id)])
        visible = shown_menu_ids(self.env, user)
        actions = [(menu.complete_name, menu.action.id) for menu in menus
                   if menu.id in visible and menu.action
                   and menu.action._name in ('ir.actions.act_window', 'ir.actions.client')]
        self.assertTrue(actions)
        self.browser_js('/odoo', CRAWL_JS % {'actions': json.dumps(actions)},
                        ready="odoo.isReady", login=login, timeout=900)

    def test_leasing_agent_screens(self):
        self._crawl('crawl_agent', 'atmta_real_estate.group_rental_agent',
                    'atmta_real_estate.group_rental_all_portfolios')

    def test_property_manager_screens(self):
        self._crawl('crawl_pm', 'atmta_real_estate.group_property_manager')

    def test_rental_manager_with_invoicing_screens(self):
        self._crawl('crawl_manager', 'atmta_real_estate.group_rental_manager',
                    'account.group_account_invoice')
