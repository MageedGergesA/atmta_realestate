"""Shared fixtures for the leasing test suite.

Everything inherits ``LeaseCase``, which builds a small but complete estate:
a company with leasing configuration, a compound containing a building with two
units, a tenant, and helpers for creating leases and driving them through their
lifecycle.

Uses ``TransactionCase`` (rolled back per test) rather than ``SavepointCase`` so
each test starts from an identical, isolated world -- these tests write invoices
and payments, and cross-test contamination there is very hard to debug.
"""

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'atmta_leasing')
class LeaseCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The clock the *production* code uses.
        #
        # `fields.Date.today()` is UTC; every compute in this module dates
        # things with `fields.Date.context_today()`, which is the user's
        # timezone. For users ahead of UTC the two return different dates
        # between local midnight and UTC midnight, and a fixture built on one
        # clock then fails an assertion made against the other — `vacant_days`
        # came out 11 instead of 10 for two hours out of every twenty-four.
        # Aligning the fixture with the code under test removes the difference
        # rather than papering over it.
        cls.today = fields.Date.context_today(cls.env['res.partner'])
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id

        cls._configure_company()
        cls._build_estate()
        cls._build_partners()

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    @classmethod
    def _configure_company(cls):
        """Give the company the accounts a deposit needs.

        Created rather than looked up: the test database may or may not have a
        chart of accounts installed, and a test that silently skips because a
        fixture was missing is worse than no test.
        """
        Account = cls.env['account.account']
        cls.deposit_account = Account.create({
            'name': 'Tenant Security Deposits',
            'code': 'REDEP01',
            'account_type': 'liability_current',
            'reconcile': True,
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.forfeit_income_account = Account.create({
            'name': 'Forfeited Deposits',
            'code': 'REFRF01',
            'account_type': 'income_other',
            'company_ids': [Command.link(cls.company.id)],
        })
        cls.bank_journal = cls.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.bank_journal:
            cls.bank_journal = cls.env['account.journal'].create({
                'name': 'Test Bank',
                'type': 'bank',
                'code': 'TBNK',
                'company_id': cls.company.id,
            })
        cls.company.write({
            're_deposit_account_id': cls.deposit_account.id,
            're_deposit_journal_id': cls.bank_journal.id,
            're_deposit_forfeit_income_account_id': cls.forfeit_income_account.id,
            're_proration_method': 'actual',
            're_notice_period_days': 60,
            # The test user creates and approves its own leases. Self-approval
            # is exercised properly in test_security; switching it on here
            # keeps every other test focused on what it is actually testing.
            're_allow_self_approval': True,
        })
        # The workflow methods gate on the leasing groups, and a fresh admin
        # does not carry them. Grant them so the workflow tests exercise the
        # workflow rather than the ACL.
        cls.env.user.groups_id |= (
            cls.env.ref('atmta_real_estate.group_rental_manager')
            | cls.env.ref('atmta_real_estate.group_property_manager')
            | cls.env.ref('atmta_real_estate.group_rental_agent')
        )

    @classmethod
    def _build_estate(cls):
        Property = cls.env['realestate.property']
        cls.compound = Property.create({
            'name': 'Test Compound',
            'property_code': 'TST-CMP',
            'hierarchy_level': 'compound',
            'company_id': cls.company.id,
        })
        cls.building = Property.create({
            'name': 'Test Building A',
            'property_code': 'TST-BLD-A',
            'hierarchy_level': 'building',
            'parent_id': cls.compound.id,
            'company_id': cls.company.id,
        })
        cls.unit_a = Property.create({
            'name': 'Unit A-101',
            'property_code': 'TST-A-101',
            'hierarchy_level': 'unit',
            'parent_id': cls.building.id,
            'usage_category': 'apartment',
            'area_sqm': 100.0,
            'company_id': cls.company.id,
        })
        cls.unit_b = Property.create({
            'name': 'Unit A-102',
            'property_code': 'TST-A-102',
            'hierarchy_level': 'unit',
            'parent_id': cls.building.id,
            'usage_category': 'apartment',
            'area_sqm': 80.0,
            'company_id': cls.company.id,
        })
        cls.parking = Property.create({
            'name': 'Parking P-01',
            'property_code': 'TST-P-01',
            'hierarchy_level': 'unit',
            'parent_id': cls.building.id,
            'usage_category': 'parking',
            'area_sqm': 12.5,
            'company_id': cls.company.id,
        })

    @classmethod
    def _build_partners(cls):
        Partner = cls.env['res.partner']
        cls.tenant = Partner.create({'name': 'Test Tenant'})
        cls.co_tenant = Partner.create({'name': 'Test Co-Tenant'})
        cls.guarantor = Partner.create({'name': 'Test Guarantor'})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def make_lease(self, prop=None, start=None, end=None, rent=1000.0, **kwargs):
        """Create a single-property lease with sensible defaults."""
        prop = prop or self.unit_a
        start = start or self.today
        end = end if end is not None else start + relativedelta(years=1) - relativedelta(days=1)
        values = {
            'partner_id': kwargs.pop('partner', self.tenant).id,
            'property_id': prop.id,
            'is_single_property': True,
            'is_multi_property': False,
            'start_date': start,
            'end_date': end,
            'price': rent,
            'company_id': self.company.id,
            'currency_id': self.currency.id,
        }
        values.update(kwargs)
        return self.env['realestate.contract'].create(values)

    def activate(self, lease):
        """Drive a lease to ACTIVE through the real workflow.

        Deliberately goes through the transition methods rather than writing
        ``lifecycle_state`` -- a test that bypasses the workflow proves nothing
        about the workflow.
        """
        lease.action_to_proposal()
        lease.action_submit_for_approval()
        lease.action_approve_lease()
        lease.action_mark_signed()
        lease.action_activate_lease()
        return lease

    def allocations_of(self, lease):
        return self.env['realestate.contract.property.line'].search(
            [('contract_id', '=', lease.id)])


def shown_menu_ids(env, user):
    """Ids of the menus ``user`` actually gets in the web client.

    ``ir.ui.menu._visible_menu_ids`` keeps a menu with an action even when its
    parent section is hidden; the client builds the tree from the root down,
    so such a menu is never on screen. Only menus whose every ancestor is
    visible count.
    """
    raw = env['ir.ui.menu'].with_user(user)._visible_menu_ids()
    ids = set()
    for menu in env['ir.ui.menu'].browse(raw):
        node = menu
        while node and node.id in raw:
            node = node.parent_id
        if not node:
            ids.add(menu.id)
    return ids
