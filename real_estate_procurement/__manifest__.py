# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Procurement",
    'summary': "Shared procurement infrastructure for the real-estate suite.",
    'description': """
Real Estate Procurement
=======================
Cross-module supply-chain foundation used by Construction, Handover, Rental and Aftermarket:

* Unified Material Request workflow (any module raises a request, one PO loop consumes it)
* Vendor tags (contractor, material supplier, service vendor, marketing, consultant, utility, landowner)
* Real-estate product flags (construction material, property fitting, marketing asset, maintenance consumable)
* Filtered procurement menus (POs, vendors, catalog) scoped to real-estate activity
* Starter catalog (~150 products across civil, structural, MEP, finishing, fittings, marketing, services)
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.1',
    'category': 'Real Estate',
    'depends': [
        'atmta_real_estate',
        'purchase',
        'product',
        'stock',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequences.xml',
        'data/partner_categories.xml',
        'data/product_categories.xml',
        'data/products_seed.xml',
        'views/material_request_views.xml',
        'views/purchase_order_views.xml',
        'views/product_views.xml',
        'views/partner_views.xml',
        'views/menus.xml',
    ],
    'application': True,
}
