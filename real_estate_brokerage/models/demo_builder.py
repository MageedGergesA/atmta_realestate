# -*- coding: utf-8 -*-
"""Demo layer: a working brokerage desk.

The Brokerage dashboard counts listings by state, viewings *this week*,
offers awaiting a decision, commissions paid *this month* and a CRM funnel.
Every one of those is time-relative, so the data here is generated relative to
today rather than pinned to fixed dates -- demo data with hard-coded 2025
dates reads as an empty desk the moment the year turns.

What it builds
--------------
* mandates and listings across every listing state, including sold ones dated
  inside the current month so "Sold (MTD)" is not a permanent zero;
* a CRM pipeline of real-estate opportunities spread over the stages, with
  some won and some still open, so the funnel and the conversion rate have
  something to divide;
* viewings in the current week, in the past and cancelled;
* offers submitted, countered, accepted and rejected;
* transactions and their commission lines, some paid this month and some still
  in the pipeline.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

MODULE = 'real_estate_brokerage'
SENTINEL = '%s.demo_listing_1' % MODULE


class BrokerageDemoBuilder(models.AbstractModel):
    _name = 'realestate.demo.brokerage'
    _description = 'Demo Builder — Brokerage Desk'

    @api.model
    def _xmlid(self, suffix, record):
        self.env['ir.model.data']._update_xmlids([{
            'xml_id': '%s.%s' % (MODULE, suffix),
            'record': record,
            'noupdate': True,
        }])
        return record

    # ------------------------------------------------------------------
    @api.model
    def _build(self):
        if self.env.ref(SENTINEL, raise_if_not_found=False):
            return True
        foundation = self.env['realestate.demo.property']
        foundation._ensure_foundation()

        listings = self._build_listings(foundation)
        self._build_pipeline(foundation)
        self._build_viewings(listings, foundation)
        self._build_offers(listings, foundation)
        self._build_transactions(listings, foundation)
        return True

    # ------------------------------------------------------------------
    #: (unit code, listing state, deal type, months since listing)
    LISTINGS = [
        ('TMR-B-102', 'active', 'sale', 4),
        ('TMR-B-202', 'active', 'sale', 3),
        ('TMR-B-301', 'active', 'rent', 3),
        ('TMR-B-302', 'active', 'rent', 2),
        ('TMR-B-401', 'active', 'sale', 2),
        ('TMR-B-402', 'under_offer', 'sale', 5),
        ('TMR-B-501', 'under_offer', 'sale', 4),
        ('TMR-B-502', 'sold', 'sale', 6),
        ('TMR-B-601', 'sold', 'sale', 7),
        ('TMR-B-602', 'withdrawn', 'sale', 9),
        ('TMR-RP-S01', 'active', 'rent', 2),
        ('TMR-RP-S02', 'active', 'rent', 1),
        ('TMR-RP-S03', 'draft', 'rent', 0),
        ('TMR-RP-O01', 'active', 'rent', 3),
        ('TMR-RP-O02', 'expired', 'rent', 11),
    ]

    @api.model
    def _build_listings(self, foundation):
        Listing = self.env['realestate.listing']
        Mandate = self.env['realestate.listing.mandate']
        today = fields.Date.context_today(self)
        owner = foundation._demo_partner('broker_1')
        agents = self._agents()

        built = {}
        for index, (code, state, deal_type, months) in enumerate(
                self.LISTINGS, start=1):
            unit = foundation._demo_unit(code)
            if not unit:
                _logger.warning("Demo: unit %s missing, listing skipped", code)
                continue
            listed_on = today - relativedelta(months=months)
            # "Sold this month" has to land inside the current month AND not
            # in the future: the dashboard bounds Sold (MTD) by month start
            # and today. `today - 6 days` looked right and was wrong -- on the
            # 5th of a month it lands in the previous one, and the tile reads
            # zero. Anchor to the first of the month instead and clamp.
            sold_date = self._this_month(index) if state == 'sold' else False
            price = unit.base_price or 5000000
            if deal_type == 'rent':
                price = round(price * 0.08 / 12, -2)

            listing = Listing.create({
                'property_id': unit.id,
                'deal_type': deal_type,
                'inventory_type': 'internal',
                'state': state,
                'owner_partner_id': owner.id,
                'lister_agent_id': agents[index % len(agents)].id,
                'list_date': listed_on,
                'expiry_date': listed_on + relativedelta(months=12),
                'list_price': price,
                'minimum_price': round(price * 0.93),
                'commission_basis': 'percentage',
                'commission_percentage': 2.5,
                'publication_state': 'published' if state == 'active'
                                     else 'internal_only',
                'featured': index <= 3,
                'sold_date': sold_date,
            })
            self._xmlid('demo_listing_%d' % index, listing)
            built[code] = listing

            Mandate.create({
                'listing_id': listing.id,
                'owner_partner_id': owner.id,
                'mandate_type': 'exclusive' if index % 3 else 'sole_agency',
                'state': 'active' if state in ('active', 'under_offer')
                         else 'expired',
                'date_start': listed_on,
                'date_end': listed_on + relativedelta(months=12),
                'asking_price': price,
                'minimum_price': round(price * 0.93),
                'commission_basis': 'percentage',
                'commission_percentage': 2.5,
                'negotiation_authority': 'above_minimum',
                'authorized_marketing': True,
            })
        return built

    @api.model
    def _this_month(self, offset=0):
        """A date inside the current month, never in the future.

        Every "this month" tile in the suite bounds on (month start, today),
        so demo data has to land in that window on whatever day it is loaded
        -- including the 1st.
        """
        today = fields.Date.context_today(self)
        day = min(1 + offset, today.day)
        return today.replace(day=max(day, 1))

    @api.model
    def _agents(self):
        """Whatever internal users exist; the demo must not invent logins."""
        agents = self.env['res.users'].search(
            [('share', '=', False), ('active', '=', True)], limit=4)
        return agents or self.env.user

    # ------------------------------------------------------------------
    #: (buyer key, intent, stage order, priority, budget min, budget max, won)
    PIPELINE = [
        ('buyer_1', 'buy', 0, '0', 6000000, 9000000, False),
        ('buyer_2', 'buy', 1, '2', 8000000, 12000000, False),
        ('buyer_3', 'buy', 1, '3', 12000000, 18000000, False),
        ('buyer_4', 'rent', 2, '1', 40000, 70000, False),
        ('buyer_5', 'buy', 2, '3', 9000000, 11000000, False),
        ('buyer_6', 'buy', 3, '2', 7000000, 9500000, False),
        ('buyer_7', 'buy', 3, '3', 15000000, 20000000, True),
        ('buyer_8', 'rent', 0, '1', 30000, 50000, False),
    ]

    @api.model
    def _build_pipeline(self, foundation):
        if 'crm.lead' not in self.env:
            return
        Lead = self.env['crm.lead']
        Stage = self.env['crm.stage']
        # The won stage is searched for explicitly. Taking the first eight
        # stages by sequence and filtering them for `is_won` found nothing in
        # a database whose won stage sorts last, so every demo lead landed in
        # an open stage and the dashboard's conversion rate was a hard 0%.
        won = Stage.search([('is_won', '=', True)], order='sequence', limit=1)
        open_stages = Stage.search([('is_won', '=', False)],
                                   order='sequence', limit=6)
        if not open_stages:
            open_stages = Stage.search([], order='sequence', limit=6)
        if not open_stages:
            return
        won = won or open_stages[-1]
        today = fields.Date.context_today(self)
        agents = self._agents()
        project = self.env.ref('real_estate_developer.demo_tmr_project',
                               raise_if_not_found=False)

        for index, (buyer_key, intent, order, priority, lo, hi,
                    is_won) in enumerate(self.PIPELINE, start=1):
            partner = foundation._demo_partner(buyer_key)
            stage = won if is_won else open_stages[order % len(open_stages)]
            vals = {
                'name': '%s — %s enquiry' % (
                    partner.name, 'purchase' if intent == 'buy' else 'rental'),
                'type': 'opportunity',
                'partner_id': partner.id,
                'email_from': partner.email,
                'phone': partner.phone,
                'stage_id': stage.id,
                'priority': priority,
                'user_id': agents[index % len(agents)].id,
                'expected_revenue': hi,
                'date_deadline': today + relativedelta(months=2),
                're_intent': intent,
                're_market': 'developer',
                're_budget_min': lo,
                're_budget_max': hi,
                're_bedrooms_min': 2,
                're_bedrooms_max': 4,
                're_urgency': '3_months' if priority in ('2', '3')
                              else 'exploring',
            }
            if project:
                vals['re_project_ids'] = [(6, 0, project.ids)]
            self._xmlid('demo_lead_%d' % index, Lead.create(vals))

    # ------------------------------------------------------------------
    @api.model
    def _build_viewings(self, listings, foundation):
        Viewing = self.env['realestate.viewing']
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)
        # Anchored to TODAY, not to this week's Monday. Anchoring to Monday
        # put the whole week's viewings behind us the moment the week rolled
        # over -- data generated on a Sunday showed "this week: 0" the next
        # morning. Hanging them off today keeps them inside the current week
        # for as long as the demo database is young, which is when anybody
        # looks at it.
        anchor = fields.Datetime.to_datetime(today)
        agents = self._agents()
        codes = [c for c in listings]
        if not codes:
            return

        plan = [
            # (code index, when, state, outcome)
            (0, anchor + relativedelta(hours=10), 'completed', 'interested'),
            (1, anchor + relativedelta(hours=15), 'completed',
             'very_interested'),
            (2, anchor + relativedelta(days=1, hours=11), 'confirmed', False),
            (3, anchor + relativedelta(days=1, hours=16), 'scheduled', False),
            (4, anchor + relativedelta(days=2, hours=12), 'scheduled', False),
            (5, anchor + relativedelta(days=3, hours=14), 'scheduled', False),
            (6, now - relativedelta(days=12), 'completed', 'not_interested'),
            (7, now - relativedelta(days=25), 'no_show', False),
            (8, now - relativedelta(days=33), 'cancelled', False),
            (9, now - relativedelta(days=41), 'completed', 'offer_intent'),
        ]
        for index, (code_index, when, state, outcome) in enumerate(plan,
                                                                   start=1):
            code = codes[code_index % len(codes)]
            partner = foundation._demo_partner('buyer_%d' % ((index % 8) + 1))
            vals = {
                'listing_id': listings[code].id,
                'partner_id': partner.id,
                'agent_id': agents[index % len(agents)].id,
                'scheduled_at': when,
                'duration': 1.0,
                'state': state,
            }
            if outcome:
                vals['outcome'] = outcome
                vals['feedback_rating'] = (
                    outcome if outcome in ('very_interested', 'interested',
                                           'not_interested') else 'interested')
                vals['feedback'] = 'Demo feedback recorded after the viewing.'
            if state == 'cancelled':
                vals['cancel_reason'] = 'customer'
                vals['cancel_note'] = 'Customer rescheduled to a later date.'
            self._xmlid('demo_viewing_%d' % index, Viewing.create(vals))

    # ------------------------------------------------------------------
    @api.model
    def _build_offers(self, listings, foundation):
        Offer = self.env['realestate.offer']
        today = fields.Date.context_today(self)
        agents = self._agents()
        codes = list(listings)
        if not codes:
            return

        plan = [
            # (code, buyer, state, % of list price, days ago)
            ('TMR-B-402', 'buyer_2', 'submitted', 0.97, 9),
            ('TMR-B-501', 'buyer_3', 'countered', 0.92, 6),
            ('TMR-B-102', 'buyer_5', 'submitted', 0.95, 3),
            ('TMR-B-502', 'buyer_7', 'accepted', 0.99, 40),
            ('TMR-B-601', 'buyer_1', 'accepted', 1.00, 55),
            ('TMR-B-202', 'buyer_4', 'rejected', 0.85, 20),
            ('TMR-B-401', 'buyer_6', 'withdrawn', 0.90, 30),
        ]
        for index, (code, buyer_key, state, ratio, days) in enumerate(
                plan, start=1):
            listing = listings.get(code)
            if not listing:
                continue
            offer = Offer.create({
                'listing_id': listing.id,
                'partner_id': foundation._demo_partner(buyer_key).id,
                'agent_id': agents[index % len(agents)].id,
                'amount': round(listing.list_price * ratio),
                'deposit_amount': round(listing.list_price * ratio * 0.05),
                'offer_date': today - relativedelta(days=days),
                'expiry_date': today - relativedelta(days=days)
                               + relativedelta(days=21),
                'proposed_closing_date': today + relativedelta(days=45),
                'state': state,
                'approval_state': 'approved' if state == 'accepted'
                                  else 'not_required',
                'conditions': 'Subject to a satisfactory structural survey.',
            })
            self._xmlid('demo_offer_%d' % index, offer)

    # ------------------------------------------------------------------
    @api.model
    def _build_transactions(self, listings, foundation):
        """Close two deals properly, including the money.

        `realestate.commission.paid` is a stored compute over the vendor
        bill's payment state -- it cannot be written. So the demo calls
        ``action_mark_paid``, which raises the bill, posts it and reconciles a
        real payment, exactly as a user would. That is also what puts
        commission bills and payments into Accounting, so the finance screens
        have something in them too.
        """
        Transaction = self.env['realestate.transaction']
        Commission = self.env['realestate.commission']
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        agents = self._agents()
        brokerage = foundation._demo_partner('broker_1')

        plan = [
            # (unit code, buyer, transaction state, days ago, pay the commission?)
            ('TMR-B-502', 'buyer_7', 'closed', 38, True),
            ('TMR-B-601', 'buyer_1', 'closed', 52, True),
            ('TMR-B-402', 'buyer_2', 'contract_signed', 12, False),
            ('TMR-B-501', 'buyer_3', 'deposit_received', 5, False),
        ]
        for index, (code, buyer_key, state, days, pay) in enumerate(plan,
                                                                    start=1):
            listing = listings.get(code)
            if not listing:
                continue
            agreed = today - relativedelta(days=days)
            transaction = Transaction.create({
                'listing_id': listing.id,
                'transaction_type': 'brokerage',
                'buyer_id': foundation._demo_partner(buyer_key).id,
                'seller_id': listing.owner_partner_id.id,
                'selling_agent_id': agents[index % len(agents)].id,
                'sale_price': listing.list_price,
                'deposit_amount': round(listing.list_price * 0.05),
                'deposit_paid_date': agreed,
                'sale_agreement_date': agreed,
                'closing_date': (agreed + relativedelta(days=30)
                                 if state == 'closed' else False),
                'state': state,
            })
            self._xmlid('demo_transaction_%d' % index, transaction)

            # Dated inside the current month on purpose: the dashboard bounds
            # "Commission MTD" by month start and today, so a payout dated
            # last month would never appear however large it was.
            payment_date = month_start + relativedelta(
                days=min(4 + index, max(today.day - 1, 0)))
            for role, share, recipient in (
                    ('brokerage', 0.5, brokerage),
                    ('selling', 0.3, agents[index % len(agents)].partner_id),
                    ('lister', 0.2, (listing.lister_agent_id.partner_id
                                     or brokerage))):
                commission = Commission.create({
                    'transaction_id': transaction.id,
                    'partner_id': recipient.id,
                    'role': role,
                    'calculation_method': 'percentage',
                    'percentage': 2.5 * share,
                    'state': 'approved',
                    'effective_date': agreed,
                })
                if not pay:
                    continue
                try:
                    commission.action_mark_paid()
                    commission.payment_date = payment_date
                except Exception as error:      # noqa: BLE001 - demo only
                    # Accounting may not be configured in every database this
                    # demo lands in. Say so and leave the commission approved
                    # rather than taking the whole demo load down.
                    _logger.warning(
                        "Demo: could not pay commission %s: %s",
                        commission.id, error)
        return True
