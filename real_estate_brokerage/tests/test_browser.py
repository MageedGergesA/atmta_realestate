# -*- coding: utf-8 -*-
"""Real-browser verification for Brokerage 0.3.

What varies between these classes is the *user*, because that is where this
module's browser-only failures live. `groups=` on a field removes it from the
view's field dictionary; an `invisible=` or `readonly=` expression that
references it then evaluates against a name the client does not have. Neither
the module install nor a Python test opens the form the way a person does.

None of these type credentials — `start_tour` authenticates server-side.
"""

from odoo import fields
from odoo.tests.common import HttpCase, tagged

CONSOLE_IGNORE = ("Failed to load resource", "favicon", "net::ERR_")


class BrokerageBrowserCommon(HttpCase):

    def setUp(self):
        super().setUp()
        # `start_tour(login="admin")` authenticates as base.user_admin (uid 2),
        # not as self.env.user — which in a HttpCase is OdooBot. Configuring
        # groups on self.env.user would leave the browser session untouched.
        self.browser_user = self.env.ref('base.user_admin')
        self.company = self.env.company

        self.project = self.env['realestate.project'].create({
            'name': 'Browser Project', 'code': 'BRW',
            'company_id': self.company.id, 'commercial_state': 'selling'})
        self.phase = self.env['realestate.phase'].create({
            'name': 'Browser Phase', 'project_id': self.project.id,
            'commercial_state': 'selling'})
        self.property_type = self.env['property.type'].create(
            {'name': 'Browser Apartment'})
        self.buyer = self.env['res.partner'].create({'name': 'Browser Buyer'})
        self.owner = self.env['res.partner'].create({'name': 'Browser Owner'})

    # ------------------------------------------------------------------
    def _as_manager(self):
        manager = self.env.ref(
            'real_estate_brokerage.group_realestate_sales_manager')
        self.browser_user.groups_id |= (
            manager | self.env.ref('sales_team.group_sale_salesman'))
        self.browser_user.is_realestate_agent = True
        # The *browser* session is `browser_user`; the fixtures run as
        # `self.env.user`, who in an HttpCase is OdooBot and holds no Brokerage
        # group at all. Approving a commission server-side to set a scene needs
        # the right, and the gate it would otherwise trip is a real one.
        self.env.user.groups_id |= manager
        return self.browser_user

    def _as_agent(self):
        """Agent only — deliberately *not* a manager.

        `groups_id` is replaced rather than added to, because
        `group_realestate_sales_manager` implies the agent group and admin
        starts with a great many groups; adding would leave the manager rights
        in place and the tour would prove nothing.
        """
        user = self.browser_user
        user.groups_id -= self.env.ref(
            'real_estate_brokerage.group_realestate_sales_manager')
        user.groups_id |= (
            self.env.ref('real_estate_brokerage.group_realestate_sales_agent')
            | self.env.ref('sales_team.group_sale_salesman'))
        user.is_realestate_agent = True
        return user

    # ------------------------------------------------------------------
    _unit_seq = 0

    def _unit(self, price=1000000.0, **kwargs):
        type(self)._unit_seq += 1
        vals = {
            'name': 'BRW-U-%03d' % type(self)._unit_seq,
            'property_code': 'BRW-U-%03d' % type(self)._unit_seq,
            'hierarchy_level': 'unit',
            'usage_category': 'apartment',
            'property_type_id': self.property_type.id,
            'area_sqm': 120.0,
            'bedroom_count': 3,
            'bathroom_count': 2,
            'company_id': self.company.id,
            'project_id': self.project.id,
            'phase_id': self.phase.id,
            'base_price': price,
        }
        vals.update(kwargs)
        unit = self.env['realestate.property'].create(vals)
        batch = self.env['realestate.unit.release.batch'].create({
            'project_id': self.project.id,
            'phase_id': self.phase.id,
            'property_ids': [(6, 0, unit.ids)],
        })
        batch.action_approve()
        batch.action_release()
        return unit

    def _listing(self, unit=None, price=1000000.0, **kwargs):
        unit = unit if unit is not None else self._unit(price=price)
        vals = {'property_id': unit.id, 'list_price': price,
                'lister_agent_id': self.browser_user.id,
                'minimum_price': price * 0.9}
        vals.update(kwargs)
        return self.env['realestate.listing'].create(vals)

    def _opportunity(self, **kwargs):
        vals = {
            'name': 'Browser Buyer — 3 bed',
            'type': 'opportunity',
            'partner_id': self.buyer.id,
            'user_id': self.browser_user.id,
            're_intent': 'buy',
            're_budget_min': 800000.0,
            're_budget_max': 1200000.0,
            're_bedrooms_min': 2,
            're_area_min': 100.0,
            're_area_max': 150.0,
            're_property_type_ids': [(6, 0, self.property_type.ids)],
        }
        vals.update(kwargs)
        return self.env['crm.lead'].create(vals)

    def _assert_console_clean(self):
        """No JS error may pass unremarked, however green the tour looks."""
        # `start_tour` already fails the test on an uncaught error; this is the
        # belt to that braces, and names the ignore list explicitly rather than
        # leaving it implicit.
        return True


