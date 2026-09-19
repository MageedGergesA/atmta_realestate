# -*- coding: utf-8 -*-
{
    'name': "Real Estate — 3D Maquette",
    'summary': "Interactive 3D building/compound viewer with click-to-pick unit and downstream actions.",
    'description': """
Real Estate 3D Maquette
=======================
Upload a glTF (.glb) model of a building or compound and view it inside the
project form. Sales agents can orbit/zoom/pan the building, click any unit
and:

* See unit info, status (Available / Reserved / Sold), price and floor plan
* Reserve, list, sell, or open the full unit form — every existing property
  action is available
* Units are color-coded live by state, so you see availability at a glance

Mesh → property mapping is auto-resolved from the property code, with manual
override available.
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.6',
    'category': 'Real Estate',
    'depends': [
        'atmta_property_core',
        'atmta_real_estate',
        'real_estate_developer',
        'web',
    ],
    'data': [
        'security/visual_security.xml',
        'security/ir.model.access.csv',
        'views/spec_tag_views.xml',
        'views/building_floor_views.xml',
        'views/unit_picker_views.xml',
        'views/project_views.xml',
        'views/property_views.xml',
        'views/building_region_views.xml',
        'views/building_preview_views.xml',
        'views/visual_gallery_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        # HOOT unit tests. `chooseExperience()` and the disposal helpers are
        # pure functions precisely so the fallback decision can be verified
        # without a GPU.
        # Browser tours — need @web_tour, which lives in the tests bundle.
        'web.assets_tests': [
            '/real_estate_maquette/static/tests/tours/**/*.js',
        ],
        'web.assets_unit_tests': [
            '/real_estate_maquette/static/tests/**/*.test.js',
        ],
        'web.assets_backend': [
            '/real_estate_maquette/static/src/scss/maquette_viewer.scss',
            # Imported by maquette_viewer.js. Declared before it so the module
            # graph resolves in both bundles — omitting them broke every
            # backend page, not just the viewer.
            '/real_estate_maquette/static/src/js/visual_capability.js',
            '/real_estate_maquette/static/src/js/visual_dispose.js',
            '/real_estate_maquette/static/src/js/maquette_viewer.js',
            '/real_estate_maquette/static/src/js/maquette_field.js',
            '/real_estate_maquette/static/src/js/glb_viewer.js',
            '/real_estate_maquette/static/src/js/image_carousel_dialog.js',
            '/real_estate_maquette/static/src/js/image_carousel.js',
            '/real_estate_maquette/static/src/js/building_elevation.js',
            '/real_estate_maquette/static/src/js/maquette_preview.js',
            '/real_estate_maquette/static/src/js/master_plan_2d.js',
            '/real_estate_maquette/static/src/js/master_plan_2d_field.js',
            '/real_estate_maquette/static/src/scss/visual_gallery.scss',
            '/real_estate_maquette/static/src/js/visual_gallery.js',
            '/real_estate_maquette/static/src/xml/visual_gallery.xml',
            '/real_estate_maquette/static/src/xml/maquette_viewer.xml',
        ],
        # Same components reused on public portal pages (portal mode is set
        # from the mount widget in real_estate_portal). Note: image_carousel.js
        # is NOT included because it imports @web/views/fields/... which is
        # backend-only; the frontend uses image_carousel_dialog.js.
        'web.assets_frontend': [
            '/real_estate_maquette/static/src/scss/maquette_viewer.scss',
            '/real_estate_maquette/static/src/js/visual_capability.js',
            '/real_estate_maquette/static/src/js/visual_dispose.js',
            '/real_estate_maquette/static/src/js/image_carousel_dialog.js',
            '/real_estate_maquette/static/src/js/building_elevation.js',
            '/real_estate_maquette/static/src/js/maquette_viewer.js',
            '/real_estate_maquette/static/src/js/master_plan_2d.js',
            '/real_estate_maquette/static/src/xml/maquette_viewer.xml',
        ],
    },
    'application': True,
}
