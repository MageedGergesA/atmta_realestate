# -*- coding: utf-8 -*-
"""Shared fixtures for the Brokerage suite.

The module had **no tests at all** before 0.2, which is how a second customer
pipeline, zero record rules and zero `company_id` columns all shipped without
anyone noticing.

Everything here builds the smallest believable brokerage: a company, a project
with a released unit, a buyer, a CRM opportunity carrying real-estate
requirements, and a listing.
"""

from odoo import fields
from odoo.tests.common import TransactionCase


def module_installed(env, name):
    """Whether an addon is installed in the database the test runs in."""
    return bool(env['ir.module.module'].sudo().search_count(
        [('name', '=', name), ('state', '=', 'installed')]))


def _spec(env, model, node):
    """The ``web_read`` specification a form view asks for, for one field node.

    A copy of the Rental suite's helper: importing it from
    ``atmta_real_estate.tests`` fails wherever Rental's code is not deployed."""
    field = env[model]._fields.get(node.get('name'))
    if field is None:
        return None
    if field.type in ('one2many', 'many2many'):
        sub = {}
        for child in node.iter('field'):
            if child is node:
                continue
            child_field = env[field.comodel_name]._fields.get(child.get('name'))
            if child_field is not None:
                sub[child.get('name')] = ({'fields': {'display_name': {}}}
                                          if child_field.type == 'many2one' else {})
        return {'fields': sub or {'display_name': {}}}
    if field.type == 'many2one':
        return {'fields': {'display_name': {}}}
    return {}



class BrokerageCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Lead = cls.env['crm.lead']
        cls.LegacyLead = cls.env['realestate.lead']
        cls.Listing = cls.env['realestate.listing']
        cls.Viewing = cls.env['realestate.viewing']
        cls.Offer = cls.env['realestate.offer']
        cls.Transaction = cls.env['realestate.transaction']
        cls.Team = cls.env['crm.team']
        cls.Migration = cls.env['realestate.lead.migration']

        # The test author is a brokerage manager. Below-floor approvals and
        # accepting over a higher offer are deliberately manager-only, so the
        # acting user has to hold the group or every such test would be
        # testing the permission check rather than the behaviour behind it.
        cls.env.user.groups_id |= cls.env.ref(
            'real_estate_brokerage.group_realestate_sales_manager')

        cls.buyer = cls.env['res.partner'].create({'name': 'Brokerage Buyer'})
        cls.other_buyer = cls.env['res.partner'].create({'name': 'Second Buyer'})
        cls.seller = cls.env['res.partner'].create({'name': 'Property Owner'})

        cls.agent = cls.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Listing Agent',
                'login': 'brokerage.agent@test.example',
                'email': 'brokerage.agent@test.example',
                'company_id': cls.company.id,
                'company_ids': [(6, 0, [cls.company.id])],
                'groups_id': [(6, 0, [
                    cls.env.ref('base.group_user').id,
                    cls.env.ref(
                        'real_estate_brokerage.group_realestate_sales_agent').id,
                    cls.env.ref('sales_team.group_sale_salesman').id,
                ])],
                'is_realestate_agent': True,
            })

        cls.project = cls.env['realestate.project'].create({
            'name': 'Brokerage Project', 'code': 'BRK',
            'company_id': cls.company.id,
            'commercial_state': 'selling',
        })
        cls.phase = cls.env['realestate.phase'].create({
            'name': 'Phase 1', 'project_id': cls.project.id,
            'commercial_state': 'selling',
        })
        cls.property_type = cls.env['property.type'].create(
            {'name': 'Apartment'})

        cls.re_team = cls.env.ref('real_estate_brokerage.team_realestate_sales')

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------
    _unit_seq = 0

    def _unit(self, released=True, price=1000000.0, **kwargs):
        type(self)._unit_seq += 1
        vals = {
            'name': 'BRK-U-%03d' % type(self)._unit_seq,
            'property_code': 'BRK-U-%03d' % type(self)._unit_seq,
            'hierarchy_level': 'unit',
            'usage_category': 'apartment',
            'property_type_id': self.property_type.id,
            'area_sqm': 120.0,
            'bedroom_count': 3,
            'company_id': self.company.id,
            'project_id': self.project.id,
            'phase_id': self.phase.id,
            'base_price': price,
        }
        vals.update(kwargs)
        unit = self.env['realestate.property'].create(vals)
        if released:
            batch = self.env['realestate.unit.release.batch'].create({
                'project_id': self.project.id,
                'phase_id': self.phase.id,
                'property_ids': [(6, 0, unit.ids)],
            })
            batch.action_approve()
            batch.action_release()
        return unit

    def _listing(self, unit=None, price=1000000.0, activate=False, **kwargs):
        unit = unit if unit is not None else self._unit(price=price)
        vals = {
            'property_id': unit.id,
            'list_price': price,
            'lister_agent_id': self.agent.id,
        }
        vals.update(kwargs)
        listing = self.Listing.create(vals)
        if activate:
            listing.action_activate()
        return listing

    # ------------------------------------------------------------------
    # CRM
    # ------------------------------------------------------------------
    def _opportunity(self, partner=None, **kwargs):
        """A real-estate opportunity with a workable brief."""
        vals = {
            'name': 'Brokerage Opportunity',
            'type': 'opportunity',
            'partner_id': (partner or self.buyer).id,
            'team_id': self.re_team.id,
            'user_id': self.agent.id,
            're_intent': 'buy',
            're_budget_min': 800000.0,
            're_budget_max': 1200000.0,
            're_bedrooms_min': 2,
            're_property_type_ids': [(6, 0, self.property_type.ids)],
        }
        vals.update(kwargs)
        return self.Lead.create(vals)

    def _legacy_lead(self, partner=None, **kwargs):
        """A pre-0.2 Brokerage lead.

        Creation is closed on the model, so the migration context is used —
        which is exactly how production rows will have got there.
        """
        vals = {
            'partner_id': (partner or self.buyer).id,
            'agent_id': self.agent.id,
            'budget_min': 500000.0,
            'budget_max': 900000.0,
            'bedroom_count_min': 2,
            'source': 'walk_in',
            'state': 'qualified',
        }
        vals.update(kwargs)
        return self.LegacyLead.with_context(
            re_legacy_migration=True).create(vals)

    def _stage(self, key):
        return self.env.ref('real_estate_brokerage.stage_re_%s' % key)

    # ------------------------------------------------------------------
    # Mandates
    # ------------------------------------------------------------------
    def _mandate(self, listing, **kwargs):
        vals = {
            'listing_id': listing.id,
            'owner_partner_id': (listing.owner_partner_id or self.seller).id,
            'company_id': listing.company_id.id,
            'asking_price': listing.list_price or 1000000.0,
            'commission_percentage': 2.5,
            # The model's own default is 'none' — refer every offer — which is
            # the safe default for a real mandate but makes every acceptance
            # test a test of that one rule. Fixtures use a realistic mandate;
            # the 'none' case has its own test.
            'negotiation_authority': 'above_minimum',
        }
        vals.update(kwargs)
        return self.env['realestate.listing.mandate'].create(vals)

    def _active_mandate(self, listing=None, **kwargs):
        listing = listing if listing is not None else self._listing(
            inventory_type='external', owner_partner_id=self.seller.id)
        mandate = self._mandate(listing, **kwargs)
        mandate.action_activate()
        return mandate

    def _mandated_listing(self, **mandate_vals):
        """An external listing with a signed, active mandate."""
        listing = self._listing(inventory_type='external',
                                owner_partner_id=self.seller.id)
        self._mandate(listing, **mandate_vals).action_activate()
        listing.invalidate_recordset()
        return listing

    # ------------------------------------------------------------------
    # Viewings
    # ------------------------------------------------------------------
    def _viewing(self, listing=None, lead=None, **kwargs):
        vals = {
            'listing_id': (listing or self._listing()).id,
            'crm_lead_id': (lead or self._opportunity()).id,
            'partner_id': self.buyer.id,
            'agent_id': self.agent.id,
            'scheduled_at': fields.Datetime.now(),
        }
        vals.update(kwargs)
        return self.Viewing.create(vals)

    # ------------------------------------------------------------------
    # Offers
    # ------------------------------------------------------------------
    def _offer(self, listing=None, amount=900000.0, partner=None,
               lead=None, **kwargs):
        listing = listing if listing is not None else self._listing(
            activate=True)
        vals = {
            'listing_id': listing.id,
            'partner_id': (partner or self.buyer).id,
            'agent_id': self.agent.id,
            'amount': amount,
        }
        if lead is not None:
            vals['crm_lead_id'] = lead.id
        vals.update(kwargs)
        return self.Offer.create(vals)

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------
    def _transaction(self, listing, **kwargs):
        vals = {
            'listing_id': listing.id,
            'buyer_id': self.buyer.id,
            'seller_id': self.seller.id,
            'sale_price': listing.list_price or 1000000.0,
            'selling_agent_id': self.agent.id,
        }
        vals.update(kwargs)
        return self.Transaction.create(vals)

    def _developer_contract(self, unit):
        """The Developer sale contract that actually owns an internal unit."""
        return self.env['realestate.sale.contract'].create({
            'partner_id': self.buyer.id,
            'property_id': unit.id,
            'sale_price': unit.base_price or 1000000.0,
        })

    def _commission(self, txn, percentage=2.0, **kwargs):
        vals = {
            'transaction_id': txn.id,
            'partner_id': self.agent.partner_id.id,
            'role': 'selling',
            'calculation_method': 'percentage',
            'percentage': percentage,
        }
        vals.update(kwargs)
        return self.env['realestate.commission'].create(vals)

    # ------------------------------------------------------------------
    # Whole deals, for the commission engine
    # ------------------------------------------------------------------
    def _open_deal(self, gross_percentage=2.0, price=1000000.0):
        """An external, mandated listing with a live transaction on it."""
        listing = self._mandated_listing(
            commission_basis='percentage',
            commission_percentage=gross_percentage)
        listing.action_activate()
        return self._transaction(listing, sale_price=price,
                                 transaction_type='in_house')

    def _closed_deal(self, gross_percentage=2.0, price=1000000.0):
        txn = self._open_deal(gross_percentage, price)
        txn.action_sign_contract()
        txn.action_close()
        return txn

    #: Same shape, but left open so a constraint can be exercised before the
    #: closing rules get a chance to.
    _closed_deal_setup = _open_deal

    # ------------------------------------------------------------------
    # Brokers and channel partners
    # ------------------------------------------------------------------
    _broker_seq = 0

    def _broker(self, name=None, **kwargs):
        """An eligible broker: active, licensed, KYC-verified."""
        type(self)._broker_seq += 1
        vals = {
            'name': name or 'Channel Broker %d' % type(self)._broker_seq,
            'is_realestate_broker': True,
            'broker_type': 'agency',
            'broker_state': 'active',
            'broker_kyc_state': 'verified',
            'broker_license_number': 'LIC-%04d' % type(self)._broker_seq,
            'broker_license_expiry': fields.Date.add(
                fields.Date.today(), days=365),
        }
        vals.update(kwargs)
        return self.env['res.partner'].create(vals)

    def _agreement(self, broker, activate=True, **kwargs):
        vals = {
            'broker_partner_id': broker.id,
            'company_id': self.company.id,
            'commission_percentage': 25.0,
            'protection_days': 90,
        }
        vals.update(kwargs)
        agreement = self.env['realestate.broker.agreement'].create(vals)
        if activate:
            agreement.action_activate()
        return agreement

    def _registration(self, agreement, **kwargs):
        """One customer by default — collisions are the point of this model.

        A per-call unique phone would make every collision test silently pass
        by registering a different person each time.
        """
        vals = {
            'broker_partner_id': agreement.broker_partner_id.id,
            'agreement_id': agreement.id,
            'company_id': agreement.company_id.id,
            'customer_name': 'Registered Buyer',
            'customer_phone': '+201002223344',
            'customer_email': 'registered.buyer@example.com',
        }
        vals.update(kwargs)
        return self.env['realestate.lead.registration'].create(vals)

    def _join(self, team, user=None):
        """Put a user on a team the way Odoo does."""
        return self.env['crm.team.member'].create({
            'crm_team_id': team.id,
            'user_id': (user or self.agent).id,
        })
