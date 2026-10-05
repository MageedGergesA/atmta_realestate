# -*- coding: utf-8 -*-
"""Demo foundation: the people and the bricks every other demo layer uses.

Why a Python builder rather than a demo XML file
------------------------------------------------
Forty units across three buildings, each with its own floor, area, bedroom
count and price, is a loop -- written out as `<record>` elements it is nine
hundred lines of near-identical XML that nobody will ever read or keep
correct. The builder writes the same records, assigns every one a stable XML
id so later layers (sales, brokerage, construction, handover) can reference
them exactly as if they had been declared in XML, and is called from
``demo/demo.xml`` with ``<function/>`` the way Odoo's own ``account`` module
seeds its demo chart.

Idempotence
-----------
Demo files are re-executed on every module upgrade, so ``_build`` returns
early once its sentinel XML id exists. Without that, a second upgrade would
raise a duplicate ``property_code`` and take the upgrade down with it.
"""

from odoo import api, models

# One flagship development, deliberately. A demo database with twenty
# half-populated projects teaches nobody what the suite does; one project
# carried end to end -- sold, built, inspected, handed over -- does.
PROJECT_CITY = 'Alexandria'

TOWER_A_UNITS = [
    # floor, suffix, usage, area, beds, baths, price/sqm
    (fl, k, 'villa' if fl >= 9 else 'apartment',
     265 if fl >= 9 else (185 if k == 1 else 142),
     4 if fl >= 9 else (3 if k == 1 else 2),
     3 if fl >= 9 else 2,
     62000 if fl >= 9 else 48000 + fl * 900)
    for fl in range(1, 11) for k in (1, 2)
]
TOWER_B_UNITS = [
    (fl, k, 'apartment', 168 if k == 1 else 124, 3 if k == 1 else 2, 2,
     44000 + fl * 800)
    for fl in range(1, 7) for k in (1, 2)
]
RETAIL_UNITS = [
    (1, 'S01', 'retail', 95, 85000), (1, 'S02', 'retail', 120, 85000),
    (1, 'S03', 'retail', 78, 85000), (2, 'S04', 'retail', 140, 82000),
    (2, 'S05', 'retail', 110, 82000), (2, 'S06', 'retail', 65, 82000),
    (2, 'O01', 'office', 210, 72000), (2, 'O02', 'office', 180, 72000),
]

BUYERS = [
    ('Ahmed Hassan Farouk', 'ahmed.farouk@example.com', '+20 100 111 2201'),
    ('Mona Saeed Abdel Rahman', 'mona.saeed@example.com', '+20 100 111 2202'),
    ('Khaled Mostafa Nour', 'khaled.nour@example.com', '+20 100 111 2203'),
    ('Nadia Ibrahim Zaki', 'nadia.zaki@example.com', '+20 100 111 2204'),
    ('Omar Tarek El Sayed', 'omar.elsayed@example.com', '+20 100 111 2205'),
    ('Hala Youssef Kamal', 'hala.kamal@example.com', '+20 100 111 2206'),
    ('Tamer Wagdy Shalaby', 'tamer.shalaby@example.com', '+20 100 111 2207'),
    ('Yasmin Adel Mansour', 'yasmin.mansour@example.com', '+20 100 111 2208'),
]
TENANTS = [
    ('Sherif Adel Mahmoud', 'sherif.adel@example.com', '+20 100 222 3301'),
    ('Laila Hossam Darwish', 'laila.darwish@example.com', '+20 100 222 3302'),
    ('Marwan Fathy Selim', 'marwan.selim@example.com', '+20 100 222 3303'),
    ('Rania Gamal Shawky', 'rania.shawky@example.com', '+20 100 222 3304'),
]
BROKERS = [
    ('Horizon Real Estate Brokers', 'contact@horizon-brokers.example', '+20 3 480 1100'),
    ('Nile Property Partners', 'info@nileproperty.example', '+20 2 273 4455'),
]
CONTRACTORS = [
    ('Delta Construction Co.', 'projects@deltacon.example', '+20 3 491 7788'),
    ('Pyramid MEP Contracting', 'mep@pyramidmep.example', '+20 2 266 9900'),
    ('Alamein Finishing Works', 'ops@alameinfinish.example', '+20 3 455 2211'),
]
VENDORS = [
    ('Egypt Cement & Aggregates', 'sales@egcement.example', '+20 2 240 1122'),
    ('Suez Steel Supplies', 'orders@suezsteel.example', '+20 62 330 4455'),
    ('Cairo Electrical Trading', 'sales@cairoelec.example', '+20 2 259 8877'),
    ('Mediterranean Tiles & Marble', 'info@medtiles.example', '+20 3 420 6633'),
]

