# -*- coding: utf-8 -*-
{
    'name': 'ATMTA — Procurement Core',
    'version': '18.0.2.0.0',
    'category': 'Real Estate/Procurement',
    'summary': 'Common procurement identity: policy and product classification.',
    'description': """
ATMTA Procurement Core
======================

The floor of the Procurement domain. It owns **no models of its own** and
carries only what every procurement capability has to agree on:

* **Procurement policy** — the governance settings a company and a project
  answer to: budget policy, PO governance, reservation expiry, amount
  tolerance, self-approval limits, vendor policy, receipt inspection policy,
  qualification warnings, late-bid and addendum policy.
* **Product classification** — the four booleans that say what kind of thing a
  product is to this suite (construction material, property fitting, marketing
  asset, maintenance consumable) and the derived `is_realestate_product`.

Both are read from outside the module that declared them. Measured: the material
request, the procurement control surface and the exception wizards between them
read six of the policy fields.

What it deliberately does **not** carry:

* the `purchase.order` / `purchase.order.line` bridge, which references
  sourcing, award, control, request and vendor models. Wave 11 gave it its own
  module, `atmta_procurement_purchase`, which sits above every capability;
* `account.move` three-way matching, which is receipt work and would drag
  `account` under the whole procurement domain. It belongs to
  `atmta_procurement_receipt`.

A floor that reaches upward is not a floor.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        'atmta_project_core',
        'product',
        # product_template._sync_re_storable writes `is_storable`, which the
        # stock module declares. Found by test, not by reading the catalog.
        'stock',
    ],
    'data': [
        # Wave 11 — the real-estate product classification and its starter catalog,
        # moved from the emptied `real_estate_procurement` shell.
        'data/product_categories.xml',
        'data/products_seed.xml',
        'views/product_views.xml',
        'views/product_categories_views.xml',
    ],
    'application': False,
    'installable': True,
    'auto_install': False,
}
