# -*- coding: utf-8 -*-
{
    'name': 'ATMTA — Sale Bridge',
    'version': '18.0.1.0.0',
    'category': 'Real Estate/Foundation',
    'summary': 'Mirror a real-estate deal onto a sale order, without deliveries.',
    'description': """
ATMTA Sale Bridge
=================

A real-estate sale is recorded on `sale.order` for the sales pipeline and for
invoicing: one order, one unit line at the full price. This module adds the
three fields that mark and trace such an order (`is_re_bridge`,
`re_source_model`, `re_source_id`), the factory that creates and confirms one
(`sale.order._create_re_bridge_order`), and stops those orders from generating
deliveries -- a unit's quant is moved by the property lifecycle, not by a
picking.

Rental, Developer (sale contracts) and Brokerage (in-house transactions) all
create bridge orders. It lived in `atmta_real_estate`, so Developer and
Brokerage could not run without the Rental app; it moved here so each depends
on the bridge alone. It touches no property model.

It depends on `sale_stock` because that module defines the stock-rule hook the
bridge overrides. `sale_stock` installs automatically wherever `sale` and
`stock` are both present, so this adds nothing to an app that sells units.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': ['sale_stock'],
    'data': [],
    'installable': True,
    'auto_install': False,
}
