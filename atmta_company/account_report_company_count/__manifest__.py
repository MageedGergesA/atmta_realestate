# -*- coding: utf-8 -*-
{
    'name': "account_report_company_count",

    'summary': "cheque number and description to the general ledger, and company count warning",

    'description': """
cheque number and description to the general ledger, and company count warning    """,

    'author': "MGA",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Accounting/Accounting',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base', 'account','account_reports'],

    # always loaded
    'data': [
        # 'security/ir.model.access.csv',
        'data/general_ledger_report.xml',
        'views/views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'account_report_company_count/static/src/xml/company_count.xml'
        ]
    },
    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
    'license': 'LGPL-3',
}
