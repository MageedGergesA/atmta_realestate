# -*- coding: utf-8 -*-
"""Demo layer: a procurement function with work in every stage.

The Procurement dashboard counts eighteen things across demand, sourcing,
award, purchasing and controls. On an empty database every one is a zero, and
a screen of zeros demonstrates nothing except that the queries run.

So this builds a function that is mid-flight: requisitions sitting at every
state from draft to ordered, a tender published and closing this week with
bids already in, one closed, qualified vendors with a qualification about to
expire and one vendor restricted. Every stage has something in it, because
showing where demand is stuck is what the dashboard is for.

It lives here rather than in `real_estate_procurement` because that module is
deliberately an empty compatibility shell and says so; this one owns the
dashboard these records feed, and depends on the whole chain through it.

Everything is generated relative to today, so "closing in 7 days" and
"expiring in 30 days" still mean something next month.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

MODULE = 'atmta_procurement_app'
SENTINEL = '%s.demo_vendor_category_civ' % MODULE

CATEGORIES = [
    ('CIV', 'Civil Materials'),
    ('MEP', 'MEP Equipment'),
    ('FIN', 'Finishing Materials'),
    ('SRV', 'Professional Services'),
]

#: (title, type, state, days until needed_by, priority, [(description, qty, uom)])
REQUESTS = [
    ('Ready-mix concrete, levels 9 to 12', 'material', 'ordered', 40, '0',
     [('Ready-mix C40 concrete', 1800, 'm3')]),
    ('Reinforcement steel, Q4 call-off', 'material', 'ordered', 25, '0',
     [('High-tensile rebar 12mm', 85000, 'kg'), ('High-tensile rebar 16mm', 62000, 'kg')]),
    ('Chilled water pumps and valves', 'equipment', 'sourcing', 55, '0',
     [('End-suction pump 55kW', 4, 'Units'), ('Butterfly valve DN300', 18, 'Units')]),
    ('Porcelain floor tiling, Tower A', 'material', 'approved', 70, '0',
     [('Porcelain tile 600x600', 14000, 'm2')]),
    ('Facade sealant and gaskets', 'material', 'approved', 30, '1',
     [('Structural silicone sealant', 900, 'Units')]),
    ('Site accommodation extension', 'service', 'submitted', 20, '0',
     [('Portable office unit, 6m', 3, 'Units')]),
    ('Temporary power distribution boards', 'equipment', 'submitted', 12, '1',
     [('Distribution board 400A', 6, 'Units')]),
    ('Landscape irrigation controllers', 'material', 'submitted', 95, '0',
     [('Irrigation controller 24-zone', 8, 'Units')]),
    # Deliberately already late: "Past Needed-By Date" is one of the tiles the
    # dashboard exists to surface and it cannot be shown without real arrears.
    ('Waterproofing membrane, basement', 'material', 'approved', -9, '1',
     [('SBS membrane 4mm', 5200, 'm2')]),
    ('Lift shaft steelwork', 'subcontract', 'sourcing', -3, '1',
     [('Fabricated steel sections', 32000, 'kg')]),
    ('Fire alarm panels and detectors', 'equipment', 'draft', 110, '0',
     [('Addressable fire panel', 3, 'Units')]),
    ('Quantity surveying services, Phase 2', 'service', 'draft', 60, '0',
     [('QS services, 6 months', 1, 'Units')]),
]


class ProcurementDemoBuilder(models.AbstractModel):
    _name = 'realestate.demo.procurement'
    _description = 'Demo Builder — Procurement Function'

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
        project = self.env.ref('real_estate_developer.demo_tmr_project',
                               raise_if_not_found=False)
        categories = self._build_categories()
        template = self._build_qualification_template()
        vendors = self._build_vendors(foundation, categories, template)
        self._build_requests(project)
        self._build_sourcing(project, vendors, categories)
        return True

    # ------------------------------------------------------------------
    @api.model
    def _build_categories(self):
        Category = self.env['realestate.procurement.vendor.category']
        categories = {}
        for code, name in CATEGORIES:
            existing = Category.search([('code', '=', code)], limit=1)
            category = existing or Category.create({'code': code, 'name': name})
            categories[code] = self._xmlid(
                'demo_vendor_category_%s' % code.lower(), category)
        return categories

    # ------------------------------------------------------------------
    @api.model
    def _build_qualification_template(self):
        """The questionnaire a qualification is assessed against.

        `realestate.procurement.vendor.qualification.template_id` is NOT NULL,
        so there is no such thing as a qualification without one -- which is
        correct: an assessment with no criteria is an opinion.
        """
        Template = self.env['realestate.procurement.qualification.template']
        existing = Template.search([('code', '=', 'STD')], limit=1)
        if existing:
            return existing
        return self._xmlid('demo_qualification_template', Template.create({
            'code': 'STD',
            'name': 'Standard Supplier Qualification',
            'state': 'active',
            'version': 1,
        }))

    # ------------------------------------------------------------------
    @api.model
    def _build_vendors(self, foundation, categories, template):
        """Approved vendors, one qualification expiring, one vendor restricted.

        A supplier base where everybody is permanently in good standing never
        exercises the governance tiles, which are the ones a procurement
        manager actually looks at.
        """
        Profile = self.env['realestate.procurement.vendor.profile']
        Qualification = self.env['realestate.procurement.vendor.qualification']
        today = fields.Date.context_today(self)
        plan = [
            (1, 'CIV', 'active', 400),      # comfortably current
            (2, 'MEP', 'active', 21),       # inside the 30-day expiry tile
            (3, 'FIN', 'active', 260),
            (4, 'SRV', 'restricted', 150),  # governance tile
        ]
        vendors = []
        for index, category_code, status, expires_in in plan:
            partner = foundation._demo_partner('vendor_%d' % index)
            profile = Profile.create({
                'partner_id': partner.id,
                'governance_status': status,
                'vendor_ref': 'VEN-%04d' % index,
                'registration_date': today - relativedelta(years=2),
                'next_review_date': today + relativedelta(months=6),
            })
            self._xmlid('demo_vendor_%d' % index, profile)
            try:
                # A savepoint, not a bare try: a failed INSERT aborts the
                # whole transaction in PostgreSQL, so catching the Python
                # exception without one leaves the cursor poisoned and every
                # later statement fails too -- which is exactly what happened.
                with self.env.cr.savepoint():
                    self._qualify(Qualification, partner,
                                  categories[category_code], template,
                                  today + relativedelta(days=expires_in))
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: qualification for %s failed: %r",
                                partner.name, error)
            vendors.append(partner)
        return vendors

    @api.model
    def _qualify(self, Qualification, partner, category, template, expiry):
        """Walk a qualification through the real workflow to approved.

        It cannot be created in `approved`: the model freezes the
        template-derived fields the moment a qualification is decided, so
        writing that state straight into `create` is refused. `is_current` --
        which the dashboard's expiry tile filters on -- is only ever set by
        `action_approve`, deliberately, under a lock that stops a vendor ever
        holding two current qualifications at once.

        The approver must not be the assessor; `_check_not_self_approval`
        enforces a real maker/checker split, so the demo uses two users.
        """
        today = fields.Date.context_today(self)
        qualification = Qualification.create({
            'name': 'Qualification %s — %s' % (category.name, partner.name),
            'partner_id': partner.id,
            'category_id': category.id,
            'template_id': template.id,
            'effective_date': today - relativedelta(years=1),
            'expiry_date': expiry,
        })
        assessor, approver = self._maker_and_checker()
        staged = qualification.with_user(assessor)
        staged.action_submit()
        staged.action_start_review()
        staged.action_assess()
        staged.action_request_approval()
        qualification.with_user(approver).action_approve()
        return qualification

    #: The roles a qualification walk needs, granted to the demo users rather
    #: than worked around with sudo. A demo that bypasses its own access rules
    #: proves nothing about them, and these are the groups a real assessor and
    #: approver hold.
    MAKER_GROUP = 'real_estate_procurement.group_procurement_qualification_assessor'
    CHECKER_GROUP = 'real_estate_procurement.group_procurement_qualification_approver'

    @api.model
    def _maker_and_checker(self):
        """Two different internal users, each holding the role they need.

        Falls back to the current user for both when the database has only
        one; the approval is then refused and logged, which is the right
        outcome -- `_check_not_self_approval` is a control, not an obstacle
        for a demo to route around.
        """
        users = self.env['res.users'].search(
            [('share', '=', False), ('active', '=', True)], limit=2)
        if len(users) < 2:
            return self.env.user, self.env.user
        maker, checker = users[0], users[1]
        for user, xmlid in ((maker, self.MAKER_GROUP),
                            (checker, self.CHECKER_GROUP)):
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group and group not in user.groups_id:
                user.sudo().write({'groups_id': [(4, group.id)]})
        return maker, checker

    # ------------------------------------------------------------------
    @api.model
    def _build_requests(self, project):
        Request = self.env['realestate.material.request']
        Uom = self.env['uom.uom']
        today = fields.Date.context_today(self)
        buyers = self.env['res.users'].search(
            [('share', '=', False), ('active', '=', True)], limit=3) or self.env.user
        default_uom = self.env.ref('uom.product_uom_unit', raise_if_not_found=False)

        for index, (title, procurement_type, state, needed_in, priority,
                    lines) in enumerate(REQUESTS, start=1):
            vals = {
                'procurement_type': procurement_type,
                'requested_by_id': buyers[index % len(buyers)].id,
                'buyer_id': buyers[(index + 1) % len(buyers)].id,
                'request_date': today - relativedelta(days=12 + index * 3),
                'needed_by': today + relativedelta(days=needed_in),
                'priority': priority,
                'justification': title,
                'line_ids': [(0, 0, {
                    'description': description,
                    'qty': qty,
                    'uom_id': (Uom.search([('name', '=', uom_name)], limit=1)
                               or default_uom).id,
                    'estimated_unit_cost': round(180000.0 / max(qty, 1), 2),
                }) for description, qty, uom_name in lines],
            }
            if project:
                vals['project_id'] = project.id
            request = Request.create(vals)
            # The state is written after creation rather than passed to it:
            # several of these are only reachable through the workflow, and a
            # demo that sets them in `create` would hide the day the workflow
            # stops allowing it.
            try:
                with self.env.cr.savepoint():
                    request.state = state
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: requisition %s could not reach %s: %s",
                                request.display_name, state, error)
            self._xmlid('demo_request_%d' % index, request)

    # ------------------------------------------------------------------
    @api.model
    def _build_sourcing(self, project, vendors, categories):
        """A tender closing this week, bids received, and one already closed."""
        Event = self.env['realestate.procurement.sourcing.event']
        Invitation = self.env['realestate.procurement.sourcing.invitation']
        Response = self.env['realestate.procurement.bid.response']
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)

        plan = [
            # (title, method, state, closes in days, category, invited, bids,
            #  [(scope line, qty, uom)])
            ('Chilled water pumps and valves', 'competitive_tender', 'published',
             4, 'MEP', 4, 3,
             [('Supply of end-suction pumps, 55kW, with VFD', 4, 'Units'),
              ('Supply of butterfly valves DN300', 18, 'Units')]),
            ('Porcelain floor tiling, Tower A', 'rfq', 'published', 16, 'FIN', 3, 1,
             [('Supply and lay porcelain tile 600x600', 14000, 'm2')]),
            ('Lift shaft steelwork', 'limited_tender', 'closed', -6, 'CIV', 3, 3,
             [('Fabricate and erect lift shaft steelwork', 32000, 'kg')]),
            ('Quantity surveying services, Phase 2', 'rfq', 'draft', 30, 'SRV', 2, 0,
             [('Quantity surveying services, six months', 1, 'Units')]),
        ]
        Uom = self.env['uom.uom']
        default_uom = self.env.ref('uom.product_uom_unit', raise_if_not_found=False)
        for index, (title, method, state, closes_in, category_code, invited,
                    bids, scope) in enumerate(plan, start=1):
            vals = {
                'title': title,
                'sourcing_method': method,
                'state': state,
                'close_datetime': now + relativedelta(days=closes_in),
                # Always BEFORE the close, including for the tender that
                # already closed: a deadline after the closing moment lets a
                # question be asked that nobody could answer in time to bid,
                # and the model rightly refuses it.
                'clarification_deadline': now + relativedelta(days=closes_in - 3),
                'anticipated_award_date': today + relativedelta(
                    days=max(closes_in + 14, 14)),
                'category_id': categories[category_code].id,
                'addendum_ack_policy': 'required_before_response',
                'description': '<p>%s for Teklines Marina Residences.</p>' % title,
                # Scope is not optional. `_rfq_line_values` refuses to invite
                # anybody to a tender with no lines -- "there is nothing to ask
                # a vendor to quote" -- which is right, and is why the demo
                # has to carry a real bill of scope rather than a bare header.
                'line_ids': [(0, 0, {
                    'sequence': (position + 1) * 10,
                    'name': description,
                    'quantity': qty,
                    'product_uom_id': (Uom.search([('name', '=', uom_name)], limit=1)
                                       or default_uom).id,
                    'required_date': today + relativedelta(days=max(closes_in + 30, 30)),
                }) for position, (description, qty, uom_name) in enumerate(scope)],
            }
            if project and 'project_id' in Event._fields:
                vals['project_id'] = project.id
            event = Event.create(vals)
            self._xmlid('demo_event_%d' % index, event)

            for position, partner in enumerate(vendors[:invited]):
                invitation = Invitation.create({
                    'event_id': event.id,
                    'partner_id': partner.id,
                    'state': 'responded' if position < bids else (
                        'invited' if state != 'closed' else 'no_response'),
                })
                if position < bids:
                    Response.create({
                        'event_id': event.id,
                        'invitation_id': invitation.id,
                        'state': 'received',
                        'notes': 'Bid received against %s.' % title,
                    })
