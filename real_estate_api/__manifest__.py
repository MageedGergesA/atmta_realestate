# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Public REST API + Embeddable 2D / 3D Viewers",
    'summary': "REST endpoints for catalog/maps/interest lead capture, plus iframe-embeddable 2D drill plan and 3D maquette.",
    'description': """
Real Estate Public API
======================
A versioned REST layer (``/api/v1/``) and an iframe embed surface
(``/embed/v1/``) over the existing real-estate suite.

* Catalog: developers, projects, buildings, units, master plans
* 2D drill plan as JSON (image url + regions + child stubs)
* 3D maquette descriptor (GLB url + camera + hotspots)
* Interest lead capture (creates ``crm.lead``)
* Signed-token, origin-restricted embeds for the 2D and 3D viewers
* Strict 404 / 400 / 403 error contract — no silent fallbacks
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.1',
    'category': 'Real Estate',
    'depends': [
        'base',
        'web',
        'crm',
        'atmta_real_estate',
        'real_estate_developer',
        'real_estate_plan',
        'real_estate_maquette',
    ],
    'data': [
        'security/api_groups.xml',
        'security/ir.model.access.csv',
        'data/ir_config_parameter.xml',
        'data/cron.xml',
        'reports/sale_contract_report.xml',
        'reports/partner_statement_report.xml',
        'views/embed_templates.xml',
        'views/embed_token_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        # The embed assets are loaded directly by the chromeless embed templates
        # via <t t-call-assets="real_estate_api.embed_assets"/>, NOT by
        # web.assets_backend/frontend. This keeps them out of the main bundles.
        'real_estate_api.embed_assets': [
            '/real_estate_api/static/lib/embed/embed.scss',
            '/real_estate_api/static/lib/embed/embed_bridge.js',
        ],
        'real_estate_api.embed_plan_2d': [
            ('include', 'real_estate_api.embed_assets'),
            '/real_estate_api/static/lib/embed/plan_2d_boot.js',
        ],
        'real_estate_api.embed_maquette_3d': [
            ('include', 'real_estate_api.embed_assets'),
            '/real_estate_api/static/lib/embed/maquette_3d_boot.js',
        ],
    },
    'application': False,
    'installable': True,
}