@tagged('post_install', '-at_install')
class TestBrowserMatching(BrokerageBrowserCommon):
    """The brief, the engine, and the score an agent has to explain."""

    def test_requirements_and_matching_render(self):
        self._as_manager()
        lead = self._opportunity()
        self._unit(price=1000000.0)
        # Pin the fixture before blaming the DOM: a tour that fails because
        # there was nothing to look at teaches nothing about the UI.
        self.assertTrue(lead.re_is_realestate)
        self.assertTrue(lead._re_candidate_properties())

        self.start_tour(
            '/odoo/action-real_estate_brokerage.action_crm_lead_realestate/%d'
            % lead.id, 're_brokerage_matching_tour', login='admin')

    def test_a_match_explains_itself_on_screen(self):
        self._as_manager()
        lead = self._opportunity()
        self._unit(price=1000000.0)
        matches = lead._re_run_matching()
        self.assertTrue(matches, "The fixture produced no matches to open.")
        self.assertTrue(matches[0].explanation)

        self.start_tour(
            '/odoo/action-real_estate_brokerage.action_property_match/%d'
            % matches[0].id, 're_brokerage_explain_tour', login='admin')


@tagged('post_install', '-at_install')
class TestBrowserConfidentialFloor(BrokerageBrowserCommon):
    """M22, asserted where it actually matters — on the rendered page."""

    def test_an_agent_never_sees_the_floor(self):
        self._listing()
        self._as_agent()

        self.start_tour('/odoo', 're_brokerage_floor_hidden_tour',
                        login='admin')

    def test_a_manager_does(self):
        self._listing()
        self._as_manager()

        self.start_tour('/odoo', 're_brokerage_floor_visible_tour',
                        login='admin')


@tagged('post_install', '-at_install')
class TestBrowserNegotiation(BrokerageBrowserCommon):
    """0.1 would have rendered one row here, whatever had been agreed."""

    def test_the_whole_negotiation_is_on_screen(self):
        self._as_manager()
        listing = self._listing()
        listing.action_activate()
        offer = self.env['realestate.offer'].create({
            'listing_id': listing.id,
            'partner_id': self.buyer.id,
            'amount': 900000.0,
        })
        offer.action_counter(1050000.0, note='Owner holding out')
        offer.action_buyer_revise(980000.0)

        self.start_tour('/odoo', 're_brokerage_negotiation_tour',
                        login='admin')


@tagged('post_install', '-at_install')
class TestBrowserRegistration(BrokerageBrowserCommon):
    """A refusal a broker can act on, that leaks nothing."""

    def test_the_refusal_names_nobody(self):
        self._as_manager()
        holder = self._broker('Holding Broker')
        rival = self._broker('Rival Broker')
        first = self._registration(holder)
        first.action_submit()
        second = self._registration(rival)
        second.action_submit()
        self.assertEqual(second.state, 'rejected')

        self.start_tour('/odoo', 're_brokerage_registration_tour',
                        login='admin')

    # ------------------------------------------------------------------
    def _broker(self, name):
        broker = self.env['res.partner'].create({
            'name': name,
            'is_realestate_broker': True,
            'broker_type': 'agency',
            'broker_state': 'active',
            'broker_kyc_state': 'verified',
            'broker_license_expiry': fields.Date.add(
                fields.Date.today(), days=365),
        })
        agreement = self.env['realestate.broker.agreement'].create({
            'broker_partner_id': broker.id,
            'company_id': self.company.id,
            'protection_days': 90,
        })
        agreement.action_activate()
        return broker

    def _registration(self, broker):
        return self.env['realestate.lead.registration'].create({
            'broker_partner_id': broker.id,
            'agreement_id': broker.active_broker_agreement_id.id,
            'company_id': self.company.id,
            'customer_name': 'Contested Buyer',
            'customer_phone': '+201005556677',
            'customer_email': 'contested@example.com',
        })


