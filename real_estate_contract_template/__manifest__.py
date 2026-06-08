# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Contract Template Engine",
    'summary': "Render Word and PDF contracts from .docx templates with Jinja2 placeholders.",
    'description': """
Real Estate Contract Template Engine
====================================
Upload a `.docx` master template, fill it with Jinja2 placeholders
({{ partner_id.name }}, {%tr for line in payment_line_ids %}…{%tr endfor %}),
and generate per-contract Word or PDF documents from any target model
(realestate.sale.contract, realestate.contract, realestate.transaction,
or any other Odoo model).

* Multi-jurisdiction tagging (EG / SA / AE-Dubai / AE-Abu-Dhabi / Generic)
* Multi-kind tagging (sale / rental / brokerage / subcontract / other)
* Output format chosen at generate time: .docx or .pdf
* Bilingual content supported (write Arabic + English in the template itself)
* Form-version stamping per contract — old contracts keep rendering their original template

Requires:
  - Python package: docxtpl
  - System binary: libreoffice (or soffice) for DOCX → PDF conversion
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.1',
    'category': 'Real Estate',
    'depends': [
        'atmta_real_estate',
        'real_estate_developer',
        'real_estate_brokerage',
        'real_estate_construction',
    ],
    'external_dependencies': {
        'python': ['docxtpl'],
        'bin': ['libreoffice'],
    },
    'data': [
        'security/ir.model.access.csv',
        'views/contract_template_views.xml',
        'views/contract_document_views.xml',
        'wizard/contract_generate_wizard_views.xml',
        'wizard/placeholder_catalog_wizard_views.xml',
        'wizard/placeholder_autofill_wizard_views.xml',
        'views/contract_form_buttons.xml',
        'views/menus.xml',
    ],
    'application': False,
}
