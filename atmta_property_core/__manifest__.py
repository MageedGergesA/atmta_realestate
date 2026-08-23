# -*- coding: utf-8 -*-
{
    'name': 'ATMTA — Property Core',
    'version': '18.0.1.0.0',
    'category': 'Real Estate/Core',
    'summary': 'Authoritative definition of Property and its reference data.',
    'description': """
ATMTA Property Core
===================

The single authoritative owner of the physical asset and the reference data
that classifies it:

* ``realestate.property`` — the unit, building, floor or compound itself,
  including its hierarchy, its location and its delegated product;
* ``property.type`` — the category taxonomy ("Apartment", "Villa");
* ``property.usage`` — what the asset is used for;
* ``property.image`` — its media.

It knows nothing about leases, sales, reservations, handover, brokerage or
maintenance. Anything that reads a contract, a price or a work order belongs in
a module *above* this one.

``realestate.property`` delegates to ``product.template`` through
``_inherits``, so every property owns a product record; that is why this module
depends on ``product``. ``property.image`` renders video embeds through
``web_editor``, which ``atmta_real_estate`` picked up transitively via ``sale``
and which is declared explicitly here.

Extracted from ``atmta_real_estate`` in ATMTA V2 Wave 3. The models, their
tables and their record identifiers are unchanged; only the module that
declares them moved.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        # realestate.property _inherits product.template.
        'product',
        # property.image builds video embed codes with web_editor.tools.
        'web_editor',
    ],
    'data': [
        'data/property_sequence.xml',
        'security/property_core_rules.xml',
    ],
    'application': False,
    'installable': True,
    'auto_install': False,
}