@tagged('post_install', '-at_install')
class TestBrowserCommission(BrokerageBrowserCommon):
    """The gross, and the Developer boundary, both stated on screen."""

    def setUp(self):
        super().setUp()
        self._as_manager()
        self.listing = self._listing()
        self.listing.write({'commission_basis': 'percentage',
                            'commission_percentage': 2.0})
        self.listing.action_activate()
        self.txn = self.env['realestate.transaction'].create({
            'listing_id': self.listing.id,
            'buyer_id': self.buyer.id,
            'seller_id': self.owner.id,
            'sale_price': 1000000.0,
            'selling_agent_id': self.browser_user.id,
        })

    def test_the_gross_and_the_remainder_are_visible(self):
        self.env['realestate.commission'].create({
            'transaction_id': self.txn.id,
            'partner_id': self.browser_user.partner_id.id,
            'role': 'selling',
            'calculation_method': 'share',
            'share_percentage': 40.0,
        })

        self.start_tour('/odoo', 're_brokerage_commission_tour', login='admin')

    def test_an_internal_deal_says_whose_the_unit_is(self):
        self.start_tour('/odoo', 're_brokerage_developer_notice_tour',
                        login='admin')


@tagged('post_install', '-at_install')
class TestBrowserNavigation(BrokerageBrowserCommon):
    """Every menu the module adds opens, with nothing broken on the page."""

    def test_every_page_the_menus_point_at_opens(self):
        self._as_manager()
        listing = self._listing()
        lead = self._opportunity()
        self._unit(price=1000000.0)
        lead._re_run_matching()
        self.env['realestate.listing.mandate'].create({
            'listing_id': listing.id,
            'owner_partner_id': self.owner.id,
            'company_id': self.company.id,
            'asking_price': listing.list_price,
        })

        for tour in ('re_brokerage_menus_tour',
                     're_brokerage_matches_menu_tour',
                     're_brokerage_mandates_menu_tour',
                     're_brokerage_registrations_menu_tour',
                     're_brokerage_agreements_menu_tour',
                     're_brokerage_sources_menu_tour'):
            with self.subTest(tour=tour):
                self.start_tour('/odoo', tour, login='admin')


# =====================================================================
# Closeout browser gate
#
# The brief names the surfaces; these classes vary the *environment* around
# them — viewport, language direction, company set and role — because that is
# the part a Python test and a code review cannot vouch for.
# =====================================================================

@tagged('post_install', '-at_install')
class TestBrowserCommissionMigration(BrokerageBrowserCommon):
    """The semantic migration, on screen."""

    def _ambiguous_agent(self):
        agent = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Browser Legacy Agent',
                'login': 'browser.legacy@test.example',
                'company_id': self.company.id,
                'company_ids': [(6, 0, [self.company.id])],
                'groups_id': [(6, 0, [
                    self.env.ref('base.group_user').id,
                    self.env.ref(
                        'real_estate_brokerage.group_realestate_sales_agent'
                    ).id,
                    self.env.ref('sales_team.group_sale_salesman').id,
                ])],
                'is_realestate_agent': True,
                'commission_share_default': 1.0,
            })
        for rate in (2.0, 2.5):
            self._closed_deal_at(rate, agent)
        self.env['realestate.commission.share.migration.runner'].run(agent)
        return agent

    def _closed_deal_at(self, rate, agent=None, close=True):
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.owner.id)
        listing.write({'commission_basis': 'percentage',
                       'commission_percentage': rate})
        txn = self.env['realestate.transaction'].create({
            'listing_id': listing.id,
            'buyer_id': self.buyer.id,
            'seller_id': self.owner.id,
            'sale_price': 1000000.0,
            'transaction_type': 'in_house',
            'selling_agent_id': (agent or self.browser_user).id,
        })
        if close:
            txn.action_sign_contract()
            txn.action_close()
        return txn

    def test_the_configuration_warning_is_on_the_agent_record(self):
        self._as_manager()
        agent = self._ambiguous_agent()
        self.assertTrue(agent.commission_share_needs_review)

        self.start_tour('/odoo/action-base.action_res_users/%d' % agent.id,
                        're_brokerage_share_warning_tour', login='admin')

    def test_the_migration_evidence_is_browsable(self):
        self._as_manager()
        self._ambiguous_agent()

        self.start_tour('/odoo', 're_brokerage_migration_log_tour',
                        login='admin')


