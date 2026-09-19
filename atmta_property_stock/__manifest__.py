# -*- coding: utf-8 -*-
{
    'name': 'ATMTA — Property Stock',
    'version': '18.0.1.1.0',
    'category': 'Real Estate/Foundation',
    'summary': 'Units as stock: the location tree and the quant lifecycle.',
    'description': """
ATMTA Property Stock
====================

Treats each unit-level property as one storable item and keeps its single quant
where the unit's state says it should be:

* available / rented / maintenance / inactive -> home location
* reserved -> `Reserved Units`
* sold -> `Sold Units`

It owns the four locations that tree is made of (the `Real Estate` root under
the warehouse, `Unassigned Units`, `Reserved Units`, `Sold Units`) and the code
that relocates quants between them. Developer overrides the home location to put
each unit under its project.

Both used to live in `atmta_real_estate`. The locations moved first, so Developer
no longer needed Rental to find the root; the lifecycle followed, because without
it Developer's reservations and sales would install fine and quietly stop moving
stock. Nothing here depends on leasing.

`invoice_policy` belongs to `sale`. Units get the `order` policy whenever that
field exists -- every installation with Rental or Developer -- so behaviour there
is unchanged; this module does not pull `sale` in by itself.

Upgrading an existing database adopts the four location identifiers before this
module's own data loads (`pre_init_hook`), so the existing locations are reused
rather than duplicated and every quant keeps its location.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'stock',
        # realestate.property, which the lifecycle extends, and the property
        # form the stock button is added to.
        'atmta_property_core',
    ],
    'data': [
        'data/stock_locations.xml',
        'views/property_views.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'auto_install': False,
}
