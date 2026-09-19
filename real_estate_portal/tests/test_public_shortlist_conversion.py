# -*- coding: utf-8 -*-
"""A visitor's browser-held shortlist, and the one act that makes it real.

The rule this protects: **anonymous public navigation must not create
permanent CRM records.** A visitor may favourite eleven apartments and leave;
nothing about that is a commercial event, and nothing about it should appear in
anybody's pipeline.

The enquiry form is the exception, and it is an exception because the visitor
typed their name into it and pressed a button. At that moment there is a lead
to attach favourites to, and the favourites go onto it — once, checked against
the project they claim to belong to.
"""

import base64

from odoo import http
from odoo.tests import tagged
from odoo.tests.common import HttpCase


#: The smallest valid PNG: 1x1, transparent. Enough for "a plan image exists",
#: and a real image so Odoo's own image processing accepts it.
ONE_PIXEL_PNG = base64.b64decode(
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
    b'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')


@tagged('post_install', '-at_install')
class TestPublicShortlistConversion(HttpCase):

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        # Publishing is a Visual Publisher's decision — a real gate, and the
        # fixture user (OdooBot) does not hold it. Granted here rather than
        # worked around, so the project reaches the public through the same
        # door a person would use.
        self.env.user.groups_id |= self.env.ref(
            'real_estate_maquette.group_visual_manager')
        self.project = self.env['realestate.project'].create({
            'name': 'Portal Conversion Project',
            'code': 'PCP',
            'company_id': self.company.id,
            'visual_public_enabled': True,
        })
        self.unit = self._unit('PCP-01')
        self.other_unit = self._unit('PCP-02')
        # Enough of a gallery to publish: the page gate reads the publication
        # state, and an unpublished project is a 404 before any of this runs.
        self.project.master_plan_2d = base64.b64encode(ONE_PIXEL_PNG)
        # A 2D-only gallery: no GLB, so 3D is switched off rather than left on
        # and failing validation for a model that was never uploaded.
        self.project.visual_3d_enabled = False
        self.project.action_visual_validate()
        self.project.action_visual_publish()

    def _unit(self, code):
        return self.env['realestate.property'].create({
            'name': code,
            'property_code': code,
            'project_id': self.project.id,
            'hierarchy_level': 'unit',
            'company_id': self.company.id,
            'base_price': 1000000.0,
        })

    def _submit(self, **extra):
        # A public session, so the form's CSRF token is the one a real visitor
        # would carry. `authenticate(None, None)` is Odoo's way of saying
        # "nobody is logged in" rather than "no session exists".
        if not getattr(self, 'session', None):
            self.authenticate(None, None)
        payload = {
            'name': 'Anonymous Visitor',
            'email': 'visitor@example.com',
            'csrf_token': http.Request.csrf_token(self),
        }
        payload.update(extra)
        return self.url_open(
            '/projects/%s/eoi' % self.project.id, data=payload)

    def _leads(self):
        return self.env['crm.lead'].search(
            [('re_project_id', '=', self.project.id)])

    def _shortlist_rows(self, lead):
        # Shortlist rows are Brokerage's matches; without it the enquiry still
        # succeeds, and there is no shortlist to assert on.
        if 'realestate.property.match' not in self.env:
            self.skipTest("Shortlists are Brokerage records, and Brokerage is not installed.")
        return self.env['realestate.property.match'].search(
            [('crm_lead_id', '=', lead.id), ('shortlisted', '=', True)])

    # ------------------------------------------------------------------
    def test_browsing_the_public_page_writes_nothing(self):
        leads_before = self.env['crm.lead'].search_count([])
        partners_before = self.env['res.partner'].search_count([])

        self.url_open('/projects/%s' % self.project.id)
        self.url_open('/projects/%s/units.json' % self.project.id)

        self.assertEqual(self.env['crm.lead'].search_count([]), leads_before)
        self.assertEqual(
            self.env['res.partner'].search_count([]), partners_before)

    def test_an_enquiry_carries_the_visitor_s_favourites_onto_the_lead(self):
        self._submit(shortlist='%s,%s' % (self.unit.id, self.other_unit.id))

        lead = self._leads()
        self.assertEqual(len(lead), 1)
        rows = self._shortlist_rows(lead)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            set(rows.mapped('property_id').ids),
            {self.unit.id, self.other_unit.id})

    def test_an_enquiry_without_favourites_still_works(self):
        self._submit()

        lead = self._leads()
        self.assertEqual(len(lead), 1)
        self.assertFalse(self._shortlist_rows(lead))

    def test_a_unit_from_another_project_is_not_absorbed(self):
        """A hidden field is a value somebody typed."""
        other_project = self.env['realestate.project'].create({
            'name': 'Somebody Else', 'code': 'ELSE',
            'company_id': self.company.id})
        stranger = self.env['realestate.property'].create({
            'name': 'ELSE-01', 'property_code': 'ELSE-01',
            'project_id': other_project.id, 'hierarchy_level': 'unit',
            'company_id': self.company.id})

        self._submit(shortlist='%s,%s' % (self.unit.id, stranger.id))

        rows = self._shortlist_rows(self._leads())
        self.assertEqual(rows.mapped('property_id').ids, [self.unit.id])

    def test_rubbish_in_the_field_does_not_break_the_enquiry(self):
        """The message is the thing that must not be lost."""
        self._submit(shortlist='not-a-number,,;drop table')

        lead = self._leads()
        self.assertEqual(len(lead), 1, "The enquiry itself was lost.")
        self.assertFalse(self._shortlist_rows(lead))

    def test_submitting_twice_does_not_duplicate_the_shortlist(self):
        self._submit(shortlist=str(self.unit.id))
        first_lead = self._leads()

        self._submit(shortlist=str(self.unit.id))

        # Two enquiries are two leads — that is correct, somebody enquired
        # twice. What must not double is one lead's shortlist.
        self.assertEqual(len(self._shortlist_rows(first_lead)), 1)