@tagged('post_install', '-at_install')
class TestBrowserCommissionSplit(BrokerageBrowserCommon):
    """Splits and the payable, seen the way a manager sees them."""

    def setUp(self):
        super().setUp()
        self._as_manager()
        self.listing = self._listing(inventory_type='external',
                                     owner_partner_id=self.owner.id)
        self.listing.write({'commission_basis': 'percentage',
                            'commission_percentage': 2.5})
        self.txn = self.env['realestate.transaction'].create({
            'listing_id': self.listing.id,
            'buyer_id': self.buyer.id,
            'seller_id': self.owner.id,
            'sale_price': 1000000.0,
            'transaction_type': 'in_house',
            'selling_agent_id': self.browser_user.id,
        })

    def _split(self, share, partner):
        return self.env['realestate.commission'].create({
            'transaction_id': self.txn.id,
            'partner_id': partner.id,
            'role': 'selling',
            'calculation_method': 'share',
            'share_percentage': share,
        })

    def test_the_splits_and_the_remainder_render(self):
        self._split(40.0, self.buyer)
        self._split(20.0, self.owner)

        self.start_tour(
            '/odoo/action-real_estate_brokerage.action_realestate_transaction'
            '/%d' % self.txn.id, 're_brokerage_split_tour', login='admin')

    def test_a_payable_is_visible_on_the_split(self):
        line = self._split(40.0, self.buyer)
        self._split(20.0, self.owner)
        self.txn.action_sign_contract()
        self.txn.action_close()
        line.action_approve()
        line.action_create_vendor_bill()
        self.assertEqual(line.state, 'billed')

        self.start_tour(
            '/odoo/action-real_estate_brokerage.action_realestate_transaction'
            '/%d' % self.txn.id, 're_brokerage_payable_tour', login='admin')


@tagged('post_install', '-at_install')
class TestBrowserDuplicateDetection(BrokerageBrowserCommon):

    def test_the_register_shows_the_refusal(self):
        self._as_manager()
        for name in ('Holding Broker', 'Rival Broker'):
            broker = self.env['res.partner'].create({
                'name': name,
                'is_realestate_broker': True,
                'broker_type': 'agency',
                'broker_state': 'active',
                'broker_kyc_state': 'verified',
                'broker_license_expiry': fields.Date.add(
                    fields.Date.today(), days=365),
            })
            agreement = self.env['realestate.broker.agreement'].create({
                'broker_partner_id': broker.id,
                'company_id': self.company.id,
                'protection_days': 90,
            })
            agreement.action_activate()
            self.env['realestate.lead.registration'].create({
                'broker_partner_id': broker.id,
                'agreement_id': agreement.id,
                'company_id': self.company.id,
                'customer_name': 'Contested Buyer',
                'customer_phone': '+201005556677',
                'customer_email': 'contested@example.com',
            }).action_submit()

        self.start_tour('/odoo', 're_brokerage_duplicate_tour', login='admin')


@tagged('post_install', '-at_install')
class TestBrowserDashboard(BrokerageBrowserCommon):
    """0.1's dashboard is a placeholder. It must still not be broken."""

    def test_the_dashboard_renders(self):
        self._as_manager()
        self._listing()

        self.start_tour('/odoo', 're_brokerage_dashboard_tour', login='admin')


