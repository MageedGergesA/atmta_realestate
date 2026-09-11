# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Construction",
    'summary': "Enterprise construction project controls: WBS, budget baselines, commitments, ledger-backed actual, forecast/EAC, change control, BOQ certification, retention and advances, RFI/submittals/document control, QA/QC, claims and EOT, risk and issues, and the Construction Control Tower.",
    'description': """
Real Estate Construction
========================
Track construction progress for projects and phases:
* Milestones with weights and completion %
* Contractor management
* Cost tracking against project budget
* Auto-computed project/phase progress
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    # 1.0.1 — M3 added one additive view file (procurement_coding_views.xml).
    # The bump exists so an existing database actually loads it on upgrade; no
    # Construction model, record, formula or test changes with it.
    'version': '1.0.1',
    'category': 'Real Estate',
    'depends': [
        'real_estate_developer', 'account', 'purchase', 'real_estate_procurement', 'stock',
        # Wave 12 — the WBS, the cost codes, the analytic helper and the
        # company accounts moved down to the floor. Declared so the floor
        # loads first and the field metadata can change owner cleanly.
        'atmta_construction_core',
        # Wave 13 — contractors and contract packages moved down to the
        # commercial floor so the capabilities above could point at them.
        'atmta_construction_contract',
        # Wave 14 — documents, submittals, transmittals and RFIs moved down.
        'atmta_construction_documents',
        # Wave 15 — inspections, observations and NCRs moved down.
        'atmta_construction_quality',
        # Wave 16 — change events and change orders moved down.
        'atmta_construction_change',
        # Wave 17 — claims, delay events, EOTs and notices moved down.
        'atmta_construction_claims',
        # Wave 18 — BOQ, milestones, tasks, daily reports, labour and cost
        # lines moved down to site execution.
        'atmta_construction_site',
        # Wave 19 — certificates, retention, advances and owner billing
        # moved down to certification.
        'atmta_construction_certification',
        # Named for the bridge below, which gives each legacy construction
        # group its canonical twin.
        'atmta_roles',
    ],
    'data': [
        'security/security.xml',
        'security/canonical_role_bridge.xml',
        'security/ir.model.access.csv',
        'security/construction_rules.xml',
        'data/sequences.xml',
        # Wave 12 — the analytic plans moved to `atmta_construction_core`
        # with the helper that names them.
        'wizard/subcontract_po_wizard_views.xml',
        # Wave 12 — cost-structure screens moved with their models to
        # `atmta_construction_core`.
        'views/budget_views.xml',
        'views/forecast_views.xml',
        'views/change_views.xml',
        # Wave 14 — the models moved to `atmta_construction_documents`; these
        # screens did not, because they render `change_event_id` and the
        # action that raises it, which this module declares.
        'views/information_views.xml',
        'views/quality_views.xml',
        'views/certification_views.xml',
        'views/claims_views.xml',
        'views/contractor_views.xml',
        'views/milestone_views.xml',
        'views/construction_task_views.xml',
        'views/boq_views.xml',
        'views/payment_certificate_views.xml',
        'views/cost_line_views.xml',
        'views/project_views.xml',
        'views/menus.xml',
        'views/owner_progress_billing_views.xml',
        'views/labor_log_views.xml',
        'views/construction_dashboard_views.xml',
        # Additive: Construction's coding fields on Procurement's documents.
        'views/procurement_coding_views.xml',
    ],
    'assets': {
        # Browser tours need @web_tour, which lives in the tests bundle.
        'web.assets_tests': [
            '/real_estate_construction/static/tests/tours/**/*.js',
        ],
        'web.assets_unit_tests': [
            '/real_estate_construction/static/tests/**/*.test.js',
        ],
        'web.assets_backend': [
            '/real_estate_construction/static/src/scss/construction_dashboard.scss',
            '/real_estate_construction/static/src/scss/control_tower.scss',
            '/real_estate_construction/static/src/js/control_tower/control_tower.js',
            '/real_estate_construction/static/src/xml/control_tower.xml',
            '/real_estate_construction/static/src/js/construction_dashboard.js',
            '/real_estate_construction/static/src/xml/construction_dashboard.xml',
        ],
    },
    'application': True,
}
