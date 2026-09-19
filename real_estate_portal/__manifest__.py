# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Customer Portal",
    'summary': "Customer self-service: sale contracts, installments, handover defects.",
    'description': """
Real Estate Customer Portal
===========================
Customers (buyers) can:
* View their sale contracts and progress
* See the installment schedule with status
* Track handover events and warranty
* Submit snagging issues
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.2',
    'category': 'Real Estate',
    'depends': [
        'real_estate_developer',
        'real_estate_handover',
        'real_estate_maquette',
        # For the multi-level 2D drill viewer used on /projects/<id>:
        # we reuse the vanilla-JS drill renderer that ships in the API
        # module's static/lib/embed/ — no iframe involved.
        'real_estate_api',
        'portal',
        'website',
        'crm',
        'utm',
    ],
    'data': [
        'views/portal_templates.xml',
        'views/public_templates.xml',
    ],
    'assets': {
        # HOOT unit tests for the anonymous shortlist. Its list helpers are
        # pure functions of a storage object precisely so the "nothing leaves
        # the browser" rule can be verified without a browser session.
        'web.assets_unit_tests': [
            # The module under test as well as the test. `public_shortlist.js`
            # ships in the *frontend* bundle, which the unit-test bundle does
            # not include — importing it from a test without this produces
            # "needed by other modules but have not been defined".
            '/real_estate_portal/static/src/js/public_shortlist.js',
            '/real_estate_portal/static/tests/**/*.test.js',
        ],
        'web.assets_frontend': [
            # New multi-level 2D drill viewer (shared with the iframe embed)
            '/real_estate_api/static/lib/embed/embed.scss',
            '/real_estate_api/static/lib/embed/plan_2d_boot.js',
            # Portal-specific mount + EOI-form wiring for the 2D drill
            '/real_estate_portal/static/src/js/portal_drill_2d.js',
            # Existing portal styles + OWL service that still mounts the 3D
            '/real_estate_portal/static/src/scss/public_portal.scss',
            '/real_estate_portal/static/src/js/portal_viewers.js',
            # The anonymous visitor's favourites: held in their own browser,
            # and carried onto a lead only when they submit an enquiry.
            '/real_estate_portal/static/src/js/public_shortlist.js',
        ],
    },
    'application': False,
}