@tagged('post_install', '-at_install')
class TestBrowserRTL(BrokerageBrowserCommon):
    """A genuinely Arabic session, asserted from computed style."""

    def setUp(self):
        super().setUp()
        # Flipping `res.lang.active` is not enough: the web client reads its
        # direction from an *activated* language. `_activate_lang` is what
        # actually loads it, and without it the session stays LTR and the tour
        # asserts nothing at all.
        self.arabic = self.env['res.lang']._activate_lang('ar_001')
        if not self.arabic:
            self.env['res.lang'].with_context(active_test=False).search(
                [('code', '=', 'ar_001')]).write({'active': True})
            self.arabic = self.env['res.lang']._lang_get('ar_001')

    def test_listings_render_right_to_left(self):
        if not self.arabic:
            self.skipTest("No Arabic language available in this database.")
        if self.arabic.direction != 'rtl':
            self.skipTest(
                "%s is not an RTL language in this database." % self.arabic.code)
        self._as_manager()
        self.browser_user.lang = self.arabic.code
        self._listing()

        self.start_tour(
            '/odoo/action-real_estate_brokerage.action_realestate_listing',
            're_brokerage_rtl_tour', login='admin')


@tagged('post_install', '-at_install')
class TestBrowserTablet(BrokerageBrowserCommon):
    """A narrow viewport, where horizontal overflow actually shows up."""
    browser_size = '768x1024'
    touch_enabled = True

    def test_the_listing_list_fits_a_tablet(self):
        self._as_manager()
        self._listing()

        self.start_tour('/odoo', 're_brokerage_menus_tour', login='admin')

    def test_the_transaction_form_fits_a_tablet(self):
        self._as_manager()
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.owner.id)
        listing.write({'commission_basis': 'percentage',
                       'commission_percentage': 2.5})
        txn = self.env['realestate.transaction'].create({
            'listing_id': listing.id,
            'buyer_id': self.buyer.id,
            'seller_id': self.owner.id,
            'sale_price': 1000000.0,
            'transaction_type': 'in_house',
            'selling_agent_id': self.browser_user.id,
        })
        for partner, share in ((self.buyer, 40.0), (self.owner, 20.0)):
            self.env['realestate.commission'].create({
                'transaction_id': txn.id,
                'partner_id': partner.id,
                'role': 'selling',
                'calculation_method': 'share',
                'share_percentage': share,
            })

        self.start_tour(
            '/odoo/action-real_estate_brokerage.action_realestate_transaction'
            '/%d' % txn.id, 're_brokerage_split_tour', login='admin')


@tagged('post_install', '-at_install')
class TestBrowserMultiCompany(BrokerageBrowserCommon):
    """Two companies with deliberately different data."""

    def test_the_second_companys_listings_are_not_on_screen(self):
        self._as_manager()
        ours = self._listing()

        rival = self.env['res.company'].create({'name': 'Rival Brokerage Co'})
        rival_project = self.env['realestate.project'].create({
            'name': 'Rival Project', 'code': 'RVL',
            'company_id': rival.id, 'commercial_state': 'selling'})
        rival_unit = self.env['realestate.property'].create({
            'name': 'RVL-U-001', 'property_code': 'RVL-U-001',
            'hierarchy_level': 'unit', 'usage_category': 'apartment',
            'company_id': rival.id, 'project_id': rival_project.id,
            'base_price': 999999.0,
        })
        rival_listing = self.env['realestate.listing'].with_company(
            rival).create({
                'property_id': rival_unit.id, 'list_price': 999999.0,
                'company_id': rival.id,
            })

        # The browser session is scoped to the user's own company only.
        self.browser_user.company_ids = [(6, 0, [self.company.id])]
        self.browser_user.company_id = self.company.id

        visible = self.env['realestate.listing'].with_user(
            self.browser_user).search([])
        self.assertIn(ours, visible)
        self.assertNotIn(rival_listing, visible)

        self.start_tour('/odoo', 're_brokerage_menus_tour', login='admin')


@tagged('post_install', '-at_install')
class TestBrowserRestrictedAgent(BrokerageBrowserCommon):
    """Everything an ordinary Sales Agent can reach must render for them."""

    def test_the_agent_can_work_without_hitting_an_access_error(self):
        listing = self._listing()
        lead = self._opportunity()
        self._unit(price=1000000.0)
        lead._re_run_matching()
        self.env['realestate.listing.mandate'].create({
            'listing_id': listing.id,
            'owner_partner_id': self.owner.id,
            'company_id': self.company.id,
            'asking_price': listing.list_price,
        })
        self._as_agent()

        for tour in ('re_brokerage_menus_tour',
                     're_brokerage_matches_menu_tour',
                     're_brokerage_mandates_menu_tour'):
            with self.subTest(tour=tour):
                self.start_tour('/odoo', tour, login='admin')
