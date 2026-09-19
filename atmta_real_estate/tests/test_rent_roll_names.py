"""Rent roll hierarchy names are text in the reader's language.

``compound_name``, ``building_name`` and ``floor_name`` came straight from
``product_template.name``, which is translatable and therefore stored as JSON.
The view returned ``{"en_US": "Tower A"}`` for a Char field; Python did not
mind, but the web client rendered the object in a list cell and Rent Roll
crashed for every user as soon as a unit had a building ("this.child.mount is
not a function"). Found by the browser crawl.
"""

from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestRentRollNames(LeaseCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Property = cls.env['realestate.property']
        cls.compound = Property.create({
            'name': 'Names Compound', 'property_code': 'NMS-CMP',
            'hierarchy_level': 'compound', 'company_id': cls.company.id,
        })
        cls.building = Property.create({
            'name': 'Tower A', 'property_code': 'NMS-BLD',
            'hierarchy_level': 'building', 'parent_id': cls.compound.id,
            'company_id': cls.company.id,
        })
        cls.floor = Property.create({
            'name': 'Floor 3', 'property_code': 'NMS-FLR',
            'hierarchy_level': 'floor', 'parent_id': cls.building.id,
            'company_id': cls.company.id,
        })
        cls.named_unit = Property.create({
            'name': 'Unit 301', 'property_code': 'NMS-301',
            'hierarchy_level': 'unit', 'parent_id': cls.floor.id,
            'usage_category': 'apartment', 'area_sqm': 80.0,
            'company_id': cls.company.id,
        })

    def _row(self, **context):
        Roll = self.env['realestate.rent.roll'].with_context(**context)
        self.env.flush_all()
        # Each web request reads with an empty cache. Within one test the cache
        # keeps one value per record for these (untranslated) Char fields,
        # whatever the language, so start each read afresh as a request would.
        self.env.invalidate_all()
        return Roll.search([('property_id', '=', self.named_unit.id)])

    def test_names_are_plain_text(self):
        row = self._row()
        self.assertTrue(row)
        spec = {name: {} for name in ('compound_name', 'building_name', 'floor_name')}
        values = row.web_search_read([('id', 'in', row.ids)], spec)['records'][0]
        self.assertEqual(
            (values['compound_name'], values['building_name'], values['floor_name']),
            ('Names Compound', 'Tower A', 'Floor 3'))

    def test_names_follow_the_reader_language(self):
        self.env['res.lang']._activate_lang('ar_001')
        self.building.with_context(lang='ar_001').name = 'البرج أ'
        self.assertEqual(self._row(lang='ar_001').building_name, 'البرج أ')
        self.assertEqual(self._row(lang='en_US').building_name, 'Tower A')

    def test_untranslated_names_fall_back_to_english(self):
        self.env['res.lang']._activate_lang('fr_FR')
        self.assertEqual(self._row(lang='fr_FR').floor_name, 'Floor 3')

    def test_grouping_by_building_gives_text_groups(self):
        groups = self.env['realestate.rent.roll'].read_group(
            [('property_id', '=', self.named_unit.id)], ['annualized_rent:sum'], ['building_name'])
        self.assertEqual([g['building_name'] for g in groups], ['Tower A'])
