# -*- coding: utf-8 -*-
{
    "name": "Google Maps Viewer Widget",
    "summary": "A widget that allows embedding Google Maps URLs inside Odoo form views.",
    "version": "1.0",
    "author": "MGA",
    "category": "Tools",
    "license": "LGPL-3",
    "depends": ["base", "web"],
    'version': '0.1',
    # always loaded
    'data': [
        # 'security/ir.model.access.csv',
        # 'views/views.xml',
        # 'views/templates.xml',
    ],

    'assets': {
        'web.assets_backend': [
            'google_maps_viewer_widget/static/src/js/*'
        ]
    },

    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
    "urrency": "USD",
    "price": "20.0",
    "images": ["static/description/icon.png"],
    "installable": True,
    "description": """
    Google Maps Viewer Widget for Odoo

    This module introduces a new Google Maps Widget called 'embed_map_viewer', 
    allowing users to embed and view Google Maps directly inside Odoo form views.

    🔹 Key Features:
    ✅ New Widget: embed_map_viewer – Easily add it to any Odoo form field.
    ✅ Embed Google Maps in Form Views – Copy and paste the Google Maps embed code.
    ✅ Supports Google Maps Embed API – Works with iframe embed links from Google Maps.
    ✅ Interactive & Responsive – Allows zooming, panning, and interaction within Odoo.
    ✅ No Coding Required – Simple installation and usage with Odoo’s built-in form fields.

    📌 How to Use:
    To use this widget, follow these steps:

    🔹 Step 1: Copy Google Maps Embed Code
    Go to Google Maps (https://www.google.com/maps) and find the location you want to embed.
    1. Click the "Share" button.
    2. Go to the "Embed a Map" tab.
    3. Click "Copy HTML".

    🔹 Step 2: Paste the Embed Code into Odoo
    Paste the copied iframe code inside the Odoo form field that supports the 'embed_map_viewer' widget.

    📌 Example Embed Code:
    <iframe src="https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d75658.17791774835!2d31.642648599999998!3d30.09177585!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x14581e0197d20347%3A0x77e815673cfde03b!2sMadinaty, Second New Cairo, Cairo Governorate!5e1!3m2!1sen!2seg!4v1739799423167!5m2!1sen!2seg" 
    width="600" height="450" style="border:0;" allowfullscreen="" loading="lazy"></iframe>

    🔹 Step 3: Save and View the Map
    The embedded map will now appear inside the Odoo form view.

    🎯 Use Cases:
    📍 Add Google Maps location previews for partners, customers, or suppliers.
    🏢 Embed Google Maps into custom Odoo models (Real Estate, Logistics, CRM, etc.).
    🗺️ Allow users to view and confirm addresses directly in Odoo.

    🚀 Enhance your Odoo experience by embedding Google Maps with this easy-to-use widget!
    """
}
