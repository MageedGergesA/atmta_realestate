# -*- coding: utf-8 -*-
{
    'name': "atmta_clinic",

    'summary': "Comprehensive Clinic Management System (EHR, Practice, Billing, Analytics)",

    'description': """
A full-featured Odoo 18 module for clinics and medical practices. Includes:
- Integrated EHR (patient records, clinical documentation, e-prescribing)
- Practice management (scheduling, workflow automation)
- Billing & revenue cycle management
- Patient engagement (portal, telehealth)
- Advanced analytics and reporting
- Multi-tenant SaaS ready, API, and integrations
    """,

    'author': "My Company",
    'website': "https://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Healthcare',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base', 'web','website','portal'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/views.xml',
        'views/templates.xml',
        'data/website_menu.xml',
    ],
    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
}