MODULE = 'atmta_property_core'
SENTINEL = '%s.demo_tmr_compound' % MODULE


class PropertyDemoBuilder(models.AbstractModel):
    _name = 'realestate.demo.property'
    _description = 'Demo Builder — Partners and Property Hierarchy'

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------
    @api.model
    def _xmlid(self, suffix, record):
        """Give ``record`` the XML id ``atmta_property_core.<suffix>``."""
        self.env['ir.model.data']._update_xmlids([{
            'xml_id': '%s.%s' % (MODULE, suffix),
            'record': record,
            'noupdate': True,
        }])
        return record

    @api.model
    def _ref(self, suffix):
        return self.env.ref('%s.%s' % (MODULE, suffix), raise_if_not_found=False)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    @api.model
    def _ensure_foundation(self):
        """Build the units once, on first ask. Idempotent.

        This module does NOT build them from its own demo file, and must not.
        It sits low in the dependency graph, and both of the tables involved
        grow NOT NULL columns from modules that load after it:

        * ``account`` adds ``res_partner.autopost_bills``;
        * ``stock`` adds ``product_template.tracking``, and
          ``realestate.property`` ``_inherits`` ``product.template``.

        A record created at this module's position is missing those defaults
        and dies with a NotNullViolation on a fresh install -- which is
        exactly what the first version of this file did. So every app layer
        calls this from its own demo file instead; by then the registry is
        complete, and whichever app happens to load first pays for it.
        """
        if self.env.ref(SENTINEL, raise_if_not_found=False):
            return True
        self._build_properties()
        return True

    # ------------------------------------------------------------------
    # The lazy factories every other demo layer builds on.
    #
    # They resolve by XML id first and fall back to creating, so a layer can
    # call them whatever the load order is and whether or not its module
    # happens to depend on the one that first asked for the record.
    # ------------------------------------------------------------------
    @api.model
    def _demo_partner(self, key):
        """``_demo_partner('buyer_1')`` -> the partner, created on first ask."""
        suffix = 'demo_%s' % key
        existing = self._ref(suffix)
        if existing:
            return existing
        group, _sep, raw_index = key.rpartition('_')
        spec = {
            'buyer': (BUYERS, False, PROJECT_CITY, 0),
            'tenant': (TENANTS, False, PROJECT_CITY, 0),
            'broker': (BROKERS, True, 'Cairo', 0),
            'contractor': (CONTRACTORS, True, PROJECT_CITY, 1),
            'vendor': (VENDORS, True, 'Cairo', 1),
        }.get(group)
        if not spec:
            raise ValueError("Unknown demo partner group %r" % (group,))
        rows, is_company, city, supplier = spec
        index = int(raw_index) - 1
        if not 0 <= index < len(rows):
            raise ValueError("No demo partner %r" % (key,))
        name, email, phone = rows[index]
        country = self.env.ref('base.eg', raise_if_not_found=False)
        vals = {
            'name': name, 'email': email, 'phone': phone,
            'country_id': country.id if country else False,
            'city': city,
            'company_type': 'company' if is_company else 'person',
        }
        Partner = self.env['res.partner']
        if 'supplier_rank' in Partner._fields:
            vals['supplier_rank'] = supplier
            vals['customer_rank'] = 0 if supplier else 1
        return self._xmlid(suffix, Partner.create(vals))

    @api.model
    def _demo_partners(self, group, count=None):
        """Every demo partner of a group, in order."""
        rows = {'buyer': BUYERS, 'tenant': TENANTS, 'broker': BROKERS,
                'contractor': CONTRACTORS, 'vendor': VENDORS}[group]
        total = len(rows) if count is None else min(count, len(rows))
        return [self._demo_partner('%s_%d' % (group, i))
                for i in range(1, total + 1)]

    @api.model
    def _demo_unit(self, code):
        """A demo unit by its property code, e.g. ``TMR-A-101``."""
        return self.env['realestate.property'].search(
            [('property_code', '=', code)], limit=1)

    @api.model
    def _demo_units(self, prefix='TMR-'):
        return self.env['realestate.property'].search(
            [('property_code', '=like', prefix + '%'),
             ('hierarchy_level', '=', 'unit')], order='property_code')

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    @api.model
    def _build_properties(self):
        Property = self.env['realestate.property']
        country = self.env.ref('base.eg', raise_if_not_found=False)

        common = {
            'country_id': country.id if country else False,
            'city': PROJECT_CITY,
            'district': 'Sidi Abdel Rahman',
            'construction_status': 'under_construction',
        }
        # A unit with no company makes every record derived from it companyless
        # too, and `_check_company` then rejects the first reservation that
        # points at a company-owned payment plan. Pin the units to the active
        # company so the whole chain below them is consistent.
        if 'company_id' in Property._fields:
            common['company_id'] = self.env.company.id

        def make(suffix, vals):
            existing = self._ref(suffix)
            if existing:
                return existing
            return self._xmlid(suffix, Property.create(dict(common, **vals)))

        compound = make('demo_tmr_compound', {
            'name': 'TMR — Marina Compound',
            'property_code': 'TMR-CMP',
            'hierarchy_level': 'compound',
            'usage_category': 'common_area',
            'area_sqm': 48000,
            'commercial_status': 'unreleased',
            # Sidi Abdel Rahman, North Coast. Real coordinates so the map
            # dashboards plot the portfolio somewhere that exists.
            'latitude': 30.9612,
            'longitude': 28.7644,
        })
        tower_a = make('demo_tmr_tower_a', {
            'name': 'TMR — Tower A (Seaview)', 'property_code': 'TMR-TA',
            'hierarchy_level': 'building', 'parent_id': compound.id,
            'usage_category': 'common_area', 'area_sqm': 18000,
            'number_of_floors': 10, 'number_of_elevators': 4,
            'number_of_parking_lots': 60, 'commercial_status': 'unreleased',
            'latitude': 30.9615, 'longitude': 28.7639,
        })
        tower_b = make('demo_tmr_tower_b', {
            'name': 'TMR — Tower B (Gardenview)', 'property_code': 'TMR-TB',
            'hierarchy_level': 'building', 'parent_id': compound.id,
            'usage_category': 'common_area', 'area_sqm': 14000,
            'number_of_floors': 6, 'number_of_elevators': 3,
            'number_of_parking_lots': 40, 'commercial_status': 'unreleased',
            'latitude': 30.9607, 'longitude': 28.7651,
        })
        podium = make('demo_tmr_podium', {
            'name': 'TMR — Retail Podium', 'property_code': 'TMR-RP',
            'hierarchy_level': 'building', 'parent_id': compound.id,
            'usage_category': 'common_area', 'area_sqm': 6000,
            'number_of_floors': 2, 'commercial_status': 'unreleased',
            'latitude': 30.9618, 'longitude': 28.7656,
        })

        def unit(suffix, code, name, parent, usage, area, floor, beds, baths,
                 price, for_rent, furnished, parking, lat, lng, status='ready'):
            make(suffix, {
                # Construction status gates availability: a unit that is still
                # under construction is correctly not available to let, and
                # setting every unit that way left the Rental dashboard with
                # nothing available and nothing to let. Phase 1's towers are
                # delivered; the Phase 2 podium is still being built.
                'construction_status': status,
                'name': name, 'property_code': code, 'hierarchy_level': 'unit',
                'parent_id': parent.id, 'usage_category': usage,
                'area_sqm': area, 'floor_number': floor,
                'bedroom_count': beds, 'bathroom_count': baths,
                'living_room_count': 1 if beds else 0,
                'has_kitchen': bool(beds), 'has_balcony': bool(beds),
                'furnished_status': furnished, 'base_price': price,
                'number_of_parking_lots': parking,
                'commercial_status': 'available', 'for_rent': for_rent,
                'latitude': lat, 'longitude': lng,
            })

        for fl, k, usage, area, beds, baths, rate in TOWER_A_UNITS:
            no = fl * 100 + k
            unit('demo_tmr_a_%d' % no, 'TMR-A-%d' % no,
                 'Tower A — Unit %d' % no, tower_a, usage, area, fl, beds,
                 baths, round(area * rate), False, 'semi',
                 2 if fl >= 9 else 1,
                 30.9615 + fl * 0.00004, 28.7639 + k * 0.00006, 'ready')
        for fl, k, usage, area, beds, baths, rate in TOWER_B_UNITS:
            no = fl * 100 + k
            unit('demo_tmr_b_%d' % no, 'TMR-B-%d' % no,
                 'Tower B — Unit %d' % no, tower_b, usage, area, fl, beds,
                 baths, round(area * rate), True, 'unfurnished', 1,
                 30.9607 + fl * 0.00004, 28.7651 + k * 0.00006, 'ready')
        for fl, code, usage, area, rate in RETAIL_UNITS:
            label = 'Retail %s' % code if usage == 'retail' else 'Office %s' % code
            unit('demo_tmr_rp_%s' % code.lower(), 'TMR-RP-%s' % code, label,
                 podium, usage, area, fl, 0, 1, round(area * rate), True,
                 'unfurnished', 0,
                 30.9618 + fl * 0.00005, 28.7656 + (ord(code[-1]) % 5) * 0.00006,
                 'under_construction')
